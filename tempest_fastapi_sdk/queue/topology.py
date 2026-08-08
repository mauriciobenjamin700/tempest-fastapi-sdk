"""Declarative queue topology, translated per transport.

:class:`~tempest_fastapi_sdk.queue.MessageBroker` takes a channel as a
plain string, which is the right default and covers most of what a
service publishes. It is also everything the facade could express — and
the properties that decide whether a queue survives a broker restart,
where a rejected message goes, and how long it lives are **not** part of
a name. On RabbitMQ they live in the queue declaration:

```python
RabbitQueue("orders.paid", durable=True, arguments={"x-dead-letter-exchange": "dlx"})
```

Reaching that through the facade used to mean dropping to
``broker.broker`` and losing everything built around it.
:class:`QueueSpec` closes that: it carries the topology as typed data,
and each transport translates the part it understands.

```python
ORDERS_PAID = QueueSpec(
    name="orders.paid",
    dead_letter=DeadLetterSpec(exchange="dlx"),
    queue_type=QueueType.QUORUM,
)

@mq.on(ORDERS_PAID)
async def handle(event: OrderPaid) -> None: ...
```

**A field a transport cannot express raises.** Silently dropping
``dead_letter`` on a broker with no such concept produces a queue that
looks configured and discards failures — the failure mode this module
exists to prevent. Only fields you actually set are checked, so a
``QueueSpec(name=...)`` is portable everywhere and behaves exactly like
the bare string it replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Final

from tempest_fastapi_sdk.core.enums import BaseStrEnum

DEAD_LETTER_EXCHANGE_ARG: Final[str] = "x-dead-letter-exchange"
"""AMQP argument naming the exchange a rejected message is routed to."""

DEAD_LETTER_ROUTING_KEY_ARG: Final[str] = "x-dead-letter-routing-key"
"""AMQP argument overriding the routing key used when dead-lettering."""

MESSAGE_TTL_ARG: Final[str] = "x-message-ttl"
"""AMQP argument expiring a message after N milliseconds."""

MAX_PRIORITY_ARG: Final[str] = "x-max-priority"
"""AMQP argument enabling a priority queue with N levels."""

MAX_LENGTH_ARG: Final[str] = "x-max-length"
"""AMQP argument capping how many messages the queue holds."""

QUEUE_TYPE_ARG: Final[str] = "x-queue-type"
"""AMQP argument selecting the queue implementation (classic/quorum)."""


class Transport(BaseStrEnum):
    """Which broker a :class:`QueueSpec` is being translated for.

    Detected from the FastStream broker class rather than recorded at
    construction, so a :class:`~tempest_fastapi_sdk.queue.MessageBroker`
    built by injecting a broker directly is classified the same way as
    one built through a transport constructor.
    """

    RABBITMQ = "rabbitmq"
    REDIS = "redis"
    KAFKA = "kafka"
    NATS = "nats"
    UNKNOWN = "unknown"


class QueueType(BaseStrEnum):
    """RabbitMQ queue implementation.

    Attributes:
        CLASSIC: The default. One queue master, mirrored only if a policy
            says so.
        QUORUM: Raft-replicated across nodes. Survives the loss of a node
            without the message loss classic mirroring can suffer, at the
            cost of memory and of not supporting priorities or TTL on the
            queue. Reach for it once losing a message matters more than
            the extra resources.
    """

    CLASSIC = "classic"
    QUORUM = "quorum"


class UnsupportedTopologyError(RuntimeError):
    """A :class:`QueueSpec` asks for something the transport cannot do.

    Raised at subscribe/publish time — startup, not per message — so the
    mismatch surfaces on the first run rather than as a queue that
    quietly lacks the property you declared.
    """


@dataclass(frozen=True)
class DeadLetterSpec:
    """Where a rejected message goes.

    On RabbitMQ, dead-lettering is a property of the **queue**, not of
    the consumer: the broker routes a message to ``exchange`` when it is
    rejected without requeue, expires, or overflows the queue length.
    Without it, a rejected message is discarded — which is the default
    and is silent.

    Attributes:
        exchange (str): The exchange rejected messages are published to.
            It must exist; see
            :meth:`~tempest_fastapi_sdk.queue.MessageBroker.declare_topology`.
        routing_key (str | None): Routing key to use when dead-lettering.
            ``None`` keeps the message's original key, which is usually
            what you want — it lets one dead-letter exchange fan out to
            per-queue dead-letter queues without extra configuration.
    """

    exchange: str
    routing_key: str | None = None


@dataclass(frozen=True)
class QueueSpec:
    """A channel plus the topology it needs.

    Accepted anywhere a channel string is, so adopting it is per-channel
    rather than all-or-nothing.

    Attributes:
        name (str): The channel name. The only required field, and the
            only one every transport understands.
        durable (bool): Whether the queue survives a broker restart.
            ``True`` matches the transport defaults; setting it to
            ``False`` on a transport that cannot express it raises.
        dead_letter (DeadLetterSpec | None): Where rejected messages go.
        message_ttl_ms (int | None): Discard (or dead-letter) a message
            after this many milliseconds.
        max_priority (int | None): Enable a priority queue with this many
            levels. Incompatible with :attr:`QueueType.QUORUM`.
        max_length (int | None): Cap the queue at this many messages;
            overflow is dead-lettered when :attr:`dead_letter` is set.
        queue_type (QueueType): Classic or quorum.
    """

    name: str
    durable: bool = True
    dead_letter: DeadLetterSpec | None = None
    message_ttl_ms: int | None = None
    max_priority: int | None = None
    max_length: int | None = None
    queue_type: QueueType = QueueType.CLASSIC

    def __post_init__(self) -> None:
        """Reject combinations the broker itself would refuse.

        Caught here rather than at declaration time so a bad constant is
        a traceback at import, pointing at the line that wrote it.

        Raises:
            UnsupportedTopologyError: When priorities are combined with a
                quorum queue, which RabbitMQ does not implement.
            ValueError: When a numeric field is not positive.
        """
        if self.max_priority is not None and self.queue_type is QueueType.QUORUM:
            raise UnsupportedTopologyError(
                f"QueueSpec({self.name!r}) sets max_priority on a quorum "
                "queue; RabbitMQ implements priorities only for classic "
                "queues. Drop max_priority, or use QueueType.CLASSIC.",
            )
        for field_name in ("message_ttl_ms", "max_priority", "max_length"):
            value = getattr(self, field_name)
            if value is not None and value <= 0:
                raise ValueError(
                    f"QueueSpec({self.name!r}).{field_name} must be positive, "
                    f"got {value!r}.",
                )


def channel_name(channel: str | QueueSpec) -> str:
    """Return the channel name for either accepted form.

    Args:
        channel (str | QueueSpec): A bare channel or a full spec.

    Returns:
        str: The name to publish to or subscribe on.
    """
    return channel.name if isinstance(channel, QueueSpec) else channel


def detect_transport(broker: Any) -> Transport:
    """Classify a FastStream broker by its class name.

    Uses the class name rather than ``isinstance`` so nothing has to be
    imported: each FastStream transport lives behind its own optional
    dependency, and importing them to run a type check would defeat the
    lazy loading the facade is built on.

    Args:
        broker (Any): The FastStream broker instance.

    Returns:
        Transport: The detected transport, or :attr:`Transport.UNKNOWN`
        for a custom or test broker — which is treated as supporting
        nothing beyond a name, the conservative direction.
    """
    name = type(broker).__name__.lower()
    for transport in (
        Transport.RABBITMQ,
        Transport.REDIS,
        Transport.KAFKA,
        Transport.NATS,
    ):
        marker = "rabbit" if transport is Transport.RABBITMQ else transport.value
        if marker in name:
            return transport
    return Transport.UNKNOWN


def unsupported_fields(spec: QueueSpec, transport: Transport) -> list[str]:
    """List the fields ``transport`` cannot express for ``spec``.

    Only fields set away from their default count. That is what keeps a
    ``QueueSpec(name="orders.paid")`` portable: it asks for nothing
    beyond a name, so it translates everywhere, exactly like the bare
    string it replaces.

    Args:
        spec (QueueSpec): The topology being translated.
        transport (Transport): The transport to translate for.

    Returns:
        list[str]: Field names that were set and cannot be honored, in
        declaration order. Empty when the spec translates cleanly.
    """
    if transport is Transport.RABBITMQ:
        return []
    defaults = {
        field.name: field.default for field in fields(spec) if field.name != "name"
    }
    return [
        name for name, default in defaults.items() if getattr(spec, name) != default
    ]


def require_supported(spec: QueueSpec, transport: Transport) -> None:
    """Raise when ``transport`` cannot honor every field ``spec`` sets.

    Args:
        spec (QueueSpec): The topology being translated.
        transport (Transport): The transport to translate for.

    Raises:
        UnsupportedTopologyError: Naming each field that cannot be
            honored. Failing is deliberate — a dropped ``dead_letter``
            produces a queue that looks configured and silently discards
            every failure, which is the exact defect this module exists
            to prevent.
    """
    missing = unsupported_fields(spec, transport)
    if not missing:
        return
    raise UnsupportedTopologyError(
        f"QueueSpec({spec.name!r}) sets {', '.join(missing)}, which the "
        f"{transport.value} transport cannot express. Remove the field, or "
        "use a bare channel name and configure the topology outside the SDK.",
    )


def rabbit_arguments(spec: QueueSpec) -> dict[str, Any]:
    """Build the AMQP ``arguments`` table for ``spec``.

    Args:
        spec (QueueSpec): The topology to translate.

    Returns:
        dict[str, Any]: The ``x-`` arguments RabbitMQ expects. Always
        carries the queue type, matching what FastStream declares by
        default so an existing queue is not re-declared with different
        arguments — which RabbitMQ rejects with ``PRECONDITION_FAILED``.
    """
    arguments: dict[str, Any] = {QUEUE_TYPE_ARG: spec.queue_type.value}
    if spec.dead_letter is not None:
        arguments[DEAD_LETTER_EXCHANGE_ARG] = spec.dead_letter.exchange
        if spec.dead_letter.routing_key is not None:
            arguments[DEAD_LETTER_ROUTING_KEY_ARG] = spec.dead_letter.routing_key
    if spec.message_ttl_ms is not None:
        arguments[MESSAGE_TTL_ARG] = spec.message_ttl_ms
    if spec.max_priority is not None:
        arguments[MAX_PRIORITY_ARG] = spec.max_priority
    if spec.max_length is not None:
        arguments[MAX_LENGTH_ARG] = spec.max_length
    return arguments


def to_rabbit_queue(spec: QueueSpec) -> Any:
    """Translate ``spec`` into a ``faststream.rabbit.RabbitQueue``.

    Args:
        spec (QueueSpec): The topology to translate.

    Returns:
        Any: The ``RabbitQueue`` to hand FastStream.

    Raises:
        ImportError: When the ``[queue]`` extra is not installed.
    """
    from tempest_fastapi_sdk.queue.broker import _require

    rabbit = _require("faststream.rabbit", "queue")
    queue: Any = rabbit.RabbitQueue(
        spec.name,
        durable=spec.durable,
        arguments=rabbit_arguments(spec),
    )
    return queue


def resolve_channel(channel: str | QueueSpec, transport: Transport) -> Any:
    """Turn a channel into what FastStream expects for ``transport``.

    Args:
        channel (str | QueueSpec): A bare channel or a full spec.
        transport (Transport): The active transport.

    Returns:
        Any: A ``RabbitQueue`` for a spec on RabbitMQ, otherwise the
        channel name — a spec that survives
        :func:`require_supported` on another transport asked for nothing
        the name does not already carry.

    Raises:
        UnsupportedTopologyError: When the spec sets a field the
            transport cannot honor.
    """
    if not isinstance(channel, QueueSpec):
        return channel
    require_supported(channel, transport)
    if transport is Transport.RABBITMQ:
        return to_rabbit_queue(channel)
    return channel.name


__all__: list[str] = [
    "DEAD_LETTER_EXCHANGE_ARG",
    "DEAD_LETTER_ROUTING_KEY_ARG",
    "MAX_LENGTH_ARG",
    "MAX_PRIORITY_ARG",
    "MESSAGE_TTL_ARG",
    "QUEUE_TYPE_ARG",
    "DeadLetterSpec",
    "QueueSpec",
    "QueueType",
    "Transport",
    "UnsupportedTopologyError",
    "channel_name",
    "detect_transport",
    "rabbit_arguments",
    "require_supported",
    "resolve_channel",
    "to_rabbit_queue",
    "unsupported_fields",
]
