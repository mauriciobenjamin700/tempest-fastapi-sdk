"""Class-based message publishers — the symmetric half of ``Consumer``.

:class:`~tempest_fastapi_sdk.queue.Consumer` has let a team group message
handlers in a class since the facade shipped. The publish side had no
equivalent: you called ``await mq.publish("orders.paid", event)`` with the
channel as a loose string and the payload typed :class:`~typing.Any`, so
nothing connected the two ends of a contract that is, in practice, one
contract.

:class:`Publisher` is that half. It carries the channel and the payload
model as class attributes, and :meth:`Publisher.publish` takes exactly the
declared type::

    class OrderPaidPublisher(Publisher[OrderPaid]):
        channel = ORDERS_PAID
        schema = OrderPaid

    orders = mq.publisher_for(OrderPaidPublisher)
    await orders.publish(OrderPaid(order_id="abc"))

Two things the loose call could not give you:

* **The type checker sees the payload.** ``Publisher[OrderPaid]`` makes
  ``publish`` take an ``OrderPaid``, so publishing the wrong model is a
  red squiggle rather than a message the consumer rejects in production.
* **The declaration is one object.** A ``QueueSpec`` on ``channel``
  registers its topology the same way :meth:`~MessageBroker.on` does, so
  the dead-letter exchange a producer-only service names still gets
  declared.

!!! note "Not the same as `MessageBroker.publisher()`"
    :meth:`~tempest_fastapi_sdk.queue.MessageBroker.publisher` returns
    FastStream's own publisher object — an escape hatch, useful mainly
    because it makes the channel show up in the generated AsyncAPI docs.
    This class goes through
    :meth:`~tempest_fastapi_sdk.queue.MessageBroker.publish` instead, so
    it keeps the ``message_id`` deduplication depends on and the
    ``traceparent`` / ``x-request-id`` headers tracing depends on. A
    publisher that bypassed those would look identical and silently break
    both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from tempest_fastapi_sdk.queue.broker import MessageBroker
    from tempest_fastapi_sdk.queue.topology import QueueSpec

MessageT = TypeVar("MessageT")
"""The payload type this publisher accepts."""


class Publisher(Generic[MessageT]):
    """Base class for class-based publishers, bound to one channel.

    Subclass it with the channel and the payload model, then obtain an
    instance from
    :meth:`~tempest_fastapi_sdk.queue.MessageBroker.publisher_for`. The
    constructor form works too, for a channel only known at runtime::

        orders = Publisher[OrderPaid](
            mq, channel="orders.paid", schema=OrderPaid,
        )

    Nothing is inferred from the class name — same rule as
    :class:`~tempest_fastapi_sdk.queue.Consumer`.

    Attributes:
        channel (str | QueueSpec | None): The destination. A
            :class:`~tempest_fastapi_sdk.queue.QueueSpec` also declares
            the topology; a plain string just names it.
        schema (type | None): The payload model. When set, :meth:`publish`
            refuses anything that is not an instance of it. ``None``
            disables the check and accepts whatever the transport does.
    """

    channel: str | QueueSpec | None = None
    schema: type | None = None

    def __init__(
        self,
        broker: MessageBroker,
        *,
        channel: str | QueueSpec | None = None,
        schema: type | None = None,
        **options: Any,
    ) -> None:
        """Bind this publisher to a broker.

        Args:
            broker (MessageBroker): The facade to publish through.
            channel (str | QueueSpec | None): Overrides the class
                attribute. Required when the class does not declare one.
            schema (type | None): Overrides the class attribute.
            **options (Any): Default publish options merged into every
                :meth:`publish` call (e.g. ``headers=``). A keyword passed
                to :meth:`publish` wins over one given here, so the
                per-message call can always override the default.

        Raises:
            ValueError: When neither the class nor the constructor names a
                channel, since there would be nowhere to publish to.
        """
        if channel is not None:
            self.channel = channel
        if schema is not None:
            self.schema = schema
        if self.channel is None:
            raise ValueError(
                f"{type(self).__name__} declares no channel — set it on the "
                "class or pass channel=... to the constructor.",
            )
        self.broker: MessageBroker = broker
        self.options: dict[str, Any] = options

    async def publish(self, message: MessageT, **options: Any) -> Any:
        """Publish ``message`` to this publisher's channel.

        Args:
            message (MessageT): The payload. Validated against
                :attr:`schema` when one is declared.
            **options (Any): Extra publish options for this message,
                overriding the defaults given to the constructor.

        Returns:
            Any: Whatever the transport's publish returns.

        Raises:
            TypeError: When :attr:`schema` is declared and ``message`` is
                not an instance of it. Caught here rather than at the
                consumer, which is a process away and can only reject what
                already left.
            RuntimeError: When the broker is not connected.
        """
        if self.schema is not None and not isinstance(message, self.schema):
            raise TypeError(
                f"{type(self).__name__} publishes {self.schema.__name__}, got "
                f"{type(message).__name__}.",
            )
        if self.channel is None:  # pragma: no cover - refused in __init__
            raise ValueError(f"{type(self).__name__} has no channel.")
        return await self.broker.publish(
            self.channel,
            message,
            **{**self.options, **options},
        )


__all__: list[str] = [
    "Publisher",
]
