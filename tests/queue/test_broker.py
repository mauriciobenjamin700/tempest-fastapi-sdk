"""Tests for the MessageBroker facade over FastStream."""

from __future__ import annotations

import asyncio

import pytest
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import MessageBroker


class OrderPaid(BaseModel):
    order_id: str


def _raw() -> RabbitBroker:
    return RabbitBroker("amqp://guest:guest@localhost:5672/")


class TestMessageBroker:
    async def test_on_and_publish_roundtrip_typed(self) -> None:
        """A published Pydantic model is decoded into the handler's type."""
        broker = _raw()
        mq = MessageBroker(broker)
        received: list[OrderPaid] = []
        seen = asyncio.Event()

        @mq.on("orders.paid")
        async def handle(event: OrderPaid) -> None:
            received.append(event)
            seen.set()

        async with TestRabbitBroker(broker):
            await mq.connect()
            await mq.publish("orders.paid", OrderPaid(order_id="abc"))
            await asyncio.wait_for(seen.wait(), timeout=1.0)
            await mq.disconnect()

        assert received == [OrderPaid(order_id="abc")]

    async def test_publish_before_connect_raises(self) -> None:
        mq = MessageBroker(_raw())
        with pytest.raises(RuntimeError):
            await mq.publish("orders.paid", OrderPaid(order_id="x"))

    async def test_connect_idempotent_and_health(self) -> None:
        broker = _raw()
        mq = MessageBroker(broker)
        assert await mq.health_check() is False
        async with TestRabbitBroker(broker):
            await mq.connect()
            await mq.connect()
            assert mq.is_connected is True
            assert await mq.health_check() is True
            await mq.disconnect()
        assert mq.is_connected is False

    async def test_lifespan_yields_self(self) -> None:
        broker = _raw()
        mq = MessageBroker(broker)
        async with TestRabbitBroker(broker), mq.lifespan() as live:
            assert live is mq
            assert mq.is_connected is True
        assert mq.is_connected is False

    async def test_rabbitmq_constructor_builds_broker(self) -> None:
        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")
        assert isinstance(mq.broker, RabbitBroker)

    async def test_publish_matches_outbox_relay_callable(self) -> None:
        """mq.publish(topic, payload) is a valid OutboxRelay publish callable."""
        broker = _raw()
        mq = MessageBroker(broker)
        received: list[dict[str, str]] = []
        seen = asyncio.Event()

        @mq.on("orders.placed")
        async def handle(event: dict[str, str]) -> None:
            received.append(event)
            seen.set()

        async with TestRabbitBroker(broker):
            await mq.connect()

            # Shape used by OutboxRelay(publish=lambda e: mq.publish(e.topic, ...))
            async def publish_event(topic: str, payload: dict[str, str]) -> None:
                await mq.publish(topic, payload)

            await publish_event("orders.placed", {"order_id": "42"})
            await asyncio.wait_for(seen.wait(), timeout=1.0)
            await mq.disconnect()

        assert received == [{"order_id": "42"}]
