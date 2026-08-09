"""Tests for tempest_fastapi_sdk.queue.publisher.

The class exists to make two things true that the loose
``mq.publish("channel", payload)`` call could not: the declared schema is
enforced before the message leaves, and the declared topology is
registered even when the service only publishes. Both are asserted
directly, because a publisher that merely forwarded would pass a test that
only checked "the message arrived".

The third property is the one that would break silently: publishing must
go through ``MessageBroker.publish``, so it keeps the ``message_id``
deduplication keys on and the headers tracing rides. A publisher built on
FastStream's own publisher object would look identical and lose both.
"""

from typing import Any

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import (
    Consumer,
    DeadLetterSpec,
    MessageBroker,
    Publisher,
    QueueSpec,
    UnsupportedTopologyError,
    subscribe,
)


class OrderPaid(BaseModel):
    """Payload used across the publisher tests."""

    order_id: str


class OrderCancelled(BaseModel):
    """A second payload, to prove the schema check is not decorative."""

    order_id: str


ORDERS_PAID = QueueSpec(
    name="orders.paid",
    dead_letter=DeadLetterSpec(exchange="dlx"),
)


class _RecordingBroker:
    """Stand-in FastStream broker recording what the facade published."""

    def __init__(self) -> None:
        """Start with nothing published."""
        self.published: list[tuple[Any, Any, dict[str, Any]]] = []

    async def start(self) -> None:
        """Pretend the connection handshake succeeded."""

    async def stop(self) -> None:
        """Pretend the connection closed."""

    async def publish(
        self,
        message: Any,
        channel: Any = None,
        **options: Any,
    ) -> str:
        """Record one publish.

        Args:
            message (Any): The payload.
            channel (Any): The destination.
            **options (Any): Everything the facade added.

        Returns:
            str: A sentinel the caller can assert on.
        """
        self.published.append((message, channel, options))
        return "published"


async def _connected() -> tuple[MessageBroker, _RecordingBroker]:
    """Build a connected facade over a recording broker.

    Returns:
        tuple[MessageBroker, _RecordingBroker]: The facade and the broker
        it wraps, so a test can assert on what reached the transport.
    """
    raw = _RecordingBroker()
    mq = MessageBroker(raw)  # type: ignore[arg-type]
    await mq.connect()
    return mq, raw


class OrderPaidPublisher(Publisher[OrderPaid]):
    """Class-based publisher used across the tests."""

    channel = "orders.paid"
    schema = OrderPaid


class TestDeclaration:
    async def test_a_declared_publisher_publishes_to_its_channel(self) -> None:
        mq, raw = await _connected()
        orders = mq.publisher_for(OrderPaidPublisher)
        assert await orders.publish(OrderPaid(order_id="abc")) == "published"
        message, channel, _ = raw.published[0]
        assert channel == "orders.paid"
        assert message.order_id == "abc"

    async def test_the_constructor_form_takes_a_runtime_channel(self) -> None:
        mq, raw = await _connected()
        orders: Publisher[OrderPaid] = Publisher(
            mq,
            channel="orders.paid",
            schema=OrderPaid,
        )
        await orders.publish(OrderPaid(order_id="abc"))
        assert raw.published[0][1] == "orders.paid"

    async def test_a_publisher_without_a_channel_is_refused(self) -> None:
        """Nowhere to publish is a startup error, not a runtime surprise."""
        mq, _ = await _connected()

        class Nowhere(Publisher[OrderPaid]):
            schema = OrderPaid

        with pytest.raises(ValueError, match="declares no channel"):
            mq.publisher_for(Nowhere)


class TestSchemaEnforcement:
    async def test_the_declared_schema_is_enforced(self) -> None:
        """The consumer is a process away and can only reject what left."""
        mq, raw = await _connected()
        orders = mq.publisher_for(OrderPaidPublisher)
        with pytest.raises(TypeError, match="publishes OrderPaid"):
            await orders.publish(OrderCancelled(order_id="abc"))  # type: ignore[arg-type]
        assert raw.published == []

    async def test_no_schema_means_no_check(self) -> None:
        mq, raw = await _connected()

        class Anything(Publisher[Any]):
            channel = "audit.events"

        await mq.publisher_for(Anything).publish({"free": "form"})
        assert raw.published[0][0] == {"free": "form"}


class TestFacadeIntegration:
    async def test_publishing_keeps_the_dedup_key_and_headers(self) -> None:
        """Bypassing the facade would drop both and look identical."""
        mq, raw = await _connected()
        await mq.publisher_for(OrderPaidPublisher).publish(OrderPaid(order_id="abc"))
        options = raw.published[0][2]
        assert options["message_id"]
        assert "headers" in options

    async def test_constructor_options_are_defaults_a_call_can_override(self) -> None:
        mq, raw = await _connected()
        orders = mq.publisher_for(OrderPaidPublisher, correlation_id="default")
        await orders.publish(OrderPaid(order_id="a"))
        await orders.publish(OrderPaid(order_id="b"), correlation_id="explicit")
        assert raw.published[0][2]["correlation_id"] == "default"
        assert raw.published[1][2]["correlation_id"] == "explicit"


class TestTopologyRegistration:
    def test_a_spec_on_a_publisher_registers_its_topology(self) -> None:
        """A producer-only service still has to declare the exchange.

        Without this, the queue carries ``x-dead-letter-exchange`` pointing
        at an exchange nobody declared, and every rejected message on the
        consuming side is dropped at routing time, silently.
        """
        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")

        class Declaring(Publisher[OrderPaid]):
            channel = ORDERS_PAID
            schema = OrderPaid

        mq.publisher_for(Declaring)
        assert mq.specs["orders.paid"] is ORDERS_PAID

    def test_an_unsupported_spec_fails_at_binding_time(self) -> None:
        """Startup, not per message — same rule as ``on()``."""
        mq = MessageBroker.redis("redis://localhost:6379/0")

        class Declaring(Publisher[OrderPaid]):
            channel = ORDERS_PAID
            schema = OrderPaid

        with pytest.raises(UnsupportedTopologyError, match="dead_letter"):
            mq.publisher_for(Declaring)


class TestConsumerTakesASpec:
    def test_the_constructor_form_accepts_a_spec(self) -> None:
        """The class path could not declare topology without falling back."""

        class OrderPaidConsumer(Consumer):
            channel = ORDERS_PAID
            schema = OrderPaid

            async def handle(self, event: OrderPaid) -> None:
                """Do nothing."""

        subscription = OrderPaidConsumer().subscriptions()[0]
        assert subscription.channel is ORDERS_PAID

    def test_the_grouped_form_accepts_a_spec(self) -> None:
        class OrdersConsumer(Consumer):
            @subscribe(ORDERS_PAID)
            async def on_paid(self, event: OrderPaid) -> None:
                """Do nothing."""

        subscription = OrdersConsumer().subscriptions()[0]
        assert subscription.channel is ORDERS_PAID

    def test_registering_a_spec_consumer_records_the_topology(self) -> None:
        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")

        class OrdersConsumer(Consumer):
            @subscribe(ORDERS_PAID)
            async def on_paid(self, event: OrderPaid) -> None:
                """Do nothing."""

        mq.register(OrdersConsumer())
        assert mq.specs["orders.paid"] is ORDERS_PAID
