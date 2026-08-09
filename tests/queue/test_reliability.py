"""Tests for tempest_fastapi_sdk.queue.reliability.

The middleware is exercised through its real ``consume_scope`` with a
stand-in message, because the behaviour that matters is conditional: the
sink must fire on the delivery that exhausts the budget and **not** on
the ones before it, and the exception must always be re-raised so the
broker still rejects. A test that only checked "sink was called" would
pass for a middleware that alerts on every attempt.
"""

from typing import Any

import pytest

from tempest_fastapi_sdk.queue import (
    ConsumerRetryPolicy,
    QueueSpec,
    QueueType,
    retry_queues,
)
from tempest_fastapi_sdk.queue.reliability import (
    DEAD_SUFFIX,
    RETRY_SUFFIX,
    delivery_attempt,
    make_dead_letter_middleware,
)
from tempest_fastapi_sdk.queue.topology import (
    DEAD_LETTER_EXCHANGE_ARG,
    DEAD_LETTER_ROUTING_KEY_ARG,
    MESSAGE_TTL_ARG,
    rabbit_arguments,
)
from tempest_fastapi_sdk.tasks import DeadLetter


def _instantiate(middleware_cls: Any, message: Any) -> Any:
    """Build a middleware instance the way FastStream does.

    ``BaseMiddleware`` takes the message positionally and a ``ContextRepo``
    keyword-only, which the framework supplies. Constructing it the same
    way here keeps the test exercising the real class rather than a
    reshaped copy of it.

    Args:
        middleware_cls (Any): The middleware class under test.
        message (Any): The message being consumed.

    Returns:
        Any: The instantiated middleware.
    """
    from faststream._internal.context.repository import ContextRepo

    return middleware_cls(message, context=ContextRepo())


class _Message:
    """Stand-in for a FastStream message."""

    def __init__(
        self,
        *,
        headers: dict[str, Any] | None = None,
        queue: str = "orders.paid",
        message_id: str = "m-1",
        body: bytes = b"{}",
    ) -> None:
        """Record the attributes the middleware reads."""
        self.headers: dict[str, Any] = headers or {}
        self.queue: str = queue
        self.message_id: str = message_id
        self.body: bytes = body


class _RecordingSink:
    """Sink that keeps every dead letter it is handed."""

    def __init__(self) -> None:
        """Start empty."""
        self.received: list[DeadLetter] = []

    async def __call__(self, dead_letter: DeadLetter) -> None:
        """Record one dead letter.

        Args:
            dead_letter (DeadLetter): The reported failure.
        """
        self.received.append(dead_letter)


def _deaths(count: int) -> dict[str, Any]:
    """Build an ``x-death`` header recording ``count`` rejections.

    Args:
        count (int): How many times the message was dead-lettered.

    Returns:
        dict[str, Any]: The header table.
    """
    return {"x-death": [{"count": count, "queue": "orders.paid"}]}


class TestPolicyValidation:
    def test_defaults_are_usable(self) -> None:
        policy = ConsumerRetryPolicy()
        assert policy.max_attempts >= 1
        assert policy.delay_ms > 0

    def test_zero_attempts_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            ConsumerRetryPolicy(max_attempts=0)

    def test_non_positive_delay_is_refused(self) -> None:
        with pytest.raises(ValueError, match="delay_ms"):
            ConsumerRetryPolicy(delay_ms=0)

    def test_one_attempt_means_no_retry(self) -> None:
        assert ConsumerRetryPolicy(max_attempts=1).max_attempts == 1


class TestDeliveryAttempt:
    def test_a_fresh_message_is_the_first_delivery(self) -> None:
        assert delivery_attempt(_Message()) == 1

    def test_x_death_counts_previous_rejections(self) -> None:
        assert delivery_attempt(_Message(headers=_deaths(2))) == 3

    def test_a_missing_header_falls_back_to_one(self) -> None:
        """An unreadable header costs an attempt, never drops a message."""
        assert delivery_attempt(_Message(headers={"x-death": "garbage"})) == 1

    def test_counts_accumulate_across_entries(self) -> None:
        message = _Message(
            headers={"x-death": [{"count": 1}, {"count": 2}]},
        )
        assert delivery_attempt(message) == 4

    def test_a_message_without_headers_is_handled(self) -> None:
        class Bare:
            pass

        assert delivery_attempt(Bare()) == 1


