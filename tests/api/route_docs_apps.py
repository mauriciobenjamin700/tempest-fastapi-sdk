"""Build every router whose OpenAPI text comes from the route-docs catalog.

Shared by ``tests/api/test_route_docs.py`` and the coverage guard. Each
router is mounted with every optional route group switched on, so the
OpenAPI document holds every operation the factory can emit — a route
left out here is a route whose translation nothing measures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, FastAPI

from tempest_fastapi_sdk import (
    AttemptThrottle,
    BaseUserModel,
    InMemoryThrottleBackend,
    MemoryWebAuthnChallengeStore,
    MFAMixin,
    NameMixin,
    UserAuthService,
    WebAuthnService,
    make_auth_router,
    make_health_router,
    make_logs_router,
    make_push_router,
    make_session_router,
    make_sse_router,
    make_tool_spec_router,
    make_user_oauth_account_model,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
    make_user_token_model,
    make_web_authn_credential_model,
    make_web_push_router,
)
from tempest_fastapi_sdk.app_errors import make_app_error_router
from tempest_fastapi_sdk.genai.audio import make_voice_router
from tempest_fastapi_sdk.pdf import PdfRenderer, make_pdf_router
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _DocsUser(NameMixin, MFAMixin, BaseUserModel):
    """User table carrying every column the optional auth groups read."""

    __tablename__ = "route_docs_users"


_DocsUserToken = make_user_token_model(
    user_table="route_docs_users",
    tablename="route_docs_user_tokens",
    class_name="_DocsUserToken",
)
_DocsRefresh = make_user_refresh_token_model(
    user_table="route_docs_users",
    tablename="route_docs_refresh_tokens",
    class_name="_DocsRefresh",
)
_DocsOAuthAccount = make_user_oauth_account_model(
    user_table="route_docs_users",
    tablename="route_docs_oauth_accounts",
    class_name="_DocsOAuthAccount",
)
_DocsRecoveryCode = make_user_recovery_code_model(
    user_table="route_docs_users",
    tablename="route_docs_recovery_codes",
    class_name="_DocsRecoveryCode",
)
_DocsCredential = make_web_authn_credential_model(
    user_table="route_docs_users",
    tablename="route_docs_webauthn_credentials",
    class_name="_DocsCredential",
)


async def _session_factory() -> AsyncIterator[Any]:
    """Yield nothing useful: these apps are only rendered, never called.

    Yields:
        Any: ``None``.
    """
    yield None


async def _current_user() -> Any:
    """Stand in for the authenticated-user dependency.

    Returns:
        Any: ``None``.
    """
    return None


def _anything(*_args: Any, **_kwargs: Any) -> Any:
    """Stand in for a factory argument the OpenAPI render never calls.

    Args:
        *_args (Any): Ignored.
        **_kwargs (Any): Ignored.

    Returns:
        Any: ``None``.
    """
    return None


class _OAuthClient:
    """Minimal OAuth client: mounting reads only ``provider_name``."""

    provider_name: str = "google"


def _auth_router(locale_kwargs: dict[str, Any]) -> APIRouter:
    """Build the auth router with every optional route group mounted.

    Args:
        locale_kwargs (dict[str, Any]): ``{"docs_locale": ...}`` or ``{}``.

    Returns:
        APIRouter: The router.
    """
    auth = AuthSettings(
        _env_file=None,
        AUTH_AUTO_ACTIVATE=True,
        AUTH_MFA_ENABLED=True,
        AUTH_WEBAUTHN_ENABLED=True,
        AUTH_WEBAUTHN_RP_ID="example.com",
        AUTH_WEBAUTHN_RP_NAME="Docs",
        AUTH_OAUTH_ENABLED=True,
        AUTH_EMAIL_RECOVERY_ENABLED=True,
        AUTH_BACKEND_LINKS=True,
        AUTH_TOKEN_DELIVERY="both",
        AUTH_COOKIE_SECURE=False,
    )
    service = UserAuthService(
        user_model=_DocsUser,
        token_model=_DocsUserToken,
        auth_settings=auth,
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        oauth_account_model=_DocsOAuthAccount,
        refresh_token_model=_DocsRefresh,
    )
    webauthn = WebAuthnService(
        user_model=_DocsUser,
        credential_model=_DocsCredential,
        auth_settings=auth,
        challenge_store=MemoryWebAuthnChallengeStore(),
    )
    return make_auth_router(
        service,
        session_factory=_session_factory,
        recovery_code_model=_DocsRecoveryCode,
        webauthn=webauthn,
        oauth_clients={"google": _OAuthClient()},  # type: ignore[dict-item]
        signup_throttle=AttemptThrottle(
            InMemoryThrottleBackend(), max_attempts=5, window_seconds=60
        ),
        **locale_kwargs,
    )


ROUTER_BUILDERS: dict[str, Callable[[dict[str, Any]], APIRouter]] = {
    "auth": _auth_router,
    "sse": lambda kw: make_sse_router(
        _anything,
        tokens=_anything,
        current_user=_current_user,
        **kw,
    ),
    "push": lambda kw: make_push_router(
        service_factory=_anything,
        session_factory=_session_factory,
        current_user_id=_current_user,
        vapid_public_key="key",
        **kw,
    ),
    "webpush": lambda kw: make_web_push_router(
        service_factory=_anything,
        session_factory=_session_factory,
        current_user_id=_current_user,
        prefix="/api/webpush",
        vapid_public_key="key",
        **kw,
    ),
    "health": lambda kw: make_health_router(**kw),
    "logs": lambda kw: make_logs_router(allow_unauthenticated=True, **kw),
    "tool_spec": lambda kw: make_tool_spec_router({"tools": []}, **kw),
    "app_errors": lambda kw: make_app_error_router(
        service_factory=_anything,
        session_factory=_session_factory,
        admin_dependency=_current_user,
        **kw,
    ),
    "sessions": lambda kw: make_session_router(
        SimpleNamespace(  # type: ignore[arg-type]
            user_model=object(),
            settings=SimpleNamespace(),
            authenticate=_anything,
        ),
        session_factory=_session_factory,
        **kw,
    ),
    "pdf": lambda kw: make_pdf_router(PdfRenderer(), **kw),
    "voice": lambda kw: make_voice_router(
        session_factory=_session_factory,
        transcriber=SimpleNamespace(),  # type: ignore[arg-type]
        profiles=SimpleNamespace(),  # type: ignore[arg-type]
        current_user_id=_current_user,
        **kw,
    ),
}
"""Router name mapped to a builder taking the locale keyword arguments."""


def build_app(name: str, docs_locale: str | None = None) -> FastAPI:
    """Mount one router on a fresh app.

    Args:
        name (str): A key of :data:`ROUTER_BUILDERS`.
        docs_locale (str | None): Passed as ``docs_locale=`` when given;
            ``None`` omits the keyword, which is how a caller written
            before #643 builds the router.

    Returns:
        FastAPI: The application.
    """
    kwargs: dict[str, Any] = {} if docs_locale is None else {"docs_locale": docs_locale}
    app = FastAPI()
    app.include_router(ROUTER_BUILDERS[name](kwargs))
    return app
