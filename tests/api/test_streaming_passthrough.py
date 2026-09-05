"""Middleware that drains a body must not drain one that never ends.

Both guards here fail on the code that shipped through 0.285.0: with the
``is_unbounded_stream`` check removed, every ``hangs_without_the_guard``
test blocks until ``asyncio.wait_for`` cancels it, which is the production
symptom (the proxy's read timeout, then ``504``) reproduced in-process.

The counterpart tests matter as much: a guard that skips *everything* would
also make the hang tests pass, so each middleware is additionally asserted
to still do its job on a materialized body.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from tempest_fastapi_sdk.api.middlewares._streaming import (
    is_unbounded_stream,
    response_media_type,
)
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    IdempotencyMiddleware,
    MemoryIdempotencyStore,
)
from tempest_fastapi_sdk.api.middlewares.response_cache import (
    MemoryResponseCacheStore,
    ResponseCacheMiddleware,
)
from tempest_fastapi_sdk.sse import sse_response


def _scope(path: str, method: str, headers: list[tuple[bytes, bytes]]) -> Request:
    """Build a bare ASGI request for a middleware called directly.

    Args:
        path (str): The request path.
        method (str): The HTTP method.
        headers (list[tuple[bytes, bytes]]): Raw header pairs.

    Returns:
        Request: A request usable as ``dispatch``'s first argument.
    """
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("test", 80),
            "client": ("1.2.3.4", 1234),
        }
    )


async def _never_ends() -> AsyncIterator[bytes]:
    """Yield an SSE tick and then block, the way a live stream does.

    Yields:
        bytes: One SSE frame, then nothing until cancelled.
    """
    yield b"data: tick\n\n"
    await asyncio.Event().wait()


def _as_middleware_sees_it(response: Response) -> Response:
    """Return ``response`` shaped the way ``BaseHTTPMiddleware`` hands it over.

    Args:
        response (Response): A response built by a handler.

    Returns:
        Response: The same object when it already streams, otherwise one
        wrapped so that reading it goes through ``body_iterator``.
    """
    from starlette.middleware.base import _StreamingResponse

    if hasattr(response, "body_iterator"):
        return response

    async def _one() -> AsyncIterator[bytes]:
        yield response.body

    return _StreamingResponse(
        content=_one(),
        status_code=response.status_code,
        headers=dict(response.headers),
    )


def _streaming_call_next() -> Callable[[Request], Awaitable[Response]]:
    """Return a ``call_next`` that answers with a never-ending SSE response.

    Returns:
        Callable[[Request], Awaitable[Response]]: The handler stand-in.
    """

    async def call_next(_: Request) -> Response:
        return _as_middleware_sees_it(sse_response(_never_ends()))

    return call_next


def _json_call_next() -> Callable[[Request], Awaitable[Response]]:
    """Return a ``call_next`` that answers with a materialized JSON body.

    Returns:
        Callable[[Request], Awaitable[Response]]: The handler stand-in.
    """

    async def call_next(_: Request) -> Response:
        return _as_middleware_sees_it(JSONResponse({"ok": True}))

    return call_next


class TestUnboundedStreamDetection:
    """The predicate reads the header, because the attribute is empty here."""

    def test_media_type_attribute_is_empty_on_the_middleware_path(self) -> None:
        """``response.media_type`` cannot be the discriminator.

        This is the guard for the fix that looks right and does nothing:
        ``BaseHTTPMiddleware`` rebuilds the response, so an SSE body reaches
        ``dispatch`` with ``media_type=None`` and the ``Content-Type`` header
        intact.
        """
        seen: list[tuple[str | None, str]] = []

        class Probe:
            async def __call__(self, request: Request) -> Response:
                response = _as_middleware_sees_it(sse_response(_never_ends()))
                seen.append(
                    (response.media_type, response.headers.get("content-type", ""))
                )
                return response

        async def _run() -> None:
            await Probe()(_scope("/sse", "GET", []))

        asyncio.run(_run())
        media_type, content_type = seen[0]
        assert media_type == "text/event-stream"
        assert content_type.startswith("text/event-stream")

    def test_real_asgi_path_strips_media_type(self) -> None:
        """Through a real middleware stack the attribute is ``None``."""
        observed: list[tuple[str | None, str]] = []

        from starlette.middleware.base import BaseHTTPMiddleware

        class Probe(BaseHTTPMiddleware):
            async def dispatch(
                self,
                request: Request,
                call_next: Callable[[Request], Awaitable[Response]],
            ) -> Response:
                response = await call_next(request)
                observed.append(
                    (response.media_type, response.headers.get("content-type", ""))
                )
                return response

        async def _bounded_sse(request: Any) -> Response:
            async def _two() -> AsyncIterator[bytes]:
                yield b"data: one\n\n"

            return sse_response(_two())

        app = Starlette(routes=[Route("/sse", _bounded_sse)])
        app.add_middleware(Probe)
        TestClient(app).get("/sse")

        media_type, content_type = observed[0]
        assert media_type is None
        assert response_media_type_from(content_type) == "text/event-stream"

    def test_sse_response_is_unbounded(self) -> None:
        """An SSE body is never safe to drain."""
        response = _as_middleware_sees_it(sse_response(_never_ends()))
        assert is_unbounded_stream(response) is True
        assert response_media_type(response) == "text/event-stream"

    def test_materialized_json_is_bounded(self) -> None:
        """A body with a declared length is safe to drain."""
        response = _as_middleware_sees_it(JSONResponse({"ok": True}))
        assert is_unbounded_stream(response) is False


def response_media_type_from(content_type: str) -> str:
    """Return the media type of a raw ``Content-Type`` value.

    Args:
        content_type (str): The header value.

    Returns:
        str: The lowercased media type without parameters.
    """
    return content_type.split(";", 1)[0].strip().lower()


class TestResponseCacheStreamPassthrough:
    """``ResponseCacheMiddleware`` hangs on SSE without the guard."""

    @pytest.mark.asyncio
    async def test_hangs_without_the_guard(self) -> None:
        """The SSE response comes back promptly and is not cached."""
        middleware = ResponseCacheMiddleware(
            app=None,  # type: ignore[arg-type]
            store=MemoryResponseCacheStore(),
        )
        response = await asyncio.wait_for(
            middleware.dispatch(
                _scope("/api/sse/stream", "GET", []), _streaming_call_next()
            ),
            timeout=2.0,
        )
        assert response.headers.get("etag") is None
        assert response.headers.get("x-cache") is None

    @pytest.mark.asyncio
    async def test_still_caches_a_materialized_body(self) -> None:
        """The guard must not disable caching for ordinary responses."""
        middleware = ResponseCacheMiddleware(
            app=None,  # type: ignore[arg-type]
            store=MemoryResponseCacheStore(),
        )
        response = await asyncio.wait_for(
            middleware.dispatch(_scope("/api/items", "GET", []), _json_call_next()),
            timeout=2.0,
        )
        assert response.headers.get("etag") is not None


class TestIdempotencyStreamPassthrough:
    """``IdempotencyMiddleware`` hangs on a streamed POST without the guard."""

    @pytest.mark.asyncio
    async def test_hangs_without_the_guard(self) -> None:
        """A streamed POST with an idempotency key is passed straight through."""
        middleware = IdempotencyMiddleware(
            app=None,  # type: ignore[arg-type]
            store=MemoryIdempotencyStore(),
        )
        request = _scope(
            "/api/genai/generate/stream",
            "POST",
            [(b"idempotency-key", b"abc-123")],
        )
        response = await asyncio.wait_for(
            middleware.dispatch(request, _streaming_call_next()), timeout=2.0
        )
        assert response_media_type(response) == "text/event-stream"

    @pytest.mark.asyncio
    async def test_still_replays_a_materialized_body(self) -> None:
        """The guard must not disable replay for ordinary responses."""
        store = MemoryIdempotencyStore()
        middleware = IdempotencyMiddleware(
            app=None,  # type: ignore[arg-type]
            store=store,
        )
        request = _scope("/api/orders", "POST", [(b"idempotency-key", b"key-1")])
        first = await asyncio.wait_for(
            middleware.dispatch(request, _json_call_next()), timeout=2.0
        )
        assert first.status_code == 200

        async def _must_not_run(_: Request) -> Response:
            raise AssertionError("handler ran again on an idempotent replay")

        replayed = await asyncio.wait_for(
            middleware.dispatch(
                _scope("/api/orders", "POST", [(b"idempotency-key", b"key-1")]),
                _must_not_run,
            ),
            timeout=2.0,
        )
        assert replayed.status_code == 200
