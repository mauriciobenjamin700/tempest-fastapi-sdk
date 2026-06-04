"""``SessionMiddleware`` — reads the session cookie, populates request state.

Sits early in the middleware stack so downstream code can read
``request.state.session`` (either a live :class:`Session` or
``None``) without re-resolving the cookie on every dependency.
Refreshes ``last_seen_at`` and slides ``expires_at`` according to
``SessionSettings`` whenever the cookie matches a live session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tempest_fastapi_sdk.sessions.service import SessionAuth
    from tempest_fastapi_sdk.settings.mixins import SessionSettings


class SessionMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that resolves the session cookie per request.

    Attach with::

        app.add_middleware(
            SessionMiddleware,
            session_auth=session_auth,
            settings=session_settings,
        )

    After the middleware runs, every handler in the chain can read
    ``request.state.session`` — a :class:`Session` instance when
    the cookie was valid, ``None`` otherwise. Handlers that require
    authentication should depend on
    :func:`make_session_dependency` instead of poking
    ``request.state`` directly so missing sessions raise a clean
    ``401`` envelope.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        session_auth: SessionAuth,
        settings: SessionSettings,
    ) -> None:
        """Initialize the middleware.

        Args:
            app (ASGIApp): Wrapped ASGI app — Starlette passes this
                automatically when used with ``add_middleware``.
            session_auth (SessionAuth): Configured service used to
                resolve cookies into sessions.
            settings (SessionSettings): Read for the cookie name.
        """
        super().__init__(app)
        self.session_auth: SessionAuth = session_auth
        self.settings: SessionSettings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Resolve the cookie before delegating to the route handler."""
        cookie = request.cookies.get(self.settings.SESSION_COOKIE_NAME)
        request.state.session = None
        request.state.session_id_plaintext = cookie
        if cookie:
            request.state.session = await self.session_auth.resolve(cookie)
        return await call_next(request)


__all__: list[str] = ["SessionMiddleware"]
