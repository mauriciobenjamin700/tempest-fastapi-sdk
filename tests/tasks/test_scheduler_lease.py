"""One replica runs the schedule, no matter how many serve HTTP.

Running the scheduler inside the FastAPI lifespan is what removes the
second process from a small deployment, and it introduces the failure
the standalone ``taskiq scheduler`` never had: with N replicas, every
schedule fires N times. Nothing raises and nothing is logged — the
effect arrives as duplicated work.

So the property under test is not "the scheduler starts". It is **how
many** schedulers run when two lifespans open against the same lease,
which is why every case here builds two queues instead of one.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import fakeredis.aioredis as fakeredis
import pytest
from taskiq.brokers.inmemory_broker import InMemoryBroker

from tempest_fastapi_sdk.tasks import (
    RedisSchedulerLock,
    SchedulerLock,
    TaskQueue,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class CountingQueue(TaskQueue):
    """A queue that records every scheduler loop it starts.

    Counting the starts is the only way to observe the property: two
    loops that both ran would each fire the schedule, and asserting on
    the schedule's side effect would need a real clock.

    Attributes:
        starts (int): How many times the loop was started here.
        stops (int): How many times it was stopped here.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Wrap :class:`TaskQueue` and zero the counters.

        Args:
            *args (Any): Forwarded to :class:`TaskQueue`.
            **kwargs (Any): Forwarded to :class:`TaskQueue`.
        """
        super().__init__(*args, **kwargs)
        self.starts: int = 0
        self.stops: int = 0

    async def start_scheduler(self) -> asyncio.Task[None]:
        """Record the start and return a loop that does nothing.

        Returns:
            asyncio.Task[None]: A task standing in for the scheduler
            loop, so the supervisor has something real to cancel.
        """
        self.starts += 1

        async def idle() -> None:
            """Stand in for the scheduler's own loop."""
            await asyncio.sleep(3600)

        return asyncio.create_task(idle())

    async def stop_scheduler(self) -> None:
        """Record the stop."""
        self.stops += 1


@pytest.fixture
def leases() -> Callable[[str], SchedulerLock]:
    """Return a factory of leases contending for one Redis key.

    Returns:
        Callable[[str], SchedulerLock]: Builds a lease over a shared
        ``fakeredis`` client, so two of them actually contend. Lua is
        available here (``lupa`` is installed), which matters because
        ``redis-py`` implements renew and release as Lua scripts —
        against a ``fakeredis`` without it, renew raises
        ``ResponseError: unknown command 'evalsha'``.
    """
    client = fakeredis.FakeRedis()

    def build(name: str = "test:lease") -> SchedulerLock:
        """Build one lease over the shared client.

        Args:
            name (str): The key to lock.

        Returns:
            SchedulerLock: The lease.
        """
        return RedisSchedulerLock(client, name=name, ttl_seconds=2.0)

    return build


class TestTheLeaseItself:
    """Acquire, renew and release, with two holders contending."""

    @pytest.mark.asyncio
    async def test_only_one_holder_acquires(
        self,
        leases: Callable[[str], SchedulerLock],
    ) -> None:
        first, second = leases("k"), leases("k")

        assert await first.acquire() is True
        assert await second.acquire() is False

    @pytest.mark.asyncio
    async def test_the_holder_renews_and_a_bystander_does_not(
        self,
        leases: Callable[[str], SchedulerLock],
    ) -> None:
        """A renew that succeeded for a non-holder would be two leaders."""
        first, second = leases("k"), leases("k")
        await first.acquire()
        await second.acquire()

        assert await first.renew() is True
        assert await second.renew() is False

    @pytest.mark.asyncio
    async def test_release_hands_the_lease_over_immediately(
        self,
        leases: Callable[[str], SchedulerLock],
    ) -> None:
        """Without the release, take-over waits out the whole TTL."""
        first, second = leases("k"), leases("k")
        await first.acquire()
        assert await second.acquire() is False

        await first.release()

        assert await second.acquire() is True

    @pytest.mark.asyncio
    async def test_two_names_do_not_contend(
        self,
        leases: Callable[[str], SchedulerLock],
    ) -> None:
        """Two services on one Redis need different keys, or one starves."""
        first, second = leases("service-a"), leases("service-b")

        assert await first.acquire() is True
        assert await second.acquire() is True

    def test_a_non_positive_ttl_is_refused(self) -> None:
        """Every lease would expire on arrival, electing whoever polled."""
        with pytest.raises(ValueError, match="ttl_seconds"):
            RedisSchedulerLock(fakeredis.FakeRedis(), ttl_seconds=0)


