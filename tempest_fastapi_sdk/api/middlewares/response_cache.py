"""HTTP response caching: ETag / conditional-GET / ``Cache-Control``.

Two wins, layered:

* **ETag + conditional GET (always on).** Every cacheable response gets a
  strong ``ETag`` (a hash of the body) and a ``Cache-Control`` header. When the
  client sends a matching ``If-None-Match``, the middleware answers ``304 Not
  Modified`` with an empty body — the handler still runs, but the bytes never
  go over the wire.

* **Server-side response cache (opt-in via ``store=``).** With a
  :class:`ResponseCacheStore` wired, a cacheable ``GET`` / ``HEAD`` response is
  stored for ``ttl_seconds``; a later matching request is served **without
  running the handler at all** (``X-Cache: HIT``), and an ``If-None-Match`` hit
  against the stored ETag still short-circuits to ``304``.

Only safe methods and successful responses are cached, and responses that opt
out (``Cache-Control: no-store``/``private`` or a ``Set-Cookie``) are never
stored. Vary the cache key on request headers with ``vary=`` (also emitted as a
``Vary`` response header). The store mirrors the idempotency store shape
(memory + Redis, raw client), so it composes with the SDK's existing Redis.

**Credentialed requests never share a cache entry.** A request carrying an
``Authorization`` header or a ``Cookie`` bypasses the shared store entirely,
because its response is presumed to be about *that* caller — caching it under
a key made of method + path would serve one user's data to the next. Those
requests still get an ``ETag`` and a ``304``, which is per-response and safe.
Pass ``cache_credentialed=True`` to opt a deployment back in; the credential
headers are then folded (hashed) into the key so each caller gets its own
entry. The emitted ``Cache-Control`` also defaults to ``private``, so no
browser or CDN in the path stores a personalized response either.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from tempest_fastapi_sdk.api.middlewares.idempotency import CachedResponse

_ETAG_HEADER = "etag"

_CREDENTIAL_HEADERS: tuple[str, ...] = ("authorization", "cookie")
"""Request headers whose presence marks the response as caller-specific."""


def _compute_etag(body: bytes) -> str:
    """Return a strong ETag (quoted sha256 hex) for ``body``.

    Args:
        body (bytes): The response body.

    Returns:
        str: The quoted ETag value (e.g. ``'"a1b2..."'``).
    """
    return f'"{hashlib.sha256(body).hexdigest()}"'


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """Return whether ``If-None-Match`` covers ``etag``.

    Args:
        if_none_match (str): The raw ``If-None-Match`` header value.
        etag (str): The current strong ETag (quoted).

    Returns:
        bool: ``True`` for ``*`` or when ``etag`` is one of the listed tags
        (weak-prefix ``W/`` tolerated on the client side).
    """
    candidate = if_none_match.strip()
    if candidate == "*":
        return True
    tags = {tag.strip().removeprefix("W/") for tag in candidate.split(",")}
    return etag in tags


@runtime_checkable
class ResponseCacheStore(Protocol):
    """Where full cached responses live (get/set with TTL)."""

    async def get(self, key: str) -> CachedResponse | None:
        """Return the cached response for ``key``, or ``None``.

        Args:
            key (str): The cache key.

        Returns:
            CachedResponse | None: The stored response, or ``None`` on miss.
        """
        ...

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Store ``response`` under ``key`` for ``ttl_seconds``.

        Args:
            key (str): The cache key.
            response (CachedResponse): The response to cache.
            ttl_seconds (int): Time-to-live in seconds.
        """
        ...


