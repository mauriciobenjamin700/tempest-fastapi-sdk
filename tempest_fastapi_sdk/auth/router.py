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

from tempest_fastapi_sdk.auth.page_renderer import render_auth_page
from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema,
    LoginResponseSchema,
    LoginSchema,
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
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.auth.service import UserAuthService


def make_auth_router(
    service: UserAuthService,
    *,
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    prefix: str = "/auth",
    tags: list[str] | None = None,
    template_dir: str | None = None,
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
        access, refresh = service.issue_jwt_pair(user)
        await session.commit()
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

    return router


__all__: list[str] = [
    "make_auth_router",
]