class TestOnlyOneReplicaSchedules:
    """The property the whole feature exists for."""

    @pytest.mark.parametrize("replicas", [2, 3, 5])
    @pytest.mark.asyncio
    async def test_n_lifespans_start_exactly_one_scheduler(
        self,
        leases: Callable[[str], SchedulerLock],
        replicas: int,
    ) -> None:
        """This is the N-fold firing, counted.

        Every queue opens a lifespan asking for a scheduler against the
        same lease. Exactly one loop may run, whatever N is: two loops
        fire every schedule twice, three fire it three times, and that
        is the defect the lease exists to prevent. The counts are
        parametrized rather than fixed at two because the docs state the
        property for three replicas.
        """
        queues = [CountingQueue(InMemoryBroker()) for _ in range(replicas)]
        contexts = [
            queue.lifespan(
                scheduler=True,
                scheduler_lock=leases("k"),
                lease_ttl_seconds=2.0,
            )
            for queue in queues
        ]
        for context in contexts:
            await context.__aenter__()
        try:
            await asyncio.sleep(0.2)
            running = sum(queue.starts for queue in queues)
        finally:
            for context in reversed(contexts):
                await context.__aexit__(None, None, None)

        assert running == 1, (
            f"expected exactly one scheduler loop across {replicas} replicas, "
            f"got {running}: {[queue.starts for queue in queues]}"
        )

    @pytest.mark.asyncio
    async def test_unlocked_fires_once_per_replica(self) -> None:
        """The number the guarded mode exists to avoid, measured.

        Three replicas, three loops. Asserting it here is what makes the
        danger admonition in the recipe a fact about this package rather
        than an expectation about schedulers in general.
        """
        queues = [CountingQueue(InMemoryBroker()) for _ in range(3)]
        contexts = [queue.lifespan(scheduler="unlocked") for queue in queues]
        for context in contexts:
            await context.__aenter__()
        try:
            await asyncio.sleep(0.1)
            running = sum(queue.starts for queue in queues)
        finally:
            for context in reversed(contexts):
                await context.__aexit__(None, None, None)

        assert running == 3

    @pytest.mark.asyncio
    async def test_the_standby_takes_over_when_the_holder_leaves(
        self,
        leases: Callable[[str], SchedulerLock],
    ) -> None:
        """A leader that leaves must not stop the schedule for good.

        The contexts are entered and exited by hand rather than nested,
        because the lease goes to whoever asks first and a nested
        ``async with`` always exits the innermost — the opposite of the
        order this case needs.
        """
        holder = CountingQueue(InMemoryBroker())
        standby = CountingQueue(InMemoryBroker())
        ttl = 0.9

        holder_cm = holder.lifespan(
            scheduler=True,
            scheduler_lock=leases("k"),
            lease_ttl_seconds=ttl,
        )
        standby_cm = standby.lifespan(
            scheduler=True,
            scheduler_lock=leases("k"),
            lease_ttl_seconds=ttl,
        )

        await holder_cm.__aenter__()
        try:
            await standby_cm.__aenter__()
            try:
                await asyncio.sleep(ttl / 3)
                assert holder.starts == 1
                assert standby.starts == 0

                await holder_cm.__aexit__(None, None, None)
                await asyncio.sleep(ttl)

                assert standby.starts == 1
            finally:
                await standby_cm.__aexit__(None, None, None)
        except BaseException:
            await holder_cm.__aexit__(None, None, None)
            raise

    @pytest.mark.asyncio
    async def test_unlocked_is_spelled_out_and_runs_everywhere(self) -> None:
        """The unsafe mode exists, and cannot be reached by accident.

        ``scheduler=True`` is the guarded default and ``"unlocked"`` has
        to be typed, because a service that genuinely runs one replica
        is making a choice and a service that forgot is not.
        """
        first = CountingQueue(InMemoryBroker())
        second = CountingQueue(InMemoryBroker())

        async with (
            first.lifespan(scheduler="unlocked"),
            second.lifespan(scheduler="unlocked"),
        ):
            await asyncio.sleep(0.05)

        assert first.starts == 1
        assert second.starts == 1

    @pytest.mark.asyncio
    async def test_no_scheduler_by_default(self) -> None:
        """The pre-v0.282.0 behavior of ``lifespan()`` is unchanged."""
        queue = CountingQueue(InMemoryBroker())

        async with queue.lifespan():
            await asyncio.sleep(0.05)

        assert queue.starts == 0

    @pytest.mark.asyncio
    async def test_the_memory_broker_needs_no_lease(self) -> None:
        """One process by construction, so there is nothing to elect."""
        queue = CountingQueue(InMemoryBroker())

        async with queue.lifespan(scheduler=True):
            await asyncio.sleep(0.05)

        assert queue.starts == 1

    @pytest.mark.asyncio
    async def test_a_broker_with_no_derivable_lease_refuses(self) -> None:
        """Guessing here is what ships the N-fold firing.

        A broker built by hand carries no Redis URL, so the lease cannot
        be derived. Starting anyway would be the defect; the error names
        the three ways forward.
        """

        class Unknown(InMemoryBroker):
            """A broker the lease cannot be derived from."""

        queue = TaskQueue(Unknown())

        with pytest.raises(ValueError, match="needs a lease"):
            async with queue.lifespan(scheduler=True):
                pass
