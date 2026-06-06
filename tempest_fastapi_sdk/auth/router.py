"""``make_auth_router`` — FastAPI router for the bundled auth flow.

Wraps the canonical signup-with-email endpoints every project
ends up implementing the same way. The router exposes two
flavors:

**Default (SPA mode)** — five JSON endpoints designed to be
consumed by a frontend that owns the activation / reset UI:

* ``POST /auth/signup`` — create user + maybe send activation
* ``POST /auth/activate/{token}`` — consume activation + log in
* ``POST /auth/login`` — email + password → JWT pair
* ``POST /auth/password-reset/request`` — issue reset token
* ``POST /auth/password-reset/confirm`` — consume reset token

**Backend-only mode** — enabled by setting
``AuthSettings.AUTH_BACKEND_LINKS=True``. On top of the JSON
endpoints above, the router mounts three HTML endpoints that
render activation success / error pages and a password-reset
form directly from the backend, so the project doesn't need a
SPA route to process tokens:

* ``GET /auth/activate/{token}`` — activate + render an HTML
  success page (or an HTML error page on bad / expired
  tokens)
* ``GET /auth/password-reset/{token}`` — peek the token +
  render the reset form
* ``POST /auth/password-reset/{token}`` *(form-encoded)* —
  process the form + render a success / error HTML page

The router is generic over the service so the consuming
application keeps full control over the underlying user /
token models and the email rendering pipeline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Form, status
from fastapi.responses import HTMLResponse

from tempest_fastapi_sdk.api.dependencies import make_jwt_user_dependency
from tempest_fastapi_sdk.auth.page_renderer import render_auth_page
from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema,
    LoginResponseSchema,
    LoginSchema,
    MFAConfirmSchema,
    MFADisableSchema,
    MFAEnrollResponseSchema,
    MFAVerifySchema,
    PasswordResetConfirmSchema,
    PasswordResetRequestSchema,
    PasswordResetResponseSchema,
    SignupResponseSchema,
    SignupSchema,
)
from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose
from tempest_fastapi_sdk.exceptions import (
    InvalidTokenException,
    NotFoundException,
    ValidationException,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.auth.service import UserAuthService
    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.db.user_recovery_code_model import (
        BaseUserRecoveryCodeModel,
    )


def make_auth_router(
    service: UserAuthService,
    *,
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    prefix: str = "/auth",
    tags: list[str] | None = None,
    template_dir: str | None = None,
    recovery_code_model: type[BaseUserRecoveryCodeModel] | None = None,
) -> APIRouter:
    """Build the bundled auth router.

    Args:
        service (UserAuthService): The configured service handling
            signup / activation / reset.
        session_factory (Callable[[], AsyncIterator[AsyncSession]]):
            FastAPI dependency yielding an async session. Typically
            wired as ``db.session_dependency`` where ``db`` is
            an :class:`AsyncDatabaseManager`. Used inside each
            handler to scope the transaction to the request.
        prefix (str): URL prefix; defaults to ``"/auth"``.
        tags (list[str] | None): OpenAPI tags. Defaults to
            ``["auth"]``.
        template_dir (str | None): Optional directory holding
            HTML templates that override the SDK-bundled
            ``activation_success.html`` /
            ``activation_error.html`` /
            ``password_reset_form.html`` /
            ``password_reset_success.html`` /
            ``password_reset_error.html``. Only consulted when
            ``AuthSettings.AUTH_BACKEND_LINKS=True``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    from fastapi import Depends

    router = APIRouter(
        prefix=prefix,
        tags=list(tags or ["auth"]),
    )

    async def _session() -> AsyncIterator[AsyncSession]:
        async for s in session_factory():
            yield s

    session_dep = Depends(_session)

    auth_settings = service.auth_settings
    backend_links = auth_settings.AUTH_BACKEND_LINKS
    login_url = auth_settings.AUTH_LOGIN_URL
    min_length = auth_settings.AUTH_PASSWORD_MIN_LENGTH

    def _render_error(template: str, reason: str) -> HTMLResponse:
        html = render_auth_page(
            template,
            {"reason": reason, "login_url": login_url},
            template_dir=template_dir,
        )
        return HTMLResponse(content=html, status_code=400)

    # ------------------------------------------------------------------
    # JSON / SPA endpoints — always mounted.
    # ------------------------------------------------------------------

    @router.post(
        "/signup",
        response_model=SignupResponseSchema,
        status_code=status.HTTP_201_CREATED,
        summary="Create a new account",
        description=(
            "Creates a user with email + password. When "
            "``AUTH_AUTO_ACTIVATE`` is set the response carries JWT "
            "tokens directly; otherwise the user must confirm via "
            "the activation link before logging in."
        ),
    )
    async def signup(
        payload: SignupSchema,
        session: AsyncSession = session_dep,
    ) -> SignupResponseSchema:
        user, activation = await service.signup(
            session,
            email=payload.email,
            password=payload.password,
            name=payload.name,
        )
        await session.commit()
        if activation is None:
            access, refresh = service.issue_jwt_pair(user)
            return SignupResponseSchema(
                user_id=user.id,
                activation_required=False,
                activation_url=None,
                access_token=access,
                refresh_token=refresh,
            )
        return_url = (
            activation.url
            if service.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE
            or service.email is None
            else None
        )
        return SignupResponseSchema(
            user_id=user.id,
            activation_required=True,
            activation_url=return_url,
        )

    @router.post(
        "/activate/{token}",
        response_model=ActivationResponseSchema,
        summary="Activate the account using the emailed token",
    )
    async def activate(
        token: str,
        session: AsyncSession = session_dep,
    ) -> ActivationResponseSchema:
        user = await service.activate(session, token=token)
        access, refresh = service.issue_jwt_pair(user)
        await session.commit()
        return ActivationResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    @router.post(
        "/login",
        response_model=LoginResponseSchema,
        summary="Log in with email + password",
    )
    async def login(
        payload: LoginSchema,
        session: AsyncSession = session_dep,
    ) -> LoginResponseSchema:
        user = await service.login(
            session,
            email=payload.email,
            password=payload.password,
        )
        await session.commit()
        if service.is_mfa_enrolled(user):
            mfa_token = service.issue_mfa_token(user)
            return LoginResponseSchema(
                user_id=user.id,
                access_token=None,
                refresh_token=None,
                mfa_required=True,
                mfa_token=mfa_token,
            )
        access, refresh = service.issue_jwt_pair(user)
        return LoginResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    @router.post(
        "/password-reset/request",
        response_model=PasswordResetResponseSchema,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Request a password-reset link",
        description=(
            "Always returns 202 so attackers can't enumerate accounts "
            "by probing emails. The link is mailed when ``EmailUtils`` "
            "is wired; otherwise (or when "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE`` is on) it ships in the "
            "response body."
        ),
    )
    async def password_reset_request(
        payload: PasswordResetRequestSchema,
        session: AsyncSession = session_dep,
    ) -> PasswordResetResponseSchema:
        token = await service.request_password_reset(session, email=payload.email)
        await session.commit()
        message = "If the email matches an account, a reset link was sent."
        if token is None:
            return PasswordResetResponseSchema(message=message, reset_url=None)
        return PasswordResetResponseSchema(message=message, reset_url=token.url)

    @router.post(
        "/password-reset/confirm",
        response_model=LoginResponseSchema,
        summary="Confirm a password reset with the issued token",
    )
    async def password_reset_confirm(
        payload: PasswordResetConfirmSchema,
        session: AsyncSession = session_dep,
    ) -> LoginResponseSchema:
        user = await service.confirm_password_reset(
            session,
            token=payload.token,
            new_password=payload.new_password,
        )
        access, refresh = service.issue_jwt_pair(user)
        await session.commit()
        return LoginResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    # ------------------------------------------------------------------
    # Backend-only HTML endpoints — mounted only when AUTH_BACKEND_LINKS.
    # ------------------------------------------------------------------

    if backend_links:

        @router.get(
            "/activate/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Activate via emailed link (HTML page)",
        )
        async def activate_html(
            token: str,
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            try:
                user = await service.activate(session, token=token)
            except InvalidTokenException as exc:
                await session.rollback()
                return _render_error(
                    auth_settings.AUTH_ACTIVATION_ERROR_TEMPLATE,
                    reason=exc.message,
                )
            await session.commit()
            html = render_auth_page(
                auth_settings.AUTH_ACTIVATION_SUCCESS_TEMPLATE,
                {"user": user, "login_url": login_url},
                template_dir=template_dir,
            )
            return HTMLResponse(content=html)

        @router.get(
            "/password-reset/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Render the password-reset form for this token",
        )
        async def password_reset_form(
            token: str,
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            try:
                _record, user = await service.peek_token(
                    session,
                    token=token,
                    purpose=UserTokenPurpose.PASSWORD_RESET,
                )
            except (InvalidTokenException, NotFoundException) as exc:
                return _render_error(
                    auth_settings.AUTH_PASSWORD_RESET_ERROR_TEMPLATE,
                    reason=exc.message,
                )
            html = render_auth_page(
                auth_settings.AUTH_PASSWORD_RESET_FORM_TEMPLATE,
                {
                    "user": user,
                    "form_action": f"{prefix}/password-reset/{token}",
                    "min_length": min_length,
                    "error": None,
                    "login_url": login_url,
                },
                template_dir=template_dir,
            )
            return HTMLResponse(content=html)

        @router.post(
            "/password-reset/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Process the password-reset form (form-encoded)",
        )
        async def password_reset_form_submit(
            token: str,
            new_password: str = Form(...),
            confirm_password: str = Form(...),
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            if new_password != confirm_password:
                try:
                    _record, user = await service.peek_token(
                        session,
                        token=token,
                        purpose=UserTokenPurpose.PASSWORD_RESET,
                    )
                except (InvalidTokenException, NotFoundException) as exc:
                    return _render_error(
                        auth_settings.AUTH_PASSWORD_RESET_ERROR_TEMPLATE,
                        reason=exc.message,
                    )
                html = render_auth_page(
                    auth_settings.AUTH_PASSWORD_RESET_FORM_TEMPLATE,
                    {
                        "user": user,
                        "form_action": f"{prefix}/password-reset/{token}",
                        "min_length": min_length,
                        "error": "Passwords do not match.",
                        "login_url": login_url,
                    },
                    template_dir=template_dir,
                )
                return HTMLResponse(content=html, status_code=400)
            try:
                user = await service.confirm_password_reset(
                    session,
                    token=token,
                    new_password=new_password,
                )
            except (InvalidTokenException, NotFoundException) as exc:
                await session.rollback()
                return _render_error(
                    auth_settings.AUTH_PASSWORD_RESET_ERROR_TEMPLATE,
                    reason=exc.message,
                )
            except ValidationException as exc:
                await session.rollback()
                try:
                    _record, peek_user = await service.peek_token(
                        session,
                        token=token,
                        purpose=UserTokenPurpose.PASSWORD_RESET,
                    )
                except (InvalidTokenException, NotFoundException):
                    return _render_error(
                        auth_settings.AUTH_PASSWORD_RESET_ERROR_TEMPLATE,
                        reason=exc.message,
                    )
                html = render_auth_page(
                    auth_settings.AUTH_PASSWORD_RESET_FORM_TEMPLATE,
                    {
                        "user": peek_user,
                        "form_action": f"{prefix}/password-reset/{token}",
                        "min_length": min_length,
                        "error": exc.message,
                        "login_url": login_url,
                    },
                    template_dir=template_dir,
                )
                return HTMLResponse(content=html, status_code=400)
            await session.commit()
            html = render_auth_page(
                auth_settings.AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE,
                {"user": user, "login_url": login_url},
                template_dir=template_dir,
            )
            return HTMLResponse(content=html)

    # ------------------------------------------------------------------
    # MFA endpoints — mounted only when AUTH_MFA_ENABLED.
    # ------------------------------------------------------------------

    if auth_settings.AUTH_MFA_ENABLED:
        if recovery_code_model is None:
            raise RuntimeError(
                "AUTH_MFA_ENABLED=True requires a concrete recovery_code_model "
                "(subclass of BaseUserRecoveryCodeModel) passed to "
                "make_auth_router(recovery_code_model=...)."
            )

        current_user_dep = make_jwt_user_dependency(
            service.jwt,
            user_loader=_make_user_loader(service, session_factory),
        )

        @router.post(
            "/mfa/enroll",
            response_model=MFAEnrollResponseSchema,
            summary="Enroll the current user in TOTP (returns secret + codes once)",
        )
        async def mfa_enroll(
            session: AsyncSession = session_dep,
            user: BaseUserModel = Depends(current_user_dep),
        ) -> MFAEnrollResponseSchema:
            secret, uri, codes = await service.mfa_enroll(
                session,
                user=user,
                recovery_code_model=recovery_code_model,
            )
            await session.commit()
            return MFAEnrollResponseSchema(
                secret=secret,
                provisioning_uri=uri,
                recovery_codes=codes,
            )

        @router.post(
            "/mfa/confirm",
            status_code=status.HTTP_204_NO_CONTENT,
            summary="Confirm enrollment by submitting the first TOTP code",
        )
        async def mfa_confirm(
            payload: MFAConfirmSchema,
            session: AsyncSession = session_dep,
            user: BaseUserModel = Depends(current_user_dep),
        ) -> None:
            await service.mfa_confirm(session, user=user, code=payload.code)
            await session.commit()

        @router.post(
            "/mfa/disable",
            status_code=status.HTTP_204_NO_CONTENT,
            summary="Disable MFA (requires password + active code)",
        )
        async def mfa_disable(
            payload: MFADisableSchema,
            session: AsyncSession = session_dep,
            user: BaseUserModel = Depends(current_user_dep),
        ) -> None:
            await service.mfa_disable(
                session,
                user=user,
                password=payload.password,
                code=payload.code,
                recovery_code_model=recovery_code_model,
            )
            await session.commit()

        @router.post(
            "/mfa/verify",
            response_model=LoginResponseSchema,
            summary="Step 2 of login — exchange mfa_token + code for JWT pair",
        )
        async def mfa_verify(
            payload: MFAVerifySchema,
            session: AsyncSession = session_dep,
        ) -> LoginResponseSchema:
            user = await service.mfa_verify(
                session,
                mfa_token=payload.mfa_token,
                code=payload.code,
                recovery_code_model=recovery_code_model,
            )
            access, refresh = service.issue_jwt_pair(user)
            await session.commit()
            return LoginResponseSchema(
                user_id=user.id,
                access_token=access,
                refresh_token=refresh,
            )

    return router


def _make_user_loader(
    service: UserAuthService,
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
) -> Callable[[str], Coroutine[Any, Any, BaseUserModel | None]]:
    """Build the awaitable ``(user_id) -> BaseUserModel`` JWT user loader.

    Opens a fresh session per call so the dependency stays
    request-scope-agnostic.
    """
    from uuid import UUID

    async def _load(user_id: str) -> BaseUserModel | None:
        async for s in session_factory():
            obj: BaseUserModel | None = await s.get(service.user_model, UUID(user_id))
            return obj
        return None  # pragma: no cover - session_factory always yields once

    return _load


__all__: list[str] = [
    "make_auth_router",
]
