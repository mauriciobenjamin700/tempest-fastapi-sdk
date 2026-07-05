"""TaskIQ-backed periodic task scheduler manager."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask
    from taskiq.abc.schedule_source import ScheduleSource
    from taskiq.scheduler.scheduler import TaskiqScheduler

logger = logging.getLogger(__name__)


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
            "TaskIQ scheduler support requires the optional [tasks] extra. "
            "Install with: pip install tempest-fastapi-sdk[tasks]",
        ) from exc
    return taskiq


class AsyncTaskScheduler:
    """Manage the lifecycle of a TaskIQ periodic-task scheduler.

    Wraps :class:`taskiq.TaskiqScheduler` together with
    :class:`taskiq.schedule_sources.LabelScheduleSource` so periodic
    tasks are declared with decorators alongside regular tasks and the
    scheduler is started, stopped and (optionally) driven in-process
    from the FastAPI lifespan.

    The scheduler does **not** execute task bodies directly — it kicks
    them into the same broker used by
    :class:`AsyncTaskBrokerManager`, where the worker process consumes
    and runs them. For production deployments, prefer running the
    standalone ``taskiq scheduler <module>:scheduler.scheduler`` CLI
    process; :meth:`run_in_background` is convenient for development
    and single-process services.

    Typical usage::

        from taskiq_aio_pika import AioPikaBroker
        from tempest_fastapi_sdk.tasks import (
            AsyncTaskBrokerManager,
            AsyncTaskScheduler,
        )

        broker = AioPikaBroker("amqp://guest:guest@localhost:5672/")
        tasks = AsyncTaskBrokerManager(broker)
        scheduler = AsyncTaskScheduler(broker)

        @scheduler.cron("*/5 * * * *")
        async def heartbeat() -> None:
            ...

        @scheduler.interval(seconds=30)
        async def poll_remote() -> None:
            ...

        # FastAPI lifespan
        await tasks.connect()
        await scheduler.connect()
        await scheduler.run_in_background()  # dev / single-process only
        ...
        await scheduler.disconnect()
        await tasks.disconnect()

    Attributes:
        broker (AsyncBroker): The TaskIQ broker tasks are kicked into.
        sources (list[ScheduleSource]): Active schedule sources.
        scheduler (TaskiqScheduler): Underlying TaskIQ scheduler.
    """

    def __init__(
        self,
        broker: AsyncBroker,
        sources: Sequence[ScheduleSource] | None = None,
    ) -> None:
        """Initialize the scheduler manager.

        Args:
            broker (AsyncBroker): The TaskIQ broker periodic tasks will
                be kicked into. Reuse the same broker instance that
                :class:`AsyncTaskBrokerManager` wraps so registered
                tasks are visible to the schedule source.
            sources (Sequence[ScheduleSource] | None): Schedule sources
                to read scheduled tasks from. Defaults to a single
                :class:`taskiq.schedule_sources.LabelScheduleSource`
                bound to ``broker``.
        """
        _require_taskiq()
        from taskiq.schedule_sources import LabelScheduleSource
        from taskiq.scheduler.scheduler import TaskiqScheduler

        resolved: list[ScheduleSource] = (
            list(sources) if sources is not None else [LabelScheduleSource(broker)]
        )
        self.broker: AsyncBroker = broker
        self.sources: list[ScheduleSource] = resolved
        self.scheduler: TaskiqScheduler = TaskiqScheduler(
            broker=broker,
            sources=resolved,
        )
        self._started: bool = False
        self._loop_task: asyncio.Task[None] | None = None

    def cron(
        self,
        expr: str,
        *,
        cron_offset: str | timedelta | None = None,
        task_name: str | None = None,
        **labels: Any,
    ) -> Any:
        """Register a task to run on a cron schedule.

        Args:
            expr (str): A cron expression (e.g. ``"*/5 * * * *"`` for
                every five minutes).
            cron_offset (str | timedelta | None): Optional timezone
                offset applied to ``expr`` (``"-03:00"`` or a
                :class:`datetime.timedelta`).
            task_name (str | None): Override the auto-generated
                ``module:function`` task name.
            **labels (Any): Extra TaskIQ labels forwarded to
                ``broker.task``.

        Returns:
            Any: The decorated task callable.
        """
        from tempest_fastapi_sdk.core import BaseStrEnum

        expr_str: str = expr.value if isinstance(expr, BaseStrEnum) else expr
        schedule: list[dict[str, Any]] = [{"cron": expr_str}]
        if cron_offset is not None:
            schedule[0]["cron_offset"] = (
                cron_offset.value
                if isinstance(cron_offset, BaseStrEnum)
                else cron_offset
            )
        return self.broker.task(task_name=task_name, schedule=schedule, **labels)

    def interval(
        self,
        seconds: float | timedelta,
        *,
        task_name: str | None = None,
        **labels: Any,
    ) -> Any:
        """Register a task to run every ``seconds`` interval.

        Args:
            seconds (float | timedelta): Interval between runs. A
                :class:`datetime.timedelta` is used verbatim; a number
                is coerced to seconds via :class:`datetime.timedelta`.
            task_name (str | None): Override the auto-generated task
                name.
            **labels (Any): Extra TaskIQ labels forwarded to
                ``broker.task``.

        Returns:
            Any: The decorated task callable.
        """
        delta: timedelta = (
            seconds if isinstance(seconds, timedelta) else timedelta(seconds=seconds)
        )
        schedule: list[dict[str, Any]] = [{"interval": delta}]
        return self.broker.task(task_name=task_name, schedule=schedule, **labels)

    def schedule(
        self,
        spec: list[dict[str, Any]],
        *,
        task_name: str | None = None,
        **labels: Any,
    ) -> Any:
        """Register a task with a raw TaskIQ schedule spec.

        Use this when you need ``time`` (one-shot at a specific
        datetime), multiple triggers on the same task, or any
        combination not covered by :meth:`cron` / :meth:`interval`.

        Args:
            spec (list[dict[str, Any]]): The schedule list passed
                verbatim to ``broker.task`` (each entry must carry one
                of ``cron``, ``interval`` or ``time``).
            task_name (str | None): Override the auto-generated task
                name.
            **labels (Any): Extra TaskIQ labels forwarded to
                ``broker.task``.

        Returns:
            Any: The decorated task callable.
        """
        return self.broker.task(task_name=task_name, schedule=spec, **labels)

    def register(
        self,
        func: Any,
        *,
        schedule: list[dict[str, Any]],
        task_name: str | None = None,
        **labels: Any,
    ) -> AsyncTaskiqDecoratedTask[Any, Any]:
        """Register ``func`` as a scheduled task without decorator syntax.

        Args:
            func (Any): The async callable to register.
            schedule (list[dict[str, Any]]): The schedule list (see
                :meth:`schedule` for the format).
            task_name (str | None): Override the auto-generated task
                name.
            **labels (Any): Extra TaskIQ labels forwarded to
                ``broker.task``.

        Returns:
            AsyncTaskiqDecoratedTask[Any, Any]: The registered task.
        """
        decorator = self.broker.task(
            task_name=task_name,
            schedule=schedule,
            **labels,
        )
        return decorator(func)

    async def connect(self) -> None:
        """Start the scheduler and every configured source.

        :class:`taskiq.TaskiqScheduler` itself only starts the broker;
        we additionally call ``startup()`` on each source so the default
        :class:`LabelScheduleSource` discovers tasks declared via
        :meth:`cron` / :meth:`interval` / :meth:`schedule` /
        :meth:`register`. Safe to call multiple times — subsequent
        calls are no-ops while the scheduler is alive.
        """
        if self._started:
            return
        await self.scheduler.startup()
        for source in self.sources:
            await source.startup()
        self._started = True

    async def disconnect(self) -> None:
        """Stop the background loop (if any) and shut the scheduler down."""
        if not self._started:
            return
        if self._loop_task is not None:
            self._loop_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._loop_task
            self._loop_task = None
        for source in self.sources:
            await source.shutdown()
        await self.scheduler.shutdown()
        self._started = False

    async def run_in_background(self) -> asyncio.Task[None]:
        """Run TaskIQ's scheduler loop as an in-process asyncio task.

        Suitable for development and single-process services. For
        production deployments with multiple workers, run the standalone
        CLI instead so only one scheduler is active::

            taskiq scheduler myapp.tasks:scheduler.scheduler

        Returns:
            asyncio.Task[None]: The spawned task running
            :class:`taskiq.cli.scheduler.run.SchedulerLoop`. Calling
            :meth:`disconnect` cancels it.

        Raises:
            RuntimeError: When :meth:`connect` has not been called yet.
        """
        if not self._started:
            raise RuntimeError(
                "AsyncTaskScheduler.connect() must be called before "
                "run_in_background().",
            )
        if self._loop_task is not None and not self._loop_task.done():
            return self._loop_task

        from taskiq.cli.scheduler.run import SchedulerLoop

        loop = SchedulerLoop(self.scheduler)
        self._loop_task = asyncio.create_task(
            loop.run(),
            name="tempest-fastapi-sdk.tasks.scheduler",
        )
        return self._loop_task

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[TaskiqScheduler]:
        """Yield the scheduler inside an ``async with`` block.

        Connects on entry, disconnects on exit. The in-process loop is
        **not** started automatically — call :meth:`run_in_background`
        explicitly when you need it.

        Yields:
            TaskiqScheduler: The connected scheduler.
        """
        await self.connect()
        try:
            yield self.scheduler
        finally:
            await self.disconnect()

    @property
    def is_connected(self) -> bool:
        """Return ``True`` once :meth:`connect` succeeded.

        Returns:
            bool: ``True`` while the scheduler is started.
        """
        return self._started

    async def health_check(self) -> bool:
        """Return ``True`` when the scheduler has been started.

        Schedule sources don't expose a generic ping, so we only report
        whether :meth:`connect` succeeded and (when applicable) the
        background loop is still alive.

        Returns:
            bool: ``True`` while the scheduler is started and any
            in-process loop is still running.
        """
        if not self._started:
            return False
        return not (self._loop_task is not None and self._loop_task.done())


__all__: list[str] = [
    "AsyncTaskScheduler",
]
