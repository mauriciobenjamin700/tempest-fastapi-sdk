"""Tests for exchange binding, dead-letter queues and replay (#592).

These pin what the facade hands FastStream and in what order. Whether
that is a working topology — a copy per consumer, the rejection in the
right queue, the replay reaching one consumer — is answered against a real
broker in ``test_fanout_live.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from faststream.rabbit import ExchangeType, RabbitExchange

from tempest_fastapi_sdk.queue import (
    DeadLetterSpec,
    ExchangeBinding,
    ExchangeKind,
    ExchangeSpec,
    MessageBroker,
    QueueSpec,
    QueueType,
    UnsupportedTopologyError,
    retry_queues,
)
from tempest_fastapi_sdk.queue.reliability import DEATH_HEADERS, replay_headers
from tempest_fastapi_sdk.queue.topology import (
    DEAD_LETTER_ROUTING_KEY_ARG,
    rabbit_arguments,
    to_rabbit_exchange,
    to_rabbit_queue,
)

AMQP_URL = "amqp://guest:guest@localhost:5672/"
REDIS_URL = "redis://localhost:6379/0"


class _RabbitRecorder:
    """Stand-in RabbitBroker recording every call the facade makes.

    Named with ``Rabbit`` on purpose: ``detect_transport`` classifies by
    class name.
    """

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.calls: list[str] = []
        self.exchanges: list[Any] = []
        self.queues: list[Any] = []
        self.bindings: list[tuple[str, str, str | None]] = []
        self.published: list[tuple[Any, dict[str, Any]]] = []
        self.subscribed: list[tuple[Any, dict[str, Any]]] = []

    async def connect(self) -> None:
        """Record the connection."""
        self.calls.append("connect")

    async def start(self) -> None:
        """Record the start of the subscribers."""
        self.calls.append("start")

    async def declare_exchange(self, exchange: Any) -> Any:
        """Record an exchange declaration.

        Args:
            exchange (Any): The ``RabbitExchange``.

        Returns:
            Any: The exchange.
        """
        self.calls.append(f"exchange:{exchange.name}")
        self.exchanges.append(exchange)
        return exchange

    async def declare_queue(self, queue: Any) -> Any:
        """Record a queue declaration and hand back a bindable stub.

        Args:
            queue (Any): The ``RabbitQueue``.

        Returns:
            Any: An object whose ``bind`` records the binding.
        """
        self.calls.append(f"queue:{queue.name}")
        self.queues.append(queue)
        recorder = self

        class _Bound:
            async def bind(self, exchange: str, *, routing_key: str | None) -> None:
                recorder.bindings.append((queue.name, exchange, routing_key))

        return _Bound()

    async def publish(self, message: Any, *args: Any, **options: Any) -> None:
        """Record a publish.

        Args:
            message (Any): The payload.
            *args (Any): Positional destination, when given.
            **options (Any): Publish options.
        """
        if args:
            options["queue"] = args[0]
        self.published.append((message, options))

    def subscriber(self, queue: Any, **options: Any) -> Any:
        """Record a subscription.

        Args:
            queue (Any): The queue.
            **options (Any): Subscriber options.

        Returns:
            Any: A decorator returning the handler unchanged.
        """
        self.subscribed.append((queue, options))
        return lambda handler: handler

    def publisher(self, queue: Any, **options: Any) -> Any:
        """Record a publisher binding.

        Args:
            queue (Any): The queue.
            **options (Any): Publisher options.

        Returns:
            Any: The recorded options.
        """
        return options


class _NatsStandIn:
    """Stand-in whose class name makes ``detect_transport`` say NATS."""

    async def publish(self, message: Any, *args: Any, **options: Any) -> None:
        """Accept anything; the facade must refuse before reaching here.

        Args:
            message (Any): The payload.
            *args (Any): Destination.
            **options (Any): Options.
        """


def _recorded(declare_topology: bool = True) -> tuple[MessageBroker, _RabbitRecorder]:
    """Build a RabbitMQ facade over a recorder.

    Args:
        declare_topology (bool): Forwarded to the facade.

    Returns:
        tuple[MessageBroker, _RabbitRecorder]: The facade and its recorder.
    """
    mq = MessageBroker.rabbitmq(AMQP_URL, declare_topology=declare_topology)
    recorder = _RabbitRecorder()
    object.__setattr__(mq, "broker", recorder)
    return mq, recorder


class TestConsumerSpec:
    """``QueueSpec.consumer`` — one queue per consumer of an event."""

    def test_the_queue_is_named_after_consumer_and_channel(self) -> None:
        spec = QueueSpec.consumer("gamification", "task.completed", exchange="events")
        assert spec.name == "gamification.task.completed"

    def test_it_is_bound_by_the_event_key(self) -> None:
        spec = QueueSpec.consumer("gamification", "task.completed", exchange="events")
        assert spec.binding == ExchangeBinding(
            exchange=ExchangeSpec("events"),
            routing_key="task.completed",
        )

    def test_a_bare_exchange_name_is_a_durable_topic(self) -> None:
        spec = QueueSpec.consumer("g", "task.completed", exchange="events")
        assert spec.binding is not None
        assert spec.binding.exchange.kind is ExchangeKind.TOPIC
        assert spec.binding.exchange.durable is True

    def test_an_exchange_spec_is_kept_as_given(self) -> None:
        exchange = ExchangeSpec("events", kind=ExchangeKind.DIRECT)
        spec = QueueSpec.consumer("g", "task.completed", exchange=exchange)
        assert spec.binding is not None
        assert spec.binding.exchange is exchange

    def test_without_a_dead_letter_exchange_nothing_is_dead_lettered(self) -> None:
        spec = QueueSpec.consumer("g", "task.completed", exchange="events")
        assert spec.dead_letter is None

    def test_dead_letters_are_keyed_by_the_queue_not_the_event(self) -> None:
        """The event key is shared by every consumer; the queue name is not."""
        spec = QueueSpec.consumer(
            "gamification",
            "task.completed",
            exchange="events",
            dead_letter_exchange="dlx",
        )
        assert spec.dead_letter == DeadLetterSpec(
            exchange="dlx",
            routing_key="gamification.task.completed",
            queue="gamification.task.completed.dlq",
        )
        assert (
            rabbit_arguments(spec)[DEAD_LETTER_ROUTING_KEY_ARG]
            == "gamification.task.completed"
        )

    def test_two_consumers_of_one_event_get_two_queues(self) -> None:
        """Same name would mean one queue, and the messages split."""
        first = QueueSpec.consumer("gamification", "task.completed", exchange="e")
        second = QueueSpec.consumer("rules", "task.completed", exchange="e")
        assert first.name != second.name
        assert first.binding == second.binding

    def test_queue_type_is_forwarded(self) -> None:
        spec = QueueSpec.consumer(
            "g",
            "c",
            exchange="e",
            queue_type=QueueType.QUORUM,
        )
        assert spec.queue_type is QueueType.QUORUM


class TestDeadLetterQueueValidation:
    def test_a_queue_without_a_routing_key_is_refused(self) -> None:
        """An unset key would bind by the event key every consumer shares."""
        with pytest.raises(ValueError, match="routing_key"):
            DeadLetterSpec(exchange="dlx", queue="orders.dlq")

    def test_exchange_only_stays_valid(self) -> None:
        """The retry chain dead-letters to an exchange its retry queue reads."""
        assert DeadLetterSpec(exchange="dlx").queue is None


class TestTranslation:
    def test_a_bound_queue_carries_the_routing_key(self) -> None:
        queue = to_rabbit_queue(QueueSpec.consumer("g", "task.completed", exchange="e"))
        assert queue.routing_key == "task.completed"

    def test_an_unbound_queue_keeps_its_name_as_the_key(self) -> None:
        queue = to_rabbit_queue(QueueSpec(name="orders.paid"))
        assert queue.routing() == "orders.paid"

    @pytest.mark.parametrize("kind", list(ExchangeKind))
    def test_the_exchange_type_is_the_enum_not_a_string(
        self,
        kind: ExchangeKind,
    ) -> None:
        """FastStream reads ``exchange.type.value``; a raw string breaks connect()."""
        exchange = to_rabbit_exchange(ExchangeSpec("e", kind=kind))
        assert isinstance(exchange.type, ExchangeType)
        assert exchange.type.value == kind.value

    def test_exchange_kinds_mirror_faststream(self) -> None:
        """Ported value for value, so the module imports nothing from FastStream."""
        assert {kind.value for kind in ExchangeKind} == {
            member.value
            for member in ExchangeType
            if member.value in {"topic", "direct", "fanout", "headers"}
        }

    def test_a_binding_is_refused_off_rabbitmq(self) -> None:
        mq = MessageBroker.redis(REDIS_URL)
        with pytest.raises(UnsupportedTopologyError, match="binding"):
            mq.on(QueueSpec.consumer("g", "c", exchange="e"))


class TestSubscribing:
    def test_on_hands_the_exchange_to_the_subscriber(self) -> None:
        mq, recorder = _recorded()

        @mq.on(QueueSpec.consumer("g", "task.completed", exchange="events"))
        async def handle(body: str) -> None: ...

        queue, options = recorder.subscribed[0]
        assert queue.name == "g.task.completed"
        assert isinstance(options["exchange"], RabbitExchange)
        assert options["exchange"].name == "events"

    def test_an_unbound_spec_gets_no_exchange(self) -> None:
        mq, recorder = _recorded()

        @mq.on(QueueSpec(name="orders.paid"))
        async def handle(body: str) -> None: ...

        assert "exchange" not in recorder.subscribed[0][1]

    def test_a_binding_plus_exchange_option_is_refused(self) -> None:
        mq, _ = _recorded()
        with pytest.raises(ValueError, match="bound to exchange"):
            mq.on(
                QueueSpec.consumer("g", "c", exchange="events"),
                exchange="other",
            )

    def test_register_binds_the_same_way(self) -> None:
        from tempest_fastapi_sdk.queue import Consumer, subscribe

        spec = QueueSpec.consumer("g", "task.completed", exchange="events")

        class _Handlers(Consumer):
            @subscribe(spec)
            async def handle(self, body: str) -> None: ...

        mq, recorder = _recorded()
        mq.register(_Handlers())
        assert recorder.subscribed[0][1]["exchange"].name == "events"

    def test_publisher_binds_the_same_way(self) -> None:
        mq, _ = _recorded()
        options = mq.publisher(QueueSpec.consumer("g", "c", exchange="events"))
        assert options["exchange"].name == "events"


class TestPublishToExchange:
    async def test_the_channel_becomes_the_routing_key(self) -> None:
        mq, recorder = _recorded()
        mq._started = True
        await mq.publish("task.completed", "t-1", exchange=ExchangeSpec("events"))
        message, options = recorder.published[0]
        assert message == "t-1"
        assert options["routing_key"] == "task.completed"
        assert options["exchange"].name == "events"

    async def test_a_spec_is_declared_once_per_process(self) -> None:
        """A relay loop must not re-declare the exchange on every round."""
        mq, recorder = _recorded()
        mq._started = True
        for _ in range(3):
            await mq.publish("task.completed", "t", exchange=ExchangeSpec("events"))
        assert [exchange.name for exchange in recorder.exchanges] == ["events"]
        assert len(recorder.published) == 3

    async def test_a_bare_name_is_passed_through_undeclared(self) -> None:
        """``amq.topic`` exists already and its type is not ours to assert."""
        mq, recorder = _recorded()
        mq._started = True
        await mq.publish("task.completed", "t", exchange="amq.topic")
        assert recorder.exchanges == []
        assert recorder.published[0][1]["exchange"] == "amq.topic"

    async def test_without_exchange_the_queue_is_addressed_by_name(self) -> None:
        mq, recorder = _recorded()
        mq._started = True
        await mq.publish("orders.paid", "t")
        options = recorder.published[0][1]
        assert options["queue"] == "orders.paid"
        assert "exchange" not in options

    async def test_a_transport_without_exchanges_is_refused(self) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)
        object.__setattr__(mq, "broker", _NatsStandIn())
        mq._started = True
        with pytest.raises(UnsupportedTopologyError, match="exchange"):
            await mq.publish("c", "t", exchange=ExchangeSpec("events"))


class TestDeclareTopology:
    async def test_the_dead_letter_queue_is_declared_and_bound(self) -> None:
        """An exchange with nothing bound routes the rejection nowhere."""
        mq, recorder = _recorded()
        mq._bind(QueueSpec.consumer("g", "c", exchange="e", dead_letter_exchange="dlx"))
        assert await mq.declare_topology() == ["dlx"]
        assert [queue.name for queue in recorder.queues] == ["g.c.dlq"]
        assert recorder.bindings == [("g.c.dlq", "dlx", "g.c")]

    async def test_the_dead_letter_queue_matches_the_queue_type(self) -> None:
        mq, recorder = _recorded()
        mq._bind(
            QueueSpec.consumer(
                "g",
                "c",
                exchange="e",
                dead_letter_exchange="dlx",
                queue_type=QueueType.QUORUM,
            ),
        )
        await mq.declare_topology()
        assert recorder.queues[0].arguments == {"x-queue-type": "quorum"}
        assert recorder.queues[0].durable is True

    async def test_an_exchange_only_dead_letter_declares_no_queue(self) -> None:
        """The retry chain's retry queue is the consumer; a DLQ would duplicate."""
        mq, recorder = _recorded()
        mq._bind(
            retry_queues(
                "c", retry_exchange="r", main_exchange="m", dead_exchange="d"
            ).main
        )
        await mq.declare_topology()
        assert recorder.queues == []

    async def test_connect_declares_before_the_subscribers_start(self) -> None:
        """The other order left a window where a rejection had nowhere to go."""
        mq, recorder = _recorded()
        mq._bind(QueueSpec.consumer("g", "c", exchange="e", dead_letter_exchange="dlx"))
        await mq.connect()
        assert recorder.calls == ["connect", "exchange:dlx", "queue:g.c.dlq", "start"]

    async def test_connect_without_declaring_only_starts(self) -> None:
        mq, recorder = _recorded(declare_topology=False)
        mq._bind(QueueSpec.consumer("g", "c", exchange="e", dead_letter_exchange="dlx"))
        await mq.connect()
        assert recorder.calls == ["start"]


