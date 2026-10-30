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

Keys are scoped to the caller
-----------------------------

The header value is chosen by the client, so it is not on its own a safe
cache key: two callers that pick the same string on the same endpoint —
by collision or by one guessing the other's — would share an entry, and
the replay hands back the stored **response**, body and headers included.
The middleware therefore folds a digest of the request's credentials
(``Authorization``, ``Cookie``, ``X-Token`` and ``X-API-Key`` by default;
``credential_headers=`` changes the list) into the key, so an entry is only
ever replayed to the credentials that created it. ``principal_resolver=``
overrides that with your own notion of identity (an API-key id) when
credentials alone are too coarse or too fine.

A request with **no** credentials has no identity to scope by, so by
default it is not cached at all: it passes straight to the handler, exactly
as if it carried no ``Idempotency-Key``. Endpoints such as login carry the
secret in the body and return a token pair, and a shared anonymous scope
would replay one caller's tokens to anyone who repeats the key.
``allow_anonymous=True`` restores a shared anonymous scope for services
whose anonymous mutations are safe to replay across callers.

Each entry is bound to the request
----------------------------------

The stored entry carries a digest of the method, path, query string and
body. A later request under the same key whose digest differs is answered
``422`` (``code="IDEMPOTENCY_KEY_REUSED"``) instead of being replayed, the
behaviour Stripe documents: a key reused for a different request is a client
bug, and silently returning the first request's response hides it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from tempest_fastapi_sdk.api.middlewares._credentials import (
    DEFAULT_CREDENTIAL_HEADERS,
    credential_digest,
    normalize_credential_headers,
)
from tempest_fastapi_sdk.api.middlewares._streaming import is_unbounded_stream


class _RedisLike(Protocol):
    """Minimal async-Redis surface used by :class:`RedisIdempotencyStore`.

    Lets the store accept any client (``redis.asyncio.Redis``,
    ``fakeredis.aioredis``, an in-house wrapper) without coupling
    the SDK to ``redis-py`` for type-checking purposes.
    """

    def get(self, key: str, /) -> Awaitable[str | bytes | None]:
        """Return the stored value (or ``None`` when absent).

        Declared with a positional-only parameter and an ``Awaitable``
        return rather than as ``async def``: ``redis.asyncio.Redis`` names
        the parameter ``name`` and returns ``Awaitable``, not ``Coroutine``,
        so an ``async def`` member typed ``key`` rejects the very client
        this protocol exists to accept.

        Args:
            key (str): The idempotency key from the request header.

        Returns:
            Awaitable[str | bytes | None]: The stored payload, or ``None``
            on a miss.
        """
        ...

    def set(self, key: str, value: str, /, *, ex: int) -> Awaitable[object]:
        """Store ``value`` under ``key`` with TTL ``ex`` (seconds).

        Args:
            key (str): The idempotency key from the request header.
            value (str): The serialized payload to store.
            ex (int): Time-to-live in seconds.

        Returns:
            Awaitable[object]: Whatever the client returns; the caller
            ignores it.
        """
        ...


IDEMPOTENCY_HEADER: str = "Idempotency-Key"
"""Header name canonical to the industry: Stripe, AWS and GitHub all use it."""

_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
"""Verbs the middleware caches; reads are idempotent and replaying them wastes
the cache."""

_UNREPLAYABLE_HEADERS: frozenset[str] = frozenset({"set-cookie"})
"""Never replayed: a ``Set-Cookie`` minted for the original caller would be
re-issued to whoever replays the key, handing over that session."""

_KEY_REUSED_CODE: str = "IDEMPOTENCY_KEY_REUSED"
"""``code`` of the ``422`` answered when a key is reused for another request."""


