"""Tests for tempest_fastapi_sdk.queue.topology.

The translation is asserted on the ``arguments`` table RabbitMQ actually
receives, because that table *is* the feature — a queue declared without
``x-dead-letter-exchange`` accepts messages and drops every failure, and
nothing about the call site would look wrong.

The refusal path gets equal weight. Dropping a field a transport cannot
express is the failure this module exists to prevent, so the tests pin
that it raises, and pin just as hard that a spec asking for nothing
beyond a name stays portable.
"""

import pytest
from faststream.rabbit import ExchangeType

from tempest_fastapi_sdk.queue import (
    DeadLetterSpec,
    MessageBroker,
    QueueSpec,
    QueueType,
    UnsupportedTopologyError,
)
from tempest_fastapi_sdk.queue.topology import (
    DEAD_LETTER_EXCHANGE_ARG,
    DEAD_LETTER_ROUTING_KEY_ARG,
    MAX_LENGTH_ARG,
    MAX_PRIORITY_ARG,
    MESSAGE_TTL_ARG,
    QUEUE_TYPE_ARG,
    Transport,
    channel_name,
    detect_transport,
    rabbit_arguments,
    require_supported,
    resolve_channel,
    to_rabbit_queue,
    unsupported_fields,
)

AMQP_URL = "amqp://guest:guest@localhost:5672/"
REDIS_URL = "redis://localhost:6379/0"


