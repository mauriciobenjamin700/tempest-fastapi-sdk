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
``redis.asyncio.Redis``), plus the bundled
:class:`InMemoryThrottleBackend` for a single process and for tests. When
the backend raises and ``fail_open`` is ``True`` (default), the throttle
degrades to "allow" rather than locking users out on a cache outage.

Example:
    throttle = AttemptThrottle(redis, max_attempts=5, window_seconds=900)
    await throttle.raise_if_blocked(key)          # 429 if over budget
    if not await verify(code):
        await throttle.hit(key)                   # count the failure
        raise InvalidCodeError()
    await throttle.reset(key)                     # success clears it
"""

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from tempest_fastapi_sdk.exceptions.too_many_requests import (
    TooManyRequestsException,
)


class ThrottleBackend(Protocol):
    """Minimal async key-value contract a throttle backend must satisfy.

    Matches the relevant subset of ``redis.asyncio.Redis``.
    """

    def incr(self, name: str, /) -> Awaitable[int]:
        """Atomically increment ``name`` and return the new value.

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).
                Positional-only: ``redis-py`` is free to call it whatever
                it likes.

        Returns:
            Awaitable[int]: Resolves to the current attempt count.
        """

    def expire(self, name: str, seconds: int, /) -> Awaitable[object]:
        """Set a TTL (seconds) on ``name``.

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).
            seconds (int): Window length in seconds. Positional-only
                because ``redis.asyncio.Redis.expire`` calls this
                parameter ``time``.

        Returns:
            Awaitable[object]: Resolves once the backend call completes.
                The result is discarded, so any return type is accepted —
                ``redis-py`` resolves ``bool``.
        """

    def ttl(self, name: str, /) -> Awaitable[int]:
        """Return remaining TTL in seconds (``-1``/``-2`` when unset).

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).

        Returns:
            Awaitable[int]: Resolves to the remaining TTL in seconds.
        """

    def get(self, name: str, /) -> Awaitable[str | bytes | None]:
        """Return the value at ``name`` (``None`` when absent).

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).

        Returns:
            Awaitable[str | bytes | None]: Resolves to the stored counter
                as text, or ``None`` when the key is absent. Both halves
                matter: a client with ``decode_responses=True`` resolves
                ``str``, one without resolves ``bytes``.
        """

    def delete(self, name: str, /) -> Awaitable[object]:
        """Delete ``name``.

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).
                Positional-only because ``redis.asyncio.Redis.delete``
                takes ``*names``, which no keyword call can reach.

        Returns:
            Awaitable[object]: Resolves once the backend call completes.
                The result is discarded, so any return type is accepted —
                ``redis-py`` resolves ``int``.
        """


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


class InMemoryThrottleBackend:
    """Process-local :class:`ThrottleBackend` — no Redis, no network.

    Holds one ``(count, expires_at)`` pair per key in a dict and serves the
    five async verbs :class:`AttemptThrottle` calls, with the same return
    contract Redis has: ``ttl`` answers ``-2`` for an absent key and ``-1``
    for one carrying no TTL.

    !!! warning "The counter lives in the process that served the request."
        Two workers each keep their own dict, so the budget an account
        can reach is ``max_attempts * workers``: the bound holds per
        worker, not per account. Anything served by more than one
        process (``uvicorn --workers``, gunicorn, more than one pod) needs a
        shared backend; pass the Redis client to
        :class:`AttemptThrottle` there. Correct for a single-process
        deployment, a dev server, and tests.

    Every ``incr`` first drops the entries whose TTL already passed, so the
    dict stays bounded by the keys touched inside one window and a key
    nobody touches again does not live as long as the process.

    Example:
        >>> backend = InMemoryThrottleBackend()
        >>> throttle = AttemptThrottle(backend, max_attempts=5, window_seconds=900)
        >>> await throttle.hit("mfa:user-id")
        ThrottleStatus(attempts=1, blocked=False, retry_after_seconds=0)
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Initialize an empty backend.

        Args:
            clock (Callable[[], float]): Zero-argument callable returning
                the current time in seconds. Defaults to
                :func:`time.monotonic`.
        """
        self._clock: Callable[[], float] = clock
        self._counters: dict[str, tuple[int, float | None]] = {}

    def _live(self, name: str) -> tuple[int, float | None] | None:
        """Return the entry for ``name``, evicting it once its TTL passed.

        Args:
            name (str): The backend key to read.

        Returns:
            tuple[int, float | None] | None: The ``(count, expires_at)``
            pair still alive under ``name``, or ``None`` when the key is
            absent or expired. ``expires_at`` is ``None`` when no TTL was
            set, which is the Redis ``-1`` case.
        """
        entry = self._counters.get(name)
        if entry is None:
            return None
        expires_at = entry[1]
        if expires_at is not None and expires_at <= self._clock():
            del self._counters[name]
            return None
        return entry

    def _evict_expired(self) -> None:
        """Drop every entry whose TTL already passed.

        Runs on the write path only: a key that stops being touched would
        otherwise stay in the dict for the life of the process.
        """
        now = self._clock()
        for name in [
            name
            for name, (_, expires_at) in self._counters.items()
            if expires_at is not None and expires_at <= now
        ]:
            del self._counters[name]

    async def incr(self, name: str, /) -> int:
        """Atomically increment ``name`` and return the new count.

        The first increment of a window creates the key with no TTL, exactly
        like Redis ``INCR`` — the caller is the one that follows up with
        :meth:`expire`.

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).

        Returns:
            int: The attempt count after the increment.
        """
        self._evict_expired()
        entry = self._live(name)
        count = (entry[0] if entry is not None else 0) + 1
        self._counters[name] = (count, entry[1] if entry is not None else None)
        return count

    async def expire(self, name: str, seconds: int, /) -> None:
        """Set a TTL (seconds) on ``name``.

        A key that is absent or already expired is left absent, which is
        what ``EXPIRE`` does.

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).
            seconds (int): Window length in seconds.
        """
        entry = self._live(name)
        if entry is None:
            return
        self._counters[name] = (entry[0], self._clock() + seconds)

    async def ttl(self, name: str, /) -> int:
        """Return remaining TTL in seconds (``-1``/``-2`` when unset).

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).

        Returns:
            int: ``-2`` when the key is absent, ``-1`` when it carries no
            TTL, and otherwise the seconds left, rounded **up** — a key
            that is still alive never answers ``0``, which the throttle
            reads as "no TTL" and would replace with the full window.
        """
        entry = self._live(name)
        if entry is None:
            return -2
        expires_at = entry[1]
        if expires_at is None:
            return -1
        return math.ceil(expires_at - self._clock())

    async def get(self, name: str, /) -> str | bytes | None:
        """Return the counter at ``name`` as text.

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).

        Returns:
            str | bytes | None: The attempt count as a decimal string, or
            ``None`` when the key is absent or expired. The throttle only
            ever parses it as an ``int``.
        """
        entry = self._live(name)
        if entry is None:
            return None
        return str(entry[0])

    async def delete(self, name: str, /) -> None:
        """Delete ``name``, whether or not it was there.

        Args:
            name (str): Identifier being throttled (an IP, a user, an email).
        """
        self._counters.pop(name, None)


__all__: list[str] = [
    "AttemptThrottle",
    "InMemoryThrottleBackend",
    "ThrottleBackend",
    "ThrottleStatus",
]
