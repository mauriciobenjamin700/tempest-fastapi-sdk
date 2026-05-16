"""Async FastStream broker manager mirroring AsyncRedisManager."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from faststream.broker.core.usecase import BrokerUsecase

logger = logging.getLogger(__name__)


def _require_faststream() -> Any:
    """Import the ``faststream`` package or raise a helpful error.

    Returns:
        Any: The ``faststream`` module.

    Raises:
        ImportError: When the optional ``[queue]`` extra was not
            installed (``pip install tempest-fastapi-sdk[queue]``).
    """
    try:
        import faststream
    except ImportError as exc:
        raise ImportError(
            "FastStream support requires the optional [queue] extra. "
            "Install with: pip install tempest-fastapi-sdk[queue]",
        ) from exc
    return faststream


class AsyncBrokerManager:
    """Manage the lifecycle of a FastStream broker.

    Wraps any FastStream broker (RabbitBroker, KafkaBroker,
    NatsBroker, RedisBroker, etc.) with a uniform connect / disconnect
    / health-check surface that matches the SDK's other backends.

    The broker is injected so consumers stay free to choose the
    transport without forcing FastStream to import every backend.

    Typical usage::

        from faststream.rabbit import RabbitBroker
        from tempest_fastapi_sdk import AsyncBrokerManager

        broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
        queue = AsyncBrokerManager(broker)

        @queue.broker.subscriber("orders")
        async def handle(msg: OrderMessage) -> None:
            ...

        # FastAPI lifespan
        await queue.connect()
        ...
        await queue.disconnect()

    Attributes:
        broker (BrokerUsecase[Any, Any]): The wrapped broker instance.
    """

    def __init__(self, broker: BrokerUsecase[Any, Any]) -> None:
        """Initialize the manager.

        Args:
            broker (BrokerUsecase[Any, Any]): A FastStream broker
                instance. Construct it with whatever transport-specific
                configuration the application needs.
        """
        _require_faststream()
        self.broker: BrokerUsecase[Any, Any] = broker
        self._started: bool = False

    async def connect(self) -> None:
        """Start the broker so subscribers and publishers are live.

        Safe to call multiple times — subsequent calls are no-ops while
        the same broker is alive.
        """
        if self._started:
            return
        await self.broker.start()
        self._started = True

    async def disconnect(self) -> None:
        """Stop the broker and release its connections."""
        if not self._started:
            return
        await self.broker.close()
        self._started = False

    async def publish(
        self,
        message: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Publish ``message`` through the underlying broker.

        Args:
            message (Any): The payload to publish. Forwarded as-is.
            *args (Any): Positional broker-specific arguments (queue,
                exchange, topic, etc.).
            **kwargs (Any): Keyword broker-specific arguments.

        Returns:
            Any: Whatever the broker's ``publish`` returns (often
            ``None``; AMQP returns a confirmation when enabled).

        Raises:
            RuntimeError: When :meth:`connect` was not called yet.
        """
        if not self._started:
            raise RuntimeError(
                "AsyncBrokerManager.connect() must be called before publishing.",
            )
        return await self.broker.publish(message, *args, **kwargs)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[BrokerUsecase[Any, Any]]:
        """Yield the broker inside an ``async with`` block.

        Connects on entry, disconnects on exit. Convenient for short
        scripts and tests; long-lived applications should call
        :meth:`connect` / :meth:`disconnect` from their own lifespan.

        Yields:
            BrokerUsecase[Any, Any]: The connected broker.
        """
        await self.connect()
        try:
            yield self.broker
        finally:
            await self.disconnect()

    async def broker_dependency(self) -> AsyncIterator[BrokerUsecase[Any, Any]]:
        """Async generator dependency suitable for FastAPI ``Depends``.

        Yields:
            BrokerUsecase[Any, Any]: The connected broker.

        Raises:
            RuntimeError: When :meth:`connect` was not called yet.
        """
        if not self._started:
            raise RuntimeError(
                "AsyncBrokerManager.connect() must be called before use.",
            )
        yield self.broker

    @property
    def is_connected(self) -> bool:
        """Return ``True`` once :meth:`connect` succeeded.

        Returns:
            bool: ``True`` while the broker is started.
        """
        return self._started

    async def health_check(self) -> bool:
        """Return ``True`` when the broker is started.

        FastStream brokers don't expose a generic ping, so we only
        report whether the start handshake completed. Backend-specific
        deeper checks can be layered on by the application.

        Returns:
            bool: ``True`` while the broker is started.
        """
        return self._started


__all__: list[str] = [
    "AsyncBrokerManager",
]
