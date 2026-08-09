"""Tests for the prefetch/QoS knob on MessageBroker.

Asserted on the ``Channel`` FastStream ends up with, because that object
is what carries ``basic.qos`` to the broker. Checking that the keyword
was accepted would pass for an implementation that swallowed it.
"""

from typing import Any

import pytest

from tempest_fastapi_sdk.queue import MessageBroker

AMQP_URL = "amqp://guest:guest@localhost:5672/"


def _subscriber_channel(mq: MessageBroker, **options: Any) -> Any:
    """Register a consumer and return the Channel it was given.

    Args:
        mq (MessageBroker): The broker under test.
        **options (Any): Options forwarded to ``on``.

    Returns:
        Any: The subscriber's channel, or ``None`` when it has none.
    """

    @mq.on("orders.paid", **options)
    async def handle(message: dict[str, str]) -> None: ...

    return getattr(mq.broker.subscribers[-1], "channel", None)


class TestBrokerWidePrefetch:
    def test_no_prefetch_by_default(self) -> None:
        """Unchanged behaviour: the knob is opt-in, not a new default."""
        mq = MessageBroker.rabbitmq(AMQP_URL)
        assert _subscriber_channel(mq) is None

    def test_prefetch_becomes_a_channel(self) -> None:
        from tempest_fastapi_sdk.queue.broker import _with_prefetch

        options = _with_prefetch({}, 32, "default_channel")
        assert options["default_channel"].prefetch_count == 32

    def test_prefetch_is_not_forwarded_to_faststream_verbatim(self) -> None:
        """``RabbitBroker`` has no ``prefetch`` keyword; it would TypeError."""
        MessageBroker.rabbitmq(AMQP_URL, prefetch=8)

    def test_an_explicit_default_channel_wins(self) -> None:
        """Rebuilding it would drop the confirms and QoS they also set."""
        from faststream.rabbit import Channel

        from tempest_fastapi_sdk.queue.broker import _with_prefetch

        mine = Channel(prefetch_count=4)
        options = _with_prefetch({"default_channel": mine}, 32, "default_channel")
        assert options["default_channel"] is mine

    def test_no_prefetch_leaves_the_options_untouched(self) -> None:
        from tempest_fastapi_sdk.queue.broker import _with_prefetch

        assert _with_prefetch({}, None, "default_channel") == {}


class TestPerConsumerPrefetch:
    def test_prefetch_becomes_the_subscriber_channel(self) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)
        assert _subscriber_channel(mq, prefetch=5).prefetch_count == 5

    def test_it_overrides_the_broker_wide_value(self) -> None:
        """The slow handler gets a low cap without throttling its peers."""
        mq = MessageBroker.rabbitmq(AMQP_URL, prefetch=32)
        assert _subscriber_channel(mq, prefetch=1).prefetch_count == 1

    def test_an_explicit_channel_wins(self) -> None:
        from faststream.rabbit import Channel

        mq = MessageBroker.rabbitmq(AMQP_URL)
        channel = _subscriber_channel(
            mq,
            prefetch=5,
            channel=Channel(prefetch_count=9),
        )
        assert channel.prefetch_count == 9

    def test_publisher_confirms_are_on_by_default(self) -> None:
        """Worth pinning: a lost publish on broker restart is silent."""
        mq = MessageBroker.rabbitmq(AMQP_URL)
        assert _subscriber_channel(mq, prefetch=5).publisher_confirms is True

    @pytest.mark.parametrize("value", [1, 10, 250])
    def test_any_positive_cap_is_accepted(self, value: int) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)
        assert _subscriber_channel(mq, prefetch=value).prefetch_count == value
