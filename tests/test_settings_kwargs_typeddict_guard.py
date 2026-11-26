"""Guard: every settings ``*_kwargs()`` mapper returns a matching ``TypedDict``.

Every ``*_kwargs()`` method on a settings mixin is meant to be splatted into a
concrete constructor or factory (``EmailUtils(**settings.email_kwargs())``).
Returning ``dict[str, Any]`` disables type checking at every call site: a
renamed constructor parameter or a wrong type ships unnoticed until runtime.

This guard enforces four invariants across every public ``*_kwargs()`` method
in ``tempest_fastapi_sdk.settings``:

1. **No ``Any`` in settings returns.** No public function or method under
   ``tempest_fastapi_sdk/settings/`` declares ``Any`` (bare or nested) in its
   return annotation.
2. **Exhaustive ``TypedDict`` coverage.** Every public ``*_kwargs()`` method
   exported on a settings mixin returns a ``TypedDict`` and appears in
   :data:`KWARGS_TARGETS`.
3. **Keys and optionality match ``inspect.signature``.** Every key of each
   ``TypedDict`` is a keyword-accepting parameter on its target callable, and
   every optional (``NotRequired`` / ``total=False``) key has a default on the
   target so omitting it never raises ``TypeError``.
4. **End-to-end mypy splat checking.** Running mypy against consumer code that
   splats every mapper into its target succeeds cleanly, while splatting a
   mismatched ``TypedDict`` or assigning an unknown key is rejected.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import (
    Any,
    ForwardRef,
    NotRequired,
    Required,
    TypedDict,
    get_origin,
    is_typeddict,
)

import pytest
from starlette.responses import Response

from tempest_fastapi_sdk import (
    GitHubOAuthClient,
    GoogleOAuthClient,
)
from tempest_fastapi_sdk import settings as settings_module
from tempest_fastapi_sdk.auth import FirebaseAuth
from tempest_fastapi_sdk.cache.redis_manager import AsyncRedisManager
from tempest_fastapi_sdk.core.logging import configure_logging
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    OpenAICompatGenerator,
    OpenAICompatSpeech,
    hosted_generator,
)
from tempest_fastapi_sdk.hostbridge.config import HostBridgeConfig
from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveUploadStorage,
)
from tempest_fastapi_sdk.settings import (
    AnthropicKwargs,
    BaseAppSettings,
    DatabaseKwargs,
    DatabaseSettings,
    DeepSeekKwargs,
    EmailKwargs,
    EmailSettings,
    FirebaseKwargs,
    FirebaseSettings,
    GeminiKwargs,
    GitHubOAuthKwargs,
    GoogleDriveSettings,
    GoogleDriveStorageKwargs,
    GoogleOAuthKwargs,
    HostBridgeKwargs,
    HostBridgeSettings,
    HostedGenAIKwargs,
    HostedGenAISettings,
    HTTPClientKwargs,
    JWTKwargs,
    JWTSettings,
    KafkaKwargs,
    KafkaSettings,
    LocalStorageKwargs,
    LoggingKwargs,
    LogSettings,
    MercadoPagoSettings,
    OAuthSettings,
    OpenAIKwargs,
    OpenAISpeechKwargs,
    OpenPixSettings,
    RedisKwargs,
    RedisSettings,
    RevenueCatSettings,
    SessionCookieDeleteKwargs,
    SessionCookieKwargs,
    SessionSettings,
    StorageKwargs,
    StorageSettings,
    UploadKwargs,
    UploadSettings,
    WebPushKwargs,
    WebPushSettings,
)
from tempest_fastapi_sdk.storage import (
    AsyncMinIOClient,
    BucketRoutedMinIOClient,
    LocalObjectStorageClient,
)
from tempest_fastapi_sdk.utils import EmailUtils, JWTUtils, UploadUtils
from tempest_fastapi_sdk.utils.http_client import HTTPClient
from tempest_fastapi_sdk.webpush.dispatcher import WebPushDispatcher

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
"""Repository root, used for relative paths and mypy configuration."""

SETTINGS_DIR: Path = REPO_ROOT / "tempest_fastapi_sdk" / "settings"
"""Settings subpackage scanned for ``Any`` return annotations."""


def _kafka_broker_init() -> Any:
    """Return ``faststream.kafka.KafkaBroker.__init__`` when installed.

    Returns:
        Any: The ``KafkaBroker.__init__`` callable, or ``None`` when the
        optional ``[queue-kafka]`` extra is absent.
    """
    try:
        from faststream.kafka import KafkaBroker
    except ImportError:
        return None
    return KafkaBroker.__init__


KWARGS_TARGETS: dict[
    tuple[type[BaseAppSettings], str], tuple[type[Any], tuple[Any, ...]]
] = {
    (LogSettings, "logging_kwargs"): (LoggingKwargs, (configure_logging,)),
    (DatabaseSettings, "database_kwargs"): (
        DatabaseKwargs,
        (AsyncDatabaseManager.__init__,),
    ),
    (RedisSettings, "redis_kwargs"): (RedisKwargs, (AsyncRedisManager.__init__,)),
    (JWTSettings, "jwt_kwargs"): (JWTKwargs, (JWTUtils.__init__,)),
    (EmailSettings, "email_kwargs"): (EmailKwargs, (EmailUtils.__init__,)),
    (UploadSettings, "upload_kwargs"): (UploadKwargs, (UploadUtils.__init__,)),
    (WebPushSettings, "webpush_kwargs"): (
        WebPushKwargs,
        (WebPushDispatcher.__init__,),
    ),
    (FirebaseSettings, "firebase_kwargs"): (
        FirebaseKwargs,
        (FirebaseAuth.__init__,),
    ),
    (GoogleDriveSettings, "google_drive_kwargs"): (
        GoogleDriveStorageKwargs,
        (GoogleDriveUploadStorage.from_service_account,),
    ),
    (KafkaSettings, "kafka_kwargs"): (KafkaKwargs, (_kafka_broker_init(),)),
    (OAuthSettings, "google_kwargs"): (
        GoogleOAuthKwargs,
        (GoogleOAuthClient.__init__,),
    ),
    (OAuthSettings, "github_kwargs"): (
        GitHubOAuthKwargs,
        (GitHubOAuthClient.__init__,),
    ),
    (StorageSettings, "local_storage_kwargs"): (
        LocalStorageKwargs,
        (LocalObjectStorageClient.__init__,),
    ),
    (StorageSettings, "storage_kwargs"): (
        StorageKwargs,
        (AsyncMinIOClient.__init__, BucketRoutedMinIOClient.__init__),
    ),
    (StorageSettings, "minio_kwargs"): (
        StorageKwargs,
        (AsyncMinIOClient.__init__, BucketRoutedMinIOClient.__init__),
    ),
    (SessionSettings, "session_cookie_kwargs"): (
        SessionCookieKwargs,
        (Response.set_cookie,),
    ),
    (SessionSettings, "session_cookie_delete_kwargs"): (
        SessionCookieDeleteKwargs,
        (Response.delete_cookie,),
    ),
    (HostedGenAISettings, "anthropic_kwargs"): (
        AnthropicKwargs,
        (AnthropicGenerator.__init__,),
    ),
    (HostedGenAISettings, "openai_kwargs"): (
        OpenAIKwargs,
        (OpenAICompatGenerator.__init__,),
    ),
    (HostedGenAISettings, "openai_speech_kwargs"): (
        OpenAISpeechKwargs,
        (OpenAICompatSpeech.__init__,),
    ),
    (HostedGenAISettings, "deepseek_kwargs"): (
        DeepSeekKwargs,
        (OpenAICompatGenerator.__init__,),
    ),
    (HostedGenAISettings, "gemini_kwargs"): (
        GeminiKwargs,
        (OpenAICompatGenerator.__init__,),
    ),
    (HostedGenAISettings, "hosted_kwargs"): (
        HostedGenAIKwargs,
        (hosted_generator,),
    ),
    (OpenPixSettings, "openpix_kwargs"): (HTTPClientKwargs, (HTTPClient.__init__,)),
    (MercadoPagoSettings, "mercado_pago_kwargs"): (
        HTTPClientKwargs,
        (HTTPClient.__init__,),
    ),
    (RevenueCatSettings, "revenuecat_kwargs"): (
        HTTPClientKwargs,
        (HTTPClient.__init__,),
    ),
    (HostBridgeSettings, "hostbridge_kwargs"): (
        HostBridgeKwargs,
        (HostBridgeConfig.__init__,),
    ),
}
"""Every ``(Mixin, method_name)`` mapped to ``(TypedDict, target_callables)``."""


def _discover_kwargs_mappers() -> set[tuple[type[BaseAppSettings], str]]:
    """Collect every public ``*_kwargs`` method defined on exported settings mixins.

    Returns:
        set[tuple[type[BaseAppSettings], str]]: ``(defining_class, method_name)``
        pairs across ``tempest_fastapi_sdk.settings.__all__``.
    """
    found: set[tuple[type[BaseAppSettings], str]] = set()
    for name in settings_module.__all__:
        value = getattr(settings_module, name)
        if not (
            inspect.isclass(value)
            and issubclass(value, BaseAppSettings)
            and value is not BaseAppSettings
        ):
            continue
        for attr_name, attr_val in vars(value).items():
            if (
                not attr_name.startswith("_")
                and attr_name.endswith("_kwargs")
                and inspect.isfunction(attr_val)
            ):
                found.add((value, attr_name))
    return found


def _optional_keys_of(typed_dict: type[Any]) -> frozenset[str]:
    """Return optional keys of ``typed_dict``, including deferred annotations.

    Under ``from __future__ import annotations``, Python < 3.14 stores
    ``NotRequired[T]`` as a string so ``TypedDict.__optional_keys__`` misses it.
    Inspecting both the runtime set and raw annotation strings keeps the guard
    accurate regardless of how the module declares annotations.

    Args:
        typed_dict (type[Any]): The ``TypedDict`` class to inspect.

    Returns:
        frozenset[str]: Keys that may be omitted from a conforming mapping.
    """
    optional: set[str] = set(getattr(typed_dict, "__optional_keys__", frozenset()))
    annotations: dict[str, Any] = getattr(typed_dict, "__annotations__", {})
    for key, annotation in annotations.items():
        if get_origin(annotation) is NotRequired:
            optional.add(key)
            continue
        if get_origin(annotation) is Required:
            optional.discard(key)
            continue
        raw: str | None = (
            annotation.__forward_arg__
            if isinstance(annotation, ForwardRef)
            else annotation
            if isinstance(annotation, str)
            else None
        )
        if raw is not None:
            stripped = raw.strip()
            if stripped.startswith(("NotRequired[", "typing.NotRequired[")):
                optional.add(key)
            elif stripped.startswith(("Required[", "typing.Required[")):
                optional.discard(key)
    return frozenset(optional)


def validate_typeddict_against_target(
    typed_dict: type[Any],
    target: Any,
) -> list[str]:
    """Compare a ``TypedDict``'s keys and optionality against ``target``'s signature.

    Args:
        typed_dict (type[Any]): The ``TypedDict`` class returned by a mapper.
        target (Any): The constructor or factory callable the dict is splatted into.

    Returns:
        list[str]: Human-readable discrepancy messages; empty when every key
        exists as a keyword-accepting parameter and every optional key has a
        default on ``target``.
    """
    problems: list[str] = []
    if not is_typeddict(typed_dict):
        return [f"{typed_dict!r} is not a TypedDict"]

    signature = inspect.signature(target)
    params = signature.parameters
    required_keys: frozenset[str] = getattr(
        typed_dict, "__required_keys__", frozenset()
    )
    optional_keys: frozenset[str] = _optional_keys_of(typed_dict)
    all_keys: frozenset[str] = required_keys | optional_keys

    for key in sorted(all_keys):
        param = params.get(key)
        if param is None:
            problems.append(
                f"{typed_dict.__name__}.{key}: not a parameter of {target.__qualname__}"
            )
            continue
        if param.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            problems.append(
                f"{typed_dict.__name__}.{key}: parameter on {target.__qualname__} "
                f"is {param.kind.name}, cannot be passed by keyword"
            )
            continue
        if key in optional_keys and param.default is inspect.Parameter.empty:
            problems.append(
                f"{typed_dict.__name__}.{key}: optional in TypedDict but required "
                f"(no default) on {target.__qualname__}"
            )
    return problems


def find_any_returns_in_sources(sources: dict[str, str]) -> list[str]:
    """Find public functions or methods whose return annotation mentions ``Any``.

    Args:
        sources (dict[str, str]): Relative path to Python source text.

    Returns:
        list[str]: Sorted ``path::qualname -> annotation`` findings.
    """
    findings: list[str] = []

    def _contains_any(node: ast.expr | None) -> bool:
        if node is None:
            return False
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id == "Any":
                return True
            if isinstance(child, ast.Attribute) and child.attr == "Any":
                return True
        return False

    class _Visitor(ast.NodeVisitor):
        def __init__(self, rel_path: str) -> None:
            self._rel_path: str = rel_path
            self._scope: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()

        def _visit_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            if not node.name.startswith("_") and _contains_any(node.returns):
                qualname = ".".join([*self._scope, node.name])
                assert node.returns is not None
                findings.append(
                    f"{self._rel_path}::{qualname} -> {ast.unparse(node.returns)}"
                )
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_fn(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_fn(node)

    for rel_path, text in sources.items():
        _Visitor(rel_path).visit(ast.parse(text, filename=rel_path))
    return sorted(findings)


def test_every_settings_kwargs_mapper_is_registered() -> None:
    """Every public ``*_kwargs()`` method on exported settings mixins is guarded."""
    discovered = _discover_kwargs_mappers()
    registered = set(KWARGS_TARGETS)
    assert discovered == registered, (
        f"Missing from KWARGS_TARGETS: {discovered - registered}; "
        f"stale in KWARGS_TARGETS: {registered - discovered}"
    )


def test_no_public_any_in_settings_returns() -> None:
    """No public function or method in ``settings/`` declares ``Any`` in its return."""
    sources = {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SETTINGS_DIR.rglob("*.py"))
    }
    assert find_any_returns_in_sources(sources) == []


@pytest.mark.parametrize(
    "key",
    list(KWARGS_TARGETS),
    ids=lambda key: f"{key[0].__name__}.{key[1]}",
)
def test_kwargs_typeddict_matches_target_signature(
    key: tuple[type[BaseAppSettings], str],
) -> None:
    """Each mapper's return annotation is its ``TypedDict`` and matches the target."""
    mixin_cls, method_name = key
    typed_dict, targets = KWARGS_TARGETS[key]

    assert is_typeddict(typed_dict), f"{typed_dict!r} is not a TypedDict"
    assert typed_dict.__name__ in settings_module.__all__, (
        f"{typed_dict.__name__} must be re-exported from tempest_fastapi_sdk.settings"
    )

    method = getattr(mixin_cls, method_name)
    raw_return = method.__annotations__.get("return")
    assert raw_return == typed_dict.__name__, (
        f"{mixin_cls.__name__}.{method_name} declares return {raw_return!r}, "
        f"expected {typed_dict.__name__!r}"
    )

    for target in targets:
        if target is None:
            pytest.skip(
                f"optional dependency missing for {mixin_cls.__name__}.{method_name}"
            )
        problems = validate_typeddict_against_target(typed_dict, target)
        assert problems == [], "\n".join(problems)


