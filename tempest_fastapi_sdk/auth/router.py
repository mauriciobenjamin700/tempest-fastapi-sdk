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

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse

from tempest_fastapi_sdk.api.dependencies import make_jwt_user_dependency
from tempest_fastapi_sdk.auth.locale import auth_page_message, negotiate_locale
from tempest_fastapi_sdk.auth.page_renderer import render_auth_page
from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema,
    LoginResponseSchema,
    LoginSchema,
    MFAConfirmSchema,
    MFADisableSchema,
    MFAEnrollResponseSchema,
    MFAVerifySchema,
    PasswordChangeSchema,
    PasswordResetConfirmSchema,
    PasswordResetRequestSchema,
    PasswordResetResponseSchema,
    RefreshSchema,
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
    default_locale = auth_settings.AUTH_DEFAULT_LOCALE

    def _page_locale(request: Request) -> str:
        """Pick the HTML-page locale from ``Accept-Language``.

        Falls back to ``AUTH_DEFAULT_LOCALE`` when the browser sends no
        usable header.

        Args:
            request (Request): The incoming HTTP request.

        Returns:
            str: A canonical supported locale.
        """
        return negotiate_locale(
            request.headers.get("accept-language"),
            default=default_locale,
        )

    def _render_error(template: str, reason: str, locale: str) -> HTMLResponse:
        html = render_auth_page(
            template,
            {"reason": reason, "login_url": login_url},
            template_dir=template_dir,
            locale=locale,
        )
        return HTMLResponse(content=html, status_code=400)

    # Authenticated-user dependency, shared by the password-change route
    # and (when enabled) the MFA routes.
    current_user_dep = make_jwt_user_dependency(
        service.jwt,
        user_loader=_make_user_loader(service, session_factory),
    )

    # ------------------------------------------------------------------
    # JSON / SPA endpoints — always mounted.
    # ------------------------------------------------------------------

    @router.post(
        "/signup",
        response_model=SignupResponseSchema,
        status_code=status.HTTP_201_CREATED,
        summary="Register a new account (email + password)",
        description=(
            "Create a brand-new user from an email, a password and an "
            "optional display name.\n\n"
            "**Password policy.** The password must satisfy "
            "``AUTH_PASSWORD_MIN_LENGTH`` (and the character-complexity "
            "rules when ``AUTH_PASSWORD_REQUIRE_COMPLEXITY=True``); "
            "violations return **422**. A duplicate email returns "
            "**409**.\n\n"
            "**What happens next depends on ``AUTH_AUTO_ACTIVATE``:**\n\n"
            "* ``AUTH_AUTO_ACTIVATE=True`` — the account is active "
            "immediately and the **201** response already carries the "
            "``access_token`` + ``refresh_token`` JWT pair "
            "(``activation_required=false``).\n"
            "* ``AUTH_AUTO_ACTIVATE=False`` (default) — the account "
            "starts inactive and an activation token is issued "
            "(``activation_required=true``). When ``EmailUtils`` is "
            "wired the activation link is **emailed** (localized via "
            "``AUTH_DEFAULT_LOCALE``) and ``activation_url`` is "
            "``null``; when email is not configured — or "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` — the ready-to-use "
            "``activation_url`` is returned in the body instead so you "
            "can complete activation without SMTP."
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
        summary="Activate an account from the emailed token (JSON)",
        description=(
            "Consume a single-use **activation token** (the one sent in "
            "the signup email or returned by ``/signup``) and flip the "
            "account to active.\n\n"
            "This is the **JSON / SPA** variant: a frontend reads the "
            "``{token}`` from the activation URL and POSTs it here. On "
            "success the account is activated **and logged in** — the "
            "response carries a fresh ``access_token`` + "
            "``refresh_token`` pair so the user never has to type their "
            "password again right after confirming.\n\n"
            "The token is rejected with **400** when it is unknown, "
            "already used, or past its ``AUTH_ACTIVATION_TTL_SECONDS`` "
            "expiry.\n\n"
            "!!! note\n"
            "    When ``AUTH_BACKEND_LINKS=True`` the SDK also mounts a "
            "    **GET** ``/activate/{token}`` that renders an HTML page "
            "    directly from the backend — use that one when you have "
            "    no frontend. This POST endpoint is always available."
        ),
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
        summary="Log in with email + password → JWT pair",
        description=(
            "Authenticate an **active** user with their email and "
            "password and receive a JWT ``access_token`` + "
            "``refresh_token`` pair.\n\n"
            "Returns **401** for wrong credentials and for accounts that "
            "exist but were never activated — the message is "
            "intentionally generic so callers can't tell which case it "
            "was.\n\n"
            "**MFA.** When the user has finished TOTP enrollment "
            "(and ``AUTH_MFA_ENABLED=True``) this endpoint does *not* "
            "return the JWT pair. Instead it returns "
            "``mfa_required=true`` plus a short-lived ``mfa_token``; "
            "exchange that token for the real JWT pair at "
            "``/mfa/verify``."
        ),
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
        summary="Start a password reset (request a reset link)",
        description=(
            "Kick off the forgot-password flow for the given email.\n\n"
            "**Always returns 202** with the same generic message "
            "whether or not the email matches a real account — this "
            "prevents attackers from enumerating which emails are "
            "registered by probing this endpoint.\n\n"
            "When a matching account exists a single-use reset token is "
            "issued (valid for ``AUTH_PASSWORD_RESET_TTL_SECONDS``). "
            "When ``EmailUtils`` is wired the reset link is **emailed** "
            "(localized via ``AUTH_DEFAULT_LOCALE``) and ``reset_url`` "
            "stays ``null`` in the body; when email is not configured — "
            "or ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` — the "
            "ready-to-use ``reset_url`` is returned in the body so you "
            "can drive the flow without SMTP."
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
        summary="Finish a password reset (set the new password) — JSON",
        description=(
            "Complete the forgot-password flow: consume the single-use "
            "reset token and store the new password.\n\n"
            "This is the **JSON / SPA** variant — a frontend collects "
            "the new password and POSTs it together with the ``token`` "
            "read from the reset URL. The new password must satisfy "
            "``AUTH_PASSWORD_MIN_LENGTH`` and the complexity rules; "
            "violations return **422**. A token that is unknown, "
            "already used, or expired returns **400**.\n\n"
            "On success the password is updated **and the user is "
            "logged in** — the response carries a fresh "
            "``access_token`` + ``refresh_token`` pair.\n\n"
            "!!! note\n"
            "    When ``AUTH_BACKEND_LINKS=True`` the SDK also mounts "
            "    **GET/POST** ``/password-reset/{token}`` that render a "
            "    self-contained HTML form + result page from the "
            "    backend — use those when you have no frontend."
        ),
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

    @router.post(
        "/password-change",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Change your own password (while logged in)",
        description=(
            "Rotate the **currently authenticated** user's password "
            "(requires a valid bearer ``access_token``).\n\n"
            "Unlike the forgot-password flow there is **no token** — the "
            "user is already logged in. They must re-enter their "
            "``current_password`` to confirm ownership; a mismatch "
            "returns **401**. The ``new_password`` must satisfy "
            "``AUTH_PASSWORD_MIN_LENGTH`` (and the complexity rules when "
            "``AUTH_PASSWORD_REQUIRE_COMPLEXITY=True``); violations return "
            "**422**.\n\n"
            "On success the endpoint returns **204**. The existing "
            "``access_token`` / ``refresh_token`` stay valid — this "
            "endpoint does not revoke sessions."
        ),
    )
    async def password_change(
        payload: PasswordChangeSchema,
        session: AsyncSession = session_dep,
        user: BaseUserModel = Depends(current_user_dep),
    ) -> None:
        await service.change_password(
            session,
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
        await session.commit()

    @router.post(
        "/refresh",
        response_model=LoginResponseSchema,
        summary="Exchange a refresh token for a fresh JWT pair",
        description=(
            "Mint a brand-new ``access_token`` + ``refresh_token`` pair "
            "from a valid **refresh token** — no email or password "
            "required. This is how a client keeps a session alive once "
            "the short-lived ``access_token`` expires: replay the "
            "long-lived ``refresh_token`` here instead of forcing the "
            "user to log in again.\n\n"
            "The submitted token must actually be a refresh token (it "
            "carries the ``refresh`` claim) — a stolen *access* token "
            "replayed here is rejected with **401**. An expired, "
            "malformed, or wrongly-signed token also returns **401**, and "
            "an inactive account returns **403**.\n\n"
            "!!! warning\n"
            "    Both tokens **rotate**: the response carries a new "
            "    refresh token. Persist that one and discard the token "
            "    you sent — the old pair is independent and stays valid "
            "    until its own expiry (the SDK issues stateless JWTs and "
            "    does not revoke the previous refresh token)."
        ),
    )
    async def refresh(
        payload: RefreshSchema,
        session: AsyncSession = session_dep,
    ) -> LoginResponseSchema:
        user, access, refresh_token = await service.refresh_tokens(
            session,
            refresh_token=payload.refresh_token,
        )
        return LoginResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh_token,
        )

    # ------------------------------------------------------------------
    # Backend-only HTML endpoints — mounted only when AUTH_BACKEND_LINKS.
    # ------------------------------------------------------------------

    if backend_links:

        @router.get(
            "/activate/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Activate an account from the emailed link (HTML page)",
            description=(
                "Backend-rendered activation landing page (mounted only "
                "when ``AUTH_BACKEND_LINKS=True``). This is the URL the "
                "activation **email button points at** when you have no "
                "frontend: the user clicks it, the browser issues this "
                "GET, the backend consumes the token and renders a "
                "localized HTML success page "
                "(``AUTH_ACTIVATION_SUCCESS_TEMPLATE``) — or an error "
                "page (``AUTH_ACTIVATION_ERROR_TEMPLATE``) on a bad / "
                "expired token. The page language is negotiated from the "
                "browser's ``Accept-Language`` header, falling back to "
                "``AUTH_DEFAULT_LOCALE``."
            ),
        )
        async def activate_html(
            request: Request,
            token: str,
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            locale = _page_locale(request)
            try:
                user = await service.activate(session, token=token)
            except InvalidTokenException as exc:
                await session.rollback()
                return _render_error(
                    auth_settings.AUTH_ACTIVATION_ERROR_TEMPLATE,
                    reason=exc.message,
                    locale=locale,
                )
            await session.commit()
            html = render_auth_page(
                auth_settings.AUTH_ACTIVATION_SUCCESS_TEMPLATE,
                {"user": user, "login_url": login_url},
                template_dir=template_dir,
                locale=locale,
            )
            return HTMLResponse(content=html)

        @router.get(
            "/password-reset/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Render the password-reset form (HTML page)",
            description=(
                "Backend-rendered password-reset form (mounted only when "
                "``AUTH_BACKEND_LINKS=True``). This is the URL the reset "
                "**email button points at** when you have no frontend: "
                "the user clicks it and the backend validates the token, "
                "then renders a localized HTML form "
                "(``AUTH_PASSWORD_RESET_FORM_TEMPLATE``) that POSTs back "
                "to the same path. A bad / expired token renders the "
                "error page (``AUTH_PASSWORD_RESET_ERROR_TEMPLATE``) "
                "instead. The page language is negotiated from "
                "``Accept-Language``, falling back to "
                "``AUTH_DEFAULT_LOCALE``."
            ),
        )
        async def password_reset_form(
            request: Request,
            token: str,
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            locale = _page_locale(request)
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
                    locale=locale,
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
                locale=locale,
            )
            return HTMLResponse(content=html)

        @router.post(
            "/password-reset/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Process the password-reset form (HTML, form-encoded)",
            description=(
                "Form-encoded submit target for the backend password-"
                "reset form (mounted only when "
                "``AUTH_BACKEND_LINKS=True``). Validates that "
                "``new_password`` and ``confirm_password`` match and "
                "satisfy the password policy, consumes the token, stores "
                "the new password, then re-renders the form with a "
                "localized inline error on any problem, or the success "
                "page (``AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE``) when it "
                "works. The page language is negotiated from "
                "``Accept-Language``, falling back to "
                "``AUTH_DEFAULT_LOCALE``."
            ),
        )
        async def password_reset_form_submit(
            request: Request,
            token: str,
            new_password: str = Form(...),
            confirm_password: str = Form(...),
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            locale = _page_locale(request)
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
                        locale=locale,
                    )
                html = render_auth_page(
                    auth_settings.AUTH_PASSWORD_RESET_FORM_TEMPLATE,
                    {
                        "user": user,
                        "form_action": f"{prefix}/password-reset/{token}",
                        "min_length": min_length,
                        "error": auth_page_message(locale, "passwords_do_not_match"),
                        "login_url": login_url,
                    },
                    template_dir=template_dir,
                    locale=locale,
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
                    locale=locale,
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
                        locale=locale,
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
                    locale=locale,
                )
                return HTMLResponse(content=html, status_code=400)
            await session.commit()
            html = render_auth_page(
                auth_settings.AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE,
                {"user": user, "login_url": login_url},
                template_dir=template_dir,
                locale=locale,
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

        @router.post(
            "/mfa/enroll",
            response_model=MFAEnrollResponseSchema,
            summary="Begin TOTP enrollment (returns secret + recovery codes once)",
            description=(
                "Start two-factor enrollment for the **currently "
                "authenticated** user (requires a valid bearer "
                "``access_token``).\n\n"
                "Generates a fresh TOTP ``secret`` and a "
                "``provisioning_uri`` you render as a QR code for an "
                "authenticator app (Google Authenticator, 1Password, "
                "etc.), plus a batch of one-time ``recovery_codes``.\n\n"
                "!!! warning\n"
                "    The secret and recovery codes are returned **only "
                "    this once** and are never retrievable again — show "
                "    them to the user immediately. Enrollment is not "
                "    active until confirmed at ``/mfa/confirm``."
            ),
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
            summary="Finish TOTP enrollment (verify the first code)",
            description=(
                "Activate the TOTP enrollment started at "
                "``/mfa/enroll`` for the **currently authenticated** "
                "user.\n\n"
                "Submit the 6-digit ``code`` currently shown by the "
                "authenticator app — this proves the shared secret was "
                "stored correctly. A wrong or expired code returns "
                "**401** and enrollment stays inactive. On success the "
                "endpoint returns **204** and every subsequent "
                "``/login`` for this user becomes a two-step flow."
            ),
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
            summary="Turn off TOTP for the current user",
            description=(
                "Remove two-factor authentication from the **currently "
                "authenticated** user and delete their stored recovery "
                "codes.\n\n"
                "Re-authentication is required: the caller must supply "
                "both the account ``password`` **and** a currently-valid "
                "TOTP ``code`` (or recovery code). Either one wrong "
                "returns **401** and MFA stays enabled. On success the "
                "endpoint returns **204** and ``/login`` goes back to a "
                "single step."
            ),
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
            summary="Complete a two-step login (exchange mfa_token + code)",
            description=(
                "Second and final step of an MFA-protected login.\n\n"
                "When ``/login`` returns ``mfa_required=true`` it hands "
                "back a short-lived ``mfa_token``. POST that token here "
                "together with the current 6-digit TOTP ``code`` (or a "
                "one-time recovery code). On success the endpoint "
                "returns the real ``access_token`` + ``refresh_token`` "
                "pair, finishing the login.\n\n"
                "A wrong / expired ``code`` or ``mfa_token`` returns "
                "**401**. This endpoint needs **no** bearer token — the "
                "``mfa_token`` itself is the proof that step one "
                "succeeded."
            ),
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