class TestRetryTopology:
    def test_the_main_queue_dead_letters_to_the_retry_exchange(self) -> None:
        topology = retry_queues(
            "orders.paid",
            retry_exchange="retry",
            main_exchange="main",
            dead_exchange="dead",
        )
        args = rabbit_arguments(topology.main)
        assert args[DEAD_LETTER_EXCHANGE_ARG] == "retry"

    def test_the_retry_queue_returns_to_the_main_exchange(self) -> None:
        """This is what makes the delay work without a broker plugin."""
        topology = retry_queues(
            "orders.paid",
            retry_exchange="retry",
            main_exchange="main",
            dead_exchange="dead",
        )
        args = rabbit_arguments(topology.retry)
        assert args[DEAD_LETTER_EXCHANGE_ARG] == "main"
        assert args[DEAD_LETTER_ROUTING_KEY_ARG] == "orders.paid"

    def test_the_delay_becomes_the_retry_queue_ttl(self) -> None:
        topology = retry_queues(
            "orders.paid",
            ConsumerRetryPolicy(delay_ms=5_000),
            retry_exchange="retry",
            main_exchange="main",
            dead_exchange="dead",
        )
        assert rabbit_arguments(topology.retry)[MESSAGE_TTL_ARG] == 5_000

    def test_the_dead_queue_routes_nowhere(self) -> None:
        """Terminal by construction — nothing should escape it."""
        topology = retry_queues(
            "orders.paid",
            retry_exchange="retry",
            main_exchange="main",
            dead_exchange="dead",
        )
        assert topology.dead.dead_letter is None

    def test_the_queues_are_named_from_the_channel(self) -> None:
        topology = retry_queues(
            "orders.paid",
            retry_exchange="retry",
            main_exchange="main",
            dead_exchange="dead",
        )
        assert topology.main.name == "orders.paid"
        assert topology.retry.name == f"orders.paid{RETRY_SUFFIX}"
        assert topology.dead.name == f"orders.paid{DEAD_SUFFIX}"

    def test_the_queue_type_reaches_all_three(self) -> None:
        topology = retry_queues(
            "orders.paid",
            retry_exchange="retry",
            main_exchange="main",
            dead_exchange="dead",
            queue_type=QueueType.QUORUM,
        )
        for spec in (topology.main, topology.retry, topology.dead):
            assert spec.queue_type is QueueType.QUORUM

    def test_every_queue_is_a_spec(self) -> None:
        topology = retry_queues(
            "orders.paid",
            retry_exchange="retry",
            main_exchange="main",
            dead_exchange="dead",
        )
        for spec in (topology.main, topology.retry, topology.dead):
            assert isinstance(spec, QueueSpec)


