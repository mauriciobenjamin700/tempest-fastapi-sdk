"""FastAPI dependencies for the server-side session module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

from tempest_fastapi_sdk.exceptions import UnauthorizedException

if TYPE_CHECKING:
    from tempest_fastapi_sdk.sessions.schemas import Session
    from tempest_fastapi_sdk.sessions.service import SessionAuth

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


def make_session_dependency(
    *,
    required: bool = True,
    session_auth: SessionAuth | None = None,
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

    Args:
        required (bool): When ``True`` (default), a missing session
            raises — :class:`UnauthorizedException` (``401``) unless
            ``on_missing`` says otherwise. When ``False``, the
            dependency returns ``None`` and the handler decides what to
            do (typical for endpoints that work both anonymously and
            authenticated).
        session_auth (SessionAuth | None): Resolve the cookie directly
            with this service instead of relying on the middleware.
        on_missing (MissingSessionHandler | None): Builds the exception
            to raise when no session resolves — :func:`redirect_to` for
            HTML routes. ``None`` raises :class:`UnauthorizedException`.

    Returns:
        Callable[[Request], Awaitable[Session | None]]: An async FastAPI
        dependency.

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
            cookie = request.cookies.get(session_auth.settings.SESSION_COOKIE_NAME)
            request.state.session_id_plaintext = cookie
            if cookie:
                session = await session_auth.resolve(cookie)
            request.state.session = session
        if session is None and required:
            if on_missing is not None:
                raise on_missing(request)
            raise UnauthorizedException(message="session required")
        return session

    return _resolver


__all__: list[str] = [
    "MissingSessionHandler",
    "make_session_dependency",
    "redirect_to",
]
