"""``BodySizeLimitMiddleware`` — reject oversize request bodies early.

Without an upstream WAF / nginx body-size limit, a malicious
client can POST gigabytes of data before FastAPI's parsers reject
it — wasting bandwidth and RAM and leaving the worker pinned for
seconds. This middleware short-circuits the request at the ASGI
layer the moment ``Content-Length`` exceeds the configured cap,
or if the streamed body grows past it.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimitMiddleware:
    """Pure ASGI middleware enforcing ``max_bytes`` per request.

    Two checks happen:

    1. **Header check** — ``Content-Length`` greater than the cap
       short-circuits immediately with a ``413`` response. This
       catches the common case where the client knows the size.
    2. **Streaming check** — for chunked / unknown-length uploads
       the middleware tracks bytes seen in the ``http.request``
       messages and aborts once the cap is crossed.

    Excluded paths bypass the check entirely (typical use: an
    upload endpoint that intentionally accepts larger bodies and
    enforces its own per-route limit).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        exclude_paths: tuple[str, ...] = (),
    ) -> None:
        """Initialize.

        Args:
            app (ASGIApp): The wrapped ASGI app.
            max_bytes (int): Hard cap on the request body in bytes.
                ``0`` disables the check (do not ship to production).
            exclude_paths (tuple[str, ...]): Path prefixes that
                bypass the limit. Match is ``startswith`` so the
                more specific the better.
        """
        self.app: ASGIApp = app
        self.max_bytes: int = max_bytes
        self.exclude_paths: tuple[str, ...] = exclude_paths

    def _is_excluded(self, path: str) -> bool:
        """Return ``True`` when ``path`` matches one of the exclusions."""
        return any(path.startswith(prefix) for prefix in self.exclude_paths)

    async def _reject(self, send: Send) -> None:
        """Emit a 413 ``Payload Too Large`` response.

        The ``body`` local below exists only to document the exact bytes this
        middleware answers with; ``JSONResponse`` renders its own, so it is
        discarded rather than sent.

        Args:
            send (Send): The ASGI send callable.
        """
        body = (
            b'{"detail":"Request body too large.",'
            b'"code":"REQUEST_BODY_TOO_LARGE","details":{}}'
        )
        response = JSONResponse(
            status_code=413,
            content={
                "detail": "Request body too large.",
                "code": "REQUEST_BODY_TOO_LARGE",
                "details": {"max_bytes": self.max_bytes},
            },
        )
        del body
        await response({"type": "http"}, self._noop_receive, send)

    @staticmethod
    async def _noop_receive() -> Message:
        """Receive stub for handlers that don't read the body."""
        return {"type": "http.request", "body": b"", "more_body": False}

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Enforce the limit on every HTTP request.

        Two passes, because neither alone is sufficient. The first is a fast
        path on ``Content-Length``: an honest client declares the size up
        front and gets rejected without a byte being read. The second wraps
        ``receive`` and counts what actually arrives, which is what catches a
        chunked or lying request whose header cannot be trusted.

        When the streaming guard trips it returns ``http.disconnect`` rather
        than raising: that drains the rest so the underlying transport closes
        cleanly while telling the wrapped app the body ended. The 413 is then
        emitted in the ``finally`` block, and only if the app has not already
        responded — many handlers stop gracefully on disconnect and send
        something themselves, in which case the extra ``send`` is a no-op.

        Args:
            scope (Scope): The ASGI scope.
            receive (Receive): The upstream receive callable.
            send (Send): The upstream send callable.
        """
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if self._is_excluded(path):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value.decode("latin-1"))
                except ValueError:
                    declared = 0
                if declared > self.max_bytes:
                    await self._reject(send)
                    return
                break

        seen = 0
        rejected = False

        async def _guarded_receive() -> Message:
            nonlocal seen, rejected
            message = await receive()
            if message["type"] != "http.request":
                return message
            body = message.get("body", b"")
            seen += len(body)
            if seen > self.max_bytes and not rejected:
                rejected = True
                return {"type": "http.disconnect"}
            return message

        try:
            await self.app(scope, _guarded_receive, send)
        finally:
            if rejected:
                await self._reject(send)


__all__: list[str] = [
    "BodySizeLimitMiddleware",
]
