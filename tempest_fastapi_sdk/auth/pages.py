"""Server-side login, signup and activation pages over a cookie session.

:func:`~tempest_fastapi_sdk.make_auth_router` speaks JSON to a separate
frontend. A service that serves its own HTML needs the same flows as
pages: the browser gets a form, posts it without JavaScript, and the
session is a cookie. :func:`make_auth_pages_router` mounts them on top of
the same :class:`~tempest_fastapi_sdk.UserAuthService` and an opaque,
revocable :class:`~tempest_fastapi_sdk.SessionAuth` session.

Every rule the JSON routes enforce holds here too, because both call the
same code: the login budgets (the shared ``throttled_login``), the
generic refusal for a wrong password or an inactive account, the
second-factor anti-replay and budget of :meth:`UserAuthService.mfa_verify`,
the signup kill-switch and the resend answer that does not tell accounts
apart. What the pages add is what HTML needs: a CSRF field on every form
(:class:`~tempest_fastapi_sdk.auth.form_csrf.FormCSRF`, login included),
a ``next`` that can only point back into the site, and the MFA step held
in an ``HttpOnly`` cookie rather than the URL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, Final

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import SecretStr, ValidationError

from tempest_fastapi_sdk.api.dependencies._session import session_provider_for
from tempest_fastapi_sdk.auth._login_flow import throttled_login
from tempest_fastapi_sdk.auth.exceptions import (
    LoginThrottledException,
    MfaThrottledException,
)
from tempest_fastapi_sdk.auth.form_csrf import (
    FORM_CSRF_FIELD,
    FormCSRF,
    is_local_path,
)
from tempest_fastapi_sdk.auth.locale import (
    LOCALE_QUERY_PARAM,
    auth_page_message,
    resolve_locale,
)
from tempest_fastapi_sdk.auth.page_renderer import render_auth_page
from tempest_fastapi_sdk.auth.router import (
    LOGIN_THROTTLE_MAX_ATTEMPTS,
    LOGIN_THROTTLE_WINDOW_SECONDS,
    LOGIN_THROTTLED_MESSAGE,
    MFA_THROTTLE_MAX_ATTEMPTS,
    MFA_THROTTLE_WINDOW_SECONDS,
    _read_form_fields,
    _require_jinja2,
)
from tempest_fastapi_sdk.auth.schemas import ActivationWithheld, SignupSchema
from tempest_fastapi_sdk.exceptions import (
    AppException,
    ConflictException,
    InvalidTokenException,
    UnauthorizedException,
)
from tempest_fastapi_sdk.exceptions.i18n import (
    VALIDATION_KEY_PREFIX,
    default_message_catalog,
)
from tempest_fastapi_sdk.utils._signature import accepts_keyword
from tempest_fastapi_sdk.utils.client_ip import get_client_ip
from tempest_fastapi_sdk.utils.throttle import AttemptThrottle, InMemoryThrottleBackend
from tempest_fastapi_sdk.utils.token_types import (
    AMR_EMAIL_LINK,
    PASSWORD_AMR,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.auth.router import SignupHook
    from tempest_fastapi_sdk.auth.service import UserAuthService
    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.db.user_recovery_code_model import (
        BaseUserRecoveryCodeModel,
    )
    from tempest_fastapi_sdk.sessions.service import SessionAuth

AUTH_PAGES_MFA_COOKIE: Final[str] = "tempest_auth_mfa"
"""Cookie holding the pending second-factor token between the two steps.

``HttpOnly`` and scoped to the pages prefix: the token is a short-lived
JWT that proves the password step, so it never goes in a URL, where
history, logs and ``Referer`` would keep it.
"""

ACTIVATION_THROTTLE_MAX_ATTEMPTS: Final[int] = 5
"""Resend requests one client IP may make per window on the pages.

The JSON ``/activation/request`` is unthrottled; the page is a public
form that sends mail, and five a quarter-hour covers a user who mistyped
the address a few times.
"""

ACTIVATION_THROTTLE_WINDOW_SECONDS: Final[int] = 900
"""Length, in seconds, of the resend window."""

SIGNUP_THROTTLE_MAX_ATTEMPTS: Final[int] = 10
"""Signups one client IP may submit per window on the pages."""

SIGNUP_THROTTLE_WINDOW_SECONDS: Final[int] = 3600
"""Length, in seconds, of the signup window."""

UNPROCESSABLE_STATUS: Final[int] = 422
"""Status of a signup form with invalid fields.

