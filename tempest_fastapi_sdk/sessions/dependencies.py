"""FastAPI dependencies for the server-side session module."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal, overload
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status

from tempest_fastapi_sdk.api.dependencies._session import session_provider_for
from tempest_fastapi_sdk.exceptions import ForbiddenException, UnauthorizedException
from tempest_fastapi_sdk.utils.token_types import AMR_OTP

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.sessions.schemas import Session
    from tempest_fastapi_sdk.sessions.service import SessionAuth

SessionAuthFactory = Callable[[Request], "SessionAuth"]
"""Returns the :class:`SessionAuth` that resolves a request's cookie.

Called once per request that needs a lookup, never at import — for a
service that builds its ``SessionAuth`` from settings at startup
(``lambda request: request.app.state.session_auth``) or behind a cached
factory the tests reset.
"""

MissingSessionHandler = Callable[[Request], Exception]
"""Builds the exception a session dependency raises when no session resolves.

Receives the request, so the handler can carry the requested path
along (``/login?next=...``). :func:`redirect_to` is the bundled one.
"""


def redirect_to(
    url: str,
    *,
    status_code: int = status.HTTP_303_SEE_OTHER,
) -> MissingSessionHandler:
    """Build an ``on_missing`` handler that redirects to ``url``.

    For HTML routes, where a missing session should send the browser to
    the login page instead of answering a JSON ``401``. The dependency
    raises an ``HTTPException`` carrying the status and a ``Location``
    header; FastAPI's handler and the SDK's
    :func:`register_exception_handlers` both keep the header, and the
    browser follows it.

    Args:
        url (str): Where to send the browser, e.g. ``"/login"``.
        status_code (int): Redirect status. ``303 See Other`` (default)
            makes the browser follow with a ``GET`` whatever the original
            method was.

    Returns:
        MissingSessionHandler: A handler for
        ``make_session_dependency(on_missing=...)``.

    Raises:
        ValueError: When ``status_code`` is not a ``3xx`` status.
    """
    if not 300 <= status_code < 400:
        raise ValueError(f"redirect_to needs a 3xx status, got {status_code}")

    def _handler(request: Request) -> Exception:
        """Return the redirect exception for ``request``.

        Args:
            request (Request): The request that carried no live session.

        Returns:
            Exception: An ``HTTPException`` with ``Location: url``.
        """
        return HTTPException(status_code=status_code, headers={"Location": url})

    return _handler


@overload
def make_session_dependency(
    *,
    required: Literal[True] = ...,
    session_auth: SessionAuth | SessionAuthFactory | None = ...,
    on_missing: MissingSessionHandler | None = ...,
) -> Callable[[Request], Awaitable[Session]]: ...


@overload
def make_session_dependency(
    *,
    required: Literal[False],
    session_auth: SessionAuth | SessionAuthFactory | None = ...,
    on_missing: None = ...,
) -> Callable[[Request], Awaitable[Session | None]]: ...


@overload
def make_session_dependency(
    *,
    required: bool,
    session_auth: SessionAuth | SessionAuthFactory | None = ...,
    on_missing: MissingSessionHandler | None = ...,
) -> Callable[[Request], Awaitable[Session | None]]: ...


def make_session_dependency(
    *,
    required: bool = True,
    session_auth: SessionAuth | SessionAuthFactory | None = None,
    on_missing: MissingSessionHandler | None = None,
) -> Callable[[Request], Awaitable[Session | None]]:
    """Build a FastAPI dependency that returns the resolved session.

    Two ways to find the session:

    * **Without** ``session_auth`` the dependency reads
      ``request.state.session``, which :class:`SessionMiddleware`
      populates. Mount the middleware BEFORE you use the dependency, or
      it always sees no session.
    * **With** ``session_auth`` the dependency reads the cookie and
      resolves it itself (sliding the TTL like the middleware does), so
      no middleware is needed. Only the routes that declare the
      dependency pay for the lookup, and a streaming route elsewhere in
      the app is not wrapped by a ``BaseHTTPMiddleware``. When the
      middleware did run for the request, its result is reused instead
      of resolving twice.

    ``session_auth`` may also be a :data:`SessionAuthFactory` — a callable
    taking the request — for a service whose ``SessionAuth`` only exists
    once settings are read. The dependency is then declarable at module
    level (``AdminDep = Annotated[Session, Depends(...)]``) before any
    ``SessionAuth`` is built, and the factory runs per request, only when
    the middleware left no session. Two apps in one process — the shape
    of tests that clear a settings cache — each resolve against their own.

    With ``required=True`` (the default) the dependency is typed as
    returning ``Session``, not ``Session | None``: it raises instead of
    returning ``None``, so a consumer needs no ``if`` to narrow the type.

    Args:
        required (bool): When ``True`` (default), a missing session
            raises — :class:`UnauthorizedException` (``401``) unless
            ``on_missing`` says otherwise. When ``False``, the
            dependency returns ``None`` and the handler decides what to
            do (typical for endpoints that work both anonymously and
            authenticated).
        session_auth (SessionAuth | SessionAuthFactory | None): Resolve
            the cookie directly with this service, or with the one the
            factory returns for the request, instead of relying on the
            middleware.
        on_missing (MissingSessionHandler | None): Builds the exception
            to raise when no session resolves — :func:`redirect_to` for
            HTML routes. ``None`` raises :class:`UnauthorizedException`.

    Returns:
        Callable[[Request], Awaitable[Session | None]]: An async FastAPI
        dependency, typed ``Awaitable[Session]`` when ``required=True``.

    Raises:
        ValueError: When ``on_missing`` is given with ``required=False``
            — an optional dependency never raises, so the handler would
            never run.
    """
    if on_missing is not None and not required:
        raise ValueError("on_missing only applies when required=True")

    async def _resolver(request: Request) -> Session | None:
        """Return the request's session, or raise when it is required.

        Args:
            request (Request): The inbound request.

        Returns:
            Session | None: The live session, or ``None`` when optional
            and absent.

        Raises:
            Exception: ``on_missing(request)``, or
                :class:`UnauthorizedException`, when required and absent.
        """
        session: Session | None = getattr(request.state, "session", None)
        if session is None and session_auth is not None:
            auth = session_auth(request) if callable(session_auth) else session_auth
            cookie = request.cookies.get(auth.settings.SESSION_COOKIE_NAME)
            request.state.session_id_plaintext = cookie
            if cookie:
                session = await auth.resolve(cookie)
            request.state.session = session
        if session is None and required:
            if on_missing is not None:
                raise on_missing(request)
            raise UnauthorizedException(message="session required")
        return session

    return _resolver


def redirect_to_login(
    login_path: str,
    *,
    status_code: int = status.HTTP_303_SEE_OTHER,
) -> MissingSessionHandler:
    """Build an ``on_missing`` handler that sends the browser to the login.

    Unlike :func:`redirect_to`, it carries the requested path and query
    along as ``?next=``, so the login page can bring the user back. The
    login pages of :func:`~tempest_fastapi_sdk.make_auth_pages_router`
    only follow a ``next`` that is a path on the same site.

    Args:
        login_path (str): The login page, e.g. ``"/login"``.
        status_code (int): Redirect status, ``303`` by default.

    Returns:
        MissingSessionHandler: A handler for ``on_missing=``.

    Raises:
        ValueError: When ``status_code`` is not a ``3xx`` status.
    """
    if not 300 <= status_code < 400:
        raise ValueError(f"redirect_to_login needs a 3xx status, got {status_code}")

    def _handler(request: Request) -> Exception:
        """Return the redirect carrying the requested path as ``next``.

        Args:
            request (Request): The request that carried no live session.

        Returns:
            Exception: An ``HTTPException`` with ``Location``.
        """
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        location = f"{login_path}?next={quote(target, safe='/')}"
        return HTTPException(status_code=status_code, headers={"Location": location})

    return _handler


def make_session_user_dependency[UserT: BaseUserModel](
    user_model: type[UserT],
    session_factory: Callable[..., AsyncIterator[AsyncSession]],
    *,
    session_auth: SessionAuth | SessionAuthFactory,
    on_missing: MissingSessionHandler | None = None,
    require_mfa: bool = False,
) -> Callable[..., Awaitable[UserT]]:
    """Build a dependency returning the **user** of the request's session.

    :func:`make_session_dependency` returns the :class:`Session`; a page
    usually wants the account. This one resolves the cookie, then reloads
    the row on the request's DB session — the same provider the routes
    use, so the instance stays attached — on **every** request: an account
    deactivated or deleted after the login loses access on its next click,
    not when the session expires.

    Args:
        user_model (type[UserT]): The concrete user model.
        session_factory (Callable[..., AsyncIterator[AsyncSession]]): The
            request-scoped DB session provider.
        session_auth (SessionAuth | SessionAuthFactory): Resolves the
            cookie, as in :func:`make_session_dependency`.
        on_missing (MissingSessionHandler | None): Exception for no
            session, or a session whose account is gone or inactive —
            :func:`redirect_to_login` for pages. ``None`` raises
            :class:`UnauthorizedException` (``401``).
        require_mfa (bool): Also require the session to have passed the
            second factor (``otp`` in :attr:`Session.amr`); a session
            without it is refused with ``403``, the same answer
            ``make_mfa_required_dependency`` gives a token.

    Returns:
        Callable[..., Awaitable[UserT]]: An async FastAPI dependency.
    """
    resolve_session = make_session_dependency(
        required=False,
        session_auth=session_auth,
    )
    session_provider = session_provider_for(session_factory)

    def _missing(request: Request) -> Exception:
        """Build the refusal for a request without a usable session.

        Args:
            request (Request): The inbound request.

        Returns:
            Exception: ``on_missing(request)`` or a ``401``.
        """
        if on_missing is not None:
            return on_missing(request)
        return UnauthorizedException(message="session required")

    async def _dependency(
        request: Request,
        db: Any = Depends(session_provider),
    ) -> UserT:
        """Return the active account behind the request's session.

        Args:
            request (Request): The inbound request.
            db (Any): The request-scoped ``AsyncSession``.

        Returns:
            UserT: The account.

        Raises:
            Exception: :func:`_missing` without a usable session.
            ForbiddenException: With ``require_mfa`` and no ``otp``.
        """
        session = await resolve_session(request)
        if session is None:
            raise _missing(request)
        user: UserT | None = await db.get(user_model, session.user_id)
        if user is None or not user.is_active:
            raise _missing(request)
        if require_mfa and AMR_OTP not in session.amr:
            raise ForbiddenException(message="this action requires MFA")
        return user

    return _dependency


__all__: list[str] = [
    "MissingSessionHandler",
    "SessionAuthFactory",
    "make_session_dependency",
    "make_session_user_dependency",
    "redirect_to",
    "redirect_to_login",
]
