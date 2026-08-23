"""Sliding-window rate limit middleware with pluggable stores and keys.

The middleware counts requests per *key* inside a sliding window and
rejects excess traffic with ``429 Too Many Requests`` + ``Retry-After``.
Two axes are pluggable:

* **Store** — where the counters live. :class:`MemoryRateLimitStore`
  (default, in-process) is fine for a single worker; pass
  :class:`RedisRateLimitStore` to share state across replicas via an
  atomic Lua sliding-window log.
* **Key** — *who* a request counts against. The default keys on the
  client IP; the ``key_by_*`` factories key on the authenticated user
  (``sub`` claim), an arbitrary JWT claim (e.g. ``tenant_id``) or a
  header value (e.g. an API key), each falling back to the IP for
  anonymous traffic.
* **Policy** — *which* limits apply. Passing ``policy=`` swaps the
  single sliding window for a list of
  :class:`~tempest_fastapi_sdk.api.middlewares.quota.RateLimitRule`
  resolved per request, which is what token buckets and per-plan quotas
  need. See :mod:`tempest_fastapi_sdk.api.middlewares.quota`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from tempest_fastapi_sdk.api.middlewares.quota import (
    MemoryQuotaStore,
    QuotaStore,
    RateLimitPolicy,
)
from tempest_fastapi_sdk.utils.client_ip import get_client_ip

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a single rate-limit check.

    Attributes:
        allowed (bool): ``True`` when the request fits under the limit.
        remaining (int): Requests still allowed in the current window
            (``0`` when rejected).
        retry_after (int): Seconds the caller should wait before
            retrying (``0`` when allowed). Always ``>= 1`` on rejection.
    """

    allowed: bool
    remaining: int
    retry_after: int


@runtime_checkable
class RateLimitStore(Protocol):
    """Backend that counts hits per key inside a sliding window."""

    async def hit(
        self,
        key: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Register one hit for ``key`` and report whether it is allowed.

        Args:
            key (str): The rate-limit bucket key.
            max_requests (int): Maximum hits allowed in the window.
            window_seconds (float): Sliding-window length in seconds.

        Returns:
            RateLimitResult: The decision for this hit.
        """
        ...


class FailOpenRateLimitStore:
    """Wrap a store so that an outage lets requests through.

    Measured on 2026-08-23: with a store whose ``hit`` raises, the
    exception propagates out of :class:`RateLimitMiddleware` and the caller
    gets a 500. For most endpoints that is defensible — a limiter that
    cannot count is a limiter that cannot protect. For some it is exactly
    backwards.

    The clearest case is an error-reporting endpoint: the moment the
    counter store is unwell is the moment errors spike, and rejecting the
    reports then destroys the evidence of the incident being reported.
    Losing the report is worse than serving traffic above the ceiling.

    This wrapper makes that trade explicit at the call site instead of
    leaving it to whether Redis happens to be up:

    ```python
    store = FailOpenRateLimitStore(RedisRateLimitStore(redis))
    ```

    It does **not** swallow the outage silently — every failure is logged
    at ``WARNING`` with the key, so "the ceiling is not being enforced"
    stays visible in the logs.

    Attributes:
        inner (RateLimitStore): The store doing the real counting.
    """

    def __init__(self, inner: RateLimitStore) -> None:
        """Wrap a counter store.

        Args:
            inner (RateLimitStore): The store to guard.
        """
        self.inner: RateLimitStore = inner

    async def hit(
        self,
        key: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Count one hit, allowing the request when counting fails.

        Args:
            key (str): The rate-limit bucket key.
            max_requests (int): Maximum hits allowed in the window.
            window_seconds (float): Sliding-window length in seconds.

        Returns:
            RateLimitResult: The inner store's decision, or an allowing
            result when the inner store raised.
        """
        try:
            return await self.inner.hit(key, max_requests, window_seconds)
        except Exception as exc:
            logger.warning(
                "Rate limit not enforced for %r: counter store failed (%s)",
                key,
                exc,
            )
            return RateLimitResult(allowed=True, remaining=max_requests, retry_after=0)


class MemoryRateLimitStore:
    """In-process sliding-window store backed by per-key timestamp logs.

    State lives in this worker's memory only — correct for a single
    process (dev, or one reverse-proxy worker). For multi-replica
    deployments use :class:`RedisRateLimitStore` so every worker shares
    the same counters.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._buckets: dict[str, deque[float]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def hit(
        self,
        key: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Register a hit and prune timestamps older than the window.

        Args:
            key (str): The rate-limit bucket key.
            max_requests (int): Maximum hits allowed in the window.
            window_seconds (float): Sliding-window length in seconds.

        Returns:
            RateLimitResult: The decision for this hit.
        """
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= max_requests:
                retry_after = max(1, math.ceil(bucket[0] + window_seconds - now))
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                )
            bucket.append(now)
            return RateLimitResult(
                allowed=True,
                remaining=max_requests - len(bucket),
                retry_after=0,
            )


