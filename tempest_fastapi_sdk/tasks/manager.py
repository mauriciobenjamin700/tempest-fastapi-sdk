"""Async TaskIQ broker manager mirroring AsyncRedisManager."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, overload

if TYPE_CHECKING:
    from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _require_taskiq() -> Any:
    """Import the ``taskiq`` package or raise a helpful error.

    Returns:
        Any: The ``taskiq`` module.

    Raises:
        ImportError: When the optional ``[tasks]`` extra was not
            installed (``pip install tempest-fastapi-sdk[tasks]``).
    """
    try:
        import taskiq
    except ImportError as exc:
        raise ImportError(
            "TaskIQ support requires the optional [tasks] extra. "
            "Install with: pip install tempest-fastapi-sdk[tasks]",
        ) from exc
    return taskiq


class AsyncTaskBrokerManager:
    """Manage the lifecycle of a TaskIQ broker.

    Wraps any TaskIQ broker (AioPikaBroker for RabbitMQ, RedisBroker,
    InMemoryBroker for tests, etc.) with a uniform startup / shutdown
    surface that matches the SDK's other backends and exposes the
    ``task`` decorator so application code never imports the broker
    directly.

    The broker is injected so consumers stay free to choose the
    transport without forcing TaskIQ to import every backend.

    Typical usage::

        from taskiq_aio_pika import AioPikaBroker
        from tempest_fastapi_sdk import AsyncTaskBrokerManager

        broker = AioPikaBroker("amqp://guest:guest@localhost:5672/")
        tasks = AsyncTaskBrokerManager(broker)

        @tasks.task
        async def send_email(to: str) -> None:
            ...

        # FastAPI lifespan
        await tasks.connect()
        ...
        await tasks.disconnect()

        # enqueue from a request handler
        await send_email.kiq("user@example.com")

    Attributes:
        broker (AsyncBroker): The wrapped broker instance.
    """

    def __init__(self, broker: AsyncBroker) -> None:
        """Initialize the manager.

        Args:
            broker (AsyncBroker): A TaskIQ broker instance.
        """
        _require_taskiq()
        self.broker: AsyncBroker = broker
        self._started: bool = False

    async def connect(self) -> None:
        """Start the broker so tasks can be enqueued and processed.

        Safe to call multiple times — subsequent calls are no-ops while
        the same broker is alive.
        """
        if self._started:
            return
        await self.broker.startup()
        self._started = True

    async def disconnect(self) -> None:
        """Shut the broker down and release its resources."""
        if not self._started:
            return
        await self.broker.shutdown()
        self._started = False

    @overload
    def task(
        self,
        task_name: Callable[_P, _R],
        **labels: Any,
    ) -> AsyncTaskiqDecoratedTask[_P, _R]: ...

    @overload
    def task(
        self,
        task_name: str | None = None,
        **labels: Any,
    ) -> Callable[[Callable[_P, _R]], AsyncTaskiqDecoratedTask[_P, _R]]: ...

    def task(
        self,
        task_name: Callable[_P, _R] | str | None = None,
        **labels: Any,
    ) -> (
        AsyncTaskiqDecoratedTask[_P, _R]
        | Callable[[Callable[_P, _R]], AsyncTaskiqDecoratedTask[_P, _R]]
    ):
        """Forward to ``broker.task`` so callers don't import TaskIQ.

        Can be used as either ``@manager.task`` or
        ``@manager.task(task_name="...")``. Both forms mirror the overloads
        of ``AsyncBroker.task``, so the decorated name keeps the
        function's parameters and return type.

        Args:
            task_name (Callable[_P, _R] | str | None): The function itself
                (bare ``@manager.task``) or the task-name override
                (``@manager.task(task_name="...")``).
            **labels (Any): TaskIQ labels forwarded to ``AsyncBroker.task``.

        Returns:
            AsyncTaskiqDecoratedTask[_P, _R] | Callable[[Callable[_P, _R]],
            AsyncTaskiqDecoratedTask[_P, _R]]: The decorated task (bare
            form), or the decorator that produces one.
        """
        if callable(task_name):
            return self.broker.task(task_name, **labels)
        return self.broker.task(task_name=task_name, **labels)

    def register_task(
        self,
        func: Any,
        *,
        task_name: str | None = None,
        **kwargs: Any,
    ) -> AsyncTaskiqDecoratedTask[Any, Any]:
        """Register ``func`` as a task without using decorator syntax.

        Useful when wiring third-party callables that you can't decorate
        at definition time.

        Args:
            func (Any): The async callable to register.
            task_name (str | None): Override the task name. Defaults to
                TaskIQ's auto-generated ``module:function`` form.
            **kwargs (Any): Extra keyword arguments forwarded to
                ``AsyncBroker.register_task``.

        Returns:
            AsyncTaskiqDecoratedTask[Any, Any]: The registered task.
        """
        decorator = self.broker.task(task_name=task_name, **kwargs)
        return decorator(func)

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[AsyncBroker, None]:
        """Yield the broker inside an ``async with`` block.

        Connects on entry, disconnects on exit.

        Yields:
            AsyncBroker: The connected broker.
        """
        await self.connect()
        try:
            yield self.broker
        finally:
            await self.disconnect()

    async def broker_dependency(self) -> AsyncIterator[AsyncBroker]:
        """Async generator dependency suitable for FastAPI ``Depends``.

        Yields:
            AsyncBroker: The connected broker.

        Raises:
            RuntimeError: When :meth:`connect` was not called yet.
        """
        if not self._started:
            raise RuntimeError(
                "AsyncTaskBrokerManager.connect() must be called before use.",
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

        TaskIQ brokers don't expose a generic ping, so we only report
        whether the startup handshake completed.

        Returns:
            bool: ``True`` while the broker is started.
        """
        return self._started


__all__: list[str] = [
    "AsyncTaskBrokerManager",
]
