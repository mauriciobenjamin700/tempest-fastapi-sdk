"""Tests for class-based message consumers."""

from __future__ import annotations

import asyncio

import pytest
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import Consumer, MessageBroker, subscribe


class OrderPaid(BaseModel):
    order_id: str


class OrderCancelled(BaseModel):
    order_id: str


def _raw() -> RabbitBroker:
    return RabbitBroker("amqp://guest:guest@localhost:5672/")


class TestConstructorForm:
    async def test_channel_and_schema_via_constructor(self) -> None:
        broker = _raw()
        mq = MessageBroker(broker)
        seen: list[OrderPaid] = []
        done = asyncio.Event()

        class OrderPaidConsumer(Consumer):
            async def handle(self, event: OrderPaid) -> None:
                seen.append(event)
                done.set()

        mq.register(OrderPaidConsumer(channel="orders.paid", schema=OrderPaid))

        async with TestRabbitBroker(broker):
            await mq.connect()
            await mq.publish("orders.paid", OrderPaid(order_id="abc"))
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await mq.disconnect()

        assert seen == [OrderPaid(order_id="abc")]

    def test_missing_channel_raises(self) -> None:
        class Bad(Consumer):
            async def handle(self, event: OrderPaid) -> None: ...

        with pytest.raises(ValueError, match="channel"):
            Bad().subscriptions()


class TestGroupedForm:
    async def test_multiple_channels_one_class(self) -> None:
        broker = _raw()
        mq = MessageBroker(broker)
        paid: list[OrderPaid] = []
        cancelled: list[OrderCancelled] = []
        both = asyncio.Event()

        class OrdersConsumer(Consumer):
            @subscribe("orders.paid")
            async def on_paid(self, event: OrderPaid) -> None:
                paid.append(event)
                _maybe_done()

            @subscribe("orders.cancelled")
            async def on_cancelled(self, event: OrderCancelled) -> None:
                cancelled.append(event)
                _maybe_done()

        def _maybe_done() -> None:
            if paid and cancelled:
                both.set()

        mq.register(OrdersConsumer())

        async with TestRabbitBroker(broker):
            await mq.connect()
            await mq.publish("orders.paid", OrderPaid(order_id="a"))
            await mq.publish("orders.cancelled", OrderCancelled(order_id="b"))
            await asyncio.wait_for(both.wait(), timeout=1.0)
            await mq.disconnect()

        assert paid == [OrderPaid(order_id="a")]
        assert cancelled == [OrderCancelled(order_id="b")]

    def test_grouped_takes_precedence_and_lists_all(self) -> None:
        class OrdersConsumer(Consumer):
            @subscribe("orders.paid")
            async def on_paid(self, event: OrderPaid) -> None: ...

            @subscribe("orders.cancelled")
            async def on_cancelled(self, event: OrderCancelled) -> None: ...

        subs = OrdersConsumer().subscriptions()
        channels = sorted(s.channel for s in subs)
        assert channels == ["orders.cancelled", "orders.paid"]
        assert all(s.schema is None for s in subs)  # annotation drives decoding
