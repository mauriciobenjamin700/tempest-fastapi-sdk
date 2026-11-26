"""Guard: no public function or method in the package accepts bare ``Any``.

An explicit bare ``Any`` parameter disables static checks at every call site:
misspelled attributes, swapped arguments, and wrong container shapes all pass
silently. Every public positional-only, positional-or-keyword, and keyword-only
parameter must name a real type (``TypeVar`` / ``ParamSpec`` when linked to the
return or another parameter, a concrete type imported under ``TYPE_CHECKING``,
a named alias like ``JsonValue`` / ``TabularInput`` / ``ImageSource``, a
``Protocol`` describing the slice the SDK reads, or ``object`` when the body
explicitly narrows at runtime).

``*args: Any`` and ``**kwargs: Any`` pure-passthrough varargs (``vararg`` and
``kwarg`` AST slots) are intentionally excluded from this guard: forwarding
hooks and adapter wrappers legitimately accept opaque passthrough arguments,
and reading keys out of ``**kwargs`` is already governed by
``tests/test_kwargs_guard.py``.

Read from source with ``ast`` so every module is scanned without triggering
optional extras. Generated modules (module docstring containing
``Do not edit by hand``) are exempt, as are private helpers (leading ``_``)
and non-bare container types like ``dict[str, Any]``.

Exceptions belong in :data:`ALLOWED_ANY_PARAMS`, keyed by
``<rel_path>::<qualname>(<param>)``, each with a non-empty reason (genuine
upstream overrides whose base class declares ``Any``, or items still pending
in #842). Stale allowlist entries fail the test so exceptions shrink and never
outlive the code that needed them.

Blind spot: parameter annotations that resolve to ``Any`` indirectly (through
an untyped third-party import or a missing annotation) and nested ``Any``
inside containers (``dict[str, Any]``) are outside AST scope.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE: Path = Path(__file__).resolve().parents[1] / "tempest_fastapi_sdk"
"""The package the guard scans."""

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
"""Repository root, used for relative paths and mypy config."""

GENERATED_MARKER: str = "Do not edit by hand"
"""Phrase in the module docstring of every generated module."""

ALLOWED_ANY_PARAMS: dict[str, str] = {
    (
        "tempest_fastapi_sdk/admin/auth.py::AdminAuthBackend.is_admin_account(target)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/auth.py::"
        "UserModelAuthBackend.is_admin_account(target)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/config.py::"
        "AdminModel.readonly_field_names_for(instance)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/config.py::AdminModel.scope_for(principal)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/admin/display.py::cell_view(value)": "pendente em #842",
    "tempest_fastapi_sdk/admin/display.py::format_number(value)": "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/display.py::short_identity(identity)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/forms.py::apply_password_fields(instance)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/forms.py::duplicate_values(instance)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/admin/forms.py::fk_label(instance)": "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/forms.py::parse_schema_submission(form)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/impersonation.py::"
        "make_jwt_impersonation_handler.handler(operator)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/admin/impersonation.py::"
        "make_jwt_impersonation_handler.handler(target)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/api/webhooks.py::WebhookSender.send(payload)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/app_errors/router.py::"
        "make_app_error_router.list_app_errors(session)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/app_errors/router.py::"
        "make_app_error_router.report_app_error(session)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/app_errors/router.py::"
        "make_app_error_router.report_app_error(user_id)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/authz/permissions.py::PermissionMixin.has_perm(obj)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/authz/permissions.py::"
        "PermissionRegistry.check_permission(obj)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/authz/permissions.py::"
        "PermissionRegistry.check_permission(user)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/authz/permissions.py::PermissionRegistry.has_perm(obj)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/authz/permissions.py::PermissionRegistry.has_perm(user)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/authz/permissions.py::check_permission(obj)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/authz/permissions.py::check_permission(user)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/authz/permissions.py::has_perm(obj)": "pendente em #842",
    "tempest_fastapi_sdk/authz/permissions.py::has_perm(user)": "pendente em #842",
    (
        "tempest_fastapi_sdk/chat/service.py::ChatService.receipt_counts(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/checks/builtins.py::check_bind_host(context)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/checks/builtins.py::check_cors(context)": "pendente em #842",
    (
        "tempest_fastapi_sdk/checks/builtins.py::check_database(context)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/checks/builtins.py::check_debug(context)": "pendente em #842",
    (
        "tempest_fastapi_sdk/checks/builtins.py::check_secrets(context)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/checks/registry.py::CheckRegistry.run(context)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/checks/registry.py::run_checks(context)": "pendente em #842",
    (
        "tempest_fastapi_sdk/checks/registry.py::run_system_checks(context)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/core/enums.py::_EnumHelpers.has_value(value)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/db/enum_migrations.py::render_enum_types(obj)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/db/enum_migrations.py::"
        "type_bound_check_include_object.include_object(compare_to)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/db/enum_migrations.py::"
        "type_bound_check_include_object.include_object(object_)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/db/expressions.py::build_filter_condition(value)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/db/signals.py::emit(instance)": "pendente em #842",
    (
        "tempest_fastapi_sdk/geo/db.py::haversine_distance_sql(latitude)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/geo/db.py::haversine_distance_sql(longitude)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/integrations/payment/adapters/mercado_pago.py::"
        "MercadoPagoPixProvider.parse_webhook(event)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/integrations/payment/adapters/openpix.py::"
        "OpenPixPixProvider.parse_webhook(event)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/integrations/payment/base.py::"
        "PixProvider.parse_webhook(event)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/mcp/_sessions.py::principal_key(principal)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/privacy/graph.py::SubjectGraph.condition(subject_id)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/privacy/graph.py::SubjectGraph.count(subject_id)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/privacy/graph.py::SubjectGraph.erase(subject_id)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/privacy/graph.py::SubjectGraph.export(subject_id)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/broker.py::MessageBroker.publish(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/broker.py::QueuePublisher.publish(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/broker.py::_schema_entry.entry(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/consumer.py::Consumer.handle(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/dedup.py::"
        "make_dedup_middleware._DedupMiddleware.consume_scope(call_next)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/dedup.py::"
        "make_dedup_middleware._DedupMiddleware.consume_scope(msg)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/queue/dedup.py::message_key(message)": "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/manager.py::AsyncQueueManager.publish(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/reliability.py::"
        "QueueMetrics.middleware._MetricsMiddleware.consume_scope(call_next)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/reliability.py::"
        "QueueMetrics.middleware._MetricsMiddleware.consume_scope(msg)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/reliability.py::delivery_attempt(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/reliability.py::"
        "make_dead_letter_middleware._DeadLetterMiddleware."
        "consume_scope(call_next)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/reliability.py::"
        "make_dead_letter_middleware._DeadLetterMiddleware.consume_scope(msg)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/reliability.py::replay_headers(headers)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/topology.py::detect_transport(broker)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/tracing.py::"
        "make_tracing_middleware._TracingMiddleware.consume_scope(call_next)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/queue/tracing.py::"
        "make_tracing_middleware._TracingMiddleware.consume_scope(msg)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/rooms/registry.py::RoomRegistry.relay_json(data)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/rooms/session.py::RoomSession.handle_json(message)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/settings/mixins.py::MinIOSettings.model_post_init(context)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/sse/router.py::make_sse_router.issue_ticket(user)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/sse/router.py::make_sse_router.stream(fallback)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/ssr/errors.py::"
        "register_html_error_handlers.wrap.handler(exc)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/storage/tus/router.py::make_tus_router.create_upload(user)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/storage/tus/router.py::"
        "make_tus_router.discard_upload(user)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/storage/tus/router.py::make_tus_router.upload_offset(user)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/storage/tus/router.py::make_tus_router.write_chunk(user)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/tasks/stages.py::StageMap.cancel(record)": "pendente em #842",
    (
        "tempest_fastapi_sdk/tasks/stages.py::StageMap.is_running(record)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/tasks/stages.py::StageMap.mark(record)": "pendente em #842",
    "tempest_fastapi_sdk/tasks/stages.py::StageMap.mark(result)": "pendente em #842",
    "tempest_fastapi_sdk/tasks/stages.py::StageMap.owns(record)": "pendente em #842",
    (
        "tempest_fastapi_sdk/tasks/stages.py::StageMap.snapshot(record)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/tasks/stages.py::StageMap.status(record)": "pendente em #842",
    (
        "tempest_fastapi_sdk/testing/fakes/payment.py::"
        "FakePixProvider.parse_webhook(event)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/ui/components/pagination.py::pagination_for(envelope)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/ui/components/table.py::DataTable.cell_text(value)"
    ): "pendente em #842",
    "tempest_fastapi_sdk/ui/forms/parse.py::parse_form(request)": "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.request(content)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.request(data)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.request(files)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.request(json)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream(content)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream(data)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream(files)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream(json)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream_bytes(content)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream_bytes(data)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream_bytes(files)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/http_client.py::HTTPClient.stream_bytes(json)"
    ): "pendente em #842",
    (
        "tempest_fastapi_sdk/utils/password.py::PasswordPolicy.from_settings(settings)"
    ): "pendente em #842",
}
"""Explicitly permitted bare ``Any`` parameters, keyed by ``path::qualname(param)``."""


def _is_bare_any(node: ast.expr | None) -> bool:
    """Return ``True`` when ``node`` is a bare ``Any`` parameter annotation.

    Args:
        node (ast.expr | None): The AST node of ``ast.arg.annotation``.

    Returns:
        bool: ``True`` for ``Any``, ``typing.Any`` (or any ``*.Any``), or a
        stringified forward reference to either.
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == "Any"
    if isinstance(node, ast.Attribute):
        return node.attr == "Any"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip() in {"Any", "typing.Any"}
    return False


