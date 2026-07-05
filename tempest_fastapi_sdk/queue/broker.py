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

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from faststream.broker.core.usecase import BrokerUsecase

logger = logging.getLogger("tempest_fastapi_sdk.queue")

Handler = TypeVar("Handler", bound=Callable[..., Awaitable[Any]])
"""A message handler — an async callable taking the decoded message."""


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

    def __init__(self, broker: BrokerUsecase[Any, Any]) -> None:
        """Wrap an already-constructed FastStream broker.

        Prefer the transport constructors (:meth:`rabbitmq`, :meth:`redis`,
        :meth:`kafka`, :meth:`nats`); use this directly only to inject a
        pre-configured or custom broker (e.g. a test broker).

        Args:
            broker (BrokerUsecase[Any, Any]): A FastStream broker.
        """
        self.broker: BrokerUsecase[Any, Any] = broker
        self._started: bool = False

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
                ``faststream.rabbit.RabbitBroker``.

        Returns:
            MessageBroker: A facade around a ``RabbitBroker``.
        """
        rabbit = _require("faststream.rabbit", "queue")
        return cls(rabbit.RabbitBroker(url, **options))

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
        redis = _require("faststream.redis", "queue")
        return cls(redis.RedisBroker(url, **options))

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
        kafka = _require("faststream.kafka", "queue")
        servers: str | list[str] = (
            list(bootstrap_servers)
            if len(bootstrap_servers) != 1
            else bootstrap_servers[0]
        )
        return cls(kafka.KafkaBroker(servers, **options))

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
        nats = _require("faststream.nats", "queue")
        return cls(nats.NatsBroker(servers, **options))

    # ------------------------------------------------------------------
    # Publish / subscribe
    # ------------------------------------------------------------------

    def on(self, channel: str, **options: Any) -> Callable[[Handler], Handler]:
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
        return cast(
            "Callable[[Handler], Handler]",
            self.broker.subscriber(channel, **options),
        )

    async def publish(
        self,
        channel: str,
        message: Any,
        **options: Any,
    ) -> Any:
        """Publish ``message`` to ``channel``.

        Args:
            channel (str): The destination channel. Maps to the
                transport's queue / topic / subject positionally.
            message (Any): The payload. A Pydantic model or ``dict`` is
                serialized to JSON; ``str`` / ``bytes`` are sent as-is.
            **options (Any): Extra transport-specific publish options
                forwarded to FastStream (e.g. ``headers=``,
                ``correlation_id=``).

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
        return await self.broker.publish(message, channel, **options)

    def publisher(self, channel: str, **options: Any) -> Any:
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
        return self.broker.publisher(channel, **options)

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

    async def disconnect(self) -> None:
        """Stop the broker and release its connections."""
        if not self._started:
            return
        await self.broker.stop()
        self._started = False

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[MessageBroker]:
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
