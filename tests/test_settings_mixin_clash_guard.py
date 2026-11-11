"""Guard: two settings mixins never define the same public name.

Settings mixins are meant to be composed (``class Settings(HostedGenAISettings,
OAuthSettings, ...)``), and composition resolves a shared name by MRO: the
first base wins and the other method disappears without a word. Up to
0.352.0 ``OAuthSettings.google_kwargs(self, *, prefix="/auth")`` (Google login)
and ``HostedGenAISettings.google_kwargs(self)`` (Gemini) shared a name; a
service composing both got the Gemini one first and its Google login failed at
runtime with ``TypeError`` on ``prefix``, while basedpyright reported "Base
classes for class define method google_kwargs in incompatible way". The Gemini
one is now ``gemini_kwargs``.

A shared name with the **same** signature fails just as quietly. Up to
0.352.0 ``WebPushSettings.enabled`` (VAPID configured) and
``FirebaseSettings.enabled`` (FCM configured) had one signature and two
meanings; a service composing both without ``PushSettings`` read the Web Push
answer when it asked about Firebase (#742). Telling meanings apart is a
judgment no check makes, so the guard refuses **any** public name defined by
two classes, and a name shared on purpose goes in
:data:`ALLOWED_SHARED_NAMES` with its reason. The channel-specific names are
now ``webpush_enabled`` and ``firebase_enabled``.

Scope: every public ``BaseAppSettings`` subclass in
``tempest_fastapi_sdk.settings.__all__``, every public method, property,
classmethod and staticmethod they (or their SDK bases) define, minus the names
``BaseAppSettings`` already has (``production_violations`` is a cooperative
``super()`` chain by design).
"""

from __future__ import annotations

import inspect
from typing import Any

from tempest_fastapi_sdk import settings as settings_module
from tempest_fastapi_sdk.settings import BaseAppSettings


def _callable_of(value: Any) -> Any:
    """Unwrap a class attribute to the function whose signature counts.

    Args:
        value (Any): A class ``__dict__`` value.

    Returns:
        Any: The underlying function, or ``None`` when it is not a method,
        property, classmethod or staticmethod.
    """
    if isinstance(value, classmethod | staticmethod):
        return value.__func__
    if isinstance(value, property):
        return value.fget
    if inspect.isfunction(value):
        return value
    return None


ALLOWED_SHARED_NAMES: dict[str, str] = {}
"""Public names more than one settings class may define, with the reason.

Empty on purpose: ``PushSettings.enabled`` no longer shadows anything since
its bases answer through ``webpush_enabled`` / ``firebase_enabled``. A name
added here must say why composing the classes stays unambiguous.
"""


def _clashes(mixins: list[type[BaseAppSettings]]) -> dict[str, dict[str, str]]:
    """Find public names defined by more than one class across ``mixins``.

    Args:
        mixins (list[type[BaseAppSettings]]): The settings classes to compare.

    Returns:
        dict[str, dict[str, str]]: Name to ``{class name: signature}``, for
        every name two classes define, whatever the signatures, minus
        :data:`ALLOWED_SHARED_NAMES`.
    """
    inherited = set(dir(BaseAppSettings))
    seen: dict[str, dict[str, str]] = {}
    for mixin in mixins:
        for klass in mixin.__mro__:
            if klass is BaseAppSettings or not (
                isinstance(klass, type) and issubclass(klass, BaseAppSettings)
            ):
                continue
            for name, value in vars(klass).items():
                if name.startswith("_") or name in inherited:
                    continue
                function = _callable_of(value)
                if function is None:
                    continue
                signature = str(inspect.signature(function))
                seen.setdefault(name, {})[klass.__qualname__] = signature
    return {
        name: by_class
        for name, by_class in seen.items()
        if len(by_class) > 1 and name not in ALLOWED_SHARED_NAMES
    }