def test_runtime_optional_keys_match_conditional_mapper_keys() -> None:
    """Conditional keys are marked optional in ``__optional_keys__`` at runtime."""
    assert EmailKwargs.__optional_keys__ == frozenset({"enabled"})
    assert FirebaseKwargs.__optional_keys__ == frozenset(
        {"credentials_json", "credentials_path", "project_id", "enabled"}
    )
    assert KafkaKwargs.__optional_keys__ == frozenset({"client_id"})
    assert LocalStorageKwargs.__optional_keys__ == frozenset(
        {"encryption_keys", "allow_plaintext_read", "enabled"}
    )
    assert StorageKwargs.__optional_keys__ == frozenset({"enabled"})


def test_settings_import_stays_free_of_optional_extras() -> None:
    """Importing ``tempest_fastapi_sdk.settings`` does not pull heavy extras."""
    forbidden = (
        "faststream",
        "minio",
        "sqlalchemy",
        "httpx",
        "firebase_admin",
        "tempest_fastapi_sdk.genai",
        "tempest_fastapi_sdk.integrations",
    )
    code = (
        "import sys\n"
        "import tempest_fastapi_sdk.settings\n"
        f"loaded = [m for m in {forbidden!r} if m in sys.modules]\n"
        "print(','.join(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert completed.stdout.strip() == ""


def _run_mypy(source: str) -> str:
    """Run mypy with the repository config over ``source`` and return combined output.

    Args:
        source (str): Python module text to type-check.

    Returns:
        str: Combined stdout and stderr emitted by mypy.
    """
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "settings_kwargs_probe.py"
        probe.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(REPO_ROOT / "pyproject.toml"),
                "--no-error-summary",
                str(probe),
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    return proc.stdout + proc.stderr


def test_mypy_accepts_every_splat_and_rejects_mismatches() -> None:
    """Mypy accepts every valid ``**settings.xxx_kwargs()`` and rejects mismatches."""
    valid_probe = """
from starlette.responses import Response

from tempest_fastapi_sdk import GitHubOAuthClient, GoogleOAuthClient
from tempest_fastapi_sdk.auth import FirebaseAuth
from tempest_fastapi_sdk.cache.redis_manager import AsyncRedisManager
from tempest_fastapi_sdk.core.logging import configure_logging
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    OpenAICompatGenerator,
    OpenAICompatSpeech,
    hosted_generator,
)
from tempest_fastapi_sdk.hostbridge.config import HostBridgeConfig
from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveUploadStorage,
)
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.settings import (
    DatabaseSettings,
    EmailSettings,
    FirebaseSettings,
    GoogleDriveSettings,
    HostBridgeSettings,
    HostedGenAISettings,
    JWTSettings,
    KafkaSettings,
    LogSettings,
    MercadoPagoSettings,
    OAuthSettings,
    OpenPixSettings,
    RedisSettings,
    RevenueCatSettings,
    SessionSettings,
    StorageSettings,
    UploadSettings,
    WebPushSettings,
)
from tempest_fastapi_sdk.storage import (
    AsyncMinIOClient,
    BucketRoutedMinIOClient,
    LocalObjectStorageClient,
    StorageBucketCredential,
)
from tempest_fastapi_sdk.utils import EmailUtils, JWTUtils, UploadUtils
from tempest_fastapi_sdk.utils.http_client import HTTPClient
from tempest_fastapi_sdk.webpush.dispatcher import WebPushDispatcher


def check_all(
    log_s: LogSettings,
    db_s: DatabaseSettings,
    redis_s: RedisSettings,
    jwt_s: JWTSettings,
    email_s: EmailSettings,
    upload_s: UploadSettings,
    webpush_s: WebPushSettings,
    firebase_s: FirebaseSettings,
    kafka_s: KafkaSettings,
    oauth_s: OAuthSettings,
    storage_s: StorageSettings,
    session_s: SessionSettings,
    genai_s: HostedGenAISettings,
    openpix_s: OpenPixSettings,
    mp_s: MercadoPagoSettings,
    rc_s: RevenueCatSettings,
    hb_s: HostBridgeSettings,
    gd_s: GoogleDriveSettings,
    creds: dict[str, StorageBucketCredential],
) -> None:
    configure_logging(**log_s.logging_kwargs())
    AsyncDatabaseManager(**db_s.database_kwargs())
    AsyncRedisManager(**redis_s.redis_kwargs())
    JWTUtils(**jwt_s.jwt_kwargs())
    EmailUtils(**email_s.email_kwargs())
    UploadUtils(**upload_s.upload_kwargs())
    WebPushDispatcher(**webpush_s.webpush_kwargs())
    FirebaseAuth(**firebase_s.firebase_kwargs())
    MessageBroker.kafka(*kafka_s.KAFKA_BOOTSTRAP_SERVERS, **kafka_s.kafka_kwargs())
    GoogleOAuthClient(**oauth_s.google_kwargs())
    GitHubOAuthClient(**oauth_s.github_kwargs())
    LocalObjectStorageClient(**storage_s.local_storage_kwargs())
    AsyncMinIOClient(**storage_s.storage_kwargs())
    AsyncMinIOClient(**storage_s.minio_kwargs())
    BucketRoutedMinIOClient(**storage_s.storage_kwargs(), bucket_credentials=creds)
    resp = Response()
    resp.set_cookie(value="tok", **session_s.session_cookie_kwargs())
    resp.delete_cookie(**session_s.session_cookie_delete_kwargs())
    AnthropicGenerator(**genai_s.anthropic_kwargs())
    OpenAICompatGenerator(**genai_s.openai_kwargs())
    OpenAICompatSpeech(**genai_s.openai_speech_kwargs())
    OpenAICompatGenerator(**genai_s.deepseek_kwargs())
    OpenAICompatGenerator(**genai_s.gemini_kwargs())
    hosted_generator(**genai_s.hosted_kwargs("claude-haiku-5-5"))
    HTTPClient(**openpix_s.openpix_kwargs())
    HTTPClient(**mp_s.mercado_pago_kwargs())
    HTTPClient(**rc_s.revenuecat_kwargs())
    HostBridgeConfig(**hb_s.hostbridge_kwargs())
    GoogleDriveUploadStorage.from_service_account(**gd_s.google_drive_kwargs())
"""
    valid_output = _run_mypy(valid_probe)
    assert "error:" not in valid_output, f"Unexpected mypy error:\n{valid_output}"

    invalid_probe = """
from tempest_fastapi_sdk.settings import DatabaseSettings, RedisSettings
from tempest_fastapi_sdk.utils import EmailUtils, JWTUtils


def check_bad(redis_s: RedisSettings, db_s: DatabaseSettings) -> None:
    EmailUtils(**redis_s.redis_kwargs())
    JWTUtils(**db_s.database_kwargs())
    kw = redis_s.redis_kwargs()
    kw["nonexistent_key"] = 123
"""
    invalid_output = _run_mypy(invalid_probe)
    error_lines = [line for line in invalid_output.splitlines() if "error:" in line]
    assert len(error_lines) >= 3, (
        f"Expected mypy to reject all 3 errors:\n{invalid_output}"
    )
    assert "nonexistent_key" in invalid_output


class TestGuardFires:
    """Prove every detector in this guard catches the defects it exists to prevent."""

    def test_unknown_key_in_typeddict_is_reported(self) -> None:
        class StaleKwargs(TypedDict):
            url: str
            renamed_param: bool

        def target(*, url: str, decode_responses: bool = True) -> None:
            return None

        problems = validate_typeddict_against_target(StaleKwargs, target)
        assert any("renamed_param" in msg for msg in problems), problems

    def test_optional_key_for_required_target_param_is_reported(self) -> None:
        class BadOptionalKwargs(TypedDict):
            host: str
            port: NotRequired[int]

        def target(*, host: str, port: int) -> None:
            return None

        problems = validate_typeddict_against_target(BadOptionalKwargs, target)
        assert any(
            "optional in TypedDict" in msg and "port" in msg for msg in problems
        ), problems

    def test_positional_only_target_param_is_reported(self) -> None:
        class PosOnlyKwargs(TypedDict):
            value: str

        def target(value: str, /) -> None:
            return None

        problems = validate_typeddict_against_target(PosOnlyKwargs, target)
        assert any("POSITIONAL_ONLY" in msg for msg in problems), problems

    def test_nested_any_return_is_reported(self) -> None:
        historical = {
            "tempest_fastapi_sdk/settings/mixins.py": (
                "from typing import Any\n"
                "class EmailSettings:\n"
                "    def email_kwargs(self) -> dict[str, Any]:\n"
                "        return {}\n"
                "    def _private(self) -> dict[str, Any]:\n"
                "        return {}\n"
            )
        }
        assert find_any_returns_in_sources(historical) == [
            "tempest_fastapi_sdk/settings/mixins.py::"
            "EmailSettings.email_kwargs -> dict[str, Any]"
        ]
