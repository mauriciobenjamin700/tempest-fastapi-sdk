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
import time
from types import SimpleNamespace
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

DEADLINE_SECONDS: float = 10.0
"""How long a poll waits for the supervisors before the case fails.

Generous on purpose: it bounds a failing run, not a passing one, which
returns as soon as the condition holds. A fixed ``sleep`` sized for a
laptop is what made the count flaky on a loaded CI runner.
"""


async def wait_until(
    condition: Callable[[], bool],
    *,
    timeout: float = DEADLINE_SECONDS,
    interval: float = 0.01,
) -> bool:
    """Poll ``condition`` until it holds or the deadline passes.

    The supervisors elect in background tasks, so ``__aenter__`` returns
    before any of them has asked for the lease. Waiting on the state
    itself, instead of on the clock, is what keeps the count independent
    of how busy the machine is.

    Args:
        condition (Callable[[], bool]): The state to wait for.
        timeout (float): Seconds before giving up.
        interval (float): Seconds between checks.

    Returns:
        bool: ``True`` when the condition held before the deadline.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval)
    return True


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
def steady_fakeredis_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expire ``fakeredis`` keys on a clock that never steps.

    ``fakeredis`` stamps every command with ``time.time()`` and expires
    keys against it, so a step of the wall clock ages every lease at
    once. On a WSL2 host the wall clock stepped forward by 3.585 s twice
    in 60 s of sampling, idle or loaded, while ``time.monotonic()``
    advanced evenly. A step that size outlasts the 2 s lease: the holder
    loses it with no stall anywhere, a standby takes over, and the case
    counts two loops that the election never ran concurrently. Measured
    under load, that was every failure left once the fixed sleeps were
    gone.

    A real Redis expires against its own clock too, so a stepped server
    clock shortens a real lease the same way. That is a property of
    leases, not of the election under test, so the clock is pinned here
    — anchored to the current epoch, advancing at monotonic pace.

    Args:
        monkeypatch (pytest.MonkeyPatch): Restores the module afterwards.
            ``raising`` stays on, so a ``fakeredis`` that moves its clock
            elsewhere fails here instead of silently going unpinned.
    """
    offset = time.time() - time.monotonic()
    monkeypatch.setattr(
        "fakeredis._basefakesocket.time",
        SimpleNamespace(time=lambda: time.monotonic() + offset),
    )


