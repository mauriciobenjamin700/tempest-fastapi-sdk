"""A consumer with a dead-letter queue is pinned to ``REJECT_ON_ERROR`` (#642).

Asserted on the ack policy FastStream's subscriber resolves, not on the
keyword being accepted. The case that matters is a broker-wide
``ack_policy``: a ``NACK_ON_ERROR`` set on the broker reaches every
subscriber that does not name its own, and under it a failed message is
requeued forever instead of reaching the dead-letter queue the spec
declared.
"""

from __future__ import annotations

from typing import Any

import pytest
from faststream import AckPolicy

from tempest_fastapi_sdk.queue import Consumer, MessageBroker, QueueSpec, subscribe

AMQP_URL: str = "amqp://guest:guest@localhost:5672/"


def dead_lettered() -> QueueSpec:
    """Build a consumer spec with a dead-letter exchange.

    Returns:
        QueueSpec: ``gamification.task.done`` dead-lettering to ``dlx``.
    """
    return QueueSpec.consumer(
        "gamification", "task.done", exchange="events", dead_letter_exchange="dlx"
    )


def resolved(mq: MessageBroker) -> AckPolicy:
    """Return the ack policy of the last subscriber registered.

    Args:
        mq (MessageBroker): The broker under test.

    Returns:
        AckPolicy: What FastStream will apply.
    """
    policy: AckPolicy = mq.broker.subscribers[-1].ack_policy
    return policy


def broker(**options: Any) -> MessageBroker:
    """Build a RabbitMQ facade that never connects.

    Args:
        **options (Any): Broker-wide options.

    Returns:
        MessageBroker: The facade.
    """
    return MessageBroker.rabbitmq(AMQP_URL, **options)


class TestOn:
    def test_a_dead_lettered_consumer_rejects_on_error(self) -> None:
        mq = broker()

        @mq.on(dead_lettered())
        async def handle(body: str) -> None: ...

        assert resolved(mq) is AckPolicy.REJECT_ON_ERROR

    def test_a_broker_wide_nack_does_not_reach_it(self) -> None:
        mq = broker(ack_policy=AckPolicy.NACK_ON_ERROR)

        @mq.on(dead_lettered())
        async def handle(body: str) -> None: ...

        assert resolved(mq) is AckPolicy.REJECT_ON_ERROR

    def test_a_plain_consumer_keeps_the_broker_policy(self) -> None:
        mq = broker(ack_policy=AckPolicy.NACK_ON_ERROR)

        @mq.on("plain")
        async def handle(body: str) -> None: ...

        assert resolved(mq) is AckPolicy.NACK_ON_ERROR

    def test_manual_is_allowed(self) -> None:
        mq = broker()

        @mq.on(dead_lettered(), ack_policy=AckPolicy.MANUAL)
        async def handle(body: str) -> None: ...

        assert resolved(mq) is AckPolicy.MANUAL

    @pytest.mark.parametrize(
        "policy", [AckPolicy.ACK, AckPolicy.ACK_FIRST, AckPolicy.NACK_ON_ERROR]
    )
    def test_a_policy_that_never_dead_letters_is_refused(
        self, policy: AckPolicy
    ) -> None:
        mq = broker()

        with pytest.raises(ValueError, match="never sends a failed message"):
            mq.on(dead_lettered(), ack_policy=policy)


class TestRegister:
    def test_the_grouped_form_is_pinned_too(self) -> None:
        class Gamification(Consumer):
            @subscribe(dead_lettered())
            async def on_done(self, body: str) -> None: ...

        mq = broker(ack_policy=AckPolicy.NACK_ON_ERROR)
        mq.register(Gamification())

        assert resolved(mq) is AckPolicy.REJECT_ON_ERROR

    def test_the_constructor_form_is_pinned_too(self) -> None:
        class Gamification(Consumer):
            async def handle(self, body: str) -> None: ...

        mq = broker(ack_policy=AckPolicy.NACK_ON_ERROR)
        mq.register(Gamification(channel=dead_lettered(), schema=str))

        assert resolved(mq) is AckPolicy.REJECT_ON_ERROR

    def test_a_refused_policy_on_subscribe_raises_at_register(self) -> None:
        class Gamification(Consumer):
            @subscribe(dead_lettered(), ack_policy=AckPolicy.ACK)
            async def on_done(self, body: str) -> None: ...

        with pytest.raises(ValueError, match="never sends a failed message"):
            broker().register(Gamification())
