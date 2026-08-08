"""``MessageBroker`` — a typed, transport-agnostic facade over FastStream.

FastStream is powerful but its API changes shape with the transport:
you subscribe with ``@broker.subscriber("q")`` and publish with
``broker.publish(msg, queue="q")`` on RabbitMQ, ``topic=`` on Kafka,
``subject=`` on NATS. :class:`MessageBroker` hides all of that behind a
single mental model — a **channel** (a plain string) you publish to and
subscribe on — so application code reads the same regardless of the
transport underneath.

You never import ``faststream`` in application code: pick the transport
with a constructor (:meth:`MessageBroker.rabbitmq`, :meth:`redis`,
:meth:`kafka`, :meth:`nats`), declare consumers with :meth:`on`, and
publish with :meth:`publish`. The raw broker stays reachable at
:attr:`broker` for the rare case the facade doesn't cover.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import uuid4

from tempest_fastapi_sdk.queue.topology import (
    QueueSpec,
    Transport,
    channel_name,
    detect_transport,
    resolve_channel,
)
from tempest_fastapi_sdk.queue.tracing import inject_context

if TYPE_CHECKING:
    from faststream.broker.core.usecase import BrokerUsecase

    from tempest_fastapi_sdk.queue.consumer import Consumer

logger = logging.getLogger("tempest_fastapi_sdk.queue")

Handler = TypeVar("Handler", bound=Callable[..., Awaitable[Any]])
"""A message handler — an async callable taking the decoded message."""


def _schema_entry(
    handler: Callable[[Any], Awaitable[Any]],
    schema: type,
) -> Callable[[Any], Awaitable[None]]:
    """Build a single-parameter subscriber that decodes into ``schema``.

    FastStream reads the handler signature to decode the payload. The
    constructor-form :class:`~tempest_fastapi_sdk.queue.Consumer` passes
    its schema explicitly, so we wrap the user's ``handle`` in a function
    whose one parameter is annotated with that schema — making the
    explicit schema the decoding contract, no annotation-sniffing.

    Args:
        handler (Callable[[Any], Awaitable[Any]]): The consumer's handler.
        schema (type): The Pydantic model to decode the payload into.

    Returns:
        Callable[[Any], Awaitable[None]]: The wrapped subscriber.
    """

    async def entry(message: Any) -> None:
        await handler(message)

    entry.__annotations__ = {"message": schema, "return": None}
    return entry


def _require(module: str, extra: str) -> Any:
    """Import an optional FastStream backend or raise a helpful error.

    Args:
        module (str): The dotted module to import (e.g.
            ``"faststream.rabbit"``).
        extra (str): The pip extra that provides it (e.g. ``"queue"``).

    Returns:
        Any: The imported module.

    Raises:
        ImportError: When the backend is not installed, with the exact
            install command to fix it.
    """
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"This transport requires the optional [{extra}] extra. "
            f"Install with: pip install tempest-fastapi-sdk[{extra}]",
        ) from exc


def _publish_accepts(publish: Any, name: str) -> bool:
    """Whether a broker's ``publish`` takes ``name`` as a keyword.

    The facade adds keywords of its own — ``message_id`` for
    deduplication, ``headers`` for trace propagation — but FastStream's
    ``publish`` signature differs per transport and most of them take no
    ``**kwargs``. ``RedisBroker.publish`` has no ``message_id`` at all, so
    sending one unconditionally turns **every** publish on that transport
    into a ``TypeError``.

    Args:
        publish (Any): The bound ``broker.publish``.
        name (str): The keyword the facade wants to add.

    Returns:
        bool: Whether it is safe to pass. A signature that cannot be
        introspected answers ``False`` and logs once: dropping the
        keyword costs a feature (deduplication, or a trace link), while
        sending an unsupported one costs the publish itself.
    """
    try:
        parameters = inspect.signature(publish).parameters
    except (TypeError, ValueError):
        logger.warning(
            "Could not introspect %s.publish; not adding %r. Deduplication "
            "and trace propagation need it, so pass it explicitly if the "
            "transport supports it.",
            type(publish).__name__,
            name,
        )
        return False
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return True
    return name in parameters


def _with_prefetch(
    options: dict[str, Any],
    prefetch: Any,
    key: str,
) -> dict[str, Any]:
    """Put a RabbitMQ ``Channel`` carrying ``prefetch`` under ``key``.

    Prefetch is ``basic.qos`` and FastStream carries it on a ``Channel``
    object, not as a scalar keyword — so the facade's flat ``prefetch=``
    has to be translated. Kept as a function so the translation is
    testable without reaching into FastStream's private broker state,
    where the configured channel actually ends up.

    An explicit ``channel`` / ``default_channel`` always wins: silently
    rebuilding one the caller configured would drop everything else they
    set on it (publisher confirms, channel number, global QoS).

    Args:
        options (dict[str, Any]): The keyword arguments being assembled.
        prefetch (Any): The cap, or ``None`` to leave ``options`` alone.
        key (str): ``"default_channel"`` for the broker, ``"channel"``
            for a single subscriber.

    Returns:
        dict[str, Any]: The same mapping, mutated in place for the
        caller's convenience.

    Raises:
        ImportError: When the ``[queue]`` extra is not installed.
    """
    if prefetch is None or key in options:
        return options
    rabbit = _require("faststream.rabbit", "queue")
    options[key] = rabbit.Channel(prefetch_count=int(prefetch))
    return options


class MessageBroker:
    """Typed, transport-agnostic publish/subscribe over FastStream.

    A **message broker** is for event-driven fan-out between services or
    workers: a producer :meth:`publish`-es an event to a **channel** and
    every consumer subscribed to that channel (via :meth:`on`) receives
    it. Delivery is at-least-once, so handlers should be idempotent.

    Pick the transport with a constructor rather than importing
    FastStream yourself::

        from pydantic import BaseModel
        from tempest_fastapi_sdk.queue import MessageBroker

        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")

        class OrderPaid(BaseModel):
            order_id: str

        @mq.on("orders.paid")
        async def handle(event: OrderPaid) -> None:
            await mark_paid(event.order_id)

        # FastAPI lifespan
        await mq.connect()
        await mq.publish("orders.paid", OrderPaid(order_id="abc"))
        await mq.disconnect()

    The handler's type hint (``event: OrderPaid``) drives decoding:
    FastStream validates the inbound payload into that Pydantic model
    before your function runs, so a malformed message never reaches your
    code. :meth:`publish` accepts a Pydantic model, a ``dict``, ``str``
    or ``bytes`` — models are serialized to JSON automatically.

    Attributes:
        broker (BrokerUsecase[Any, Any]): The underlying FastStream
            broker — the escape hatch for transport features the facade
            doesn't wrap.
    """

    def __init__(
        self,
        broker: BrokerUsecase[Any, Any],
        *,
        declare_topology: bool = True,
    ) -> None:
        """Wrap an already-constructed FastStream broker.

        Prefer the transport constructors (:meth:`rabbitmq`, :meth:`redis`,
        :meth:`kafka`, :meth:`nats`); use this directly only to inject a
        pre-configured or custom broker (e.g. a test broker).

        Args:
            broker (BrokerUsecase[Any, Any]): A FastStream broker.
            declare_topology (bool): Whether :meth:`connect` declares the
                dead-letter exchanges named by the registered
                :class:`~tempest_fastapi_sdk.queue.QueueSpec`. ``True``
                makes a spec work with no infrastructure step, which is
                what a small team wants. Set it to ``False`` where the
                broker is managed and the application has no permission
                to declare — the exchanges then have to exist already,
                and a missing one fails at subscribe time.
        """
        self.broker: BrokerUsecase[Any, Any] = broker
        self._started: bool = False
        self._declare_topology: bool = declare_topology
        self._specs: dict[str, QueueSpec] = {}

    @property
    def transport(self) -> Transport:
        """Return which transport this facade is wrapping.

        Read from the broker class at call time, so it is correct for an
        injected broker as well as for one built by a constructor.

        Returns:
            Transport: The detected transport.
        """
        return detect_transport(self.broker)

    def _bind(self, channel: str | QueueSpec) -> Any:
        """Record a spec and translate the channel for this transport.

        Args:
            channel (str | QueueSpec): A bare channel or a full spec.

        Returns:
            Any: What FastStream expects for this transport.

        Raises:
            UnsupportedTopologyError: When the spec sets a field the
                transport cannot honor.
        """
        resolved = resolve_channel(channel, self.transport)
        if isinstance(channel, QueueSpec):
            self._specs[channel.name] = channel
        return resolved

    @property
    def specs(self) -> dict[str, QueueSpec]:
        """Return the topology declared through this broker, by channel.

        Returns:
            dict[str, QueueSpec]: A copy, so callers cannot mutate the
            registry the declaration step reads.
        """
        return dict(self._specs)

    # ------------------------------------------------------------------
    # Transport constructors
    # ------------------------------------------------------------------

    @classmethod
    def rabbitmq(cls, url: str, **options: Any) -> MessageBroker:
        """Build a RabbitMQ-backed broker (``[queue]`` extra).

        Args:
            url (str): AMQP URL, e.g.
                ``"amqp://guest:guest@localhost:5672/"``.
            **options (Any): Extra keyword arguments forwarded to
                ``faststream.rabbit.RabbitBroker``, plus two the facade
                consumes itself: ``declare_topology`` (see
                :meth:`__init__`) and ``prefetch``, which caps how many
                unacknowledged messages the broker pushes to this
                connection. Without a cap the broker delivers as fast as
                the consumer acks: one slow handler accumulates messages
                in process memory, one replica can take the whole batch
                and leave its siblings idle, and an unacked backlog is
                held in RAM until the worker is OOM-killed and the whole
                lot is redelivered. Per-consumer overrides go on
                :meth:`on`.

        Returns:
            MessageBroker: A facade around a ``RabbitBroker``.
        """
        declare_topology = bool(options.pop("declare_topology", True))
        _with_prefetch(options, options.pop("prefetch", None), "default_channel")
        rabbit = _require("faststream.rabbit", "queue")
        return cls(
            rabbit.RabbitBroker(url, **options),
            declare_topology=declare_topology,
        )

    @classmethod
    def redis(cls, url: str, **options: Any) -> MessageBroker:
        """Build a Redis-backed broker (``faststream[redis]``).

        Args:
            url (str): Redis URL, e.g. ``"redis://localhost:6379/0"``.
            **options (Any): Extra keyword arguments forwarded to
                ``faststream.redis.RedisBroker``.

        Returns:
            MessageBroker: A facade around a ``RedisBroker``.
        """
        declare_topology = bool(options.pop("declare_topology", True))
        redis = _require("faststream.redis", "queue")
        return cls(
            redis.RedisBroker(url, **options),
            declare_topology=declare_topology,
        )

    @classmethod
    def kafka(cls, *bootstrap_servers: str, **options: Any) -> MessageBroker:
        """Build a Kafka-backed broker (``faststream[kafka]``).

        Args:
            *bootstrap_servers (str): One or more ``host:port`` seeds.
            **options (Any): Extra keyword arguments forwarded to
                ``faststream.kafka.KafkaBroker``.

        Returns:
            MessageBroker: A facade around a ``KafkaBroker``.
        """
        declare_topology = bool(options.pop("declare_topology", True))
        kafka = _require("faststream.kafka", "queue")
        servers: str | list[str] = (
            list(bootstrap_servers)
            if len(bootstrap_servers) != 1
            else bootstrap_servers[0]
        )
        return cls(
            kafka.KafkaBroker(servers, **options),
            declare_topology=declare_topology,
        )

    @classmethod
    def nats(cls, servers: str | list[str], **options: Any) -> MessageBroker:
        """Build a NATS-backed broker (``faststream[nats]``).

        Args:
            servers (str | list[str]): NATS server URL(s).
            **options (Any): Extra keyword arguments forwarded to
                ``faststream.nats.NatsBroker``.

        Returns:
            MessageBroker: A facade around a ``NatsBroker``.
        """
        declare_topology = bool(options.pop("declare_topology", True))
        nats = _require("faststream.nats", "queue")
        return cls(
            nats.NatsBroker(servers, **options),
            declare_topology=declare_topology,
        )

    # ------------------------------------------------------------------
    # Publish / subscribe
    # ------------------------------------------------------------------

    def on(
        self,
        channel: str | QueueSpec,
        /,
        **options: Any,
    ) -> Callable[[Handler], Handler]:
        """Register the decorated async function as a consumer of ``channel``.

        The handler's parameter type hint drives decoding — annotate it
        with a Pydantic model and FastStream validates every inbound
        message into that model before the handler runs::

            @mq.on("orders.paid")
            async def handle(event: OrderPaid) -> None:
                ...

        Args:
            channel (str): The logical channel to subscribe to. Maps to a
                queue (RabbitMQ), topic (Kafka), subject (NATS) or channel
                (Redis) under the hood.
            **options (Any): Extra transport-specific subscriber options
                forwarded to FastStream (e.g. ``exchange=`` on RabbitMQ).

        Returns:
            Callable[[Handler], Handler]: The subscriber decorator.
        """
        _with_prefetch(options, options.pop("prefetch", None), "channel")
        return cast(
            "Callable[[Handler], Handler]",
            self.broker.subscriber(self._bind(channel), **options),
        )

    async def publish(
        self,
        channel: str | QueueSpec,
        message: Any,
        **options: Any,
    ) -> Any:
        """Publish ``message`` to ``channel``.

        Args:
            channel (str | QueueSpec): The destination channel, or the
                :class:`~tempest_fastapi_sdk.queue.QueueSpec` declaring
                it. Publishing only needs the name, so a spec here is
                accepted for symmetry with :meth:`on` — the topology is
                applied where the queue is declared, not per message.
            message (Any): The payload. A Pydantic model or ``dict`` is
                serialized to JSON; ``str`` / ``bytes`` are sent as-is.
            **options (Any): Extra transport-specific publish options
                forwarded to FastStream (e.g. ``headers=``,
                ``correlation_id=``). ``message_id`` is filled with a
                fresh UUID when absent — without a stable id there is no
                key for :meth:`deduplicate` to work from, and a
                redelivery is indistinguishable from a new event. Pass
                your own to key deduplication on something the domain
                owns.

        Returns:
            Any: Whatever the transport's publish returns (often ``None``;
            AMQP returns a confirmation when publisher confirms are on).

        Raises:
            RuntimeError: When :meth:`connect` has not been called yet.
        """
        if not self._started:
            raise RuntimeError(
                "MessageBroker.connect() must be called before publishing.",
            )
        if _publish_accepts(self.broker.publish, "message_id"):
            options.setdefault("message_id", str(uuid4()))
        if _publish_accepts(self.broker.publish, "headers"):
            options["headers"] = inject_context(dict(options.get("headers") or {}))
        return await self.broker.publish(message, channel_name(channel), **options)

    def dead_letter(
        self,
        sink: Any,
        *,
        max_attempts: int = 3,
    ) -> None:
        """Route terminally-failed consumes to ``sink``.

        Without this, a handler that raises loses its message: the ack
        policy rejects it and, unless the queue declares a dead-letter
        exchange, the broker discards it with no error surface at all.

        The sink is the **same**
        :class:`~tempest_fastapi_sdk.tasks.DeadLetterSink` protocol the
        task path uses, so `DbDeadLetterSink` and the admin panel work
        unchanged and a dead task and a dead event share one screen.

        Call it **before** :meth:`connect`.

        Args:
            sink (Any): Where dead events go.
            max_attempts (int): Deliveries to allow before reporting.
                Must match the
                :class:`~tempest_fastapi_sdk.queue.ConsumerRetryPolicy`
                used to build the retry topology, if any.
        """
        from tempest_fastapi_sdk.queue.reliability import (
            make_dead_letter_middleware,
        )

        self.broker.add_middleware(
            make_dead_letter_middleware(sink, max_attempts=max_attempts),
        )

    def deduplicate(
        self,
        store: Any,
        *,
        ttl_seconds: int = 86_400,
    ) -> None:
        """Run each message id at most once, across redeliveries.

        The transport is at-least-once: a restart, a requeue or a lost
        ack all redeliver. This claims the id before the handler runs and
        marks it done after, so the second delivery is skipped.

        **Not exactly-once.** The mark and the handler's effect are not
        atomic; a crash between them leaves a claim that expires and the
        message runs again. When the effect is a row keyed by something
        the domain owns, an ``INSERT ... ON CONFLICT DO NOTHING`` is
        idempotent with no extra moving part and is the better answer.

        Call it **before** :meth:`connect`.

        Args:
            store (Any): A
                :class:`~tempest_fastapi_sdk.queue.DedupStore` —
                ``MemoryDedupStore`` for a single replica,
                ``RedisDedupStore`` for more than one.
            ttl_seconds (int): How long an id is remembered. Must outlive
                the retry topology's total delay, or the last retry runs
                as if the message were new.
        """
        from tempest_fastapi_sdk.queue.dedup import make_dedup_middleware

        self.broker.add_middleware(
            make_dedup_middleware(store, ttl_seconds=ttl_seconds),
        )

    def enable_tracing(self) -> None:
        """Open a span per consumed message, linked to the publish.

        :meth:`publish` already injects the trace context and the current
        request id into the message headers; this is the other half —
        each consume runs inside a span carrying the messaging semantic
        conventions, **linked** to the publishing trace rather than
        parented by it, and with the publisher's request id restored so
        the worker's log lines carry it.

        A no-op without the ``[otel]`` extra, and non-recording when the
        extra is present but no provider was configured. Request-id
        propagation works either way.

        Call it **before** :meth:`connect`.
        """
        from tempest_fastapi_sdk.queue.tracing import make_tracing_middleware

        self.broker.add_middleware(make_tracing_middleware())

    def enable_metrics(self, metrics: Any) -> None:
        """Publish consume counts and durations for every channel.

        Call it **before** :meth:`connect`.

        Args:
            metrics (Any): A
                :class:`~tempest_fastapi_sdk.queue.QueueMetrics`.
        """
        self.broker.add_middleware(metrics.middleware())

    def register(self, consumer: Consumer) -> None:
        """Wire a class-based :class:`Consumer` onto this broker.

        Reads every binding the consumer declares (constructor form or
        grouped ``@subscribe`` methods — see
        :class:`~tempest_fastapi_sdk.queue.Consumer`) and subscribes each
        handler to its channel. When a binding carries an explicit
        ``schema`` (the constructor form), that model drives decoding;
        otherwise the handler's own type hint does.

        Call it at import/startup time, before :meth:`connect`.

        Args:
            consumer (Consumer): The consumer instance to register.
        """
        for sub in consumer.subscriptions():
            bound = self._bind(sub.channel)
            if sub.schema is not None:
                entry = _schema_entry(sub.handler, sub.schema)
                self.broker.subscriber(bound, **sub.options)(entry)
            else:
                self.broker.subscriber(bound, **sub.options)(sub.handler)

    def publisher(self, channel: str | QueueSpec, /, **options: Any) -> Any:
        """Return a reusable publisher bound to ``channel``.

        Useful to declare a typed outbound endpoint once and call it
        many times, and so the channel shows up in FastStream's AsyncAPI
        docs. The returned object is called as ``await pub.publish(msg)``.

        Args:
            channel (str): The destination channel.
            **options (Any): Extra transport-specific publisher options.

        Returns:
            Any: A FastStream publisher object bound to ``channel``.
        """
        return self.broker.publisher(self._bind(channel), **options)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start the broker so consumers and publishers go live.

        Idempotent — safe to call from the FastAPI lifespan and again
        elsewhere; extra calls are no-ops while the broker is alive.
        """
        if self._started:
            return
        await self.broker.start()
        self._started = True
        if self._declare_topology:
            await self.declare_topology()

    async def declare_topology(self) -> list[str]:
        """Declare the dead-letter exchanges the registered specs name.

        A queue carrying ``x-dead-letter-exchange`` is declared happily
        by RabbitMQ even when that exchange does not exist — and then
        every rejected message is dropped at routing time, silently. The
        exchange has to exist for the setting to mean anything, so
        :meth:`connect` calls this unless the broker was built with
        ``declare_topology=False``.

        Idempotent: declaring an exchange that already exists with the
        same properties is a no-op in AMQP. Only durable topic exchanges
        are declared, which is what a dead-letter exchange should be —
        a non-durable one would vanish on restart and take the routing
        with it.

        Returns:
            list[str]: The exchange names declared, in sorted order.
            Empty on a transport with no such concept, or when no spec
            asked for dead-lettering.
        """
        if self.transport is not Transport.RABBITMQ:
            return []
        exchanges = sorted(
            {
                spec.dead_letter.exchange
                for spec in self._specs.values()
                if spec.dead_letter is not None
            },
        )
        if not exchanges:
            return []
        rabbit = _require("faststream.rabbit", "queue")
        for name in exchanges:
            await self.broker.declare_exchange(
                rabbit.RabbitExchange(
                    name,
                    type=rabbit.ExchangeType.TOPIC,
                    durable=True,
                ),
            )
            logger.info("Declared dead-letter exchange %s", name)
        return exchanges

    async def disconnect(self) -> None:
        """Stop the broker and release its connections."""
        if not self._started:
            return
        await self.broker.stop()
        self._started = False

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[MessageBroker, None]:
        """Connect on entry, disconnect on exit — for scripts and tests.

        Long-lived apps should call :meth:`connect` / :meth:`disconnect`
        from their own FastAPI lifespan instead.

        Yields:
            MessageBroker: This connected facade.
        """
        await self.connect()
        try:
            yield self
        finally:
            await self.disconnect()

    async def broker_dependency(self) -> AsyncIterator[MessageBroker]:
        """FastAPI ``Depends`` provider yielding this connected facade.

        Yields:
            MessageBroker: This facade.

        Raises:
            RuntimeError: When :meth:`connect` has not been called yet.
        """
        if not self._started:
            raise RuntimeError(
                "MessageBroker.connect() must be called before use.",
            )
        yield self

    @property
    def is_connected(self) -> bool:
        """Return ``True`` once :meth:`connect` has succeeded.

        Returns:
            bool: ``True`` while the broker is started.
        """
        return self._started

    async def health_check(self) -> bool:
        """Return ``True`` while the broker is started.

        FastStream brokers expose no generic ping, so this reports
        whether the start handshake completed — enough for a readiness
        probe wired via ``make_health_router(checks={"queue": mq.health_check})``.

        Returns:
            bool: ``True`` while the broker is started.
        """
        return self._started


__all__: list[str] = [
    "MessageBroker",
]