def _request_fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    """Digest the parts of a request an idempotent replay must match.

    Each part is length-prefixed so two different requests can never
    concatenate into the same input.

    Args:
        method (str): The HTTP method.
        path (str): The URL path.
        query (str): The raw query string.
        body (bytes): The raw request body.

    Returns:
        str: The sha256 hex digest.
    """
    digest = hashlib.sha256()
    for part in (method.encode(), path.encode(), query.encode(), body):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


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
        fingerprint (str): Digest of the request that produced the response
            (method, path, query and body). Empty for entries that are not
            bound to a request, such as the response cache's.
    """

    status_code: int
    headers: list[tuple[str, str]]
    body: bytes
    media_type: str | None
    fingerprint: str = ""


@runtime_checkable
class IdempotencyStore(Protocol):
    """Protocol every idempotency cache implements."""

    async def get(self, key: str) -> CachedResponse | None:
        """Return the cached response for ``key`` or ``None`` when missing.

        Args:
            key (str): The idempotency key from the request header.

        Returns:
            CachedResponse | None: The stored response, or ``None`` on a miss.
        """
        ...

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Store ``response`` under ``key`` with a TTL.

        Args:
            key (str): The idempotency key from the request header.
            response (CachedResponse): The response to remember for replays.
            ttl_seconds (int): How long the entry stays replayable.
        """
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
        """Return the cached response, evicting if expired.

        Args:
            key (str): The idempotency key from the request header.

        Returns:
            CachedResponse | None: The stored response, or ``None`` on a miss.
        """
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
        """Store the response with an expiry.

        Args:
            key (str): The idempotency key from the request header.
            response (CachedResponse): The response to remember for replays.
            ttl_seconds (int): How long the entry stays replayable.
        """
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
        client: _RedisLike,
        *,
        prefix: str = "idem:",
    ) -> None:
        """Initialize.

        Args:
            client (_RedisLike): Async Redis-like client exposing
                ``get(key)`` / ``set(key, value, ex)`` (e.g.
                ``redis.asyncio.Redis`` or any equivalent).
            prefix (str): Key prefix so idempotency entries don't
                collide with other cached data.
        """
        self.client: _RedisLike = client
        self.prefix: str = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    async def get(self, key: str) -> CachedResponse | None:
        """Fetch and decode the cached response.

        Args:
            key (str): The idempotency key from the request header.

        Returns:
            CachedResponse | None: The stored response, or ``None`` on a miss.
        """
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
            fingerprint=str(payload.get("fingerprint", "")),
        )

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Serialize and write with EXPIRE.

        Args:
            key (str): The idempotency key from the request header.
            response (CachedResponse): The response to remember for replays.
            ttl_seconds (int): How long the entry stays replayable.
        """
        import base64

        payload = json.dumps(
            {
                "status_code": response.status_code,
                "headers": list(response.headers),
                "body_b64": base64.b64encode(response.body).decode("ascii"),
                "media_type": response.media_type,
                "fingerprint": response.fingerprint,
            }
        )
        await self.client.set(self._key(key), payload, ex=ttl_seconds)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """ASGI middleware caching responses by ``Idempotency-Key``.

    Only mutating verbs (``POST`` / ``PUT`` / ``PATCH`` /
    ``DELETE``) are eligible. The key is scoped per
    ``(caller, method, path, key)`` so a key reused across different
    endpoints — or by a different caller — doesn't collide, and a hit
    replays only when the method, path, query and body match the request
    that created the entry (``422`` otherwise). Requests without
    credentials are not cached unless ``allow_anonymous=True``.

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
        principal_resolver: Callable[[Request], str] | None = None,
        cache_server_errors: bool = False,
        credential_headers: tuple[str, ...] = DEFAULT_CREDENTIAL_HEADERS,
        allow_anonymous: bool = False,
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
            principal_resolver (Callable[[Request], str] | None): Returns
                the caller identity folded into the cache key. ``None``
                (default) digests the ``credential_headers``, which is right
                whenever those carry the identity. Supply your own when
                identity lives elsewhere, and derive it from a credential
                the resolver **verifies** (a signed token's subject): the
                middleware runs before any auth dependency, so a value read
                from a plain client header (a tenant id) isolates nothing.
                An empty string means anonymous. Returning a constant
                restores cross-caller sharing and is unsafe on a
                multi-tenant endpoint.
            cache_server_errors (bool): Whether a ``5xx`` is stored and
                replayed. ``False`` (default) lets the client's retry
                actually reach the handler — a transient failure cached
                for ``ttl_seconds`` would otherwise pin that key to the
                error for as long as the entry lives.
            credential_headers (tuple[str, ...]): Request headers digested
                into the caller identity when ``principal_resolver`` is
                ``None``. The default covers ``Authorization``, ``Cookie``,
                ``X-Token`` (the header of ``make_token_dependency``) and
                ``X-API-Key``; add the header your own auth reads when it is
                none of those.
            allow_anonymous (bool): Whether requests without an identity
                (no credential header, or a resolver returning ``""``) share
                one cached scope. ``False`` (default) passes them to the
                handler uncached: anonymous endpoints such as login carry the
                credential in the body, and a shared scope would replay one
                caller's response to another.

        Raises:
            ValueError: When ``credential_headers`` is empty.
        """
        super().__init__(app)
        self.store: IdempotencyStore = store
        self.ttl_seconds: int = ttl_seconds
        self.header_name: str = header_name
        self.principal_resolver: Callable[[Request], str] | None = principal_resolver
        self.cache_server_errors: bool = cache_server_errors
        self.credential_headers: tuple[str, ...] = normalize_credential_headers(
            credential_headers
        )
        self.allow_anonymous: bool = allow_anonymous
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard: asyncio.Lock = asyncio.Lock()

    def _principal(self, request: Request) -> str:
        """Return the caller identity to scope the cache key by.

        Args:
            request (Request): The inbound request.

        Returns:
            str: A short, opaque identity token. Empty string for an
            unauthenticated request, which is cached only under
            ``allow_anonymous=True`` (all anonymous callers then share one
            scope).
        """
        if self.principal_resolver is not None:
            return self.principal_resolver(request)
        digest = credential_digest(request, self.credential_headers)
        return "" if digest is None else digest[:32]

    async def _lock_for(self, cache_key: str) -> asyncio.Lock:
        """Return the process-local lock guarding ``cache_key``.

        Serializes concurrent requests that share a key so the second one
        waits and then replays the first's stored response, instead of both
        reaching the handler and doing the work twice. This is per-process:
        with several replicas behind a load balancer the store still
        deduplicates retries, but two *simultaneous* requests landing on
        different replicas can both execute.

        Args:
            cache_key (str): The scoped cache key.

        Returns:
            asyncio.Lock: The lock for that key.
        """
        async with self._locks_guard:
            lock = self._locks.get(cache_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[cache_key] = lock
            return lock

    async def _release_lock(self, cache_key: str) -> None:
        """Drop the lock for ``cache_key`` when nobody else holds it.

        Keeps :attr:`_locks` from growing once per key seen, which over a
        long-lived process with client-generated keys is an unbounded leak.

        Args:
            cache_key (str): The scoped cache key.
        """
        async with self._locks_guard:
            lock = self._locks.get(cache_key)
            if lock is not None and not lock.locked():
                del self._locks[cache_key]

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Replay cached responses when the same key reappears.

        Args:
            request (Request): The inbound request.
            call_next (Callable[[Request], Awaitable[Response]]): The next
                handler in the middleware chain.

        Returns:
            Response: The replayed response on a hit, otherwise the handler's
                own.
        """
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        key = request.headers.get(self.header_name)
        if not key:
            return await call_next(request)

        principal = self._principal(request)
        if not principal and not self.allow_anonymous:
            return await call_next(request)

        cache_key = f"{principal}:{request.method}:{request.url.path}:{key}"
        fingerprint = _request_fingerprint(
            request.method,
            request.url.path,
            request.url.query,
            await request.body(),
        )
        lock = await self._lock_for(cache_key)
        try:
            async with lock:
                return await self._dispatch_locked(
                    request, call_next, cache_key, fingerprint
                )
        finally:
            await self._release_lock(cache_key)

    async def _dispatch_locked(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
        cache_key: str,
        fingerprint: str,
    ) -> Response:
        """Serve the cached response for ``cache_key`` or produce and store it.

        A hit whose stored fingerprint differs from ``fingerprint`` is not
        replayed: it is answered ``422``. That includes entries written
        before fingerprints existed, which carry an empty one.

        Args:
            request (Request): The inbound request.
            call_next (Callable[[Request], Awaitable[Response]]): The next
                handler in the middleware chain.
            cache_key (str): The caller-scoped cache key.
            fingerprint (str): Digest of this request, from
                :func:`_request_fingerprint`.

        Returns:
            Response: The replayed response on a hit, a ``422`` when the key
                was used for a different request, otherwise the handler's own.
        """
        cached = await self.store.get(cache_key)
        if cached is not None:
            if not hmac.compare_digest(cached.fingerprint, fingerprint):
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": (
                            f"{self.header_name} was already used for a "
                            "different request"
                        ),
                        "code": _KEY_REUSED_CODE,
                        "details": {},
                    },
                )
            return Response(
                content=cached.body,
                status_code=cached.status_code,
                headers=dict(cached.headers),
                media_type=cached.media_type,
            )

        response = await call_next(request)
        if is_unbounded_stream(response):
            return response

        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            body_chunks.append(chunk)
        body = b"".join(body_chunks)

        replayable_headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in response.raw_headers
            if name.decode("latin-1").lower() not in _UNREPLAYABLE_HEADERS
        ]
        cached_response = CachedResponse(
            status_code=response.status_code,
            headers=replayable_headers,
            body=body,
            media_type=response.media_type,
            fingerprint=fingerprint,
        )
        if self.cache_server_errors or response.status_code < 500:
            await self.store.set(
                cache_key,
                cached_response,
                ttl_seconds=self.ttl_seconds,
            )
        return Response(
            content=body,
            status_code=response.status_code,
            headers={
                name.decode("latin-1"): value.decode("latin-1")
                for name, value in response.raw_headers
            },
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
