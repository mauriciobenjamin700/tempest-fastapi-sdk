"""Tests for the FastStream-backed AsyncBrokerManager."""

from __future__ import annotations

import asyncio

import pytest
from faststream.rabbit import RabbitBroker, TestRabbitBroker

from tempest_fastapi_sdk.queue import AsyncBrokerManager


def _make_broker() -> RabbitBroker:
    """Return a RabbitBroker pointed at a dummy URL (TestRabbitBroker patches it).

    Returns:
        RabbitBroker: An unstarted broker suitable for ``TestRabbitBroker``.
    """
    return RabbitBroker("amqp://guest:guest@localhost:5672/")


class TestAsyncBrokerManager:
    """Validate the FastStream lifecycle wrapper."""

    async def test_connect_is_idempotent(self) -> None:
        """connect() called twice keeps the broker started exactly once."""
        broker = _make_broker()
        manager = AsyncBrokerManager(broker)
        async with TestRabbitBroker(broker):
            await manager.connect()
            await manager.connect()
            assert manager.is_connected is True
            await manager.disconnect()

    async def test_disconnect_before_connect_is_noop(self) -> None:
        """disconnect() is safe even before any connect()."""
        manager = AsyncBrokerManager(_make_broker())
        await manager.disconnect()
        assert manager.is_connected is False

    async def test_publish_without_connect_raises(self) -> None:
        """publish() before connect() raises RuntimeError."""
        manager = AsyncBrokerManager(_make_broker())
        with pytest.raises(RuntimeError):
            await manager.publish("payload", queue="orders")

    async def test_publish_and_subscribe_roundtrip(self) -> None:
        """A published message reaches a subscriber registered on the broker."""
        broker = _make_broker()
        manager = AsyncBrokerManager(broker)
        received: list[str] = []
        seen = asyncio.Event()

        @broker.subscriber("orders")
        async def handle(msg: str) -> None:
            received.append(msg)
            seen.set()

        async with TestRabbitBroker(broker):
            await manager.connect()
            await manager.publish("hello", queue="orders")
            await asyncio.wait_for(seen.wait(), timeout=1.0)
            await manager.disconnect()

        assert received == ["hello"]

    async def test_lifespan_starts_and_stops_broker(self) -> None:
        """lifespan() context connects on enter, disconnects on exit."""
        broker = _make_broker()
        manager = AsyncBrokerManager(broker)
        async with TestRabbitBroker(broker), manager.lifespan() as live:
            assert live is broker
            assert manager.is_connected is True
        assert manager.is_connected is False

    async def test_health_check_reflects_state(self) -> None:
        """health_check returns False before connect, True after."""
        broker = _make_broker()
        manager = AsyncBrokerManager(broker)
        assert await manager.health_check() is False
        async with TestRabbitBroker(broker):
            await manager.connect()
            assert await manager.health_check() is True
            await manager.disconnect()
        assert await manager.health_check() is False

    async def test_broker_dependency_yields_started_broker(self) -> None:
        """The FastAPI dependency yields the live broker after connect."""
        broker = _make_broker()
        manager = AsyncBrokerManager(broker)
        async with TestRabbitBroker(broker):
            await manager.connect()
            agen = manager.broker_dependency()
            yielded = await agen.__anext__()
            assert yielded is broker
            with pytest.raises(StopAsyncIteration):
                await agen.__anext__()
            await manager.disconnect()

    async def test_broker_dependency_before_connect_raises(self) -> None:
        """The FastAPI dependency raises if connect() never ran."""
        manager = AsyncBrokerManager(_make_broker())
        agen = manager.broker_dependency()
        with pytest.raises(RuntimeError):
            await agen.__anext__()
