"""In-process sliding-window rate limit middleware."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Lightweight in-process sliding-window rate limiter.

    Each unique key (by default the client IP) is allowed at most
    ``max_requests`` requests inside every ``window_seconds`` window.
    Excess requests are rejected with ``429 Too Many Requests`` and a
    ``Retry-After`` header. State is held in-process — for multi-worker
    deployments, share state via a Redis-backed limiter outside the
    SDK or run the limiter behind a single reverse-proxy worker.

    Attributes:
        max_requests (int): Maximum requests inside the window.
        window_seconds (float): Length of the sliding window.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_requests: int = 60,
        window_seconds: float = 60.0,
        key_func: Callable[[Request], str] | None = None,
        exempt_paths: tuple[str, ...] = (),
        retry_after_header: bool = True,
        error_message: str = "Too many requests",
    ) -> None:
        """Initialize the middleware.

        Args:
            app (ASGIApp): The underlying ASGI app.
            max_requests (int): Maximum requests per window.
            window_seconds (float): Window length in seconds.
            key_func (Callable[[Request], str] | None): Build a
                rate-limit key from the request. Defaults to the
                client IP (``request.client.host`` falling back to
                ``"anonymous"``).
            exempt_paths (tuple[str, ...]): Paths to skip entirely
                (e.g. ``("/health/liveness", "/health/readiness")``).
            retry_after_header (bool): Whether to add a
                ``Retry-After`` header on 429 responses.
            error_message (str): Body of the 429 response.
        """
        super().__init__(app)
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_requests: int = max_requests
        self.window_seconds: float = window_seconds
        self._key_func: Callable[[Request], str] = key_func or _default_key
        self._exempt: frozenset[str] = frozenset(exempt_paths)
        self._retry_after_header: bool = retry_after_header
        self._error_message: str = error_message
        self._buckets: dict[str, deque[float]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        """Apply the rate limit before forwarding to the route handler.

        Args:
            request (Request): The inbound request.
            call_next (Callable): Next handler in the middleware chain.

        Returns:
            Response: The downstream response, or a 429 when the limit
            is exceeded.
        """
        if request.url.path in self._exempt:
            exempt_response: Response = await call_next(request)
            return exempt_response

        key = self._key_func(request)
        now = time.monotonic()

        async with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            cutoff = now - self.window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now))
                headers: dict[str, str] = {}
                if self._retry_after_header:
                    headers["Retry-After"] = str(retry_after)
                return Response(
                    content=self._error_message,
                    status_code=429,
                    media_type="text/plain",
                    headers=headers,
                )
            bucket.append(now)

        response: Response = await call_next(request)
        return response


def _default_key(request: Request) -> str:
    """Default key — best-effort client IP, falling back to ``"anonymous"``.

    Args:
        request (Request): The inbound request.

    Returns:
        str: A stable key per remote client.
    """
    if request.client is not None and request.client.host:
        return request.client.host
    return "anonymous"


__all__: list[str] = [
    "RateLimitMiddleware",
]
