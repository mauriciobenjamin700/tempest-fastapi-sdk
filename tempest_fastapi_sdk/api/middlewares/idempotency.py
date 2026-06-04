"""``Idempotency-Key`` middleware for safe mutation retries.

Implements the well-known idempotency pattern used by Stripe, AWS,
GitHub and most modern payment APIs:

1. Client sends a mutating request (``POST`` / ``PUT`` / ``PATCH``
   / ``DELETE``) with a unique ``Idempotency-Key`` header.
2. Server processes the request once and stores the full response
   keyed by ``(method, path, key)``.
3. Any retry of the same request returns the cached response —
   no duplicate row in the database, no double charge.

The middleware is **opt-in per request**: requests without the
``Idempotency-Key`` header pass straight through, so existing
endpoints keep working. Only handlers the client explicitly marks
get the guarantee.

Pluggable storage
-----------------

The cache backend is abstracted behind :class:`IdempotencyStore`
so deployments can pick what they have already:

* :class:`MemoryIdempotencyStore` — in-process dict with TTL.
  Fine for single-replica services / tests.
* :class:`RedisIdempotencyStore` — backed by an async ``redis``
  client. Required when more than one replica serves traffic,
  otherwise replicas can't see each other's cached responses.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Header name canonical to the industry — Stripe / AWS / GitHub all use it.
IDEMPOTENCY_HEADER: str = "Idempotency-Key"

# Mutating verbs the middleware caches. Reads are naturally idempotent
# and replaying them wastes the cache.
_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class CachedResponse:
    """Serialized response stored under an idempotency key.

    Attributes:
        status_code (int): HTTP status of the original response.
        headers (list[tuple[str, str]]): Response headers as a flat
            list of ``(name, value)`` pairs (preserving duplicates
            for ``Set-Cookie``).
        body (bytes): Raw response body bytes.
        media_type (str | None): Original ``Content-Type``.
    """

    status_code: int
    headers: list[tuple[str, str]]
    body: bytes
    media_type: str | None


@runtime_checkable
class IdempotencyStore(Protocol):
    """Protocol every idempotency cache implements."""

    async def get(self, key: str) -> CachedResponse | None:
        """Return the cached response for ``key`` or ``None`` when missing."""
        ...

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Store ``response`` under ``key`` with a TTL."""
        ...


class MemoryIdempotencyStore:
    """In-process :class:`IdempotencyStore` with TTL eviction.

    Single-replica only — a second replica won't see entries
    stored by the first. Suitable for dev, tests, and small
    services that haven't scaled out yet.

    The eviction is best-effort: TTLs are checked on access; no
    background thread cleans the dict. Memory grows linearly with
    cached requests until they expire, so set a sensible TTL.
    """

    def __init__(self) -> None:
        """Initialize the in-memory store."""
        self._store: dict[str, tuple[float, CachedResponse]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, key: str) -> CachedResponse | None:
        """Return the cached response, evicting if expired."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, response = entry
            if expires_at < time.monotonic():
                self._store.pop(key, None)
                return None
            return response

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Store the response with an expiry."""
        async with self._lock:
            self._store[key] = (time.monotonic() + ttl_seconds, response)


class RedisIdempotencyStore:
    """:class:`IdempotencyStore` backed by an async ``redis`` client.

    The cached payload is encoded as JSON so the schema stays
    portable across SDK versions: ``{"status_code", "headers",
    "body_b64", "media_type"}`` with the body base64-encoded
    because Redis values are bytes.

    Use this in production / multi-replica deployments. Requires
    the ``[cache]`` extra so the ``redis`` async client is
    available.
    """

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "idem:",
    ) -> None:
        """Initialize.

        Args:
            client (Any): Async Redis client (``redis.asyncio.Redis``).
            prefix (str): Key prefix so idempotency entries don't
                collide with other cached data.
        """
        self.client: Any = client
        self.prefix: str = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    async def get(self, key: str) -> CachedResponse | None:
        """Fetch and decode the cached response."""
        import base64

        raw = await self.client.get(self._key(key))
        if raw is None:
            return None
        payload = json.loads(raw)
        return CachedResponse(
            status_code=payload["status_code"],
            headers=[tuple(h) for h in payload["headers"]],
            body=base64.b64decode(payload["body_b64"]),
            media_type=payload.get("media_type"),
        )

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Serialize and write with EXPIRE."""
        import base64

        payload = json.dumps(
            {
                "status_code": response.status_code,
                "headers": list(response.headers),
                "body_b64": base64.b64encode(response.body).decode("ascii"),
                "media_type": response.media_type,
            }
        )
        await self.client.set(self._key(key), payload, ex=ttl_seconds)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """ASGI middleware caching responses by ``Idempotency-Key``.

    Only mutating verbs (``POST`` / ``PUT`` / ``PATCH`` /
    ``DELETE``) are eligible. The key is scoped per
    ``(method, path, key)`` so a key reused across different
    endpoints doesn't collide.

    Add to FastAPI like any other ASGI middleware:

        from tempest_fastapi_sdk import (
            IdempotencyMiddleware,
            MemoryIdempotencyStore,
        )

        app.add_middleware(
            IdempotencyMiddleware,
            store=MemoryIdempotencyStore(),
            ttl_seconds=24 * 3600,
        )
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        store: IdempotencyStore,
        ttl_seconds: int = 24 * 3600,
        header_name: str = IDEMPOTENCY_HEADER,
    ) -> None:
        """Initialize the middleware.

        Args:
            app (ASGIApp): The wrapped ASGI app.
            store (IdempotencyStore): Backend used to cache responses.
                Pass :class:`MemoryIdempotencyStore` for single-replica
                deployments, :class:`RedisIdempotencyStore` otherwise.
            ttl_seconds (int): How long to keep cached responses.
                Stripe defaults to 24 hours — long enough to cover
                client retries with exponential backoff.
            header_name (str): Header carrying the idempotency key.
                Defaults to the canonical ``Idempotency-Key``.
        """
        super().__init__(app)
        self.store: IdempotencyStore = store
        self.ttl_seconds: int = ttl_seconds
        self.header_name: str = header_name

    def _build_cache_key(self, request: Request, key: str) -> str:
        return f"{request.method}:{request.url.path}:{key}"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Replay cached responses when the same key reappears."""
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        key = request.headers.get(self.header_name)
        if not key:
            return await call_next(request)

        cache_key = self._build_cache_key(request, key)
        cached = await self.store.get(cache_key)
        if cached is not None:
            return Response(
                content=cached.body,
                status_code=cached.status_code,
                headers=dict(cached.headers),
                media_type=cached.media_type,
            )

        response = await call_next(request)

        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            body_chunks.append(chunk)
        body = b"".join(body_chunks)

        cached_response = CachedResponse(
            status_code=response.status_code,
            headers=[
                (k.decode("latin-1"), v.decode("latin-1"))
                for k, v in response.raw_headers
            ],
            body=body,
            media_type=response.media_type,
        )
        await self.store.set(
            cache_key,
            cached_response,
            ttl_seconds=self.ttl_seconds,
        )
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(cached_response.headers),
            media_type=response.media_type,
        )


__all__: list[str] = [
    "IDEMPOTENCY_HEADER",
    "CachedResponse",
    "IdempotencyMiddleware",
    "IdempotencyStore",
    "MemoryIdempotencyStore",
    "RedisIdempotencyStore",
]
