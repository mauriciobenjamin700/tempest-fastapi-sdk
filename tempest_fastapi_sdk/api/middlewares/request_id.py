"""Correlation-ID middleware bridging HTTP headers and log context."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from tempest_fastapi_sdk.core.context import clear_request_id, set_request_id


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Bind an ``X-Request-ID`` header to the request-scoped context.

    Reads the inbound header (or generates a fresh UUID v4 when
    absent), stores it via :func:`set_request_id` so log records
    written during the request carry the ``request_id`` field, and
    echoes the same value back on the response so callers can trace
    end-to-end across services.

    Args:
        app (ASGIApp): The wrapped ASGI application.
        header_name (str): The header to read/write. Defaults to
            ``"X-Request-ID"``.
    """

    def __init__(
        self,
        app: ASGIApp,
        header_name: str = "X-Request-ID",
    ) -> None:
        super().__init__(app)
        self.header_name: str = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Run the wrapped handler with a bound request ID.

        Args:
            request (Request): The inbound request.
            call_next: The downstream ASGI handler.

        Returns:
            Response: The handler's response with the request ID
            echoed in the configured header.
        """
        rid = request.headers.get(self.header_name) or str(uuid.uuid4())
        token = set_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            clear_request_id(token)
        response.headers[self.header_name] = rid
        return response


__all__: list[str] = [
    "RequestIDMiddleware",
]