def _is_generated(tree: ast.Module) -> bool:
    """Whether a module is generated code.

    Args:
        tree (ast.Module): The parsed module.

    Returns:
        bool: ``True`` when its docstring carries :data:`GENERATED_MARKER`.
    """
    docstring = ast.get_docstring(tree) or ""
    return GENERATED_MARKER in docstring


class _PublicAnyParamVisitor(ast.NodeVisitor):
    """Collect public ``def`` / ``async def`` parameters annotated bare ``Any``."""

    def __init__(self, rel_path: str) -> None:
        """Initialize the visitor for one module.

        Args:
            rel_path (str): Repository-relative POSIX path of the module.
        """
        self._rel_path: str = rel_path
        self._scope: list[str] = []
        self.findings: list[str] = []

    def _visit_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Record bare ``Any`` parameters of ``node`` when its name is public.

        Pure passthrough ``*args`` (``node.args.vararg``) and ``**kwargs``
        (``node.args.kwarg``) are deliberately skipped.

        Args:
            node (ast.FunctionDef | ast.AsyncFunctionDef): Function node.
        """
        if not node.name.startswith("_"):
            qualname = ".".join([*self._scope, node.name])
            params = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            for arg in params:
                if _is_bare_any(arg.annotation):
                    self.findings.append(f"{self._rel_path}::{qualname}({arg.arg})")
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track enclosing class names while walking the AST.

        Args:
            node (ast.ClassDef): Class definition node.
        """
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Inspect a synchronous function definition.

        Args:
            node (ast.FunctionDef): Function definition node.
        """
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Inspect an asynchronous function definition.

        Args:
            node (ast.AsyncFunctionDef): Async function definition node.
        """
        self._visit_fn(node)


