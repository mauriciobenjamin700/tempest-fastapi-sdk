"""``AccessLogMiddleware`` — one structured log line per HTTP request.

The SDK binds a correlation id (``RequestIDMiddleware``) and formats
records as JSON (:class:`~tempest_fastapi_sdk.core.logging.JSONFormatter`),
but nothing emitted the *one line per request* that makes
:func:`~tempest_fastapi_sdk.api.routers.logs.make_logs_router` worth
querying. Every service wrote that middleware itself, and each one got a
different subset of three non-obvious details right.

This module is those three details, solved once:

1. **It is pure ASGI, not a ``BaseHTTPMiddleware``.** That base class runs
   the downstream app in a separate task and surfaces its exceptions at the
   ``call_next`` boundary, so a middleware built on it either swallows the
   error or re-raises it from the wrong place. Reading the status off the
   ``http.response.start`` message with a ``send`` wrapper keeps the
   exception path untouched — it propagates exactly as it would without the
   middleware.
2. **An unhandled exception has no status.** The request that most needs a
   log line is the one whose handler blew up before sending anything. The
   line is written on the way out of the failure too, with the status that
   was actually sent or ``500`` when none was.
3. **The path may carry a secret.** A deprecated endpoint that takes a
   bearer-equivalent token as a path parameter still reaches this middleware
   with the token in the URL, and refusing the request downstream does not
   un-log it. ``redact`` is the seam that rewrites path and query before the
   record is built.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tempest_fastapi_sdk.utils.client_ip import get_client_ip_from_scope

_SERVER_ERROR: int = 500


class AccessLogMiddleware:
    """Emit one log record per HTTP request, with structured fields.

    The record's message is human-readable (``GET /api/users 200 12.4ms``)
    so a plain formatter stays useful, while method, path, query, status,
    duration and client IP also go through ``extra=`` — which is what makes
    them real keys under :class:`JSONFormatter` and therefore filterable in
    :func:`make_logs_router` rather than trapped inside an interpolated
    string.

    Level is ``level`` for anything the app answered below ``500``, and
    ``ERROR`` for a server error — whether the app rendered it itself or an
    exception propagated out. Finding failed requests by level is the point
    of writing them at all.

    Ordering note, measured on 2026-08-30: the ``request_id`` field comes
    from the context variable :class:`RequestIDMiddleware` binds, and that
    binding is cleared as that middleware unwinds. So this middleware only
    sees the id when it runs **inside** it — with Starlette's
    ``add_middleware``, which applies the last one added as the outermost,
    that means adding ``AccessLogMiddleware`` **first** and
    ``RequestIDMiddleware`` **after** it. Added the other way round, every
    line is written after the id is gone and carries none.

    Attributes:
        app (ASGIApp): The wrapped ASGI application.
        logger (logging.Logger): Where the lines are written.
        level (int): Level used for non-error responses.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        logger_name: str = "tempest.access",
        level: int = logging.INFO,
        exempt_paths: tuple[str, ...] = (),
        redact: Callable[[str], str] | None = None,
        trusted_ip_header: str | None = None,
    ) -> None:
        """Initialize.

        Args:
            app (ASGIApp): The wrapped ASGI application.
            logger_name (str): Logger the records are written to. A
                dedicated name lets a service route access lines to their
                own handler, or silence them, without touching the rest of
                its logging.
            level (int): Level for responses below ``500``. Server errors
                are always logged at ``logging.ERROR``.
            exempt_paths (tuple[str, ...]): Path **prefixes** that produce
                no line at all. The case this exists for is a streaming
                endpoint: an SSE connection held open for an hour would
                otherwise be logged once, on close, as a request that took
                an hour — and those live under a prefix
                (``("/api/sse",)`` covers ``/api/sse/stream``), which is
                why this matches by prefix where
                :class:`RateLimitMiddleware` matches exactly.
            redact (Callable[[str], str] | None): Applied to the path and
                to the query string, separately, before either reaches the
                record. ``None`` redacts nothing. Use it whenever a secret
                can appear in a URL — the middleware sees the request
                before any handler can refuse it.
            trusted_ip_header (str | None): Single edge-set header naming
                the client IP (e.g. ``"x-real-ip"``), passed through to
                :func:`get_client_ip_from_scope`. ``None`` uses the
                transport peer. Never point this at a bare
                ``X-Forwarded-For``: that header is appended to whatever
                the client sent, so its leftmost entry is
                attacker-controlled and the log would attribute requests to
                an address the caller chose.
        """
        self.app: ASGIApp = app
        self.logger: logging.Logger = logging.getLogger(logger_name)
        self.level: int = level
        self._exempt: tuple[str, ...] = exempt_paths
        self._redact: Callable[[str], str] | None = redact
        self._trusted_ip_header: str | None = trusted_ip_header

    def _clean(self, value: str) -> str:
        """Run ``value`` through the configured redactor.

        Args:
            value (str): The raw path or query string.

        Returns:
            str: The redacted value, or ``value`` unchanged when no
            redactor was configured.
        """
        return self._redact(value) if self._redact is not None else value

    def _emit(
        self,
        scope: Scope,
        *,
        status: int,
        elapsed_ms: float,
        error: str | None,
    ) -> None:
        """Write the access line for one finished request.

        Args:
            scope (Scope): The ASGI scope of the request.
            status (int): The status actually sent, or ``500`` when the
                request failed before sending one.
            elapsed_ms (float): Wall-clock duration in milliseconds.
            error (str | None): Class name of the exception that
                propagated, or ``None`` when the request completed.
        """
        method: str = scope.get("method", "")
        path: str = self._clean(scope.get("path", ""))
        raw_query = scope.get("query_string", b"")
        query: str = self._clean(raw_query.decode("latin-1"))
        fields: dict[str, object] = {
            "http_method": method,
            "http_path": path,
            "http_query": query,
            "http_status": status,
            "duration_ms": round(elapsed_ms, 3),
            "client_ip": get_client_ip_from_scope(
                scope,
                trusted_header=self._trusted_ip_header,
            ),
        }
        if error is not None:
            fields["error"] = error
        level = logging.ERROR if status >= _SERVER_ERROR else self.level
        self.logger.log(
            level,
            "%s %s %d %.1fms",
            method,
            path,
            status,
            elapsed_ms,
            extra=fields,
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Time the request, capture its status, and log it once.

        The status is read off ``http.response.start`` rather than from a
        returned ``Response``, because at the ASGI layer there is no
        returned response — and because wrapping ``send`` leaves the
        downstream exception path exactly as it was.

        A propagating exception is logged and re-raised, never swallowed:
        the service's own handlers still decide what the client sees. When
        nothing was sent before it, the line records ``500``, which is what
        the server will answer.

        Args:
            scope (Scope): The ASGI scope.
            receive (Receive): The upstream receive callable.
            send (Send): The upstream send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._exempt):
            await self.app(scope, receive, send)
            return

        status: int | None = None
        started = time.perf_counter()

        async def _watched_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, _watched_send)
        except BaseException as exc:
            self._emit(
                scope,
                status=status if status is not None else _SERVER_ERROR,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                error=type(exc).__name__,
            )
            raise
        self._emit(
            scope,
            status=status if status is not None else _SERVER_ERROR,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            error=None,
        )


__all__: list[str] = [
    "AccessLogMiddleware",
]
