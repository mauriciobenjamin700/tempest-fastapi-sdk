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


class TestSubscriberOptions:
    """Options a consumer needs from FastStream must reach FastStream.

    ``prefetch`` is asserted on the ``Channel`` the subscriber ends up
    with, not on the keyword being accepted: FastStream has no
    ``prefetch`` keyword, so an implementation that forwarded it verbatim
    would raise ``TypeError`` and one that dropped it would still accept
    the call.
    """

    @staticmethod
    def _channel(mq: MessageBroker) -> object | None:
        return getattr(mq.broker.subscribers[-1], "channel", None)

    def test_prefetch_on_subscribe_becomes_a_channel(self) -> None:
        class OrdersConsumer(Consumer):
            @subscribe("orders.paid", prefetch=5)
            async def on_paid(self, event: OrderPaid) -> None: ...

        mq = MessageBroker(_raw())
        mq.register(OrdersConsumer())
        assert self._channel(mq).prefetch_count == 5

    def test_prefetch_on_the_constructor_form(self) -> None:
        class OrderPaidConsumer(Consumer):
            async def handle(self, event: OrderPaid) -> None: ...

        mq = MessageBroker(_raw())
        mq.register(
            OrderPaidConsumer(channel="orders.paid", schema=OrderPaid, prefetch=3),
        )
        assert self._channel(mq).prefetch_count == 3

    def test_the_class_attribute_covers_every_binding(self) -> None:
        class OrdersConsumer(Consumer):
            prefetch = 7

            @subscribe("orders.paid")
            async def on_paid(self, event: OrderPaid) -> None: ...

            @subscribe("orders.cancelled")
            async def on_cancelled(self, event: OrderCancelled) -> None: ...

        assert [s.prefetch for s in OrdersConsumer().subscriptions()] == [7, 7]

    def test_subscribe_overrides_the_class_attribute(self) -> None:
        """The slow handler gets its own cap without changing its peers."""

        class OrdersConsumer(Consumer):
            prefetch = 7

            @subscribe("orders.paid", prefetch=1)
            async def on_paid(self, event: OrderPaid) -> None: ...

            @subscribe("orders.cancelled")
            async def on_cancelled(self, event: OrderCancelled) -> None: ...

        by_channel = {s.channel: s.prefetch for s in OrdersConsumer().subscriptions()}
        assert by_channel == {"orders.paid": 1, "orders.cancelled": 7}

    def test_no_prefetch_leaves_the_subscriber_alone(self) -> None:
        class OrdersConsumer(Consumer):
            @subscribe("orders.paid")
            async def on_paid(self, event: OrderPaid) -> None: ...

        mq = MessageBroker(_raw())
        mq.register(OrdersConsumer())
        assert self._channel(mq) is None

    def test_constructor_form_forwards_subscriber_options(self) -> None:
        """Without this, the constructor form could not name an exchange."""
        from faststream.rabbit import RabbitExchange

        class OrderPaidConsumer(Consumer):
            async def handle(self, event: OrderPaid) -> None: ...

        exchange = RabbitExchange("events", durable=True)
        mq = MessageBroker(_raw())
        mq.register(
            OrderPaidConsumer(
                channel="orders.paid",
                schema=OrderPaid,
                exchange=exchange,
            ),
        )
        assert mq.broker.subscribers[-1].exchange == exchange

    def test_constructor_options_are_not_shared_between_consumers(self) -> None:
        """The empty default lives on the class; it must never be mutated."""

        class OrderPaidConsumer(Consumer):
            async def handle(self, event: OrderPaid) -> None: ...

        OrderPaidConsumer(channel="a", schema=OrderPaid, exchange="events")
        plain = OrderPaidConsumer(channel="b", schema=OrderPaid)
        assert plain.subscriptions()[0].options == {}
        assert Consumer.options == {}