class MemoryResponseCacheStore:
    """In-process :class:`ResponseCacheStore` (one worker only).

    Fine for a single process / tests; use :class:`RedisResponseCacheStore` to
    share a cache across workers.
    """

    def __init__(self) -> None:
        """Initialize the empty store."""
        self._entries: dict[str, tuple[float, CachedResponse]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> CachedResponse | None:
        """Return the live cached response for ``key`` (expired entries drop).

        Args:
            key (str): Cache key derived from method, path and the ``vary``
                headers.

        Returns:
            CachedResponse | None: The stored response, or ``None`` on a miss.
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, response = entry
            if expires_at <= time.monotonic():
                del self._entries[key]
                return None
            return response

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Store ``response`` under ``key`` with a monotonic-clock expiry.

        Args:
            key (str): Cache key derived from method, path and the ``vary``
                headers.
            response (CachedResponse): The response to store.
            ttl_seconds (int): How long the entry stays fresh.
        """
        async with self._lock:
            self._entries[key] = (time.monotonic() + ttl_seconds, response)


@runtime_checkable
class _RedisLike(Protocol):
    """The subset of ``redis.asyncio.Redis`` the Redis store uses."""

    def get(self, key: str, /) -> Awaitable[Any]:
        """Return the raw stored value for ``key``, or ``None`` when absent.

        Positional-only, and typed as returning an ``Awaitable`` rather
        than declared ``async def``: ``redis.asyncio.Redis`` names this
        parameter ``name`` and returns ``Awaitable``, so the stricter
        spelling rejects the client this protocol is written for.

        Args:
            key (str): The cache key.

        Returns:
            Awaitable[Any]: The stored payload, or ``None`` on a miss.
        """
        ...

    def set(self, key: str, value: str, /, *, ex: int) -> Awaitable[Any]:
        """Store ``value`` under ``key`` with a TTL.

        Args:
            key (str): The cache key.
            value (str): The payload to store.
            ex (int): Time-to-live in seconds.

        Returns:
            Awaitable[Any]: Whatever the client returns; the caller
            ignores it.
        """
        ...


class RedisResponseCacheStore:
    """Redis-backed :class:`ResponseCacheStore`, shared across workers.

    Serializes each response as JSON (body base64-encoded), the same wire shape
    the idempotency store uses.
    """

    def __init__(self, client: _RedisLike, *, prefix: str = "respcache:") -> None:
        """Configure the store.

        Args:
            client (_RedisLike): A raw async Redis client (``get`` / ``set``).
            prefix (str): Key prefix namespacing this cache.
        """
        self._client = client
        self._prefix = prefix

    def _key(self, key: str) -> str:
        """Return the namespaced Redis key."""
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> CachedResponse | None:
        """Return the cached response for ``key``, or ``None`` on miss.

        Args:
            key (str): Cache key derived from method, path and the ``vary``
                headers.

        Returns:
            CachedResponse | None: The stored response, or ``None`` on a miss.
        """
        import base64
        import json

        raw = await self._client.get(self._key(key))
        if raw is None:
            return None
        payload = json.loads(raw)
        return CachedResponse(
            status_code=int(payload["status_code"]),
            headers=[(name, value) for name, value in payload["headers"]],
            body=base64.b64decode(payload["body"]),
            media_type=payload["media_type"],
        )

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        """Store ``response`` under ``key`` with ``ex=ttl_seconds``.

        Args:
            key (str): Cache key derived from method, path and the ``vary``
                headers.
            response (CachedResponse): The response to store.
            ttl_seconds (int): How long the entry stays fresh.
        """
        import base64
        import json

        payload = json.dumps(
            {
                "status_code": response.status_code,
                "headers": [[name, value] for name, value in response.headers],
                "body": base64.b64encode(response.body).decode("ascii"),
                "media_type": response.media_type,
            }
        )
        await self._client.set(self._key(key), payload, ex=ttl_seconds)


class ResponseCacheMiddleware(BaseHTTPMiddleware):
    """Add ETag / conditional-GET and optional server-side response caching.

    Example:

        >>> app.add_middleware(ResponseCacheMiddleware, ttl_seconds=30)
        >>> app.add_middleware(
        ...     ResponseCacheMiddleware,
        ...     store=RedisResponseCacheStore(redis),
        ...     ttl_seconds=60,
        ...     vary=("Accept-Encoding",),
        ... )
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        store: ResponseCacheStore | None = None,
        ttl_seconds: int = 60,
        max_age: int | None = None,
        cache_control: str | None = None,
        methods: tuple[str, ...] = ("GET", "HEAD"),
        cacheable_status: tuple[int, ...] = (200,),
        vary: tuple[str, ...] = (),
        exempt_paths: tuple[str, ...] = (),
        cacheable: Callable[[Request], bool] | None = None,
        cache_credentialed: bool = False,
    ) -> None:
        """Configure the middleware.

        Args:
            app (ASGIApp): The wrapped application.
            store (ResponseCacheStore | None): When set, full responses are
                cached server-side and served without re-running the handler.
                When ``None``, only ETag / ``304`` handling is applied.
            ttl_seconds (int): Server-cache TTL and the default ``max-age``.
            max_age (int | None): ``Cache-Control: max-age`` value; defaults to
                ``ttl_seconds``.
            cache_control (str | None): Explicit ``Cache-Control`` value; when
                set it overrides the ``max_age``-derived one. The default is
                ``private, max-age=<max_age>`` — ``private`` because the
                middleware cannot know whether a given route's body is
                personalized, and a wrong ``public`` there means a shared
                proxy serves one user's response to another. Set it to
                ``"public, max-age=…"`` deliberately, on a router that only
                serves shared content.
            methods (tuple[str, ...]): HTTP methods eligible for caching.
            cacheable_status (tuple[int, ...]): Status codes eligible for
                caching (default: only ``200``).
            vary (tuple[str, ...]): Request headers folded into the cache key
                and emitted as ``Vary``.
            exempt_paths (tuple[str, ...]): Exact paths that bypass the
                middleware.
            cacheable (Callable[[Request], bool] | None): Optional predicate;
                when it returns ``False`` the request bypasses caching.
            cache_credentialed (bool): Whether a request carrying an
                ``Authorization`` header or a ``Cookie`` may use the shared
                store. ``False`` (default) makes such requests bypass it —
                their response belongs to one caller, and the key does not
                identify callers. ``True`` folds a digest of those headers
                into the key, so each set of credentials gets its own entry;
                only turn it on once you have checked that the cached routes
                do not embed a second identity (a tenant header, a
                path-independent session).

        Raises:
            ValueError: When ``ttl_seconds`` is not positive.
        """
        super().__init__(app)
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._store = store
        self._ttl = ttl_seconds
        resolved_max_age = ttl_seconds if max_age is None else max_age
        self._cache_control = (
            cache_control
            if cache_control is not None
            else f"private, max-age={resolved_max_age}"
        )
        self._methods = frozenset(methods)
        self._cacheable_status = frozenset(cacheable_status)
        self._vary = tuple(vary)
        self._exempt = frozenset(exempt_paths)
        self._cacheable = cacheable
        self._cache_credentialed = cache_credentialed

    @staticmethod
    def _credentials(request: Request) -> str | None:
        """Return a digest of the request's credential headers, if any.

        Args:
            request (Request): The inbound request.

        Returns:
            str | None: A short digest identifying the caller's credentials,
            or ``None`` when the request carries none.
        """
        raw = "|".join(
            f"{name}={request.headers.get(name, '')}" for name in _CREDENTIAL_HEADERS
        )
        if raw == "|".join(f"{name}=" for name in _CREDENTIAL_HEADERS):
            return None
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _build_key(self, request: Request, credentials: str | None) -> str:
        """Build the cache key from method, path, query, and varied headers.

        Args:
            request (Request): The inbound request.
            credentials (str | None): Digest from :meth:`_credentials`, folded
                in so two callers never collide on one entry.

        Returns:
            str: The cache key.
        """
        parts = [request.method, request.url.path, request.url.query]
        parts.extend(f"{name}={request.headers.get(name, '')}" for name in self._vary)
        if credentials is not None:
            parts.append(f"cred={credentials}")
        return "|".join(parts)

    def _decorate(self, headers: dict[str, str], etag: str) -> dict[str, str]:
        """Return ``headers`` with ETag / ``Cache-Control`` / ``Vary`` set."""
        headers[_ETAG_HEADER] = etag
        headers.setdefault("cache-control", self._cache_control)
        if self._vary:
            headers["vary"] = ", ".join(self._vary)
        return headers

    def _not_modified(self, etag: str) -> Response:
        """Build a ``304 Not Modified`` response carrying the cache headers."""
        headers = self._decorate({}, etag)
        return Response(status_code=304, headers=headers)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Serve from cache / ``304`` when possible, else cache the response.

        Args:
            request (Request): The inbound request.
            call_next (Callable[[Request], Awaitable[Response]]): The next
                handler in the middleware chain.

        Returns:
            Response: A ``304`` when the ETag matches, the cached body on a
                hit, otherwise the handler's own response.
        """
        if request.method not in self._methods or request.url.path in self._exempt:
            return await call_next(request)
        if self._cacheable is not None and not self._cacheable(request):
            return await call_next(request)

        credentials = self._credentials(request)
        share_store = credentials is None or self._cache_credentialed
        store = self._store if share_store else None
        key_credentials = credentials if self._cache_credentialed else None
        key = self._build_key(request, key_credentials)
        if_none_match = request.headers.get("if-none-match")

        if store is not None:
            cached = await store.get(key)
            if cached is not None:
                etag = dict(cached.headers).get(
                    _ETAG_HEADER, _compute_etag(cached.body)
                )
                if if_none_match and _etag_matches(if_none_match, etag):
                    return self._not_modified(etag)
                headers = self._decorate(dict(cached.headers), etag)
                headers["x-cache"] = "HIT"
                return Response(
                    content=cached.body,
                    status_code=cached.status_code,
                    headers=headers,
                    media_type=cached.media_type,
                )

        response = await call_next(request)
        if response.status_code not in self._cacheable_status or _skip_caching(
            response
        ):
            return response

        body = await _drain(response)
        etag = _compute_etag(body)
        headers = self._decorate(
            {k.decode("latin-1"): v.decode("latin-1") for k, v in response.raw_headers},
            etag,
        )

        if store is not None:
            stored = CachedResponse(
                status_code=response.status_code,
                headers=list(headers.items()),
                body=body,
                media_type=response.media_type,
            )
            await store.set(key, stored, ttl_seconds=self._ttl)

        if if_none_match and _etag_matches(if_none_match, etag):
            return self._not_modified(etag)

        if store is not None:
            headers["x-cache"] = "MISS"
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )


def _skip_caching(response: Response) -> bool:
    """Return whether ``response`` opts out of caching.

    Args:
        response (Response): The downstream response.

    Returns:
        bool: ``True`` when it sets a cookie or declares ``no-store`` /
        ``private`` — such responses are personalized and must not be cached.
    """
    cache_control = response.headers.get("cache-control", "").lower()
    if "no-store" in cache_control or "private" in cache_control:
        return True
    return "set-cookie" in response.headers


async def _drain(response: Response) -> bytes:
    """Concatenate a streamed response body into bytes.

    Args:
        response (Response): A response whose ``body_iterator`` is unread.

    Returns:
        bytes: The full body.
    """
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:  # type: ignore[attr-defined]
        chunks.append(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
    return b"".join(chunks)


__all__: list[str] = [
    "MemoryResponseCacheStore",
    "RedisResponseCacheStore",
    "ResponseCacheMiddleware",
    "ResponseCacheStore",
]
