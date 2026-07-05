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

:meth:`~tempest_fastapi_sdk.queue.MessageBroker.register` reads
:meth:`Consumer.subscriptions` and wires each one to the broker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

_CHANNEL_ATTR = "__tempest_channel__"
_OPTIONS_ATTR = "__tempest_subscribe_options__"


def subscribe(
    channel: str,
    **options: Any,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Mark a :class:`Consumer` method as the handler for ``channel``.

    The decorated method keeps its normal signature — its message
    parameter's type hint is what validates the payload. Only used in the
    grouped form; the constructor form overrides :meth:`Consumer.handle`
    instead.

    Args:
        channel (str): The channel this method consumes.
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
        return method

    return mark


@dataclass(slots=True)
class Subscription:
    """One channel → handler binding produced by a :class:`Consumer`.

    Attributes:
        channel (str): The channel to subscribe on.
        handler (Callable[..., Awaitable[Any]]): The async callable that
            receives each decoded message.
        schema (type | None): Explicit payload model. When set, it drives
            decoding regardless of the handler's annotation; when ``None``
            the handler's own type hint is used.
        options (dict[str, Any]): Extra transport-specific subscriber
            options.
    """

    channel: str
    handler: Callable[..., Awaitable[Any]]
    schema: type | None
    options: dict[str, Any]


class Consumer:
    """Base class for class-based message consumers.

    Subclass it in one of the two styles shown in the module docstring.
    Both are explicit — nothing is inferred from the class name. Register
    an instance with
    :meth:`~tempest_fastapi_sdk.queue.MessageBroker.register`.

    Attributes:
        channel (str | None): The channel for the constructor form. Left
            ``None`` in the grouped form.
        schema (type | None): The payload model for the constructor form.
    """

    channel: str | None = None
    schema: type | None = None

    def __init__(
        self,
        *,
        channel: str | None = None,
        schema: type | None = None,
    ) -> None:
        """Configure the constructor form.

        Args:
            channel (str | None): The channel to consume. Required for the
                constructor form; omit it in the grouped (``@subscribe``)
                form.
            schema (type | None): The Pydantic model the payload is
                validated into. Passing it here is the explicit,
                no-magic path — it drives decoding instead of relying on
                an annotation.
        """
        if channel is not None:
            self.channel = channel
        if schema is not None:
            self.schema = schema

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
                grouped.append(
                    Subscription(
                        channel=channel,
                        handler=attr,
                        schema=None,
                        options=getattr(attr, _OPTIONS_ATTR, {}),
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
                options={},
            ),
        ]


__all__: list[str] = [
    "Consumer",
    "Subscription",
    "subscribe",
]
