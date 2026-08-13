"""``make_auth_router`` — FastAPI router for the bundled auth flow.

Wraps the canonical signup-with-email endpoints every project
ends up implementing the same way. The router exposes two
flavors:

**Default (SPA mode)** — five JSON endpoints designed to be
consumed by a frontend that owns the activation / reset UI:

* ``POST /auth/signup`` — create user + maybe send activation
* ``POST /auth/activate/{token}`` — consume activation + log in
* ``POST /auth/login`` — email + password → JWT pair
* ``GET /auth/me`` — the account behind the bearer token
* ``POST /auth/refresh`` — exchange a refresh token for a new pair
* ``POST /auth/logout`` — revoke a refresh token *(mounted only
  when a ``refresh_token_model`` is wired — DB-backed mode)*
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

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from tempest_fastapi_sdk.api.dependencies import make_jwt_user_dependency
from tempest_fastapi_sdk.auth.locale import auth_page_message, negotiate_locale
from tempest_fastapi_sdk.auth.page_renderer import render_auth_page
from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema,
    AuthUserSchema,
    EmailChangeConfirmSchema,
    EmailChangeRequestSchema,
    EmailChangeResponseSchema,
    EmailRecoveryRequestSchema,
    LoginResponseSchema,
    LoginSchema,
    LogoutSchema,
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
    WebAuthnAuthenticateBeginSchema,
    WebAuthnAuthenticateCompleteSchema,
    WebAuthnCredentialSchema,
    WebAuthnDeleteSchema,
    WebAuthnOptionsSchema,
    WebAuthnRegisterCompleteSchema,
)
from tempest_fastapi_sdk.auth.token_delivery import (
    AuthCookieConfig,
    TokenDelivery,
    apply_auth_cookies,
    clear_auth_cookies,
)
from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    InvalidTokenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.auth.service import UserAuthService
    from tempest_fastapi_sdk.auth.webauthn import WebAuthnService
    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.db.user_recovery_code_model import (
        BaseUserRecoveryCodeModel,
    )
    from tempest_fastapi_sdk.db.user_webauthn_credential_model import (
        BaseWebAuthnCredentialModel,
    )


def make_auth_router(
    service: UserAuthService,
    *,
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    prefix: str = "/auth",
    tags: list[str] | None = None,
    template_dir: str | None = None,
    recovery_code_model: type[BaseUserRecoveryCodeModel] | None = None,
    me_response_model: type[BaseModel] | None = None,
    token_delivery: TokenDelivery | None = None,
    cookie_config: AuthCookieConfig | None = None,
    webauthn: WebAuthnService | None = None,
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
        me_response_model (type[BaseModel] | None): Response model for
            ``GET /auth/me``. ``None`` (default) uses
            :class:`~tempest_fastapi_sdk.auth.schemas.AuthUserSchema`,
            which covers exactly the columns ``BaseUserModel``
            guarantees. Pass a subclass to expose the extra columns your
            user table carries — the endpoint hands the ORM instance to
            FastAPI, so whatever the model does not declare is never
            serialized.
        token_delivery (TokenDelivery | None): How login / refresh hand
            back the JWT pair — ``"bearer"`` (body only), ``"cookie"``
            (``HttpOnly`` cookies, body omits tokens) or ``"both"``
            (bearer at ``/auth/*`` plus a parallel cookie set at
            ``/auth/cookie/*``). ``None`` (default) reads
            ``AuthSettings.AUTH_TOKEN_DELIVERY``.
        cookie_config (AuthCookieConfig | None): Override the cookie
            security attributes. ``None`` (default) builds one from the
            ``AUTH_COOKIE_*`` settings and the JWT TTLs. Only used when
            the delivery mode involves cookies.
        recovery_code_model (type[BaseUserRecoveryCodeModel] | None): Model
            storing the one-time MFA recovery codes. ``None`` (default)
            falls back to the SDK's bundled model, built on demand — pass
            your own when the project owns that table.
        webauthn (WebAuthnService | None): Configured
            :class:`~tempest_fastapi_sdk.auth.webauthn.WebAuthnService`.
            Required when ``AUTH_WEBAUTHN_ENABLED`` is on; it carries the
            relying-party identity, the credential model and the challenge
            store, none of which the router can infer.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.

    Notes:
        Which endpoint groups get mounted, and where:

        * **JSON / SPA endpoints** are always mounted.
        * **Cookie endpoints** are mounted when ``AUTH_TOKEN_DELIVERY`` is
          ``cookie`` or ``both``. In ``cookie`` mode they take the normal
          ``/login``, ``/refresh`` and ``/logout`` paths; in ``both`` mode
          they move under a ``/cookie/*`` sub-prefix so they do not collide
          with the bearer endpoints sharing the router.
        * **Backend-rendered HTML endpoints** are mounted only when
          ``AUTH_BACKEND_LINKS`` is on.
        * **MFA endpoints** are mounted only when ``AUTH_MFA_ENABLED`` is on.
        * **WebAuthn endpoints** are mounted only when
          ``AUTH_WEBAUTHN_ENABLED`` is on and a ``webauthn`` service is
          passed.

        The refresh cookie is scoped to the auth base path rather than the
        site root, so it reaches the refresh and logout endpoints but is not
        sent along with ordinary API requests.

        The authenticated-user dependency is shared by the password-change
        route and, when enabled, the MFA routes. With cookies in play it also
        accepts the access token from the cookie, with the header still
        taking precedence.

    Raises:
        RuntimeError: When the requested token delivery mode needs a
            refresh-token model and the service was built without one.
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

    # --- token delivery (bearer / cookie / both) ----------------------
    delivery: TokenDelivery = token_delivery or auth_settings.AUTH_TOKEN_DELIVERY
    mount_bearer = delivery in ("bearer", "both")
    cookie_enabled = delivery in ("cookie", "both")
    cookie_suffix = "/cookie" if delivery == "both" else ""
    cookie_base = f"{prefix}{cookie_suffix}"
    cookies = cookie_config or AuthCookieConfig(
        access_name=auth_settings.AUTH_ACCESS_COOKIE_NAME,
        refresh_name=auth_settings.AUTH_REFRESH_COOKIE_NAME,
        access_max_age=service.jwt_settings.JWT_ACCESS_TTL_SECONDS,
        refresh_max_age=service.jwt_settings.JWT_REFRESH_TTL_SECONDS,
        refresh_path=cookie_base or "/",
        secure=auth_settings.AUTH_COOKIE_SECURE,
        samesite=auth_settings.AUTH_COOKIE_SAMESITE,
        domain=auth_settings.AUTH_COOKIE_DOMAIN,
    )

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

    current_user_dep = make_jwt_user_dependency(
        service.jwt,
        user_loader=_make_user_loader(service),
        cookie_name=cookies.access_name if cookie_enabled else None,
        session_dependency=_session,
    )

    def _mount_if(
        condition: bool,
        decorator: Callable[[Callable[..., Any]], Callable[..., Any]],
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Apply ``decorator`` only when ``condition`` holds.

        Lets a route be defined unconditionally but registered on the
        router only in the relevant delivery mode, without reindenting
        the handler into an ``if`` block.

        Args:
            condition (bool): Whether to register the route.
            decorator (Callable): The ``router.post(...)`` decorator.

        Returns:
            Callable: ``decorator`` when ``condition`` is truthy,
            otherwise an identity decorator that skips registration.
        """
        if condition:
            return decorator
        return lambda func: func

    # ------------------------------------------------------------------
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
        """Register a new account.

        Args:
            payload (SignupSchema): Email, password and optional name.
            session (AsyncSession): The request-scoped DB session.

        Returns:
            SignupResponseSchema: The created user, plus the activation
            token or JWT pair depending on the configured flow.
        """
        user, activation = await service.signup(
            session,
            email=payload.email,
            password=payload.password,
            name=payload.name,
        )
        if activation is None:
            access, refresh = await service.issue_token_pair(session, user)
            await session.commit()
            return SignupResponseSchema(
                user_id=user.id,
                activation_required=False,
                activation_url=None,
                access_token=access,
                refresh_token=refresh,
            )
        await session.commit()
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
        """Activate an account from its activation token.

        Args:
            token (str): The activation token from the emailed link.
            session (AsyncSession): The request-scoped DB session.

        Returns:
            ActivationResponseSchema: The activated user.
        """
        user = await service.activate(session, token=token)
        access, refresh = await service.issue_token_pair(session, user)
        await session.commit()
        return ActivationResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    @_mount_if(
        mount_bearer,
        router.post(
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
        ),
    )
    async def login(
        payload: LoginSchema,
        session: AsyncSession = session_dep,
    ) -> LoginResponseSchema:
        """Authenticate and issue the JWT pair.

        Args:
            payload (LoginSchema): Email and password.
            session (AsyncSession): The request-scoped DB session.

        Returns:
            LoginResponseSchema: The token pair, or an MFA challenge when
            the account has TOTP enrolled.
        """
        user = await service.login(
            session,
            email=payload.email,
            password=payload.password,
        )
        if service.is_mfa_enrolled(user):
            mfa_token = service.issue_mfa_token(user)
            await session.commit()
            return LoginResponseSchema(
                user_id=user.id,
                access_token=None,
                refresh_token=None,
                mfa_required=True,
                mfa_token=mfa_token,
            )
        access, refresh = await service.issue_token_pair(session, user)
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
        access, refresh = await service.issue_token_pair(session, user)
        await session.commit()
        return LoginResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    @router.get(
        "/me",
        response_model=me_response_model or AuthUserSchema,
        summary="Return the authenticated account",
        description=(
            "Resolve the bearer ``access_token`` to the account that "
            "owns it.\n\n"
            "Two jobs in one endpoint: it tells the client **who** is "
            "logged in without the client caching a profile, and it says "
            "whether a stored token is **still valid** — a **401** is the "
            "signal to call ``/refresh`` before sending the user back to "
            "the login screen.\n\n"
            "The response never carries the password hash. FastAPI "
            "serializes through the response model, so only declared "
            "fields reach the wire; a project that wants extra columns "
            "subclasses ``AuthUserSchema`` and passes it as "
            "``me_response_model``.\n\n"
            "Returns **404** when the token is well-formed and unexpired "
            "but the account behind it no longer exists."
        ),
    )
    async def me(
        user: BaseUserModel = Depends(current_user_dep),
    ) -> BaseUserModel:
        """Return the account owning the request's bearer token.

        Args:
            user (BaseUserModel): Resolved from the token by the shared
                authenticated-user dependency.

        Returns:
            BaseUserModel: The account, serialized through
            ``me_response_model`` (defaults to
            :class:`~tempest_fastapi_sdk.auth.schemas.AuthUserSchema`).
        """
        return user

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
        "/email-change/request",
        response_model=EmailChangeResponseSchema,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Start an email change (while logged in)",
        description=(
            "Stage a move to a new email for the **currently "
            "authenticated** user (requires a valid bearer "
            "``access_token``). The user re-enters ``current_password`` "
            "to confirm ownership; a mismatch returns **401**, an "
            "already-taken address returns **409**.\n\n"
            "A single-use confirmation link is sent to the **new** "
            "address (valid for ``AUTH_EMAIL_CHANGE_TTL_SECONDS``); the "
            "account email only changes once that link is confirmed via "
            "``POST /auth/email-change/confirm``. When "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` or the ``[email]`` "
            "extra is absent, the ready-to-use ``confirm_url`` is "
            "returned in the body instead of emailed."
        ),
    )
    async def email_change_request(
        payload: EmailChangeRequestSchema,
        session: AsyncSession = session_dep,
        user: BaseUserModel = Depends(current_user_dep),
    ) -> EmailChangeResponseSchema:
        token = await service.request_email_change(
            session,
            user=user,
            current_password=payload.current_password,
            new_email=payload.new_email,
        )
        await session.commit()
        message = "Check your new inbox to confirm the change."
        if token is None:
            return EmailChangeResponseSchema(message=message, confirm_url=None)
        return EmailChangeResponseSchema(message=message, confirm_url=token.url)

    @router.post(
        "/email-change/confirm",
        response_model=EmailChangeResponseSchema,
        summary="Finish an email change (apply the new address) — JSON",
        description=(
            "Consume the single-use token from the confirmation link and "
            "flip the account email to the staged address. An unknown, "
            "already-used or expired token returns **400**; a target "
            "address taken in the meantime returns **409**.\n\n"
            "When ``AUTH_EMAIL_CHANGE_NOTIFY_OLD=True`` a security notice "
            "is sent to the previous address on success.\n\n"
            "!!! note\n"
            "    When ``AUTH_BACKEND_LINKS=True`` the SDK also mounts "
            "    **GET** ``/email-change/{token}`` that renders a "
            "    self-contained HTML result page from the backend."
        ),
    )
    async def email_change_confirm(
        payload: EmailChangeConfirmSchema,
        session: AsyncSession = session_dep,
    ) -> EmailChangeResponseSchema:
        await service.confirm_email_change(session, token=payload.token)
        await session.commit()
        return EmailChangeResponseSchema(
            message="Your email was changed.",
            confirm_url=None,
        )

    @router.post(
        "/email-verify/request",
        response_model=EmailChangeResponseSchema,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Re-send a verification link for your current email",
        description=(
            "Issue a fresh verification link for the **currently "
            "authenticated** user's existing email (requires a valid "
            "bearer ``access_token``). No address change — confirming the "
            "link marks the account active. Useful when the original "
            "activation email was lost. The link is emailed, or returned "
            "as ``confirm_url`` when ``AUTH_RETURN_TOKEN_IN_RESPONSE=True``."
        ),
    )
    async def email_verify_request(
        session: AsyncSession = session_dep,
        user: BaseUserModel = Depends(current_user_dep),
    ) -> EmailChangeResponseSchema:
        token = await service.request_email_verification(session, user=user)
        await session.commit()
        message = "Check your inbox to verify your email."
        if token is None:
            return EmailChangeResponseSchema(message=message, confirm_url=None)
        return EmailChangeResponseSchema(message=message, confirm_url=token.url)

    @router.post(
        "/email-verify/confirm",
        response_model=EmailChangeResponseSchema,
        summary="Confirm your current email (mark the account verified) — JSON",
        description=(
            "Consume the single-use token from the verification link and "
            "mark the account active. An unknown, already-used or expired "
            "token returns **400**."
        ),
    )
    async def email_verify_confirm(
        payload: EmailChangeConfirmSchema,
        session: AsyncSession = session_dep,
    ) -> EmailChangeResponseSchema:
        await service.confirm_email_verification(session, token=payload.token)
        await session.commit()
        return EmailChangeResponseSchema(
            message="Your email was verified.",
            confirm_url=None,
        )

    @_mount_if(
        auth_settings.AUTH_EMAIL_RECOVERY_ENABLED,
        router.post(
            "/email-recovery/request",
            response_model=EmailChangeResponseSchema,
            status_code=status.HTTP_202_ACCEPTED,
            summary="Recover an account whose mailbox is no longer accessible",
            description=(
                "**Unauthenticated** entry point for a user who lost access "
                "to their email. Mounted only when "
                "``AUTH_EMAIL_RECOVERY_ENABLED=True``.\n\n"
                "The account is located by its current (old) ``email``; "
                "identity is proven by ``current_password`` and — when the "
                "account has MFA enrolled — a valid ``mfa_code``. On success "
                "a confirmation link is sent to the **new** address and a "
                "security notice to the old one.\n\n"
                "**Always returns 202** with the same generic message for "
                "every soft failure (unknown email, wrong password, "
                "missing/invalid MFA code) so the endpoint can't be used to "
                "enumerate accounts."
            ),
        ),
    )
    async def email_recovery_request(
        payload: EmailRecoveryRequestSchema,
        session: AsyncSession = session_dep,
    ) -> EmailChangeResponseSchema:
        token = await service.request_email_recovery(
            session,
            email=payload.email,
            new_email=payload.new_email,
            current_password=payload.current_password,
            mfa_code=payload.mfa_code,
            recovery_code_model=recovery_code_model,
        )
        await session.commit()
        message = "If the details match an account, a confirmation link was sent."
        if token is None:
            return EmailChangeResponseSchema(message=message, confirm_url=None)
        return EmailChangeResponseSchema(message=message, confirm_url=token.url)

    @_mount_if(
        mount_bearer,
        router.post(
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
                "A stolen *access* token replayed here is rejected with "
                "**401**. An expired, malformed, revoked, or wrongly-signed "
                "token also returns **401**, and an inactive account returns "
                "**403**.\n\n"
                "!!! warning\n"
                "    Both tokens **rotate**: the response carries a new "
                "    refresh token. Persist that one and discard the token "
                "    you sent.\n\n"
                '!!! info "Stateless vs DB-backed"\n'
                "    When the service is wired with a ``refresh_token_model`` "
                "    the refresh token is an **opaque, single-use** value: the "
                "    presented token is invalidated on rotation, replaying an "
                "    already-rotated token is treated as theft and **revokes "
                "    the whole token family** (401), and ``POST /auth/logout`` "
                "    can revoke a session early. Without that model the SDK "
                "    falls back to a stateless JWT refresh token — the old "
                "    token stays valid until its own expiry and cannot be "
                "    revoked."
            ),
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
        await session.commit()
        return LoginResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh_token,
        )

    if mount_bearer and service.refresh_token_model is not None:

        @router.post(
            "/logout",
            status_code=status.HTTP_204_NO_CONTENT,
            summary="Revoke a refresh token (logout)",
            description=(
                "Revoke a **DB-backed refresh token** so it can no longer "
                "be exchanged at ``POST /auth/refresh``, even before its "
                "natural expiry.\n\n"
                "By default only the presented token's rotation **family** "
                "(the lineage descended from one login) is revoked — so a "
                "descendant token a thief may hold dies too. Pass "
                "``all_sessions=true`` to revoke **every** active refresh "
                "token the user owns (log out everywhere).\n\n"
                "The endpoint is **idempotent**: an unknown / already-"
                "revoked token still returns **204** and never leaks "
                "whether the token existed.\n\n"
                "!!! note\n"
                "    Mounted only when the service is wired with a "
                "    ``refresh_token_model``. Stateless JWT refresh tokens "
                "    cannot be revoked, so the endpoint is absent in that "
                "    mode."
            ),
        )
        async def logout(
            payload: LogoutSchema,
            session: AsyncSession = session_dep,
        ) -> None:
            await service.revoke_refresh_token(
                session,
                refresh_token=payload.refresh_token,
                all_sessions=payload.all_sessions,
            )
            await session.commit()

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------

    if cookie_enabled:

        @router.post(
            f"{cookie_suffix}/login",
            response_model=LoginResponseSchema,
            summary="Log in — set the JWT pair as HttpOnly cookies",
            description=(
                "Same credentials check as ``POST /auth/login``, but the "
                "``access_token`` / ``refresh_token`` are returned as "
                "``HttpOnly`` cookies instead of in the body — the body "
                "keeps them ``null``. The MFA branch is unchanged: when a "
                "second factor is required the response still carries "
                "``mfa_required=true`` + ``mfa_token`` and sets no session "
                "cookies."
            ),
        )
        async def login_cookie(
            payload: LoginSchema,
            response: Response,
            session: AsyncSession = session_dep,
        ) -> LoginResponseSchema:
            user = await service.login(
                session,
                email=payload.email,
                password=payload.password,
            )
            if service.is_mfa_enrolled(user):
                mfa_token = service.issue_mfa_token(user)
                await session.commit()
                return LoginResponseSchema(
                    user_id=user.id,
                    mfa_required=True,
                    mfa_token=mfa_token,
                )
            access, refresh = await service.issue_token_pair(session, user)
            await session.commit()
            apply_auth_cookies(
                response,
                access_token=access,
                refresh_token=refresh,
                config=cookies,
            )
            return LoginResponseSchema(user_id=user.id)

        @router.post(
            f"{cookie_suffix}/refresh",
            response_model=LoginResponseSchema,
            summary="Rotate the JWT pair from the refresh cookie",
            description=(
                "Mint a fresh ``access_token`` / ``refresh_token`` pair "
                "from the **refresh cookie** (no body required) and set "
                "the rotated pair back as cookies. Returns **401** when "
                "the refresh cookie is missing, expired, revoked or is "
                "actually an access token; **403** for an inactive "
                "account."
            ),
        )
        async def refresh_cookie(
            request: Request,
            response: Response,
            session: AsyncSession = session_dep,
        ) -> LoginResponseSchema:
            token = request.cookies.get(cookies.refresh_name)
            if not token:
                raise UnauthorizedException(
                    message="Missing refresh token cookie",
                )
            user, access, refresh_token = await service.refresh_tokens(
                session,
                refresh_token=token,
            )
            await session.commit()
            apply_auth_cookies(
                response,
                access_token=access,
                refresh_token=refresh_token,
                config=cookies,
            )
            return LoginResponseSchema(user_id=user.id)

        @router.post(
            f"{cookie_suffix}/logout",
            status_code=status.HTTP_204_NO_CONTENT,
            summary="Clear the auth cookies (and revoke the refresh token)",
            description=(
                "Delete both auth cookies. When the service is wired with "
                "a ``refresh_token_model`` and a refresh cookie is "
                "present, its rotation **family** is revoked too so the "
                "session dies server-side. Always returns **204**, even "
                "when no cookie was set (idempotent)."
            ),
        )
        async def logout_cookie(
            request: Request,
            response: Response,
            session: AsyncSession = session_dep,
        ) -> None:
            token = request.cookies.get(cookies.refresh_name)
            if token and service.refresh_token_model is not None:
                await service.revoke_refresh_token(
                    session,
                    refresh_token=token,
                    all_sessions=False,
                )
                await session.commit()
            clear_auth_cookies(response, config=cookies)

    # ------------------------------------------------------------------
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

        @router.get(
            "/email-change/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Confirm an email change from the emailed link (HTML page)",
            description=(
                "Backend-rendered email-change confirmation page (mounted "
                "only when ``AUTH_BACKEND_LINKS=True``). This is the URL "
                "the confirmation **email button points at** when you have "
                "no frontend: the user clicks it, the backend consumes the "
                "token and applies the new address, then renders a "
                "localized success page "
                "(``AUTH_EMAIL_CHANGE_SUCCESS_TEMPLATE``) — or an error "
                "page (``AUTH_EMAIL_CHANGE_ERROR_TEMPLATE``) on a bad / "
                "expired token or a target address taken meanwhile. The "
                "page language is negotiated from ``Accept-Language``, "
                "falling back to ``AUTH_DEFAULT_LOCALE``."
            ),
        )
        async def email_change_html(
            request: Request,
            token: str,
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            locale = _page_locale(request)
            try:
                user = await service.confirm_email_change(session, token=token)
            except (InvalidTokenException, ConflictException) as exc:
                await session.rollback()
                return _render_error(
                    auth_settings.AUTH_EMAIL_CHANGE_ERROR_TEMPLATE,
                    reason=exc.message,
                    locale=locale,
                )
            await session.commit()
            html = render_auth_page(
                auth_settings.AUTH_EMAIL_CHANGE_SUCCESS_TEMPLATE,
                {"user": user, "login_url": login_url},
                template_dir=template_dir,
                locale=locale,
            )
            return HTMLResponse(content=html)

        @router.get(
            "/email-verify/{token}",
            response_class=HTMLResponse,
            include_in_schema=False,
            summary="Verify your current email from the emailed link (HTML page)",
            description=(
                "Backend-rendered email-verification page (mounted only "
                "when ``AUTH_BACKEND_LINKS=True``). Consumes the token, "
                "marks the account active, and renders a localized success "
                "page (``AUTH_EMAIL_VERIFICATION_SUCCESS_TEMPLATE``) — or "
                "an error page (``AUTH_EMAIL_VERIFICATION_ERROR_TEMPLATE``) "
                "on a bad / expired token. The page language is negotiated "
                "from ``Accept-Language``, falling back to "
                "``AUTH_DEFAULT_LOCALE``."
            ),
        )
        async def email_verify_html(
            request: Request,
            token: str,
            session: AsyncSession = session_dep,
        ) -> HTMLResponse:
            locale = _page_locale(request)
            try:
                user = await service.confirm_email_verification(session, token=token)
            except InvalidTokenException as exc:
                await session.rollback()
                return _render_error(
                    auth_settings.AUTH_EMAIL_VERIFICATION_ERROR_TEMPLATE,
                    reason=exc.message,
                    locale=locale,
                )
            await session.commit()
            html = render_auth_page(
                auth_settings.AUTH_EMAIL_VERIFICATION_SUCCESS_TEMPLATE,
                {"user": user, "login_url": login_url},
                template_dir=template_dir,
                locale=locale,
            )
            return HTMLResponse(content=html)

    # ------------------------------------------------------------------
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
            """Start TOTP enrollment and return the secret plus recovery codes.

            Args:
                session (AsyncSession): The request-scoped DB session.
                user (BaseUserModel): The authenticated caller.

            Returns:
                MFAEnrollResponseSchema: The shared secret, provisioning URI and
            one-time recovery codes — returned only once.
            """
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
            """Confirm TOTP enrollment with a code from the authenticator.

            Args:
                payload (MFAConfirmSchema): The 6-digit code from the authenticator.
                session (AsyncSession): The request-scoped DB session.
                user (BaseUserModel): The authenticated caller.
            """
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
            """Disable TOTP after re-authenticating with password and code.

            Args:
                payload (MFADisableSchema): The account password plus a valid TOTP
                    or recovery code.
                session (AsyncSession): The request-scoped DB session.
                user (BaseUserModel): The authenticated caller.
            """
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
            """Exchange an MFA token plus TOTP code for the real JWT pair.

            Args:
                payload (MFAVerifySchema): The short-lived ``mfa_token`` from step
                    one plus the current TOTP or recovery code.
                session (AsyncSession): The request-scoped DB session.

            Returns:
                LoginResponseSchema: The access + refresh token pair.
            """
            user = await service.mfa_verify(
                session,
                mfa_token=payload.mfa_token,
                code=payload.code,
                recovery_code_model=recovery_code_model,
            )
            access, refresh = await service.issue_token_pair(session, user)
            await session.commit()
            return LoginResponseSchema(
                user_id=user.id,
                access_token=access,
                refresh_token=refresh,
            )

    # ------------------------------------------------------------------
    # WebAuthn / passkeys
    # ------------------------------------------------------------------

    if auth_settings.AUTH_WEBAUTHN_ENABLED:
        if webauthn is None:
            raise RuntimeError(
                "AUTH_WEBAUTHN_ENABLED=True requires a configured "
                "WebAuthnService passed to make_auth_router(webauthn=...)."
            )
        service_webauthn: WebAuthnService = webauthn

        @router.post(
            "/webauthn/register/begin",
            response_model=WebAuthnOptionsSchema,
            summary="Start registering a passkey / security key",
            description=(
                "First half of the registration ceremony, for the "
                "**currently authenticated** user (requires a valid "
                "bearer ``access_token``).\n\n"
                "Pass the returned ``options.publicKey`` to "
                "``navigator.credentials.create()`` and post what it "
                "returns to ``/webauthn/register/complete``, along with "
                "the ``challenge_id`` from this response.\n\n"
                "Credentials the account already holds are sent as "
                "``excludeCredentials``, so registering the same "
                "authenticator twice is refused by the device instead "
                "of creating a duplicate nobody can tell apart."
            ),
        )
        async def webauthn_register_begin(
            session: AsyncSession = session_dep,
            user: BaseUserModel = Depends(current_user_dep),
        ) -> WebAuthnOptionsSchema:
            """Return the creation options plus the ceremony handle.

            Args:
                session (AsyncSession): The request-scoped DB session.
                user (BaseUserModel): The authenticated caller.

            Returns:
                WebAuthnOptionsSchema: Options for
                ``navigator.credentials.create()`` and the
                ``challenge_id`` to echo back.
            """
            options, challenge_id = await service_webauthn.register_begin(
                session,
                user=user,
            )
            return WebAuthnOptionsSchema(
                challenge_id=challenge_id,
                options=options,
            )

        @router.post(
            "/webauthn/register/complete",
            response_model=WebAuthnCredentialSchema,
            summary="Finish registering a passkey / security key",
            description=(
                "Second half of the registration ceremony. Verifies the "
                "attestation against the challenge issued by "
                "``/webauthn/register/begin`` and stores the public "
                "key.\n\n"
                "The challenge is consumed here, so a captured response "
                "cannot be replayed. A wrong, expired or already-used "
                "``challenge_id`` returns **401**; an authenticator that "
                "is already registered returns **422**."
            ),
        )
        async def webauthn_register_complete(
            payload: WebAuthnRegisterCompleteSchema,
            session: AsyncSession = session_dep,
            user: BaseUserModel = Depends(current_user_dep),
        ) -> WebAuthnCredentialSchema:
            """Verify the attestation and persist the credential.

            Args:
                payload (WebAuthnRegisterCompleteSchema): The ceremony
                    handle, the browser's response and an optional label.
                session (AsyncSession): The request-scoped DB session.
                user (BaseUserModel): The authenticated caller.

            Returns:
                WebAuthnCredentialSchema: The stored credential.
            """
            record = await service_webauthn.register_complete(
                session,
                user=user,
                challenge_id=payload.challenge_id,
                response=payload.credential,
                name=payload.name,
            )
            await session.commit()
            return _credential_schema(record)

        @router.post(
            "/webauthn/authenticate/begin",
            response_model=WebAuthnOptionsSchema,
            summary="Start a passwordless login",
            description=(
                "First half of the login ceremony. Needs **no** bearer "
                "token — this is how a session starts.\n\n"
                "Omit ``email`` for the usernameless flow: the options "
                "carry no credential list and the authenticator offers "
                "the accounts it stores. Pass ``email`` to narrow the "
                "ceremony to one account, which helps an authenticator "
                "that keeps no discoverable credential.\n\n"
                "An unknown address returns a normal ceremony with an "
                "empty credential list — answering differently would "
                "turn this endpoint into an account-enumeration oracle."
            ),
        )
        async def webauthn_authenticate_begin(
            payload: WebAuthnAuthenticateBeginSchema,
            session: AsyncSession = session_dep,
        ) -> WebAuthnOptionsSchema:
            """Return the request options plus the ceremony handle.

            Args:
                payload (WebAuthnAuthenticateBeginSchema): Optional email
                    narrowing the ceremony.
                session (AsyncSession): The request-scoped DB session.

            Returns:
                WebAuthnOptionsSchema: Options for
                ``navigator.credentials.get()`` and the ``challenge_id``
                to echo back.
            """
            options, challenge_id = await service_webauthn.authenticate_begin(
                session,
                email=payload.email,
            )
            return WebAuthnOptionsSchema(
                challenge_id=challenge_id,
                options=options,
            )

        @router.post(
            "/webauthn/authenticate/complete",
            response_model=LoginResponseSchema,
            summary="Complete a passwordless login (returns the JWT pair)",
            description=(
                "Second half of the login ceremony. Verifies the "
                "assertion and returns the ``access_token`` + "
                "``refresh_token`` pair.\n\n"
                "A bad or replayed challenge, an unknown credential, a "
                "failed signature, an authenticator whose signature "
                "counter did not advance (the spec's cloned-device "
                "signal) or an inactive account all return **401**.\n\n"
                "MFA does not gate this endpoint: a passkey with user "
                "verification already proves possession *and* a local "
                "factor, which is what the second step exists for."
            ),
        )
        async def webauthn_authenticate_complete(
            payload: WebAuthnAuthenticateCompleteSchema,
            session: AsyncSession = session_dep,
        ) -> LoginResponseSchema:
            """Verify the assertion and mint the token pair.

            Args:
                payload (WebAuthnAuthenticateCompleteSchema): The ceremony
                    handle and the browser's assertion.
                session (AsyncSession): The request-scoped DB session.

            Returns:
                LoginResponseSchema: The access + refresh token pair.
            """
            user = await service_webauthn.authenticate_complete(
                session,
                challenge_id=payload.challenge_id,
                response=payload.credential,
            )
            access, refresh = await service.issue_token_pair(session, user)
            await session.commit()
            return LoginResponseSchema(
                user_id=user.id,
                access_token=access,
                refresh_token=refresh,
            )

        @router.get(
            "/webauthn/credentials",
            response_model=list[WebAuthnCredentialSchema],
            summary="List the current user's registered authenticators",
            description=(
                "Every passkey / security key the **currently "
                "authenticated** user registered, oldest first. Returns "
                "``200`` with an empty list when there are none.\n\n"
                "``backed_up`` tells a device-bound credential (lost "
                "with the device) from a synced one, which is what "
                "decides whether the account still needs a recovery "
                "path."
            ),
        )
        async def webauthn_list_credentials(
            session: AsyncSession = session_dep,
            user: BaseUserModel = Depends(current_user_dep),
        ) -> list[WebAuthnCredentialSchema]:
            """Return the caller's registered credentials.

            Args:
                session (AsyncSession): The request-scoped DB session.
                user (BaseUserModel): The authenticated caller.

            Returns:
                list[WebAuthnCredentialSchema]: The credentials, possibly
                empty.
            """
            records = await service_webauthn.list_credentials(session, user=user)
            return [_credential_schema(record) for record in records]

        @router.post(
            "/webauthn/credentials/delete",
            status_code=status.HTTP_204_NO_CONTENT,
            summary="Remove one of the current user's authenticators",
            description=(
                "Deletes a credential belonging to the **currently "
                "authenticated** user. The lookup is scoped to the "
                "caller, so an ID registered by somebody else answers "
                "**404** exactly like one that does not exist.\n\n"
                "Removing the last passkey leaves the account on its "
                "other factors — the endpoint does not check that one "
                "remains, because whether a password is still an "
                "acceptable fallback is the application's decision."
            ),
        )
        async def webauthn_delete_credential(
            payload: WebAuthnDeleteSchema,
            session: AsyncSession = session_dep,
            user: BaseUserModel = Depends(current_user_dep),
        ) -> None:
            """Delete one of the caller's credentials.

            Args:
                payload (WebAuthnDeleteSchema): The credential to remove.
                session (AsyncSession): The request-scoped DB session.
                user (BaseUserModel): The authenticated caller.
            """
            await service_webauthn.delete_credential(
                session,
                user=user,
                credential_id=_decode_credential_id(payload.credential_id),
            )
            await session.commit()

    return router


def _decode_credential_id(value: str) -> bytes:
    """Decode a base64url credential ID coming from a client.

    Args:
        value (str): Base64url text, with or without padding.

    Returns:
        bytes: The raw credential ID.

    Raises:
        ValidationException: When the text is not valid base64url. The
            alternative — treating it as an unknown credential — would
            answer 404 for a malformed request.
    """
    from base64 import urlsafe_b64decode
    from binascii import Error as BinasciiError

    padded = value + "=" * (-len(value) % 4)
    try:
        return urlsafe_b64decode(padded)
    except (BinasciiError, ValueError) as exc:
        raise ValidationException(
            message="credential_id is not valid base64url",
        ) from exc


def _credential_schema(
    record: BaseWebAuthnCredentialModel,
) -> WebAuthnCredentialSchema:
    """Render a stored credential for the API.

    The raw credential ID is bytes; the API speaks base64url without
    padding, matching what the browser's WebAuthn JSON serialization
    produces, so a client can compare the two directly.

    Args:
        record (BaseWebAuthnCredentialModel): The stored credential.

    Returns:
        WebAuthnCredentialSchema: The response model.
    """
    from base64 import urlsafe_b64encode

    return WebAuthnCredentialSchema(
        credential_id=urlsafe_b64encode(record.credential_id).decode().rstrip("="),
        name=record.name,
        transports=record.transports,
        aaguid=record.aaguid,
        backed_up=record.backed_up,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


def _make_user_loader(
    service: UserAuthService,
) -> Callable[[str, AsyncSession], Coroutine[Any, Any, BaseUserModel | None]]:
    """Build the awaitable ``(user_id, session) -> BaseUserModel`` JWT user loader.

    Loads the user on the **request-scoped** session handed in by
    :func:`~tempest_fastapi_sdk.make_jwt_user_dependency`, so the instance stays
    attached to the same session the route body writes through. The earlier
    version opened a private session per call and returned the user after that
    session closed, which left every authenticated route of this router holding a
    **detached** instance: a mutation in the route body was flushed against a
    session that did not contain it (a silent no-op) and the following
    ``session.refresh(user)`` raised ``InvalidRequestError: Instance is not
    persistent within this Session``. ``POST /auth/password-change`` failed that
    way — answering 500 *and* leaving the old password in place.

    Args:
        service (UserAuthService): The service owning the user model to load.

    Returns:
        Callable: The two-argument loader the shared-session dependency calls.
    """
    from uuid import UUID

    async def _load(user_id: str, session: AsyncSession) -> BaseUserModel | None:
        obj: BaseUserModel | None = await session.get(service.user_model, UUID(user_id))
        return obj

    return _load


__all__: list[str] = [
    "make_auth_router",
]
