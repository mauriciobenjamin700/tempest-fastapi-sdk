"""Single-firing for a scheduler that runs inside the web process.

Running the periodic scheduler in the FastAPI lifespan is what removes
the second process from a small deployment. It also introduces the one
failure the standalone ``taskiq scheduler`` never had: **every replica
runs its own loop**, so a schedule fires once per replica. Nothing
raises, nothing is logged, and the effect surfaces as duplicated work —
a sweep that expires the same charge three times, a backup taken three
times, a digest e-mail sent three times.

That is not a trade-off with two reasonable answers, so it is not a
warning. One replica must hold a lease and run the loop; the others
stand by and take over when the lease lapses. This module is that lease.

:class:`SchedulerLock` is the contract — three calls, no transport in
it. :class:`RedisSchedulerLock` is the implementation over the lock
``redis-py`` already ships, which handles the two parts a hand-rolled
one gets wrong: the acquire is a single ``SET NX PX`` round trip, and
both renew and release are Lua scripts that verify the caller still owns
the token before touching the key. Renewing a lease someone else now
holds is the bug that turns leader election into two leaders.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Protocol, runtime_checkable

logger = logging.getLogger("tempest_fastapi_sdk.tasks")

DEFAULT_LOCK_NAME = "tempest:tasks:scheduler"
"""Redis key the lease lives under, when the caller names none."""

DEFAULT_LOCK_TTL_SECONDS = 30.0
"""How long a lease survives without renewal.

