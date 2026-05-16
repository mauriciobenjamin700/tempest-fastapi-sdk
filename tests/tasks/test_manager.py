"""Tests for the TaskIQ-backed AsyncTaskBrokerManager."""

from __future__ import annotations

import pytest
from taskiq import InMemoryBroker

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager


class TestAsyncTaskBrokerManager:
    """Validate the TaskIQ lifecycle wrapper."""

    async def test_connect_is_idempotent(self) -> None:
        """connect() called twice keeps the broker started exactly once."""
        manager = AsyncTaskBrokerManager(InMemoryBroker())
        await manager.connect()
        await manager.connect()
        assert manager.is_connected is True
        await manager.disconnect()

    async def test_disconnect_before_connect_is_noop(self) -> None:
        """disconnect() is safe even before any connect()."""
        manager = AsyncTaskBrokerManager(InMemoryBroker())
        await manager.disconnect()
        assert manager.is_connected is False

    async def test_task_decorator_runs_via_kiq(self) -> None:
        """A task registered via the decorator can be kicked and awaited."""
        manager = AsyncTaskBrokerManager(InMemoryBroker())

        @manager.task
        async def add(a: int, b: int) -> int:
            return a + b

        await manager.connect()
        result = await add.kiq(2, 3)
        outcome = await result.wait_result()
        await manager.disconnect()

        assert outcome.return_value == 5

    async def test_register_task_without_decorator(self) -> None:
        """register_task() works for callables you couldn't decorate."""
        manager = AsyncTaskBrokerManager(InMemoryBroker())

        async def double(x: int) -> int:
            return x * 2

        registered = manager.register_task(double, task_name="double")
        await manager.connect()
        result = await registered.kiq(21)
        outcome = await result.wait_result()
        await manager.disconnect()

        assert outcome.return_value == 42

    async def test_lifespan_starts_and_stops_broker(self) -> None:
        """lifespan() context connects on enter, disconnects on exit."""
        broker = InMemoryBroker()
        manager = AsyncTaskBrokerManager(broker)
        async with manager.lifespan() as live:
            assert live is broker
            assert manager.is_connected is True
        assert manager.is_connected is False

    async def test_health_check_reflects_state(self) -> None:
        """health_check returns False before connect, True after."""
        manager = AsyncTaskBrokerManager(InMemoryBroker())
        assert await manager.health_check() is False
        await manager.connect()
        assert await manager.health_check() is True
        await manager.disconnect()
        assert await manager.health_check() is False

    async def test_broker_dependency_yields_started_broker(self) -> None:
        """The FastAPI dependency yields the live broker after connect."""
        broker = InMemoryBroker()
        manager = AsyncTaskBrokerManager(broker)
        await manager.connect()
        agen = manager.broker_dependency()
        yielded = await agen.__anext__()
        assert yielded is broker
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        await manager.disconnect()

    async def test_broker_dependency_before_connect_raises(self) -> None:
        """The FastAPI dependency raises if connect() never ran."""
        manager = AsyncTaskBrokerManager(InMemoryBroker())
        agen = manager.broker_dependency()
        with pytest.raises(RuntimeError):
            await agen.__anext__()