class TestDeadLetterMiddleware:
    async def _run(
        self,
        middleware_cls: Any,
        message: _Message,
        *,
        fail: bool = True,
    ) -> Any:
        """Drive one consume through the middleware.

        Args:
            middleware_cls (Any): The middleware class under test.
            message (_Message): The message being consumed.
            fail (bool): Whether the handler raises.

        Returns:
            Any: The handler's return value when it does not raise.
        """

        async def call_next(_: Any) -> str:
            if fail:
                raise ValueError("edge-case bug")
            return "ok"

        return await _instantiate(middleware_cls, message).consume_scope(
            call_next, message
        )

    async def test_a_successful_consume_reports_nothing(self) -> None:
        sink = _RecordingSink()
        cls = make_dead_letter_middleware(sink, max_attempts=3)
        assert await self._run(cls, _Message(), fail=False) == "ok"
        assert sink.received == []

    async def test_an_early_attempt_is_not_reported(self) -> None:
        """Alerting on every attempt turns one bad message into a stream."""
        sink = _RecordingSink()
        cls = make_dead_letter_middleware(sink, max_attempts=3)
        with pytest.raises(ValueError):
            await self._run(cls, _Message(headers=_deaths(0)))
        assert sink.received == []

    async def test_the_exhausting_attempt_is_reported(self) -> None:
        sink = _RecordingSink()
        cls = make_dead_letter_middleware(sink, max_attempts=3)
        with pytest.raises(ValueError):
            await self._run(cls, _Message(headers=_deaths(2)))
        assert len(sink.received) == 1

    async def test_the_exception_is_always_re_raised(self) -> None:
        """The broker must still reject; swallowing it would ack the bug."""
        sink = _RecordingSink()
        cls = make_dead_letter_middleware(sink, max_attempts=1)
        with pytest.raises(ValueError, match="edge-case bug"):
            await self._run(cls, _Message())

    async def test_the_record_carries_the_channel_and_message_id(self) -> None:
        sink = _RecordingSink()
        cls = make_dead_letter_middleware(sink, max_attempts=1)
        with pytest.raises(ValueError):
            await self._run(cls, _Message(queue="orders.paid", message_id="m-9"))
        dead = sink.received[0]
        assert dead.task_name == "orders.paid"
        assert dead.task_id == "m-9"

    async def test_the_record_carries_the_body_and_exception(self) -> None:
        sink = _RecordingSink()
        cls = make_dead_letter_middleware(sink, max_attempts=1)
        with pytest.raises(ValueError):
            await self._run(cls, _Message(body=b'{"order_id":1}'))
        dead = sink.received[0]
        assert dead.kwargs["body"] == b'{"order_id":1}'
        assert isinstance(dead.exception, ValueError)

    async def test_it_reuses_the_task_dead_letter_type(self) -> None:
        """One admin screen for a dead task and a dead event."""
        sink = _RecordingSink()
        cls = make_dead_letter_middleware(sink, max_attempts=1)
        with pytest.raises(ValueError):
            await self._run(cls, _Message())
        assert isinstance(sink.received[0], DeadLetter)


class TestBrokerWiring:
    def test_dead_letter_installs_a_middleware(self) -> None:
        from tempest_fastapi_sdk.queue import MessageBroker

        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")
        before = len(mq.broker.middlewares)
        mq.dead_letter(_RecordingSink())
        assert len(mq.broker.middlewares) == before + 1

    def test_enable_metrics_installs_a_middleware(self) -> None:
        from prometheus_client import CollectorRegistry

        from tempest_fastapi_sdk.queue import MessageBroker, QueueMetrics

        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")
        before = len(mq.broker.middlewares)
        mq.enable_metrics(QueueMetrics(CollectorRegistry()))
        assert len(mq.broker.middlewares) == before + 1


class TestQueueMetrics:
    async def test_a_success_counts_ok(self) -> None:
        from prometheus_client import CollectorRegistry

        from tempest_fastapi_sdk.queue import QueueMetrics

        registry = CollectorRegistry()
        metrics = QueueMetrics(registry)

        async def call_next(_: Any) -> str:
            return "ok"

        message = _Message()
        await _instantiate(metrics.middleware(), message).consume_scope(
            call_next, message
        )
        assert (
            registry.get_sample_value(
                "queue_messages_total",
                {"channel": "orders.paid", "status": "ok"},
            )
            == 1.0
        )

    async def test_a_failure_counts_error_and_re_raises(self) -> None:
        from prometheus_client import CollectorRegistry

        from tempest_fastapi_sdk.queue import QueueMetrics

        registry = CollectorRegistry()
        metrics = QueueMetrics(registry)

        async def call_next(_: Any) -> str:
            raise ValueError("boom")

        message = _Message()
        with pytest.raises(ValueError):
            await _instantiate(metrics.middleware(), message).consume_scope(
                call_next, message
            )
        assert (
            registry.get_sample_value(
                "queue_messages_total",
                {"channel": "orders.paid", "status": "error"},
            )
            == 1.0
        )

    async def test_duration_is_observed_even_on_failure(self) -> None:
        """A slow handler that then fails is exactly what you want timed."""
        from prometheus_client import CollectorRegistry

        from tempest_fastapi_sdk.queue import QueueMetrics

        registry = CollectorRegistry()
        metrics = QueueMetrics(registry)

        async def call_next(_: Any) -> str:
            raise ValueError("boom")

        message = _Message()
        with pytest.raises(ValueError):
            await _instantiate(metrics.middleware(), message).consume_scope(
                call_next, message
            )
        assert (
            registry.get_sample_value(
                "queue_message_duration_seconds_count",
                {"channel": "orders.paid"},
            )
            == 1.0
        )