class TestReplayRouting:
    """The argument checks run before anything touches the broker."""

    async def test_a_bare_name_needs_into(self) -> None:
        mq, _ = _recorded()
        mq._started = True
        with pytest.raises(ValueError, match="into="):
            await mq.replay_dead_letters("orders.dlq")

    async def test_a_spec_refuses_into(self) -> None:
        mq, _ = _recorded()
        mq._started = True
        spec = QueueSpec.consumer("g", "c", exchange="e", dead_letter_exchange="dlx")
        with pytest.raises(ValueError, match="only for a bare queue name"):
            await mq.replay_dead_letters(spec, into="elsewhere")

    async def test_a_spec_without_a_dead_letter_queue_is_refused(self) -> None:
        mq, _ = _recorded()
        mq._started = True
        with pytest.raises(ValueError, match="names no dead-letter queue"):
            await mq.replay_dead_letters(QueueSpec(name="orders.paid"))

    async def test_limit_must_be_positive(self) -> None:
        mq, _ = _recorded()
        mq._started = True
        with pytest.raises(ValueError, match="limit"):
            await mq.replay_dead_letters("orders.dlq", into="orders", limit=0)

    async def test_a_transport_without_queues_is_refused(self) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)
        object.__setattr__(mq, "broker", _NatsStandIn())
        mq._started = True
        with pytest.raises(NotImplementedError, match="AMQP"):
            await mq.replay_dead_letters("orders.dlq", into="orders")

    async def test_before_connect_is_refused(self) -> None:
        mq, _ = _recorded()
        with pytest.raises(RuntimeError, match="connect"):
            await mq.replay_dead_letters("orders.dlq", into="orders")


class TestReplayHeaders:
    def test_the_death_record_is_dropped(self) -> None:
        """Kept, it would spend the retry budget of the replayed message."""
        headers = dict.fromkeys(DEATH_HEADERS, "x") | {"traceparent": "00-ab"}
        assert replay_headers(headers) == {"traceparent": "00-ab"}

    def test_no_headers_is_an_empty_table(self) -> None:
        assert replay_headers(None) == {}
