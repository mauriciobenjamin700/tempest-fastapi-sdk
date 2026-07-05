"""Tests for the TaskQueue facade over TaskIQ."""

from __future__ import annotations

from datetime import timedelta

import pytest
from taskiq import InMemoryBroker

from tempest_fastapi_sdk.tasks import Task, TaskQueue


class TestTaskQueue:
    async def test_task_enqueue_runs_and_returns(self) -> None:
        """enqueue() runs the task (InMemoryBroker) and the result is awaitable."""
        tq = TaskQueue(InMemoryBroker())

        @tq.task
        async def add(a: int, b: int) -> int:
            return a + b

        assert isinstance(add, Task)
        await tq.connect()
        handle = await add.enqueue(2, 3)
        outcome = await handle.wait_result()
        await tq.disconnect()
        assert outcome.return_value == 5

    async def test_task_run_executes_inline(self) -> None:
        """run() calls the body directly without a broker round-trip."""
        tq = TaskQueue(InMemoryBroker())

        @tq.task
        async def double(x: int) -> int:
            return x * 2

        assert await double.run(21) == 42

    async def test_task_with_name_option(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.task(name="reports:nightly")
        async def report() -> str:
            return "ok"

        assert report.task_name == "reports:nightly"

    async def test_memory_constructor(self) -> None:
        tq = TaskQueue.memory()
        assert isinstance(tq.broker, InMemoryBroker)

    async def test_cron_registers_schedule_label(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.cron("*/5 * * * *")
        async def heartbeat() -> None:
            return None

        assert isinstance(heartbeat, Task)
        labels = heartbeat.taskiq_task.labels
        assert labels["schedule"] == [{"cron": "*/5 * * * *"}]

    async def test_cron_offset_included(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.cron("0 9 * * MON-FRI", cron_offset="-03:00")
        async def digest() -> None:
            return None

        assert digest.taskiq_task.labels["schedule"] == [
            {"cron": "0 9 * * MON-FRI", "cron_offset": "-03:00"},
        ]

    async def test_interval_coerces_seconds(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.interval(30)
        async def poll() -> None:
            return None

        assert poll.taskiq_task.labels["schedule"] == [
            {"interval": timedelta(seconds=30)},
        ]

    async def test_interval_accepts_timedelta(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.interval(timedelta(minutes=15))
        async def warm() -> None:
            return None

        assert warm.taskiq_task.labels["schedule"] == [
            {"interval": timedelta(minutes=15)},
        ]

    async def test_lifespan_and_health(self) -> None:
        tq = TaskQueue(InMemoryBroker())
        assert await tq.health_check() is False
        async with tq.lifespan() as live:
            assert live is tq
            assert tq.is_connected is True
            assert await tq.health_check() is True
        assert tq.is_connected is False

    async def test_start_scheduler_before_connect_raises(self) -> None:
        tq = TaskQueue(InMemoryBroker())
        with pytest.raises(RuntimeError):
            await tq.start_scheduler()

    async def test_scheduler_property_exposes_taskiq_scheduler(self) -> None:
        tq = TaskQueue(InMemoryBroker())
        from taskiq.scheduler.scheduler import TaskiqScheduler

        assert isinstance(tq.scheduler, TaskiqScheduler)