class TestDeclareRetryTopology:
    """Declaring the queues is not the same as wiring them.

    Verified against a real RabbitMQ: with the bindings the message comes
    back on schedule, and without them it is delivered once and vanishes
    into an exchange with nothing behind it. These tests pin that all
    three queues are declared *and* bound, which is the part a
    queues-only assertion would miss.
    """

    class _RabbitRecorder:
        """Stand-in RabbitBroker recording declarations and bindings.

        Named with ``Rabbit`` on purpose: ``detect_transport`` classifies
        by class name, so a neutrally-named stand-in reads as ``UNKNOWN``
        and the method under test refuses before doing anything.
        """

        def __init__(self) -> None:
            """Start with nothing recorded."""
            self.exchanges: list[Any] = []
            self.queues: list[Any] = []
            self.bindings: list[tuple[str, str, str]] = []

        async def declare_exchange(self, exchange: Any) -> Any:
            """Record an exchange declaration.

            Returns:
                Any: The exchange.
            """
            self.exchanges.append(exchange)
            return exchange

        async def declare_queue(self, queue: Any) -> Any:
            """Record a queue declaration and hand back a bindable stub.

            Returns:
                Any: An object whose ``bind`` records the binding.
            """
            self.queues.append(queue)
            recorder = self

            class _Bound:
                async def bind(self, exchange: str, *, routing_key: str) -> None:
                    recorder.bindings.append((queue.name, exchange, routing_key))

            return _Bound()

    def _topology(self) -> Any:
        """Build a retry topology for the tests.

        Returns:
            Any: The topology.
        """
        return retry_queues(
            "orders.paid",
            retry_exchange="orders.retry",
            main_exchange="orders",
            dead_exchange="orders.dead",
        )

    async def _declare(self) -> _RabbitRecorder:
        """Run declare_retry_topology against the recorder.

        Returns:
            _RabbitRecorder: The recorder, after declaration.
        """
        from tempest_fastapi_sdk.queue import MessageBroker

        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")
        recorder = self._RabbitRecorder()
        object.__setattr__(mq, "broker", recorder)
        await mq.declare_retry_topology(self._topology())
        return recorder

    async def test_all_three_queues_are_declared(self) -> None:
        recorder = await self._declare()
        assert [q.name for q in recorder.queues] == [
            "orders.paid",
            "orders.paid.retry",
            "orders.paid.dead",
        ]

    async def test_all_three_exchanges_are_declared(self) -> None:
        recorder = await self._declare()
        assert [e.name for e in recorder.exchanges] == [
            "orders",
            "orders.retry",
            "orders.dead",
        ]

    async def test_every_queue_is_bound(self) -> None:
        """The step whose absence made the chain silently drop messages."""
        recorder = await self._declare()
        assert recorder.bindings == [
            ("orders.paid", "orders", "orders.paid"),
            ("orders.paid.retry", "orders.retry", "orders.paid"),
            ("orders.paid.dead", "orders.dead", "orders.paid"),
        ]

    async def test_the_exchanges_are_durable_topics(self) -> None:
        from faststream.rabbit import ExchangeType

        recorder = await self._declare()
        for exchange in recorder.exchanges:
            assert exchange.durable is True
            assert exchange.type is ExchangeType.TOPIC

    async def test_a_transport_without_exchanges_is_refused(self) -> None:
        """Declaring nothing and reporting success would be worse."""
        from tempest_fastapi_sdk.queue import MessageBroker

        class _Nats:
            pass

        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")
        object.__setattr__(mq, "broker", _Nats())
        with pytest.raises(NotImplementedError, match="exchanges"):
            await mq.declare_retry_topology(self._topology())

    def test_the_topology_carries_its_exchange_names(self) -> None:
        """Without them the object cannot declare itself."""
        topology = self._topology()
        assert topology.channel == "orders.paid"
        assert topology.main_exchange == "orders"
        assert topology.retry_exchange == "orders.retry"
        assert topology.dead_exchange == "orders.dead"