def _public_mixins() -> list[type[BaseAppSettings]]:
    """Return every public settings mixin the package exports.

    Returns:
        list[type[BaseAppSettings]]: The ``BaseAppSettings`` subclasses in
        ``tempest_fastapi_sdk.settings.__all__``.
    """
    found: list[type[BaseAppSettings]] = []
    for name in settings_module.__all__:
        value = getattr(settings_module, name)
        if (
            inspect.isclass(value)
            and issubclass(value, BaseAppSettings)
            and value is not BaseAppSettings
        ):
            found.append(value)
    return found


def test_scan_sees_the_mixins() -> None:
    names = {mixin.__name__ for mixin in _public_mixins()}
    assert {"HostedGenAISettings", "OAuthSettings", "PushSettings"} <= names


def test_no_two_settings_mixins_share_a_public_name() -> None:
    assert _clashes(_public_mixins()) == {}


def test_gemini_and_google_login_compose() -> None:
    from tempest_fastapi_sdk.settings import HostedGenAISettings, OAuthSettings

    class Composed(HostedGenAISettings, OAuthSettings):
        """The pair that clashed up to 0.352.0."""

    composed = Composed(
        GOOGLE_API_KEY="AIza",
        GOOGLE_MODEL="gemini-test",
        OAUTH_GOOGLE_CLIENT_ID="id",
        OAUTH_GOOGLE_CLIENT_SECRET="secret",
    )
    assert composed.gemini_kwargs()["provider"] == "google"
    assert "client_id" in composed.google_kwargs(prefix="/auth")


class TestGuardFires:
    def test_reports_the_shape_that_shipped(self) -> None:
        class GoogleLogin(BaseAppSettings):
            """Stand-in for ``OAuthSettings`` up to 0.352.0."""

            def google_kwargs(self, *, prefix: str = "/auth") -> dict[str, Any]:
                """Build the OAuth client kwargs.

                Args:
                    prefix (str): The router prefix.

                Returns:
                    dict[str, Any]: The kwargs.
                """
                return {"prefix": prefix}

        class Gemini(BaseAppSettings):
            """Stand-in for ``HostedGenAISettings`` up to 0.352.0."""

            def google_kwargs(self) -> dict[str, Any]:
                """Build the Gemini generator kwargs.

                Returns:
                    dict[str, Any]: The kwargs.
                """
                return {}

        found = _clashes([GoogleLogin, Gemini])
        assert set(found) == {"google_kwargs"}
        assert len(set(found["google_kwargs"].values())) == 2

    def test_same_signature_is_reported(self) -> None:
        class First(BaseAppSettings):
            """One side."""

            @property
            def enabled(self) -> bool:
                """Report a flag.

                Returns:
                    bool: Always ``True``.
                """
                return True

        class Second(BaseAppSettings):
            """The other side."""

            @property
            def enabled(self) -> bool:
                """Report a flag.

                Returns:
                    bool: Always ``False``.
                """
                return False

        assert set(_clashes([First, Second])) == {"enabled"}


def test_web_push_and_firebase_compose_without_an_ambiguous_enabled() -> None:
    """Cenário: os dois mixins compostos direto, sem ``PushSettings``.

    Esperado: nenhum ``enabled`` herdado de um dos lados; cada canal responde
    pelo próprio nome, e um não muda a resposta do outro.
    """
    from tempest_fastapi_sdk.settings import FirebaseSettings, WebPushSettings

    class Composed(WebPushSettings, FirebaseSettings):
        """The pair that shared ``enabled`` up to 0.352.0."""

    web_only = Composed(VAPID_PRIVATE_KEY="k")
    assert not hasattr(web_only, "enabled")
    assert (web_only.webpush_enabled, web_only.firebase_enabled) == (True, False)
    mobile_only = Composed(FIREBASE_CREDENTIALS_PATH="sa.json")
    assert (mobile_only.webpush_enabled, mobile_only.firebase_enabled) == (
        False,
        True,
    )
