"""``CSRFMiddleware`` — double-submit cookie pattern for state changes.

CSRF (cross-site request forgery) lets a third-party site trigger
authenticated POST/PUT/PATCH/DELETE requests through a victim's
browser, because cookies (the auth token) are sent automatically.
The classic defense is the **double-submit cookie**:

1. On any GET, the server issues a cookie ``csrf_token`` with a
   random opaque value.
2. The frontend reads that cookie and echoes the value as a header
   (``X-CSRF-Token``) on every mutating request.
3. The middleware rejects mutating requests where the header is
   missing or does not match the cookie — third-party sites can't
   read the cookie (same-origin policy), so they can't forge the
   header.

This works **without server-side storage**: the cookie + header
are the entire ceremony.

JWT bearer auth (``Authorization: Bearer …``) is **not** subject to
CSRF because browsers don't auto-attach it. The middleware can
therefore be opted out per route, or scoped to cookie-auth admin
endpoints only.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

CSRF_COOKIE_NAME: str = "csrf_token"
"""Default cookie holding the CSRF token."""

CSRF_HEADER_NAME: str = "X-CSRF-Token"
"""Default header the client echoes the cookie value into."""

_UNSAFE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
"""HTTP verbs the middleware guards. GET/HEAD/OPTIONS pass through."""


def generate_csrf_token(n_bytes: int = 32) -> str:
    """Mint a fresh CSRF token.

    Args:
        n_bytes (int): Entropy bytes. 32 yields a 43-char URL-safe
            string; bring this above 16 to stay above the
            birthday-bound on the cookie set.

    Returns:
        str: URL-safe base64 token without padding.
    """
    return secrets.token_urlsafe(n_bytes)


def make_csrf_token_dependency(
    *,
    cookie_name: str = CSRF_COOKIE_NAME,
) -> Callable[[Request], str]:
    """Build a FastAPI dependency that issues + returns the CSRF token.

    Use this on the route that renders the login page (or the
    HTML shell that triggers form submissions). The dependency
    sets the cookie on the response when missing, returning the
    token so the template can embed it as a hidden input or read
    it via ``document.cookie``.

    Args:
        cookie_name (str): Cookie key — must match
            ``CSRFMiddleware(cookie_name=…)``.

    Returns:
        Callable[[Request], str]: FastAPI dependency.
    """

    def _ensure_token(request: Request) -> str:
        """Return the existing CSRF token or mint a new one."""
        token = request.cookies.get(cookie_name)
        if token is None:
            token = generate_csrf_token()
        # Stash on request.state so the route handler can read it
        # and call ``response.set_cookie`` itself when needed.
        request.state.csrf_token = token
        return token

    return _ensure_token


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF guard.

    On unsafe methods (``POST`` / ``PUT`` / ``PATCH`` / ``DELETE``)
    the request **MUST** carry:

    1. The CSRF cookie (``csrf_token`` by default).
    2. The CSRF header (``X-CSRF-Token`` by default) with the same
       value.

    Missing or mismatched values return ``403`` with the SDK
    envelope. Excluded paths bypass the check — typical use:
    ``/api/`` routes that use ``Authorization: Bearer`` (not
    susceptible to CSRF), or webhook callbacks whose
    authentication is signature-based.

    Safe methods (``GET`` / ``HEAD`` / ``OPTIONS``) always pass.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        cookie_name: str = CSRF_COOKIE_NAME,
        header_name: str = CSRF_HEADER_NAME,
        exclude_paths: tuple[str, ...] = (),
    ) -> None:
        """Initialize.

        Args:
            app (ASGIApp): Wrapped app.
            cookie_name (str): Name of the CSRF cookie.
            header_name (str): Name of the CSRF header.
            exclude_paths (tuple[str, ...]): Path prefixes that
                bypass the check (e.g. ``("/api/", "/webhooks/")``).
        """
        super().__init__(app)
        self.cookie_name: str = cookie_name
        self.header_name: str = header_name
        self.exclude_paths: tuple[str, ...] = exclude_paths

    def _is_excluded(self, path: str) -> bool:
        """Return ``True`` when ``path`` matches an exclusion prefix."""
        return any(path.startswith(prefix) for prefix in self.exclude_paths)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Enforce the double-submit check on unsafe methods."""
        if request.method not in _UNSAFE_METHODS:
            return await call_next(request)
        if self._is_excluded(request.url.path):
            return await call_next(request)

        cookie_token = request.cookies.get(self.cookie_name)
        header_token = request.headers.get(self.header_name)
        if not cookie_token or not header_token:
            return self._reject("CSRF token missing.")
        if not hmac.compare_digest(cookie_token, header_token):
            return self._reject("CSRF token mismatch.")
        return await call_next(request)

    def _reject(self, message: str) -> JSONResponse:
        """Emit a 403 with the SDK envelope."""
        return JSONResponse(
            status_code=403,
            content={
                "detail": message,
                "code": "CSRF_VALIDATION_FAILED",
                "details": {},
            },
        )


__all__: list[str] = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "CSRFMiddleware",
    "generate_csrf_token",
    "make_csrf_token_dependency",
]