Long enough that a garbage-collection pause or a slow tick does not
hand the lease to another replica, short enough that a replica killed
mid-tick does not stall the schedule for minutes. The renew loop runs
at a third of this, so two consecutive missed renewals still hold.
"""


@runtime_checkable
class SchedulerLock(Protocol):
    """The three calls leader election needs from a lease.

    A service with its own coordination — etcd, ZooKeeper, a Postgres
    advisory lock — implements this and passes it to
    :meth:`~tempest_fastapi_sdk.tasks.TaskQueue.lifespan`; nothing here
    knows about Redis.

    Every method is a coroutine because acquiring a lease is I/O in
    every implementation worth having.
    """

    async def acquire(self) -> bool:
        """Try to take the lease without waiting.

        Returns:
            bool: ``True`` when this process now holds it, ``False``
            when another one does. Must not block: a replica that
            waited here would delay its own startup behind the leader's
            entire lifetime.
        """
        ...

    async def renew(self) -> bool:
        """Extend a lease this process already holds.

        Returns:
            bool: ``True`` when the lease was extended, ``False`` when
            it was lost — expired, or taken over. ``False`` must stop
            the scheduler loop: a lost lease means another replica is
            already running it.
        """
        ...

    async def release(self) -> None:
        """Give up the lease, if this process still holds it.

        Releasing on shutdown is what lets another replica take over in
        seconds instead of after the TTL. Must tolerate being called
        when the lease was never acquired or was already lost.
        """
        ...


@runtime_checkable
class RedisLockHandle(Protocol):
    """The lock object ``redis-py`` returns from ``Redis.lock``.

    Written positional-only for the required argument on purpose: mypy
    accepts a parameter-name mismatch in protocol compatibility and
    basedpyright rejects it, and the implementer here is a library whose
    parameter names are not ours to keep in sync.
    """

    def acquire(self) -> Awaitable[bool]:
        """Take the lock.

        Returns:
            Awaitable[bool]: Whether the lock was taken.
        """
        ...

    def extend(
        self,
        additional_time: float,
        /,
        *,
        replace_ttl: bool = ...,
    ) -> Awaitable[bool]:
        """Push the expiry out, verifying ownership first.

        Args:
            additional_time (float): Seconds to add, or to set when
                ``replace_ttl`` is true.
            replace_ttl (bool): Replace the remaining TTL rather than
                adding to it, so a long-lived leader's lease does not
                grow without bound.

        Returns:
            Awaitable[bool]: Whether the extension applied.
        """
        ...

    def release(self) -> Awaitable[None]:
        """Delete the key, verifying ownership first.

        Returns:
            Awaitable[None]: Completes once the key is gone.
        """
        ...


@runtime_checkable
class RedisLockClient(Protocol):
    """The one method :class:`RedisSchedulerLock` needs from a client.

    ``redis.asyncio.Redis`` satisfies this, and so does ``fakeredis``'s
    async client — which is what the suite runs against, since a lock is
    only worth testing where two holders can actually contend.
    """

    def lock(
        self,
        name: str,
        /,
        *,
        timeout: float | None = ...,
        blocking: bool = ...,
    ) -> RedisLockHandle:
        """Build a lock object for one key.

        Args:
            name (str): The Redis key the lock lives under.
            timeout (float | None): Lease TTL in seconds.
            blocking (bool): Whether ``acquire`` waits.

        Returns:
            RedisLockHandle: The lock, not yet acquired.
        """
        ...


class RedisSchedulerLock:
    """A :class:`SchedulerLock` over the lock ``redis-py`` ships.

    Example:

        >>> import redis.asyncio as redis
        >>> from tempest_fastapi_sdk.tasks import RedisSchedulerLock
        >>>
        >>> lock: RedisSchedulerLock = RedisSchedulerLock(
        ...     redis.Redis.from_url("redis://localhost:6379/0"),
        ... )

    Attributes:
        name (str): The Redis key the lease lives under.
        ttl_seconds (float): How long the lease survives unrenewed.
    """

    def __init__(
        self,
        client: RedisLockClient,
        *,
        name: str = DEFAULT_LOCK_NAME,
        ttl_seconds: float = DEFAULT_LOCK_TTL_SECONDS,
    ) -> None:
        """Wrap a Redis client as a scheduler lease.

        Args:
            client (RedisLockClient): An async Redis client. Anything
                with ``lock`` fits, which is what lets the suite run
                this against ``fakeredis``.
            name (str): The key to lock. Give two services sharing one
                Redis instance different names, or one of them will
                never schedule.
            ttl_seconds (float): Lease duration. See
                :data:`DEFAULT_LOCK_TTL_SECONDS` for why the default is
                what it is.

        Raises:
            ValueError: When ``ttl_seconds`` is not positive, which
                would make every lease expire on arrival and hand the
                schedule to whichever replica polled last.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than 0.")
        self.name: str = name
        self.ttl_seconds: float = ttl_seconds
        self._lock: RedisLockHandle = client.lock(
            name,
            timeout=ttl_seconds,
            blocking=False,
        )
        self._held: bool = False

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        name: str = DEFAULT_LOCK_NAME,
        ttl_seconds: float = DEFAULT_LOCK_TTL_SECONDS,
    ) -> RedisSchedulerLock:
        """Build the lease from a Redis URL.

        Args:
            url (str): A ``redis://`` / ``rediss://`` URL.
            name (str): The key to lock.
            ttl_seconds (float): Lease duration.

        Returns:
            RedisSchedulerLock: The lease, over a client of its own.

        Raises:
            ImportError: When ``redis`` is not installed, which means
                the ``[cache]`` extra is missing.
        """
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise ImportError(
                "The scheduler lease requires redis. "
                "Install with: pip install tempest-fastapi-sdk[cache]",
            ) from exc
        return cls(
            redis.Redis.from_url(url),
            name=name,
            ttl_seconds=ttl_seconds,
        )

    async def acquire(self) -> bool:
        """Try to take the lease without waiting.

        Returns:
            bool: Whether this process now holds it.
        """
        self._held = await self._lock.acquire()
        return self._held

    async def renew(self) -> bool:
        """Extend the lease this process holds.

        ``replace_ttl`` is set, so the lease is pushed to a fixed
        ``ttl_seconds`` from now rather than accumulating: a leader up
        for a week would otherwise hold a week-long lease, and its death
        would stop the schedule until that ran out.

        A renewal that fails because the transport failed is
        indistinguishable, from here, from one that fails because
        another replica took the lease. Standing down is the safe
        direction in both: a gap of at most one TTL costs a late tick,
        while guessing the other way runs two schedulers. The key is
        released on the way out, so a lease this process still owned
        goes back immediately instead of waiting out the TTL.

        Returns:
            bool: Whether the lease is still held afterwards. ``False``
            once it has been lost, and it stays ``False``.
        """
        if not self._held:
            return False
        try:
            self._held = await self._lock.extend(
                self.ttl_seconds,
                replace_ttl=True,
            )
        except Exception:
            logger.warning(
                "scheduler lease %r could not be renewed; standing down",
                self.name,
                exc_info=True,
            )
            await self.release()
            return False
        if not self._held:
            await self.release()
        return self._held

    async def release(self) -> None:
        """Give up the lease if it is still held.

        A failure here is logged and swallowed: this runs on shutdown,
        and raising would replace whatever actually stopped the process.
        The lease expires on its own within ``ttl_seconds`` anyway.
        """
        if not self._held:
            return
        self._held = False
        try:
            await self._lock.release()
        except Exception:
            logger.warning(
                "scheduler lease %r could not be released; it expires in %.0fs",
                self.name,
                self.ttl_seconds,
                exc_info=True,
            )


__all__: list[str] = [
    "DEFAULT_LOCK_NAME",
    "DEFAULT_LOCK_TTL_SECONDS",
    "RedisLockClient",
    "RedisLockHandle",
    "RedisSchedulerLock",
    "SchedulerLock",
]
