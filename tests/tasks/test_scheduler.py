"""Tests for the TaskIQ-backed AsyncTaskScheduler."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

import pytest
from taskiq import InMemoryBroker
from taskiq.schedule_sources import LabelScheduleSource

from tempest_fastapi_sdk.tasks import (
    AsyncTaskScheduler,
    CronOffset,
    TaskQueue,
)


class TestAsyncTaskScheduler:
    """Validate the TaskIQ scheduler lifecycle wrapper."""

    async def test_connect_is_idempotent(self) -> None:
        """connect() called twice keeps the scheduler started exactly once."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        await scheduler.connect()
        await scheduler.connect()
        assert scheduler.is_connected is True
        await scheduler.disconnect()

    async def test_disconnect_before_connect_is_noop(self) -> None:
        """disconnect() is safe even before any connect()."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        await scheduler.disconnect()
        assert scheduler.is_connected is False

    async def test_default_source_is_label_source(self) -> None:
        """When no sources are passed, a LabelScheduleSource is created."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        assert len(scheduler.sources) == 1
        assert isinstance(scheduler.sources[0], LabelScheduleSource)

    async def test_cron_decorator_attaches_schedule_label(self) -> None:
        """@scheduler.cron(expr) attaches a cron schedule label to the task."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())

        @scheduler.cron("*/5 * * * *")
        async def heartbeat() -> str:
            return "ok"

        schedule = heartbeat.labels.get("schedule")
        assert schedule == [{"cron": "*/5 * * * *"}]

    async def test_cron_decorator_respects_offset(self) -> None:
        """cron_offset is forwarded to the schedule entry."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        offset = timedelta(hours=-3)

        @scheduler.cron("0 9 * * *", cron_offset=offset)
        async def daily() -> None:
            return None

        schedule = daily.labels["schedule"]
        assert schedule[0]["cron"] == "0 9 * * *"
        assert schedule[0]["cron_offset"] == offset

    async def test_interval_decorator_uses_timedelta(self) -> None:
        """@scheduler.interval(seconds=N) builds a timedelta-based schedule."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())

        @scheduler.interval(seconds=30)
        async def poll() -> None:
            return None

        schedule = poll.labels["schedule"]
        assert schedule[0]["interval"] == timedelta(seconds=30)

    async def test_interval_decorator_accepts_timedelta(self) -> None:
        """A timedelta argument is used verbatim."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        delta = timedelta(minutes=2)

        @scheduler.interval(delta)
        async def poll() -> None:
            return None

        assert poll.labels["schedule"][0]["interval"] is delta

    async def test_schedule_decorator_passes_raw_spec(self) -> None:
        """@scheduler.schedule(spec) forwards the spec verbatim."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        spec = [{"cron": "0 * * * *"}, {"interval": timedelta(seconds=10)}]

        @scheduler.schedule(spec)
        async def multi() -> None:
            return None

        assert multi.labels["schedule"] == spec

    async def test_register_attaches_schedule_without_decorator(self) -> None:
        """register() works for callables you can't decorate at definition time."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())

        async def reconcile() -> None:
            return None

        registered = scheduler.register(
            reconcile,
            schedule=[{"cron": "*/15 * * * *"}],
            task_name="reconcile",
        )
        assert registered.labels["schedule"] == [{"cron": "*/15 * * * *"}]
        assert registered.task_name == "reconcile"

    async def test_label_source_picks_up_scheduled_task(self) -> None:
        """After connect(), the LabelScheduleSource exposes scheduled tasks."""
        broker = InMemoryBroker()
        scheduler = AsyncTaskScheduler(broker)

        @scheduler.cron("0 0 * * *")
        async def midnight() -> None:
            return None

        await scheduler.connect()
        schedules = await scheduler.sources[0].get_schedules()
        await scheduler.disconnect()

        assert any(s.cron == "0 0 * * *" for s in schedules)

    async def test_run_in_background_requires_connect(self) -> None:
        """run_in_background() raises before connect()."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        with pytest.raises(RuntimeError):
            await scheduler.run_in_background()

    async def test_run_in_background_spawns_loop_and_disconnect_cancels(self) -> None:
        """run_in_background() spawns a task that disconnect() cancels."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        await scheduler.connect()
        task = await scheduler.run_in_background()
        assert isinstance(task, asyncio.Task)
        assert task is await scheduler.run_in_background()  # idempotent
        await asyncio.sleep(0)
        assert not task.done()
        await scheduler.disconnect()
        assert task.done()

    async def test_lifespan_starts_and_stops_scheduler(self) -> None:
        """lifespan() connects on enter and disconnects on exit."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        async with scheduler.lifespan() as live:
            assert live is scheduler.scheduler
            assert scheduler.is_connected is True
        assert scheduler.is_connected is False

    async def test_health_check_reflects_state(self) -> None:
        """health_check tracks the started flag and background loop state."""
        scheduler = AsyncTaskScheduler(InMemoryBroker())
        assert await scheduler.health_check() is False
        await scheduler.connect()
        assert await scheduler.health_check() is True
        await scheduler.disconnect()
        assert await scheduler.health_check() is False


class TestSchedulerLoopSurvivesADeclaredOffset:
    """A ``CronOffset`` declaration must not end the scheduler loop.

    ``SchedulerLoop`` guards its tick with ``except CronValueError``
    only, so a ``ZoneInfoNotFoundError`` from an offset it cannot resolve
    escapes and ends the ``while True`` — silencing every task in the
    process, not only the one that declared the offset. Measured on
    0.283.1: the loop ended 0.363s after startup. The budget below is
    five times that.
    """

    async def test_loop_stays_alive_and_healthy(self) -> None:
        scheduler = AsyncTaskScheduler(InMemoryBroker())

        @scheduler.cron(
            "* * * * *",
            cron_offset=CronOffset.BRASILIA,
            task_name="with_offset",
        )
        async def with_offset() -> None:
            return None

        @scheduler.cron("* * * * *", task_name="plain")
        async def plain() -> None:
            return None

        await scheduler.connect()
        loop_task = await scheduler.run_in_background()
        try:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(loop_task), timeout=2.0)
            assert loop_task.done() is False
            assert await scheduler.health_check() is True
        finally:
            await scheduler.disconnect()


class TestOffsetNormalizedAtEveryDoor:
    """All four registration paths normalize ``cron_offset`` alike.

    A facade keyword the class path does not translate has produced
    defects here before, so each door that can carry an offset is
    asserted, not just the one the recipe shows.
    """

    async def test_queue_cron(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.cron("0 2 * * *", cron_offset=CronOffset.BRASILIA)
        async def a() -> None:
            return None

        entry = a.taskiq_task.labels["schedule"][0]
        assert entry["cron_offset"] == timedelta(hours=-3)

    async def test_queue_task_with_raw_schedule(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.task(
            schedule=[{"cron": "0 2 * * *", "cron_offset": CronOffset.BRASILIA}],
        )
        async def b() -> None:
            return None

        entry = b.taskiq_task.labels["schedule"][0]
        assert entry["cron_offset"] == timedelta(hours=-3)

    async def test_scheduler_cron(self) -> None:
        scheduler = AsyncTaskScheduler(InMemoryBroker())

        @scheduler.cron("0 2 * * *", cron_offset=CronOffset.BRASILIA)
        async def c() -> None:
            return None

        assert c.labels["schedule"][0]["cron_offset"] == timedelta(hours=-3)

    async def test_scheduler_schedule_spec(self) -> None:
        scheduler = AsyncTaskScheduler(InMemoryBroker())

        @scheduler.schedule(
            [{"cron": "0 2 * * *", "cron_offset": CronOffset.BRASILIA}],
        )
        async def d() -> None:
            return None

        assert d.labels["schedule"][0]["cron_offset"] == timedelta(hours=-3)

    async def test_scheduler_register(self) -> None:
        scheduler = AsyncTaskScheduler(InMemoryBroker())

        async def e() -> None:
            return None

        task = scheduler.register(
            e,
            schedule=[{"cron": "0 2 * * *", "cron_offset": CronOffset.BRASILIA}],
            task_name="registered",
        )
        assert task.labels["schedule"][0]["cron_offset"] == timedelta(hours=-3)