A literal rather than ``status.HTTP_422_UNPROCESSABLE_ENTITY``: Starlette
deprecated that name with a ``UserWarning`` subclass, and its replacement
does not exist on the Starlette the ``fastapi`` floor accepts.
"""

_BASE_SIGNUP_FIELDS: Final[frozenset[str]] = frozenset({"email", "password", "name"})
"""Fields of :class:`SignupSchema` whose labels come from the page catalog."""

_PASSWORD_FIELDS: Final[frozenset[str]] = frozenset({"password"})
"""Signup fields rendered as password inputs and never echoed back."""


def _retry_after(exc: AppException) -> str:
    """Read the cooldown a throttle exception carries, for ``Retry-After``.

    Args:
        exc (AppException): A ``429`` raised by a throttle.

    Returns:
        str: Whole seconds, at least ``1``.
    """
    seconds = (exc.details or {}).get("retry_after_seconds", 1)
    return str(max(int(seconds), 1))


def _field_input_type(name: str, annotation: object) -> str:
    """Pick the ``<input type>`` for a signup field.

    Args:
        name (str): The field name.
        annotation (object): Its annotation.

    Returns:
        str: ``password``, ``email``, ``number``, ``checkbox`` or ``text``.
    """
    if name in _PASSWORD_FIELDS or annotation is SecretStr:
        return "password"
    if name == "email":
        return "email"
    if annotation is bool:
        return "checkbox"
    if annotation in (int, float):
        return "number"
    return "text"


def _signup_form_fields(
    schema: type[SignupSchema],
    locale: str,
) -> list[dict[str, Any]]:
    """Describe the signup form from ``schema`` for the template.

    The base fields (``email``, ``password``, ``name``) are labelled from
    the page catalog in ``locale``; their schema titles are English and
    would show on the Portuguese page too. A field the project adds is
    labelled by its ``title``, which the project writes in its own language.

    Args:
        schema (type[SignupSchema]): The signup schema.
        locale (str): The page language.

    Returns:
        list[dict[str, Any]]: One entry per field — ``name``, ``label``,
        ``type``, ``required`` and ``autocomplete``.
    """
    autocomplete = {"email": "email", "password": "new-password", "name": "name"}
    fields: list[dict[str, Any]] = []
    for name, info in schema.model_fields.items():
        fields.append(
            {
                "name": name,
                "label": (
                    auth_page_message(locale, f"field_{name}")
                    if name in _BASE_SIGNUP_FIELDS
                    else info.title or name.replace("_", " ").capitalize()
                ),
                "type": _field_input_type(name, info.annotation),
                "required": info.is_required(),
                "autocomplete": autocomplete.get(name, "off"),
            }
        )
    return fields


def make_auth_pages_router(
    service: UserAuthService,
    session_factory: Callable[..., AsyncIterator[AsyncSession]],
    *,
    session_auth: SessionAuth,
    template_dir: str | None = None,
    recovery_code_model: type[BaseUserRecoveryCodeModel] | None = None,
    allow_signup: bool | None = None,
    signup_schema: type[SignupSchema] | None = None,
    on_signup: SignupHook | None = None,
    login_throttle: AttemptThrottle | None = None,
    login_ip_throttle: AttemptThrottle | None = None,
    signup_throttle: AttemptThrottle | None = None,
    mfa_throttle: AttemptThrottle | None = None,
    activation_throttle: AttemptThrottle | None = None,
    trusted_ip_header: str | None = None,
) -> APIRouter:
    """Build the server-side login, signup and activation pages.

    Routes, under ``AUTH_PAGES_PREFIX`` (site root by default), all
    ``include_in_schema=False``:

    * ``GET/POST /login`` — e-mail and password. An account with MFA goes
      to ``/login/mfa`` without a session; otherwise the session opens and
      the browser goes to ``next`` or ``AUTH_PAGES_LOGIN_REDIRECT``.
    * ``GET/POST /login/mfa`` — TOTP or recovery code over the pending
      step kept in the :data:`AUTH_PAGES_MFA_COOKIE` cookie.
    * ``GET/POST /signup`` — the form generated from ``signup_schema``;
      only with signup enabled. Ends on "check your e-mail", or in a
      session with ``AUTH_AUTO_ACTIVATE``.
    * ``GET /activate/{token}`` — activates and opens the session
      (``amr=["email"]``); an account with MFA goes to ``/login/mfa``.
      Point ``AUTH_ACTIVATION_URL_TEMPLATE`` here.
    * ``GET/POST /activation/request`` — resend the link; the same answer
      with or without an account.
    * ``POST /logout`` — revokes the session in the store.

    Every POST requires the form's ``csrf_token``; a missing or
    mismatched one re-renders the form with ``403`` before anything else
    runs, so it spends no attempt.

    Args:
        service (UserAuthService): The auth service the JSON router uses.
        session_factory (Callable[..., AsyncIterator[AsyncSession]]): The
            request-scoped DB session provider.
        session_auth (SessionAuth): Opens, resolves and revokes the
            cookie session; its ``SESSION_*`` settings shape the cookie.
        template_dir (str | None): Project folder overriding the bundled
            templates, by file name (per-locale subfolder first).
        recovery_code_model (type[BaseUserRecoveryCodeModel] | None):
            Required when ``AUTH_MFA_ENABLED`` is on, for the recovery
            codes ``/login/mfa`` accepts.
        allow_signup (bool | None): Overrides ``AUTH_SIGNUP_ENABLED``.
        signup_schema (type[SignupSchema] | None): The signup form's
            schema; extra fields that are user columns are set before the
            insert, as in the JSON route.
        on_signup (SignupHook | None): Runs after the insert, before the
            commit, as in the JSON route.
        login_throttle (AttemptThrottle | None): Per-e-mail budget; the
            JSON route's default when ``None``.
        login_ip_throttle (AttemptThrottle | None): Per-IP budget; off
            when ``None``.
        signup_throttle (AttemptThrottle | None): Per-IP signup budget;
            :data:`SIGNUP_THROTTLE_MAX_ATTEMPTS` per window when ``None``.
        mfa_throttle (AttemptThrottle | None): Per-account second-factor
            budget; the JSON route's default when ``None``.
        activation_throttle (AttemptThrottle | None): Per-IP resend budget;
            :data:`ACTIVATION_THROTTLE_MAX_ATTEMPTS` per window when ``None``.
        trusted_ip_header (str | None): Header carrying the client IP for
            the IP budgets, as in the JSON router.

    Returns:
        APIRouter: The pages router.

    Raises:
        RuntimeError: When Jinja2 is not installed, or ``AUTH_MFA_ENABLED``
            is on without a ``recovery_code_model``, or ``signup_schema``
            does not subclass :class:`SignupSchema`.
    """
    _require_jinja2()
    settings = service.auth_settings
    if settings.AUTH_MFA_ENABLED and recovery_code_model is None:
        raise RuntimeError(
            "AUTH_MFA_ENABLED=True needs make_auth_pages_router("
            "recovery_code_model=...): /login/mfa accepts recovery codes."
        )
    signup_model: type[SignupSchema] = signup_schema or SignupSchema
    if not issubclass(signup_model, SignupSchema):
        raise RuntimeError(
            "signup_schema must subclass SignupSchema: the signup page reads "
            f"'email', 'password' and 'name' by name, and "
            f"{signup_model.__name__} does not inherit them."
        )
    prefix = settings.AUTH_PAGES_PREFIX
    signup_enabled = (
        settings.AUTH_SIGNUP_ENABLED if allow_signup is None else allow_signup
    )
    signup_columns: frozenset[str] = service.signup_column_names()
    forward_signup_fields = accepts_keyword(service.signup, "fields")
    forward_mfa_amr = accepts_keyword(service.issue_mfa_token, "amr")
    session_settings = session_auth.settings
    csrf = FormCSRF(
        secure=session_settings.SESSION_COOKIE_SECURE,
        samesite=session_settings.SESSION_COOKIE_SAMESITE,
    )
    resolved_login_throttle = login_throttle or AttemptThrottle(
        InMemoryThrottleBackend(),
        max_attempts=LOGIN_THROTTLE_MAX_ATTEMPTS,
        window_seconds=LOGIN_THROTTLE_WINDOW_SECONDS,
    )
    resolved_mfa_throttle = mfa_throttle or AttemptThrottle(
        InMemoryThrottleBackend(),
        max_attempts=MFA_THROTTLE_MAX_ATTEMPTS,
        window_seconds=MFA_THROTTLE_WINDOW_SECONDS,
    )
    resolved_signup_throttle = signup_throttle or AttemptThrottle(
        InMemoryThrottleBackend(),
        max_attempts=SIGNUP_THROTTLE_MAX_ATTEMPTS,
        window_seconds=SIGNUP_THROTTLE_WINDOW_SECONDS,
    )
    resolved_activation_throttle = activation_throttle or AttemptThrottle(
        InMemoryThrottleBackend(),
        max_attempts=ACTIVATION_THROTTLE_MAX_ATTEMPTS,
        window_seconds=ACTIVATION_THROTTLE_WINDOW_SECONDS,
    )
    catalog = default_message_catalog()
    login_path = f"{prefix}/login"
    mfa_path = f"{prefix}/login/mfa"
    logout_target = settings.AUTH_PAGES_LOGOUT_REDIRECT or login_path

    router = APIRouter(prefix=prefix, include_in_schema=False)
    session_dep = Depends(session_provider_for(session_factory))

    def _locale(request: Request, user: object | None = None) -> str:
        """Pick the page language: ``?lang=``, user, browser, default.

        Args:
            request (Request): The inbound request.
            user (object | None): The account, when known.

        Returns:
            str: A supported locale.
        """
        return resolve_locale(
            user=user,
            query_locale=request.query_params.get(LOCALE_QUERY_PARAM),
            accept_language=request.headers.get("accept-language"),
            default=settings.AUTH_DEFAULT_LOCALE,
        )

    def _page(
        request: Request,
        template: str,
        context: dict[str, Any],
        *,
        status_code: int = status.HTTP_200_OK,
        locale: str | None = None,
    ) -> HTMLResponse:
        """Render a page carrying the CSRF token in the form and cookie.

        Args:
            request (Request): The inbound request.
            template (str): Template file name.
            context (dict[str, Any]): Template variables.
            status_code (int): HTTP status.
            locale (str | None): Page language; resolved when ``None``.

        Returns:
            HTMLResponse: The page, with the token cookie set.
        """
        token = csrf.issue(request)
        html = render_auth_page(
            template,
            {
                **context,
                "csrf_field": FORM_CSRF_FIELD,
                "csrf_token": token,
                "prefix": prefix,
                "lang_query": _lang_query(request),
                "signup_enabled": signup_enabled,
            },
            template_dir=template_dir,
            locale=locale or _locale(request),
        )
        response = HTMLResponse(content=html, status_code=status_code)
        csrf.attach(response, token)
        return response

    def _lang_query(request: Request) -> str:
        """Carry an explicit ``?lang=`` into the page's form actions and links.

        Without it, a page opened with ``?lang=pt-BR`` posts to a bare path,
        and the answer falls back to ``Accept-Language`` — another language
        than the form the user just filled in.

        Args:
            request (Request): The inbound request.

        Returns:
            str: ``"?lang=<locale>"`` for a supported ``lang``, else ``""``.
        """
        requested = request.query_params.get(LOCALE_QUERY_PARAM)
        if not requested:
            return ""
        return f"?{LOCALE_QUERY_PARAM}={_locale(request)}"

    def _next(value: str | None) -> str:
        """Return ``value`` when it is a path on this site, else ``""``.

        Args:
            value (str | None): The submitted ``next``.

        Returns:
            str: A safe ``next`` or the empty string.
        """
        return value if value and is_local_path(value) else ""

    def _client_ip(request: Request) -> str:
        """Resolve the client IP the IP budgets count against.

        Args:
            request (Request): The inbound request.

        Returns:
            str: The address from ``trusted_ip_header``, or the peer.
        """
        return get_client_ip(request, trusted_header=trusted_ip_header)

    async def _open_session(
        request: Request,
        response: Response,
        user: BaseUserModel,
        amr: tuple[str, ...],
    ) -> None:
        """Open a cookie session for ``user``, recording ``amr``.

        The cookie the request already carried (if any) is revoked first
        when ``SESSION_ROTATE_ON_LOGIN`` is on, which closes the
        session-fixation window.

        Args:
            request (Request): The inbound request.
            response (Response): The response that sets the cookie.
            user (BaseUserModel): The signed-in account.
            amr (tuple[str, ...]): Methods the login proved.
        """
        session, plaintext = await session_auth.login(
            user_id=user.id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            previous_session_id=request.cookies.get(
                session_settings.SESSION_COOKIE_NAME,
            ),
        )
        session.amr = list(amr)
        await session_auth.store.set(session)
        response.set_cookie(value=plaintext, **session_settings.session_cookie_kwargs())

    def _redirect(target: str) -> RedirectResponse:
        """Build the ``303`` every completed step answers with.

        Args:
            target (str): A path on this site.

        Returns:
            RedirectResponse: The redirect.
        """
        return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)

    def _start_mfa(
        response: Response,
        user: BaseUserModel,
        amr: tuple[str, ...],
    ) -> None:
        """Hold the second-factor step in its cookie.

        Args:
            response (Response): The redirect to ``/login/mfa``.
            user (BaseUserModel): The account that passed step one.
            amr (tuple[str, ...]): Methods step one proved.
        """
        token = (
            service.issue_mfa_token(user, amr=amr)
            if forward_mfa_amr
            else service.issue_mfa_token(user)
        )
        response.set_cookie(
            AUTH_PAGES_MFA_COOKIE,
            token,
            max_age=settings.AUTH_MFA_TOKEN_TTL_SECONDS,
            httponly=True,
            secure=session_settings.SESSION_COOKIE_SECURE,
            samesite=session_settings.SESSION_COOKIE_SAMESITE,
            path=prefix or "/",
        )

    def _step_still_valid(mfa_token: str) -> bool:
        """Whether the held second-factor step can still be completed.

        Tells a wrong code (the form again) from a step that expired or
        was tampered with (back to the login), without reading the
        refusal's message.

        Args:
            mfa_token (str): The token in :data:`AUTH_PAGES_MFA_COOKIE`.

        Returns:
            bool: ``True`` when it decodes as a pending MFA step.
        """
        try:
            payload = service.jwt.decode(mfa_token)
        except Exception:
            return False
        return payload.get("purpose") == "mfa_pending"

    def _mfa_redirect(next_value: str) -> RedirectResponse:
        """Redirect to the second-factor page, carrying ``next``.

        Args:
            next_value (str): A safe ``next`` or ``""``.

        Returns:
            RedirectResponse: The redirect.
        """
        query = f"?next={next_value}" if next_value else ""
        return _redirect(f"{mfa_path}{query}")

    async def _finish_login(
        request: Request,
        user: BaseUserModel,
        amr: tuple[str, ...],
        next_value: str,
    ) -> RedirectResponse:
        """Open the session, or start the second factor, and redirect.

        Args:
            request (Request): The inbound request.
            user (BaseUserModel): The account that proved ``amr``.
            amr (tuple[str, ...]): Methods proved so far.
            next_value (str): A safe ``next`` or ``""``.

        Returns:
            RedirectResponse: To ``/login/mfa``, ``next`` or the default.
        """
        if service.is_mfa_enrolled(user):
            response = _mfa_redirect(next_value)
            _start_mfa(response, user, amr)
            return response
        response = _redirect(next_value or settings.AUTH_PAGES_LOGIN_REDIRECT)
        await _open_session(request, response, user, amr)
        return response

    def _login_page(
        request: Request,
        *,
        email: str = "",
        next_value: str = "",
        error_key: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        """Render the login form.

        Args:
            request (Request): The inbound request.
            email (str): E-mail to keep in the field.
            next_value (str): A safe ``next`` or ``""``.
            error_key (str | None): Message key to show.
            status_code (int): HTTP status.

        Returns:
            HTMLResponse: The login page.
        """
        locale = _locale(request)
        return _page(
            request,
            settings.AUTH_LOGIN_PAGE_TEMPLATE,
            {
                "email": email,
                "next": next_value,
                "error": auth_page_message(locale, error_key) if error_key else None,
            },
            status_code=status_code,
            locale=locale,
        )

    @router.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str | None = None) -> HTMLResponse:
        """Render the login form.

        Args:
            request (Request): The inbound request.
            next (str | None): Where to go after signing in.

        Returns:
            HTMLResponse: The login page.
        """
        return _login_page(request, next_value=_next(next))

    @router.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        session: AsyncSession = session_dep,
    ) -> Response:
        """Check the credentials and open the session (or start MFA).

        Args:
            request (Request): The form submission.
            session (AsyncSession): The DB session.

        Returns:
            Response: ``303`` onward, or the login page with ``403`` (form
            expired), ``401`` (wrong credentials, generic), ``429``
            (budget spent, with ``Retry-After``) or the service's status.
        """
        form = await _read_form_fields(request)
        email = form.get("email", "").strip()
        next_value = _next(form.get("next"))
        if not csrf.verify(request, form.get(FORM_CSRF_FIELD, "")):
            return _login_page(
                request,
                email=email,
                next_value=next_value,
                error_key="form_expired",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        try:
            user = await throttled_login(
                service,
                session,
                email=email,
                password=form.get("password", ""),
                client_ip=_client_ip(request),
                login_throttle=resolved_login_throttle,
                login_ip_throttle=login_ip_throttle,
                throttled_message=LOGIN_THROTTLED_MESSAGE,
            )
        except LoginThrottledException as exc:
            page = _login_page(
                request,
                email=email,
                next_value=next_value,
                error_key="too_many_attempts",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            page.headers["Retry-After"] = _retry_after(exc)
            return page
        except UnauthorizedException:
            await session.rollback()
            return _login_page(
                request,
                email=email,
                next_value=next_value,
                error_key="invalid_credentials",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        response = await _finish_login(request, user, PASSWORD_AMR, next_value)
        await session.commit()
        return response

    @router.get("/login/mfa", response_class=HTMLResponse)
    async def mfa_form(request: Request, next: str | None = None) -> Response:
        """Render the second-factor form, or go back to the login.

        Args:
            request (Request): The inbound request.
            next (str | None): Where to go after signing in.

        Returns:
            Response: The page, or ``303`` to ``/login`` with no step held.
        """
        if not request.cookies.get(AUTH_PAGES_MFA_COOKIE):
            return _redirect(login_path)
        return _page(
            request,
            settings.AUTH_LOGIN_MFA_PAGE_TEMPLATE,
            {"next": _next(next), "error": None},
        )

    @router.post("/login/mfa", response_class=HTMLResponse)
    async def mfa_submit(
        request: Request,
        session: AsyncSession = session_dep,
    ) -> Response:
        """Check the code and open the session.

        Args:
            request (Request): The form submission.
            session (AsyncSession): The DB session.

        Returns:
            Response: ``303`` onward; the form again with ``403`` (form
            expired), ``401`` (wrong code) or ``429`` (budget spent); or
            ``303`` to ``/login`` when the held step expired.
        """
        form = await _read_form_fields(request)
        next_value = _next(form.get("next"))
        locale = _locale(request)
        mfa_token = request.cookies.get(AUTH_PAGES_MFA_COOKIE)

        def _again(error_key: str, status_code: int) -> HTMLResponse:
            """Re-render the second-factor form with an error.

            Args:
                error_key (str): Message key.
                status_code (int): HTTP status.

            Returns:
                HTMLResponse: The page.
            """
            return _page(
                request,
                settings.AUTH_LOGIN_MFA_PAGE_TEMPLATE,
                {"next": next_value, "error": auth_page_message(locale, error_key)},
                status_code=status_code,
                locale=locale,
            )

        if not csrf.verify(request, form.get(FORM_CSRF_FIELD, "")):
            return _again("form_expired", status.HTTP_403_FORBIDDEN)
        if not mfa_token or recovery_code_model is None:
            return _redirect(login_path)
        try:
            user = await service.mfa_verify(
                session,
                mfa_token=mfa_token,
                code=form.get("code", "").strip(),
                recovery_code_model=recovery_code_model,
                throttle=resolved_mfa_throttle,
            )
        except MfaThrottledException as exc:
            page = _again("too_many_attempts", status.HTTP_429_TOO_MANY_REQUESTS)
            page.headers["Retry-After"] = _retry_after(exc)
            return page
        except UnauthorizedException:
            await session.rollback()
            if _step_still_valid(mfa_token):
                return _again("invalid_mfa_code", status.HTTP_401_UNAUTHORIZED)
            expired = _page(
                request,
                settings.AUTH_LOGIN_PAGE_TEMPLATE,
                {
                    "email": "",
                    "next": next_value,
                    "error": auth_page_message(locale, "mfa_expired"),
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
                locale=locale,
            )
            expired.delete_cookie(AUTH_PAGES_MFA_COOKIE, path=prefix or "/")
            return expired
        amr = service.second_factor_amr(mfa_token)
        response = _redirect(next_value or settings.AUTH_PAGES_LOGIN_REDIRECT)
        await _open_session(request, response, user, amr)
        response.delete_cookie(AUTH_PAGES_MFA_COOKIE, path=prefix or "/")
        await session.commit()
        return response

    def _signup_page(
        request: Request,
        *,
        values: dict[str, str] | None = None,
        errors: dict[str, str] | None = None,
        error: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        """Render the signup form, keeping what was typed (no passwords).

        Args:
            request (Request): The inbound request.
            values (dict[str, str] | None): Submitted values to keep.
            errors (dict[str, str] | None): Per-field errors.
            error (str | None): Form-level error.
            status_code (int): HTTP status.

        Returns:
            HTMLResponse: The signup page.
        """
        kept = {
            key: value
            for key, value in (values or {}).items()
            if key not in _PASSWORD_FIELDS
        }
        locale = _locale(request)
        return _page(
            request,
            settings.AUTH_SIGNUP_PAGE_TEMPLATE,
            {
                "fields": _signup_form_fields(signup_model, locale),
                "values": kept,
                "errors": errors or {},
                "error": error,
                "min_length": settings.AUTH_PASSWORD_MIN_LENGTH,
            },
            status_code=status_code,
            locale=locale,
        )

    if signup_enabled:

        @router.get("/signup", response_class=HTMLResponse)
        async def signup_form(request: Request) -> HTMLResponse:
            """Render the signup form.

            Args:
                request (Request): The inbound request.

            Returns:
                HTMLResponse: The signup page.
            """
            return _signup_page(request)

        @router.post("/signup", response_class=HTMLResponse)
        async def signup_submit(
            request: Request,
            session: AsyncSession = session_dep,
        ) -> Response:
            """Create the account from the form.

            Args:
                request (Request): The form submission.
                session (AsyncSession): The DB session.

            Returns:
                Response: "Check your e-mail", or ``303`` with a session
                under ``AUTH_AUTO_ACTIVATE``; the form again with ``403``,
                ``422`` (invalid fields), ``429`` or the service's status
                (``409`` duplicate e-mail, ``503`` mail down).
            """
            form = await _read_form_fields(request)
            locale = _locale(request)
            if not csrf.verify(request, form.get(FORM_CSRF_FIELD, "")):
                return _signup_page(
                    request,
                    values=form,
                    error=auth_page_message(locale, "form_expired"),
                    status_code=status.HTTP_403_FORBIDDEN,
                )
            attempt = await resolved_signup_throttle.hit(
                f"signup-page:{_client_ip(request)}",
            )
            if attempt.attempts > resolved_signup_throttle.max_attempts:
                page = _signup_page(
                    request,
                    values=form,
                    error=auth_page_message(locale, "too_many_attempts"),
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                )
                page.headers["Retry-After"] = str(max(attempt.retry_after_seconds, 1))
                return page
            submitted = {
                name: form[name]
                for name in signup_model.model_fields
                if name in form and form[name] != ""
            }
            try:
                payload = signup_model.model_validate(submitted)
            except ValidationError as exc:
                errors = {
                    str(item["loc"][0]): catalog.resolve(
                        f"{VALIDATION_KEY_PREFIX}{item['type']}",
                        locale,
                        item.get("ctx"),
                    )
                    or item["msg"]
                    for item in exc.errors()
                    if item["loc"]
                }
                return _signup_page(
                    request,
                    values=form,
                    errors=errors,
                    status_code=UNPROCESSABLE_STATUS,
                )
            fields = {
                key: value
                for key, value in payload.model_dump(
                    exclude=set(SignupSchema.model_fields)
                ).items()
                if key in signup_columns
            }
            try:
                if fields and forward_signup_fields:
                    user, activation = await service.signup(
                        session,
                        email=payload.email,
                        password=payload.password,
                        name=payload.name,
                        fields=fields,
                    )
                else:
                    user, activation = await service.signup(
                        session,
                        email=payload.email,
                        password=payload.password,
                        name=payload.name,
                    )
                if on_signup is not None:
                    await on_signup(session, user, payload)
            except ConflictException as exc:
                await session.rollback()
                return _signup_page(
                    request,
                    values=form,
                    errors={"email": auth_page_message(locale, "email_taken")},
                    status_code=exc.status_code,
                )
            except AppException as exc:
                await session.rollback()
                key = exc.message_key or exc.code
                message = (
                    catalog.resolve(key, locale, exc.message_params) if key else None
                ) or exc.message
                if key and key.startswith("PASSWORD_"):
                    return _signup_page(
                        request,
                        values=form,
                        errors={"password": message},
                        status_code=exc.status_code,
                    )
                return _signup_page(
                    request,
                    values=form,
                    error=message,
                    status_code=exc.status_code,
                )
            if activation is None:
                response = _redirect(settings.AUTH_PAGES_LOGIN_REDIRECT)
                await _open_session(request, response, user, PASSWORD_AMR)
                await session.commit()
                return response
            await session.commit()
            activation_url = (
                activation.url
                if not isinstance(activation, ActivationWithheld)
                and (settings.AUTH_RETURN_TOKEN_IN_RESPONSE or service.email is None)
                else None
            )
            return _page(
                request,
                settings.AUTH_SIGNUP_DONE_TEMPLATE,
                {"email": payload.email, "activation_url": activation_url},
                locale=locale,
            )

    @router.get("/activate/{token}", response_class=HTMLResponse)
    async def activate(
        request: Request,
        token: str,
        session: AsyncSession = session_dep,
    ) -> Response:
        """Activate the account and sign it in.

        Args:
            request (Request): The inbound request.
            token (str): The emailed activation token.
            session (AsyncSession): The DB session.

        Returns:
            Response: ``303`` with a session (or to ``/login/mfa``), or the
            activation error page — ``400`` for an unknown, used or
            expired link, as the ``AUTH_BACKEND_LINKS`` page answers.
        """
        locale = _locale(request)
        try:
            user = await service.activate(session, token=token)
        except InvalidTokenException as exc:
            await session.rollback()
            return _page(
                request,
                settings.AUTH_ACTIVATION_ERROR_TEMPLATE,
                {"reason": exc.message, "login_url": login_path},
                status_code=status.HTTP_400_BAD_REQUEST,
                locale=locale,
            )
        except service.exceptions.email_link_forbidden as exc:
            await session.rollback()
            return _page(
                request,
                settings.AUTH_ACTIVATION_ERROR_TEMPLATE,
                {"reason": exc.message, "login_url": login_path},
                status_code=exc.status_code,
                locale=locale,
            )
        response = await _finish_login(request, user, (AMR_EMAIL_LINK,), "")
        await session.commit()
        return response

    def _resend_page(
        request: Request,
        *,
        email: str = "",
        error_key: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        """Render the resend form.

        Args:
            request (Request): The inbound request.
            email (str): E-mail to keep in the field.
            error_key (str | None): Message key to show.
            status_code (int): HTTP status.

        Returns:
            HTMLResponse: The page.
        """
        locale = _locale(request)
        return _page(
            request,
            settings.AUTH_ACTIVATION_REQUEST_PAGE_TEMPLATE,
            {
                "email": email,
                "error": auth_page_message(locale, error_key) if error_key else None,
            },
            status_code=status_code,
            locale=locale,
        )

    @router.get("/activation/request", response_class=HTMLResponse)
    async def resend_form(request: Request) -> HTMLResponse:
        """Render the resend-activation form.

        Args:
            request (Request): The inbound request.

        Returns:
            HTMLResponse: The page.
        """
        return _resend_page(request)

    @router.post("/activation/request", response_class=HTMLResponse)
    async def resend_submit(
        request: Request,
        session: AsyncSession = session_dep,
    ) -> Response:
        """Resend the link, answering the same with or without an account.

        Args:
            request (Request): The form submission.
            session (AsyncSession): The DB session.

        Returns:
            Response: The confirmation page; the form with ``403`` or
            ``429``.
        """
        form = await _read_form_fields(request)
        email = form.get("email", "").strip()
        if not csrf.verify(request, form.get(FORM_CSRF_FIELD, "")):
            return _resend_page(
                request,
                email=email,
                error_key="form_expired",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        attempt = await resolved_activation_throttle.hit(
            f"activation-page:{_client_ip(request)}",
        )
        if attempt.attempts > resolved_activation_throttle.max_attempts:
            page = _resend_page(
                request,
                email=email,
                error_key="too_many_attempts",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            page.headers["Retry-After"] = str(max(attempt.retry_after_seconds, 1))
            return page
        if email:
            await service.request_activation(session, email=email)
            await session.commit()
        return _page(
            request,
            settings.AUTH_ACTIVATION_REQUEST_DONE_TEMPLATE,
            {"email": email},
        )

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        """Revoke the session in the store and clear its cookie.

        Args:
            request (Request): The form submission.

        Returns:
            Response: ``303`` to ``AUTH_PAGES_LOGOUT_REDIRECT`` (or the
            login page), or ``403`` without the form's token.
        """
        form = await _read_form_fields(request)
        if not csrf.verify(request, form.get(FORM_CSRF_FIELD, "")):
            return Response(status_code=status.HTTP_403_FORBIDDEN)
        cookie = request.cookies.get(session_settings.SESSION_COOKIE_NAME)
        if cookie:
            await session_auth.revoke(cookie)
        response = _redirect(logout_target)
        response.delete_cookie(**session_settings.session_cookie_delete_kwargs())
        return response

    return router


__all__: list[str] = [
    "ACTIVATION_THROTTLE_MAX_ATTEMPTS",
    "ACTIVATION_THROTTLE_WINDOW_SECONDS",
    "AUTH_PAGES_MFA_COOKIE",
    "SIGNUP_THROTTLE_MAX_ATTEMPTS",
    "SIGNUP_THROTTLE_WINDOW_SECONDS",
    "UNPROCESSABLE_STATUS",
    "make_auth_pages_router",
]
