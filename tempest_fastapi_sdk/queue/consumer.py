"""Class-based message consumers — an alternative to the ``@on`` decorator.

Some teams prefer grouping message handlers in a class (shared setup,
dependency injection, inheritance) over free functions. :class:`Consumer`
supports **both** class styles, and both are deliberately explicit — no
channel is guessed from the class name and no schema is sniffed from
anything but a type you can see:

**1. Constructor form** — pass the channel and the payload schema
explicitly to the constructor; override :meth:`Consumer.handle`::

    class OrderPaidConsumer(Consumer):
        async def handle(self, event: OrderPaid) -> None:
            await mark_paid(event.order_id)

    mq.register(OrderPaidConsumer(channel="orders.paid", schema=OrderPaid))

**2. Grouped form** — one class, many channels, each method marked with
:func:`subscribe`; the schema is the method's own parameter annotation::

    class OrdersConsumer(Consumer):
        @subscribe("orders.paid")
        async def on_paid(self, event: OrderPaid) -> None: ...

        @subscribe("orders.cancelled")
        async def on_cancelled(self, event: OrderCancelled) -> None: ...

    mq.register(OrdersConsumer())

A channel may be a plain string or a
:class:`~tempest_fastapi_sdk.queue.QueueSpec`, exactly as in
:meth:`~tempest_fastapi_sdk.queue.MessageBroker.on` — so declaring a
dead-letter exchange or a quorum queue does not force you back to the
decorator form::

    class OrderPaidConsumer(Consumer):
        channel = ORDERS_PAID
        schema = OrderPaid

        async def handle(self, event: OrderPaid) -> None: ...

:meth:`~tempest_fastapi_sdk.queue.MessageBroker.register` reads
:meth:`Consumer.subscriptions` and wires each one to the broker.
:class:`~tempest_fastapi_sdk.queue.Publisher` is the publish-side
counterpart.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_fastapi_sdk.queue.topology import QueueSpec

_CHANNEL_ATTR = "__tempest_channel__"
_OPTIONS_ATTR = "__tempest_subscribe_options__"
_PREFETCH_ATTR = "__tempest_prefetch__"


def subscribe(
    channel: str | QueueSpec,
    *,
    prefetch: int | None = None,
    **options: Any,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Mark a :class:`Consumer` method as the handler for ``channel``.

    The decorated method keeps its normal signature — its message
    parameter's type hint is what validates the payload. Only used in the
    grouped form; the constructor form overrides :meth:`Consumer.handle`
    instead.

    Args:
        channel (str | QueueSpec): The channel this method consumes, or
            the ``QueueSpec`` declaring it along with its topology.
        prefetch (int | None): Caps how many unacknowledged messages the
            broker pushes to **this** handler, exactly as
            :meth:`~tempest_fastapi_sdk.queue.MessageBroker.on` does.
            Named rather than left to ``**options`` because FastStream
            has no ``prefetch`` keyword — it carries the cap on a
            ``Channel`` object, so forwarding it verbatim raises
            ``TypeError``. Overrides :attr:`Consumer.prefetch`. RabbitMQ
            only.
        **options (Any): Extra transport-specific subscriber options
            forwarded to FastStream (e.g. ``exchange=`` on RabbitMQ).

    Returns:
        Callable: The same method, tagged so ``register`` can find it.
    """

    def mark(
        method: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        setattr(method, _CHANNEL_ATTR, channel)
        setattr(method, _OPTIONS_ATTR, options)
        setattr(method, _PREFETCH_ATTR, prefetch)
        return method

    return mark


@dataclass(slots=True)
class Subscription:
    """One channel → handler binding produced by a :class:`Consumer`.

    Attributes:
        channel (str | QueueSpec): The channel to subscribe on, or the
            spec declaring it.
        handler (Callable[..., Awaitable[Any]]): The async callable that
            receives each decoded message.
        schema (type | None): Explicit payload model. When set, it drives
            decoding regardless of the handler's annotation; when ``None``
            the handler's own type hint is used.
        options (dict[str, Any]): Extra transport-specific subscriber
            options.
        prefetch (int | None): Per-consumer unacknowledged-message cap,
            kept apart from :attr:`options` because it is translated into
            a FastStream ``Channel`` rather than forwarded as a keyword.
    """

    channel: str | QueueSpec
    handler: Callable[..., Awaitable[Any]]
    schema: type | None
    options: dict[str, Any]
    prefetch: int | None = None


class Consumer:
    """Base class for class-based message consumers.

    Subclass it in one of the two styles shown in the module docstring.
    Both are explicit — nothing is inferred from the class name. Register
    an instance with
    :meth:`~tempest_fastapi_sdk.queue.MessageBroker.register`.

    Attributes:
        channel (str | QueueSpec | None): The channel for the constructor
            form, or the ``QueueSpec`` declaring it along with the
            topology it needs. Left ``None`` in the grouped form.
        schema (type | None): The payload model for the constructor form.
        prefetch (int | None): Unacknowledged-message cap for every
            binding this consumer declares. A :func:`subscribe` that
            names its own ``prefetch`` overrides it for that channel.
        options (dict[str, Any]): Subscriber options for the constructor
            form. Never mutated, so the empty class-level default is safe
            to share.
    """

    channel: str | QueueSpec | None = None
    schema: type | None = None
    prefetch: int | None = None
    options: dict[str, Any] = {}  # noqa: RUF012

    def __init__(
        self,
        *,
        channel: str | QueueSpec | None = None,
        schema: type | None = None,
        prefetch: int | None = None,
        **options: Any,
    ) -> None:
        """Configure the constructor form.

        Args:
            channel (str | QueueSpec | None): The channel to consume, or
                the ``QueueSpec`` declaring it. Required for the
                constructor form; omit it in the grouped (``@subscribe``)
                form.
            schema (type | None): The Pydantic model the payload is
                validated into. Passing it here is the explicit,
                no-magic path — it drives decoding instead of relying on
                an annotation.
            prefetch (int | None): Caps how many unacknowledged messages
                the broker pushes to this consumer, the same knob
                :meth:`~tempest_fastapi_sdk.queue.MessageBroker.on`
                exposes. Applies to every binding the consumer declares,
                including the grouped form's, unless a
                :func:`subscribe` names its own. RabbitMQ only.
            **options (Any): Extra transport-specific subscriber options
                forwarded to FastStream (e.g. ``exchange=`` on RabbitMQ),
                for the constructor form. The grouped form takes its own
                on each :func:`subscribe`.
        """
        if channel is not None:
            self.channel = channel
        if schema is not None:
            self.schema = schema
        if prefetch is not None:
            self.prefetch = prefetch
        if options:
            self.options = options

    async def handle(self, message: Any) -> None:
        """Handle one message — override in the constructor form.

        Args:
            message (Any): The decoded payload (an instance of
                :attr:`schema` when one was given).

        Raises:
            NotImplementedError: When neither ``handle`` is overridden nor
                any :func:`subscribe` method is defined.
        """
        raise NotImplementedError(
            "Override handle() (constructor form) or mark methods with "
            "@subscribe (grouped form).",
        )

    def subscriptions(self) -> list[Subscription]:
        """Return every channel → handler binding this consumer declares.

        Grouped ``@subscribe`` methods take precedence; if none are
        present, the constructor form (``channel`` + ``handle``) is used.
        A binding with no ``prefetch`` of its own inherits
        :attr:`prefetch`.

        Returns:
            list[Subscription]: One entry per subscription.

        Raises:
            ValueError: When the consumer declares neither a ``@subscribe``
                method nor a ``channel``.
        """
        grouped: list[Subscription] = []
        for name in dir(self):
            if name.startswith("__"):
                continue
            attr = getattr(self, name)
            channel = getattr(attr, _CHANNEL_ATTR, None)
            if channel is not None:
                prefetch = getattr(attr, _PREFETCH_ATTR, None)
                grouped.append(
                    Subscription(
                        channel=channel,
                        handler=attr,
                        schema=None,
                        options=getattr(attr, _OPTIONS_ATTR, {}),
                        prefetch=self.prefetch if prefetch is None else prefetch,
                    ),
                )
        if grouped:
            return grouped
        if self.channel is None:
            raise ValueError(
                f"{type(self).__name__} declares no @subscribe method and no "
                "channel — pass channel=... to the constructor or mark a "
                "method with @subscribe.",
            )
        return [
            Subscription(
                channel=self.channel,
                handler=self.handle,
                schema=self.schema,
                options=dict(self.options),
                prefetch=self.prefetch,
            ),
        ]


__all__: list[str] = [
    "Consumer",
    "Subscription",
    "subscribe",
]