def find_public_any_params(sources: dict[str, str]) -> list[str]:
    """Return every ``path::qualname(param)`` with a public bare ``Any`` param.

    Args:
        sources (dict[str, str]): Repository-relative POSIX path to source.

    Returns:
        list[str]: Sorted identifiers of offending parameters.
    """
    hits: list[str] = []
    for path, text in sources.items():
        tree = ast.parse(text, filename=path)
        if _is_generated(tree):
            continue
        visitor = _PublicAnyParamVisitor(path)
        visitor.visit(tree)
        hits.extend(visitor.findings)
    return sorted(hits)


def _package_sources() -> dict[str, str]:
    """Read every Python source file under :data:`PACKAGE`.

    Returns:
        dict[str, str]: Repository-relative POSIX path to source text.
    """
    return {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE.rglob("*.py"))
    }


def test_no_public_any_param_outside_allowlist() -> None:
    """Every public parameter outside the allowlist has a concrete annotation."""
    found = set(find_public_any_params(_package_sources()))
    unexpected = sorted(found - set(ALLOWED_ANY_PARAMS))
    assert not unexpected, (
        "Public function/method parameters annotated bare Any outside "
        "ALLOWED_ANY_PARAMS:\n" + "\n".join(f"  - {item}" for item in unexpected)
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every allowlist entry must still exist and carry a non-empty reason."""
    found = set(find_public_any_params(_package_sources()))
    stale = sorted(set(ALLOWED_ANY_PARAMS) - found)
    assert not stale, (
        "Stale entries in ALLOWED_ANY_PARAMS (remove once resolved):\n"
        + "\n".join(f"  - {item}" for item in stale)
    )
    blank_reasons = [
        key for key, reason in ALLOWED_ANY_PARAMS.items() if not reason.strip()
    ]
    assert not blank_reasons, f"Allowlist entries missing a reason: {blank_reasons}"


def test_guard_fires_on_synthetic_violations_and_allows_passthrough() -> None:
    """Prove the guard catches all bare-Any param kinds and allows passthrough."""
    sources: dict[str, str] = {
        "tempest_fastapi_sdk/vision/router.py": (
            "from typing import Any\n"
            "import typing\n"
            "def make_router(pos: Any, /, normal: typing.Any, *, kw: 'Any') -> None:\n"
            "    return None\n"
            "def forward(*args: Any, **kwargs: Any) -> None:\n"
            "    return None\n"
            "def mapping_param(payload: dict[str, Any]) -> None:\n"
            "    return None\n"
            "def _private(secret: Any) -> None:\n"
            "    return None\n"
        ),
        "tempest_fastapi_sdk/genai/sample.py": (
            "class Client:\n"
            '    async def send(self, body: "typing.Any") -> None:\n'
            "        return None\n"
        ),
        "tempest_fastapi_sdk/integrations/generated.py": (
            '"""Generated client. Do not edit by hand."""\n'
            "from typing import Any\n"
            "def send(payload: Any) -> None:\n"
            "    return None\n"
        ),
    }
    assert find_public_any_params(sources) == [
        "tempest_fastapi_sdk/genai/sample.py::Client.send(body)",
        "tempest_fastapi_sdk/vision/router.py::make_router(kw)",
        "tempest_fastapi_sdk/vision/router.py::make_router(normal)",
        "tempest_fastapi_sdk/vision/router.py::make_router(pos)",
    ]


def test_ai_consumer_surfaces_type_check() -> None:
    """Verify mypy accepts real consumer shapes on typed AI/vision/modelops APIs."""
    snippet = """
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel

from tempest_fastapi_sdk.genai import (
    ModelRegistry,
    build_prefix_allowed_tokens_fn,
    count_tokens,
    describe_model,
    is_document_part,
    make_model_cards,
    truncate_messages,
)
from tempest_fastapi_sdk.genai.anthropic import anthropic_usage
from tempest_fastapi_sdk.genai.schemas import precision_kwarg
from tempest_fastapi_sdk.genai.tokens import TokenUsage
from tempest_fastapi_sdk.modelops import (
    OnnxPredictor,
    baseline_from_samples,
    default_providers,
    export_sklearn_to_compact,
    export_sklearn_to_onnx,
    predict_compact,
)
from tempest_fastapi_sdk.vision import Detector, make_vision_router


class _Tok:
    @property
    def eos_token_id(self) -> int | None:
        return 0

    @property
    def all_special_ids(self) -> list[int]:
        return [0]

    def __len__(self) -> int:
        return 4

    def encode(self, text: str) -> list[int]:
        return [1]

    def decode(self, ids: list[int]) -> str:
        return "a"


class _Item(BaseModel):
    name: str


class _Est:
    def predict(self, x: object) -> list[int]:
        return [0]


class _Ort:
    def get_available_providers(self) -> list[str]:
        return ["CPUExecutionProvider"]


kw = precision_kwarg("bfloat16")
reveal_type(kw)
n_tokens = count_tokens("hi", _Tok())
trimmed = truncate_messages([{"role": "user", "content": "hi"}], 10, _Tok())
prefix_fn = build_prefix_allowed_tokens_fn(_Tok(), _Item)
cards_reg = make_model_cards(ModelRegistry())
cards_map = make_model_cards({"chat": object()})
model_info = describe_model(object())
usage_anth = anthropic_usage({"input_tokens": 1, "output_tokens": 2})
usage_none = TokenUsage.from_payload(None)
is_doc = is_document_part({"type": "file"})
providers = default_providers(_Ort())
baseline = baseline_from_samples([[1.0, 2.0]], labels=[0])
onnx_exp = export_sklearn_to_onnx(_Est(), [[1.0, 2.0]], Path("m.onnx"))
compact_exp = export_sklearn_to_compact(_Est(), False, Path("m.tmc"))
compact_pred = predict_compact(Path("m.tmc"), [[1.0, 2.0]])
vision_router = make_vision_router(detector=Detector("yolov8n.onnx"))


def check_predictor(predictor: OnnxPredictor) -> None:
    predictor.predict([[1.0, 2.0]])
"""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "ai_consumer_probe.py"
        probe.write_text(snippet, encoding="utf-8")
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
    output = proc.stdout + proc.stderr
    assert "error:" not in output, f"Unexpected mypy error:\n{output}"
    revealed = [
        line.split('Revealed type is "', 1)[1].rsplit('"', 1)[0]
        for line in output.splitlines()
        if 'Revealed type is "' in line
    ]
    assert revealed == ["dict[str, str]"], output