@runtime_checkable
class RedisLike(Protocol):
    """Minimal async Redis surface used by :class:`RedisRateLimitStore`.

    Matches the relevant subset of ``redis.asyncio.Redis``.
    """

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Awaitable[Any]:
        """Evaluate a Lua ``script`` server-side."""
        ...


# Atomic sliding-window log: drop expired members, count, and only add
# the new member when still under the limit. Returns
# ``{allowed, remaining, retry_after_ms}``.
_SLIDING_WINDOW_LUA: str = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window)
  return {1, limit - count - 1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry = window
if oldest[2] then
  retry = (tonumber(oldest[2]) + window) - now
end
if retry < 1 then retry = 1 end
return {0, 0, retry}
"""


class RedisRateLimitStore:
    """Distributed sliding-window store backed by a Redis sorted set.

    Every key maps to a sorted set whose members are individual request
    timestamps (in milliseconds). A single Lua script prunes expired
    members, counts the survivors and conditionally adds the new hit, so
    the check is atomic across replicas — no race between count and add.

    When the backend raises and ``fail_open`` is ``True`` (the default),
    the request is allowed rather than locking every caller out on a
    transient Redis outage.
    """

    def __init__(
        self,
        redis: RedisLike,
        *,
        namespace: str = "ratelimit",
        fail_open: bool = True,
    ) -> None:
        """Initialize the store.

        Args:
            redis (RedisLike): Async Redis client (e.g.
                ``redis.asyncio.Redis``).
            namespace (str): Prefix for every Redis key.
            fail_open (bool): Allow the request when the backend errors.
        """
        self._redis: RedisLike = redis
        self._namespace: str = namespace
        self._fail_open: bool = fail_open

    def _key(self, key: str) -> str:
        """Return the namespaced Redis key for ``key``."""
        return f"{self._namespace}:{key}"

    async def hit(
        self,
        key: str,
        max_requests: int,
        window_seconds: float,
    ) -> RateLimitResult:
        """Register a hit atomically via the sliding-window Lua script.

        Args:
            key (str): The rate-limit bucket key.
            max_requests (int): Maximum hits allowed in the window.
            window_seconds (float): Sliding-window length in seconds.

        Returns:
            RateLimitResult: The decision for this hit. On a backend
            error this is ``allowed`` when ``fail_open`` is set.

        Raises:
            Exception: Propagates the backend error when ``fail_open``
                is ``False``.
        """
        now_ms = int(time.time() * 1000)
        window_ms = int(window_seconds * 1000)
        member = uuid.uuid4().hex
        try:
            raw: list[int] = await self._redis.eval(
                _SLIDING_WINDOW_LUA,
                1,
                self._key(key),
                now_ms,
                window_ms,
                max_requests,
                member,
            )
        except Exception:
            if self._fail_open:
                return RateLimitResult(
                    allowed=True,
                    remaining=max_requests - 1,
                    retry_after=0,
                )
            raise
        allowed = bool(raw[0])
        remaining = int(raw[1])
        retry_after = 0 if allowed else max(1, math.ceil(int(raw[2]) / 1000))
        return RateLimitResult(
            allowed=allowed,
            remaining=remaining,
            retry_after=retry_after,
        )


@runtime_checkable
class _JWTDecoder(Protocol):
    """Minimal JWT decoder surface used by the ``key_by_jwt_*`` helpers.

    Matches :meth:`tempest_fastapi_sdk.utils.JWTUtils.decode_or_none`.
    """

    def decode_or_none(self, token: str) -> dict[str, Any] | None:
        """Decode a token, returning ``None`` when it is missing/invalid.

        Args:
            token (str): The bearer token to decode.

        Returns:
            dict[str, Any] | None: The decoded claims, or ``None`` when the
                token is absent or undecodable.
        """
        ...


def _bearer_token(request: Request) -> str | None:
    """Extract the bearer token from the ``Authorization`` header.

    Args:
        request (Request): The inbound request.

    Returns:
        str | None: The token, or ``None`` when no bearer is present.
    """
    header = request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def key_by_ip(*, trusted_header: str | None = None) -> Callable[[Request], str]:
    """Build a key function that buckets by client IP.

    Args:
        trusted_header (str | None): Single edge-set header to resolve
            the real client IP from (e.g. ``"x-real-ip"``). ``None``
            uses the transport peer only.

    Returns:
        Callable[[Request], str]: A key function yielding ``"ip:<addr>"``.
    """

    def _key(request: Request) -> str:
        return f"ip:{get_client_ip(request, trusted_header=trusted_header)}"

    return _key


def key_by_header(
    header_name: str,
    *,
    scope: str = "key",
    fallback_to_ip: bool = True,
    trusted_ip_header: str | None = None,
) -> Callable[[Request], str]:
    """Build a key function that buckets by a request header value.

    Useful for per-API-key limits (e.g. ``header_name="x-api-key"``).

    Args:
        header_name (str): Header whose value is the bucket key.
        scope (str): Prefix for the key (keeps namespaces from
            colliding, e.g. ``"apikey:<value>"``).
        fallback_to_ip (bool): When the header is absent, key by client
            IP instead of collapsing every anonymous caller into one
            bucket.
        trusted_ip_header (str | None): Header to resolve the client IP
            from when falling back.

    Returns:
        Callable[[Request], str]: The key function.
    """

    def _key(request: Request) -> str:
        value = request.headers.get(header_name)
        if value:
            return f"{scope}:{value.strip()}"
        if fallback_to_ip:
            return f"ip:{get_client_ip(request, trusted_header=trusted_ip_header)}"
        return f"{scope}:anonymous"

    return _key


def key_by_jwt_claim(
    jwt: _JWTDecoder,
    claim: str,
    *,
    scope: str | None = None,
    fallback_to_ip: bool = True,
    trusted_ip_header: str | None = None,
) -> Callable[[Request], str]:
    """Build a key function that buckets by a claim in the bearer token.

    The token is decoded opportunistically (no exception on a
    missing/invalid token) — anonymous traffic falls back to the client
    IP so it is still limited.

    Args:
        jwt (_JWTDecoder): A decoder exposing
            ``decode_or_none(token) -> dict | None`` (e.g.
            :class:`tempest_fastapi_sdk.utils.JWTUtils`).
        claim (str): Claim whose value is the bucket key (e.g.
            ``"tenant_id"``).
        scope (str | None): Prefix for the key. Defaults to ``claim``.
        fallback_to_ip (bool): Key by client IP when no valid token
            carries the claim.
        trusted_ip_header (str | None): Header to resolve the client IP
            from when falling back.

    Returns:
        Callable[[Request], str]: The key function.
    """
    label = scope or claim

    def _key(request: Request) -> str:
        token = _bearer_token(request)
        if token:
            claims = jwt.decode_or_none(token)
            if claims is not None:
                value = claims.get(claim)
                if value is not None:
                    return f"{label}:{value}"
        if fallback_to_ip:
            return f"ip:{get_client_ip(request, trusted_header=trusted_ip_header)}"
        return f"{label}:anonymous"

    return _key


def key_by_jwt_subject(
    jwt: _JWTDecoder,
    *,
    fallback_to_ip: bool = True,
    trusted_ip_header: str | None = None,
) -> Callable[[Request], str]:
    """Build a key function that buckets by the JWT ``sub`` claim.

    Convenience wrapper over :func:`key_by_jwt_claim` for per-user
    limits, yielding keys like ``"user:<sub>"``.

    Args:
        jwt (_JWTDecoder): A decoder exposing ``decode_or_none``.
        fallback_to_ip (bool): Key by client IP for anonymous traffic.
        trusted_ip_header (str | None): Header to resolve the client IP
            from when falling back.

    Returns:
        Callable[[Request], str]: The key function.
    """
    return key_by_jwt_claim(
        jwt,
        "sub",
        scope="user",
        fallback_to_ip=fallback_to_ip,
        trusted_ip_header=trusted_ip_header,
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiter with pluggable store, key and limit policy.

    Each unique key (by default the client IP) is allowed at most
    ``max_requests`` requests inside every ``window_seconds`` window.
    Excess requests are rejected with ``429 Too Many Requests`` and a
    ``Retry-After`` header.

    By default counting happens in-process (:class:`MemoryRateLimitStore`)
    — fine for a single worker. Pass ``store=RedisRateLimitStore(redis)``
    to share counters across replicas. Pass a ``key_func`` (e.g.
    :func:`key_by_jwt_subject`) to limit per authenticated principal
    instead of per IP.

    **Token buckets and per-plan quotas.** ``max_requests`` /
    ``window_seconds`` describe a single sliding window. Pass ``policy=``
    (a :class:`~tempest_fastapi_sdk.api.middlewares.quota.RateLimitPolicy`)
    to apply a *list* of limits resolved per request instead — a token
    bucket, a per-minute limit stacked under a daily ceiling, or a
    different list per plan. The two are alternatives: with ``policy=``
    set, ``max_requests`` / ``window_seconds`` / ``store`` are unused,
    and the counters live in ``quota_store``.

    **Running behind a proxy:** the default IP key is the direct
    transport peer, which is the *proxy* once a reverse proxy fronts the
    app — so every client collapses into one bucket. Pass
    ``trusted_ip_header`` (e.g. ``"x-real-ip"``) naming a header your
    edge sets from its own connection (never a client-supplied
    ``X-Forwarded-For``, which is spoofable). See
    :func:`tempest_fastapi_sdk.utils.get_client_ip`.

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
        trusted_ip_header: str | None = None,
        store: RateLimitStore | None = None,
        policy: RateLimitPolicy | None = None,
        quota_store: QuotaStore | None = None,
        exempt_paths: tuple[str, ...] = (),
        retry_after_header: bool = True,
        limit_headers: bool = True,
        error_message: str = "Too many requests",
    ) -> None:
        """Initialize the middleware.

        Args:
            app (ASGIApp): The underlying ASGI app.
            max_requests (int): Maximum requests per window.
            window_seconds (float): Window length in seconds.
            key_func (Callable[[Request], str] | None): Build a
                rate-limit key from the request. Overrides
                ``trusted_ip_header``. Defaults to the resolved client
                IP. See the ``key_by_*`` factories for per-user /
                per-tenant / per-API-key strategies.
            trusted_ip_header (str | None): When set (and ``key_func``
                is not), the rate-limit key is the client IP resolved
                from this single edge-set header (e.g. ``"x-real-ip"``),
                falling back to the transport peer. ``None`` keys on the
                transport peer only — correct only when the app is not
                behind a proxy.
            store (RateLimitStore | None): Counter backend for the
                single-window mode. Defaults to an in-process
                :class:`MemoryRateLimitStore`; pass
                :class:`RedisRateLimitStore` for multi-replica deploys.
                Unused when ``policy`` is set.
            policy (RateLimitPolicy | None): Resolves a *list* of limits
                per request — token buckets, stacked windows, per-plan
                quotas. See
                :class:`~tempest_fastapi_sdk.api.middlewares.quota.PlanRateLimitPolicy`
                and
                :class:`~tempest_fastapi_sdk.api.middlewares.quota.StaticRateLimitPolicy`.
                ``None`` (default) keeps the single-window behavior.
            quota_store (QuotaStore | None): Counter backend for the
                policy mode. Defaults to an in-process
                :class:`~tempest_fastapi_sdk.api.middlewares.quota.MemoryQuotaStore`;
                pass
                :class:`~tempest_fastapi_sdk.api.middlewares.quota.RedisQuotaStore`
                for multi-replica deploys. Ignored without ``policy``.
            exempt_paths (tuple[str, ...]): Paths to skip entirely
                (e.g. ``("/health/liveness", "/health/readiness")``).
            retry_after_header (bool): Whether to add a
                ``Retry-After`` header on 429 responses.
            limit_headers (bool): Whether to advertise the limit with
                ``RateLimit-Limit`` / ``RateLimit-Remaining`` /
                ``RateLimit-Reset``. ``RateLimit-Reset`` is emitted only
                when its value is known: always in policy mode, and on
                429 in single-window mode — the sliding-window store
                does not report a reset for an accepted request, and a
                guessed number is worse than an absent header.
            error_message (str): Body of the 429 response.

        Raises:
            ValueError: If ``max_requests`` < 1, ``window_seconds`` <= 0,
                or both ``policy`` and ``store`` are passed (the two
                select different counter backends, so honoring one would
                silently ignore the other).
        """
        super().__init__(app)
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if policy is not None and store is not None:
            raise ValueError(
                "pass either store= (single-window mode) or policy= with "
                "quota_store= (policy mode), not both",
            )
        self.max_requests: int = max_requests
        self.window_seconds: float = window_seconds
        if key_func is not None:
            self._key_func: Callable[[Request], str] = key_func
        else:
            self._key_func = lambda request: get_client_ip(
                request,
                trusted_header=trusted_ip_header,
            )
        self._policy: RateLimitPolicy | None = policy
        self._store: RateLimitStore = store or MemoryRateLimitStore()
        self._quota_store: QuotaStore = quota_store or MemoryQuotaStore()
        self._exempt: frozenset[str] = frozenset(exempt_paths)
        self._retry_after_header: bool = retry_after_header
        self._limit_headers: bool = limit_headers
        self._error_message: str = error_message

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
        if self._policy is not None:
            quota = await self._quota_store.consume(
                key,
                self._policy.rules_for(request),
            )
            allowed = quota.allowed
            retry_after = quota.retry_after
            limit = quota.limit
            remaining = quota.remaining
            reset: int | None = quota.reset_seconds
        else:
            result = await self._store.hit(key, self.max_requests, self.window_seconds)
            allowed = result.allowed
            retry_after = result.retry_after
            limit = self.max_requests
            remaining = result.remaining
            reset = None if allowed else result.retry_after

        headers: dict[str, str] = {}
        if self._limit_headers:
            headers["RateLimit-Limit"] = str(limit)
            headers["RateLimit-Remaining"] = str(remaining)
            if reset is not None:
                headers["RateLimit-Reset"] = str(reset)

        if not allowed:
            if self._retry_after_header:
                headers["Retry-After"] = str(retry_after)
            return Response(
                content=self._error_message,
                status_code=429,
                media_type="text/plain",
                headers=headers,
            )

        response: Response = await call_next(request)
        response.headers.update(headers)
        return response


__all__: list[str] = [
    "MemoryRateLimitStore",
    "RateLimitMiddleware",
    "RateLimitResult",
    "RateLimitStore",
    "RedisRateLimitStore",
    "key_by_header",
    "key_by_ip",
    "key_by_jwt_claim",
    "key_by_jwt_subject",
]