class TestSpecValidation:
    def test_a_name_is_enough(self) -> None:
        assert QueueSpec(name="orders.paid").name == "orders.paid"

    def test_durable_defaults_to_true(self) -> None:
        """A queue that does not survive a restart is never the default."""
        assert QueueSpec(name="orders.paid").durable is True

    def test_priority_on_a_quorum_queue_is_refused(self) -> None:
        """RabbitMQ implements priorities only for classic queues."""
        with pytest.raises(UnsupportedTopologyError, match="max_priority"):
            QueueSpec(
                name="orders.paid",
                max_priority=5,
                queue_type=QueueType.QUORUM,
            )

    @pytest.mark.parametrize(
        "field",
        ["message_ttl_ms", "max_priority", "max_length"],
    )
    def test_non_positive_numbers_are_refused(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            QueueSpec(name="orders.paid", **{field: 0})

    def test_the_spec_is_frozen(self) -> None:
        """It is shared as a module constant; mutation would be a bug."""
        spec = QueueSpec(name="orders.paid")
        with pytest.raises(AttributeError):
            spec.name = "other"  # type: ignore[misc]


class TestChannelName:
    def test_a_string_is_itself(self) -> None:
        assert channel_name("orders.paid") == "orders.paid"

    def test_a_spec_yields_its_name(self) -> None:
        assert channel_name(QueueSpec(name="orders.paid")) == "orders.paid"


class TestRabbitArguments:
    def test_queue_type_is_always_present(self) -> None:
        """Re-declaring with different arguments is a PRECONDITION_FAILED."""
        args = rabbit_arguments(QueueSpec(name="q"))
        assert args[QUEUE_TYPE_ARG] == "classic"

    def test_a_bare_spec_asks_for_nothing_else(self) -> None:
        assert rabbit_arguments(QueueSpec(name="q")) == {QUEUE_TYPE_ARG: "classic"}

    def test_dead_letter_exchange_is_translated(self) -> None:
        args = rabbit_arguments(
            QueueSpec(name="q", dead_letter=DeadLetterSpec(exchange="dlx")),
        )
        assert args[DEAD_LETTER_EXCHANGE_ARG] == "dlx"

    def test_routing_key_is_omitted_when_not_set(self) -> None:
        """Omitting it keeps the message's original key, which fans out."""
        args = rabbit_arguments(
            QueueSpec(name="q", dead_letter=DeadLetterSpec(exchange="dlx")),
        )
        assert DEAD_LETTER_ROUTING_KEY_ARG not in args

    def test_routing_key_is_translated_when_set(self) -> None:
        args = rabbit_arguments(
            QueueSpec(
                name="q",
                dead_letter=DeadLetterSpec(exchange="dlx", routing_key="q.dead"),
            ),
        )
        assert args[DEAD_LETTER_ROUTING_KEY_ARG] == "q.dead"

    def test_ttl_priority_and_length_are_translated(self) -> None:
        args = rabbit_arguments(
            QueueSpec(name="q", message_ttl_ms=30_000, max_priority=5, max_length=100),
        )
        assert args[MESSAGE_TTL_ARG] == 30_000
        assert args[MAX_PRIORITY_ARG] == 5
        assert args[MAX_LENGTH_ARG] == 100

    def test_quorum_reaches_the_arguments(self) -> None:
        args = rabbit_arguments(QueueSpec(name="q", queue_type=QueueType.QUORUM))
        assert args[QUEUE_TYPE_ARG] == "quorum"


class TestTransportDetection:
    def test_rabbit_broker_is_detected(self) -> None:
        assert MessageBroker.rabbitmq(AMQP_URL).transport is Transport.RABBITMQ

    def test_an_unknown_broker_is_not_guessed(self) -> None:
        """Unknown means 'supports nothing but a name' — the safe way."""

        class SomeCustomBroker:
            pass

        assert detect_transport(SomeCustomBroker()) is Transport.UNKNOWN


class TestPortability:
    def test_a_bare_spec_translates_everywhere(self) -> None:
        spec = QueueSpec(name="orders.paid")
        for transport in Transport:
            assert unsupported_fields(spec, transport) == []

    def test_rabbitmq_supports_every_field(self) -> None:
        spec = QueueSpec(
            name="q",
            durable=False,
            dead_letter=DeadLetterSpec(exchange="dlx"),
            message_ttl_ms=1,
            max_priority=5,
            max_length=10,
        )
        assert unsupported_fields(spec, Transport.RABBITMQ) == []

    def test_another_transport_reports_each_set_field(self) -> None:
        spec = QueueSpec(
            name="q",
            dead_letter=DeadLetterSpec(exchange="dlx"),
            message_ttl_ms=1,
        )
        missing = unsupported_fields(spec, Transport.NATS)
        assert set(missing) == {"dead_letter", "message_ttl_ms"}

    def test_an_unsupported_field_raises_naming_it(self) -> None:
        """Dropping it silently is the defect; the message has to say what."""
        spec = QueueSpec(name="q", dead_letter=DeadLetterSpec(exchange="dlx"))
        with pytest.raises(UnsupportedTopologyError) as exc:
            require_supported(spec, Transport.KAFKA)
        assert "dead_letter" in str(exc.value)
        assert "kafka" in str(exc.value)

    def test_a_bare_spec_never_raises(self) -> None:
        require_supported(QueueSpec(name="q"), Transport.REDIS)


class TestResolveChannel:
    def test_a_string_passes_through_untouched(self) -> None:
        assert resolve_channel("orders.paid", Transport.RABBITMQ) == "orders.paid"

    def test_a_spec_becomes_a_rabbit_queue(self) -> None:
        queue = resolve_channel(
            QueueSpec(name="q", dead_letter=DeadLetterSpec(exchange="dlx")),
            Transport.RABBITMQ,
        )
        assert queue.name == "q"
        assert queue.arguments[DEAD_LETTER_EXCHANGE_ARG] == "dlx"

    def test_a_portable_spec_degrades_to_its_name(self) -> None:
        assert resolve_channel(QueueSpec(name="q"), Transport.NATS) == "q"

    def test_durable_reaches_the_queue(self) -> None:
        queue = to_rabbit_queue(QueueSpec(name="q", durable=False))
        assert queue.durable is False


class TestBrokerIntegration:
    def test_subscribing_with_a_spec_registers_it(self) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)
        spec = QueueSpec(name="orders.paid", dead_letter=DeadLetterSpec("dlx"))

        @mq.on(spec)
        async def handle(message: dict[str, str]) -> None: ...

        assert mq.specs == {"orders.paid": spec}

    def test_a_string_channel_registers_nothing(self) -> None:
        """Nothing to declare, so the declaration step stays a no-op."""
        mq = MessageBroker.rabbitmq(AMQP_URL)

        @mq.on("orders.paid")
        async def handle(message: dict[str, str]) -> None: ...

        assert mq.specs == {}

    def test_the_registry_is_a_copy(self) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)

        @mq.on(QueueSpec(name="orders.paid"))
        async def handle(message: dict[str, str]) -> None: ...

        mq.specs.clear()
        assert "orders.paid" in mq.specs

    def test_the_arguments_reach_the_subscriber(self) -> None:
        """End to end: what the call site declares is what AMQP receives."""
        mq = MessageBroker.rabbitmq(AMQP_URL)
        subscriber = mq.broker.subscriber(
            resolve_channel(
                QueueSpec(
                    name="orders.paid",
                    dead_letter=DeadLetterSpec(exchange="dlx"),
                    message_ttl_ms=60_000,
                ),
                Transport.RABBITMQ,
            ),
        )
        assert subscriber.queue.arguments[DEAD_LETTER_EXCHANGE_ARG] == "dlx"
        assert subscriber.queue.arguments[MESSAGE_TTL_ARG] == 60_000

    def test_declare_topology_can_be_turned_off(self) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL, declare_topology=False)
        assert mq._declare_topology is False

    def test_declare_topology_is_on_by_default(self) -> None:
        assert MessageBroker.rabbitmq(AMQP_URL)._declare_topology is True

    def test_every_constructor_names_declare_topology(self) -> None:
        """It reached the facade through ``**options`` on three of four.

        A keyword the facade consumes but does not name is invisible to
        the type checker and absent from autocomplete, so the only way to
        find it was to read the source. It was also undocumented on
        ``redis``/``kafka``/``nats`` — a supported parameter nobody could
        discover.
        """
        import inspect

        for name in ("rabbitmq", "redis", "kafka", "nats"):
            parameters = inspect.signature(getattr(MessageBroker, name)).parameters
            assert "declare_topology" in parameters, name
            assert parameters["declare_topology"].annotation == "bool", name
            assert parameters["declare_topology"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_another_transport_takes_declare_topology(self) -> None:
        """It was consumed by ``redis``/``kafka``/``nats`` and documented
        by none of them."""
        assert (
            MessageBroker.redis(REDIS_URL, declare_topology=False)._declare_topology
            is False
        )

    async def test_declaring_on_a_transport_without_exchanges_is_a_noop(
        self,
    ) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)
        object.__setattr__(mq, "broker", _FakeNatsBroker())
        assert await mq.declare_topology() == []

    async def test_nothing_is_declared_without_a_dead_letter(self) -> None:
        mq = MessageBroker.rabbitmq(AMQP_URL)

        @mq.on(QueueSpec(name="orders.paid"))
        async def handle(message: dict[str, str]) -> None: ...

        assert await mq.declare_topology() == []

    async def test_each_dead_letter_exchange_is_declared_once(self) -> None:
        """Two queues sharing one DLX must not declare it twice."""
        mq = MessageBroker.rabbitmq(AMQP_URL)
        recorder = _RecordingRabbitBroker()
        object.__setattr__(mq, "broker", recorder)
        for name in ("orders.paid", "orders.shipped"):
            mq._bind(QueueSpec(name=name, dead_letter=DeadLetterSpec("dlx")))

        assert await mq.declare_topology() == ["dlx"]
        assert [exchange.name for exchange in recorder.declared] == ["dlx"]

    async def test_the_exchange_is_durable_and_topic(self) -> None:
        """A transient DLX would vanish on restart, taking routing with it.

        The type is asserted as the enum, not as its string value:
        ``RabbitExchange(type="topic")`` stores the raw string and
        FastStream then reads ``exchange.type.value`` when declaring, so a
        string here is an ``AttributeError`` at ``connect()`` — which a
        tolerant ``getattr(..., "value", ...)`` assertion would hide.
        """
        mq = MessageBroker.rabbitmq(AMQP_URL)
        recorder = _RecordingRabbitBroker()
        object.__setattr__(mq, "broker", recorder)
        mq._bind(QueueSpec(name="q", dead_letter=DeadLetterSpec("dlx")))

        await mq.declare_topology()
        declared = recorder.declared[0]
        assert declared.durable is True
        assert declared.type is ExchangeType.TOPIC


class _FakeNatsBroker:
    """Stand-in whose class name makes ``detect_transport`` say NATS."""


class _RecordingRabbitBroker:
    """Stand-in RabbitBroker that records declared exchanges."""

    def __init__(self) -> None:
        """Start with nothing declared."""
        self.declared: list[object] = []

    async def declare_exchange(self, exchange: object) -> object:
        """Record the exchange instead of talking to a server.

        Args:
            exchange (object): The exchange FastStream would declare.

        Returns:
            object: The same exchange.
        """
        self.declared.append(exchange)
        return exchange
