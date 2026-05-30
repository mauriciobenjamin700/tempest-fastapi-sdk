"""Backend-agnostic fixed-window attempt throttle.

A programmatic counter for "N failed attempts per window per key, then
block" flows — login, OTP, password-reset and security-code
verification. Distinct from
:class:`tempest_fastapi_sdk.api.RateLimitMiddleware` (a blanket per-IP
HTTP limiter): this throttles a specific domain action keyed by
whatever you choose (``f"{event_id}:{ip}"``, ``user_id``, …) and only
counts *failures*, so legitimate use is never penalised.

The backend is injected — anything implementing the async Redis verbs
``incr``/``expire``/``ttl``/``get``/``delete`` works (e.g.
``redis.asyncio.Redis``). When the backend raises and ``fail_open`` is
``True`` (default), the throttle degrades to "allow" rather than locking
users out on a cache outage.

Example:
    throttle = AttemptThrottle(redis, max_attempts=5, window_seconds=900)
    await throttle.raise_if_blocked(key)          # 429 if over budget
    if not await verify(code):
        await throttle.hit(key)                   # count the failure
        raise InvalidCodeError()
    await throttle.reset(key)                     # success clears it
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol

from tempest_fastapi_sdk.exceptions.too_many_requests import (
    TooManyRequestsException,
)


class ThrottleBackend(Protocol):
    """Minimal async key-value contract a throttle backend must satisfy.

    Matches the relevant subset of ``redis.asyncio.Redis``.
    """

    def incr(self, name: str) -> Awaitable[int]:
        """Atomically increment ``name`` and return the new value."""

    def expire(self, name: str, seconds: int) -> Awaitable[Any]:
        """Set a TTL (seconds) on ``name``."""

    def ttl(self, name: str) -> Awaitable[int]:
        """Return remaining TTL in seconds (``-1``/``-2`` when unset)."""

    def get(self, name: str) -> Awaitable[Any]:
        """Return the value at ``name`` (``None`` when absent)."""

    def delete(self, name: str) -> Awaitable[Any]:
        """Delete ``name``."""


@dataclass(frozen=True)
class ThrottleStatus:
    """Outcome of a throttle query.

    Attributes:
        attempts (int): Failures recorded in the current window.
        blocked (bool): Whether the attempt budget is exhausted.
        retry_after_seconds (int): Seconds until the window resets.
            ``0`` when not blocked.
    """

    attempts: int
    blocked: bool
    retry_after_seconds: int


class AttemptThrottle:
    """Fixed-window failure counter over an injected async KV backend."""

    def __init__(
        self,
        backend: ThrottleBackend,
        *,
        max_attempts: int,
        window_seconds: int,
        namespace: str = "throttle",
        fail_open: bool = True,
    ) -> None:
        """Initialize the throttle.

        Args:
            backend (ThrottleBackend): Async KV store (e.g.
                ``redis.asyncio.Redis``).
            max_attempts (int): Failures allowed before a key is
                blocked. Must be ``>= 1``.
            window_seconds (int): Sliding window length (also the TTL
                applied on the first failure). Must be ``> 0``.
            namespace (str): Key prefix so multiple throttles can share
                a backend without colliding.
            fail_open (bool): When ``True`` (default), backend errors
                degrade to "allowed" instead of raising — a cache
                outage must not lock every user out.

        Raises:
            ValueError: If ``max_attempts < 1`` or ``window_seconds <= 0``.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._backend: ThrottleBackend = backend
        self.max_attempts: int = max_attempts
        self.window_seconds: int = window_seconds
        self._namespace: str = namespace
        self._fail_open: bool = fail_open

    def _key(self, key: str) -> str:
        """Return the namespaced backend key for ``key``."""
        return f"{self._namespace}:{key}"

    def _status(self, attempts: int, ttl: int) -> ThrottleStatus:
        """Build a :class:`ThrottleStatus` from a count and TTL."""
        blocked = attempts >= self.max_attempts
        retry = ttl if ttl and ttl > 0 else self.window_seconds
        return ThrottleStatus(
            attempts=attempts,
            blocked=blocked,
            retry_after_seconds=retry if blocked else 0,
        )

    async def status(self, key: str) -> ThrottleStatus:
        """Read the current status for ``key`` without mutating it.

        Args:
            key (str): The domain key (e.g. ``f"{event_id}:{ip}"``).

        Returns:
            ThrottleStatus: Current attempts / blocked state. On a
                backend error with ``fail_open`` set, an empty,
                unblocked status.
        """
        try:
            raw = await self._backend.get(self._key(key))
            attempts = int(raw) if raw is not None else 0
            ttl = await self._backend.ttl(self._key(key)) if attempts else 0
        except Exception:
            if self._fail_open:
                return ThrottleStatus(0, False, 0)
            raise
        return self._status(attempts, ttl)

    async def hit(self, key: str) -> ThrottleStatus:
        """Record one failure for ``key`` and return the new status.

        Increments the counter and, on the first failure of a window,
        applies the TTL so the window expires on its own.

        Args:
            key (str): The domain key.

        Returns:
            ThrottleStatus: Status after the increment. On a backend
                error with ``fail_open`` set, an empty, unblocked status.
        """
        try:
            attempts = await self._backend.incr(self._key(key))
            if attempts == 1:
                await self._backend.expire(self._key(key), self.window_seconds)
            ttl = await self._backend.ttl(self._key(key))
        except Exception:
            if self._fail_open:
                return ThrottleStatus(0, False, 0)
            raise
        return self._status(attempts, ttl)

    async def reset(self, key: str) -> None:
        """Clear the counter for ``key`` (e.g. after a success).

        Args:
            key (str): The domain key.
        """
        try:
            await self._backend.delete(self._key(key))
        except Exception:
            if not self._fail_open:
                raise

    async def raise_if_blocked(
        self,
        key: str,
        *,
        message: str | None = None,
    ) -> ThrottleStatus:
        """Raise :class:`TooManyRequestsException` when ``key`` is blocked.

        Args:
            key (str): The domain key.
            message (str | None): Optional override for the 429 message.

        Returns:
            ThrottleStatus: The (unblocked) status when within budget.

        Raises:
            TooManyRequestsException: When the attempt budget for ``key``
                is exhausted; carries ``Retry-After``.
        """
        current = await self.status(key)
        if current.blocked:
            raise TooManyRequestsException(
                message=message,
                retry_after_seconds=current.retry_after_seconds,
            )
        return current


__all__: list[str] = [
    "AttemptThrottle",
    "ThrottleBackend",
    "ThrottleStatus",
]