@pytest.fixture
def leases(steady_fakeredis_clock: None) -> Callable[[str], SchedulerLock]:
    """Return a factory of leases contending for one Redis key.

    Args:
        steady_fakeredis_clock (None): Pins the key-expiry clock, so a
            wall-clock step cannot expire a lease mid-case.

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


class RecordingLock:
    """A lease that records every answer it gave, around a real one.

    The records are what the polls wait on: a replica that has been
    refused twice has already had its chance to start a second loop, so
    counting then measures the election rather than the scheduler.

    Attributes:
        acquired (list[bool]): Every ``acquire`` answer, in order.
        renewed (list[bool]): Every ``renew`` answer, in order.
    """

    def __init__(self, inner: SchedulerLock) -> None:
        """Wrap ``inner`` with empty records.

        Args:
            inner (SchedulerLock): The lease that actually contends.
        """
        self._inner: SchedulerLock = inner
        self.acquired: list[bool] = []
        self.renewed: list[bool] = []

    @property
    def answers(self) -> int:
        """Return how many ``acquire`` and ``renew`` calls completed.

        Returns:
            int: Completed calls, whichever they were.
        """
        return len(self.acquired) + len(self.renewed)

    async def acquire(self) -> bool:
        """Try to take the lease, and record the answer.

        The answer is recorded and returned with no ``await`` in between,
        and the supervisor starts the loop without one either — so a
        poll never observes a granted lease whose loop has not started.

        Returns:
            bool: The inner lease's answer.
        """
        granted = await self._inner.acquire()
        self.acquired.append(granted)
        return granted

    async def renew(self) -> bool:
        """Extend the lease, and record the answer.

        Returns:
            bool: The inner lease's answer.
        """
        renewed = await self._inner.renew()
        self.renewed.append(renewed)
        return renewed

    async def release(self) -> None:
        """Give the lease up."""
        await self._inner.release()


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

        The count is taken once every lease has answered twice: the
        holder's acquire and first renew, and two refusals for each
        standby — a whole poll round after the election, in which a
        second loop would have started. The starts are read into one
        list at that instant, so the failure message reports the same
        state the assertion judged.
        """
        queues = [CountingQueue(InMemoryBroker()) for _ in range(replicas)]
        locks = [RecordingLock(leases("k")) for _ in range(replicas)]
        contexts = [
            queue.lifespan(
                scheduler=True,
                scheduler_lock=lock,
                lease_ttl_seconds=2.0,
            )
            for queue, lock in zip(queues, locks, strict=True)
        ]
        for context in contexts:
            await context.__aenter__()
        try:
            settled = await wait_until(
                lambda: all(lock.answers >= 2 for lock in locks),
            )
            starts = [queue.starts for queue in queues]
        finally:
            for context in reversed(contexts):
                await context.__aexit__(None, None, None)

        assert settled, (
            f"the leases did not all answer twice in {DEADLINE_SECONDS}s: "
            f"{[lock.answers for lock in locks]}"
        )
        assert sum(starts) == 1, (
            f"expected exactly one scheduler loop across {replicas} replicas, "
            f"got {sum(starts)}: {starts}"
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

        The standby opens only once the holder's loop runs, so which one
        leads is decided by the case and not by task scheduling. After
        the holder leaves, the standby must take over on its first
        ``acquire`` that began after the release: at most one refusal
        (an attempt already in flight during the release) may follow it.
        """
        holder = CountingQueue(InMemoryBroker())
        standby = CountingQueue(InMemoryBroker())
        standby_lock = RecordingLock(leases("k"))
        ttl = 0.9

        holder_cm = holder.lifespan(
            scheduler=True,
            scheduler_lock=leases("k"),
            lease_ttl_seconds=ttl,
        )
        standby_cm = standby.lifespan(
            scheduler=True,
            scheduler_lock=standby_lock,
            lease_ttl_seconds=ttl,
        )

        await holder_cm.__aenter__()
        try:
            assert await wait_until(lambda: holder.starts == 1)
            await standby_cm.__aenter__()
            try:
                assert await wait_until(lambda: len(standby_lock.acquired) >= 1)
                assert holder.starts == 1
                assert standby.starts == 0

                await holder_cm.__aexit__(None, None, None)
                released_at = len(standby_lock.acquired)

                assert await wait_until(lambda: standby.starts == 1), (
                    f"the standby never took over: {standby_lock.acquired}"
                )
                after_release = standby_lock.acquired[released_at:]
                assert after_release[-1] is True
                assert len(after_release) <= 2, after_release
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


class CancelSwallowingLock:
    """A lease whose ``renew`` swallows the cancel, as a dependency can.

    Stands in for ``fakeredis`` on CPython 3.11, where ``asyncio.wait_for``
    returns the result when the inner future completes in the same tick
    as the outer cancel. The swallow here is deterministic, and happens
    once — as the race does: the first cancelled renew returns ``True``,
    later ones raise. Swallowing every cancel would make the pre-fix code
    hang the test run instead of failing it.

    Attributes:
        swallowed (int): How many cancels ``renew`` absorbed.
        renewing (asyncio.Event): Set once a ``renew`` is under way, so
            the cases cancel inside it rather than after a fixed sleep
            that a loaded runner can outlast or undershoot.
    """

    def __init__(self) -> None:
        """Start with no cancel absorbed."""
        self.swallowed: int = 0
        self.renewing: asyncio.Event = asyncio.Event()

    async def acquire(self) -> bool:
        """Take the lease.

        Returns:
            bool: Always ``True``.
        """
        return True

    async def renew(self) -> bool:
        """Wait, and absorb the first cancel that arrives.

        Returns:
            bool: Always ``True``.
        """
        self.renewing.set()
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            if self.swallowed:
                raise
            self.swallowed += 1
        return True

    async def release(self) -> None:
        """Give the lease up."""


class TestShutdownSurvivesASwallowedCancel:
    """The CI hang: a dependency ate the supervisor's ``CancelledError``."""

    @pytest.mark.asyncio
    async def test_lifespan_exit_finishes(self) -> None:
        lock = CancelSwallowingLock()
        queue = CountingQueue(InMemoryBroker())
        context = queue.lifespan(
            scheduler=True,
            scheduler_lock=lock,
            lease_ttl_seconds=0.03,
        )
        await context.__aenter__()
        await asyncio.wait_for(lock.renewing.wait(), timeout=DEADLINE_SECONDS)

        exit_task = asyncio.create_task(context.__aexit__(None, None, None))
        done, _ = await asyncio.wait({exit_task}, timeout=2)

        assert exit_task in done, "the lifespan exit hung on the supervisor"
        assert lock.swallowed == 1

    @pytest.mark.asyncio
    async def test_a_cancelled_exit_is_not_reported_as_finished(self) -> None:
        """``suppress(CancelledError)`` used to absorb the caller's own cancel."""
        lock = CancelSwallowingLock()
        queue = CountingQueue(InMemoryBroker())
        context = queue.lifespan(
            scheduler=True,
            scheduler_lock=lock,
            lease_ttl_seconds=0.03,
        )
        await context.__aenter__()
        await asyncio.wait_for(lock.renewing.wait(), timeout=DEADLINE_SECONDS)

        exit_task = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.sleep(0)
        exit_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await exit_task
        assert queue.is_connected is False
