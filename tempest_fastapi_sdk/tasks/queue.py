"""``TaskQueue`` — a typed facade over TaskIQ (tasks + scheduler).

A **task queue** offloads slow work out of a request handler onto a
worker process, keeping the HTTP response fast. TaskIQ does this well
but its surface is spread across a broker, a scheduler, a schedule
source and ``.kiq()``-style calls. :class:`TaskQueue` folds all of that
into one object with an obvious vocabulary:

* :meth:`task` — mark an async function as runnable in the background.
* :meth:`Task.enqueue` — send a call to the worker (replaces ``.kiq``).
* :meth:`Task.run` — run it inline right here (no broker), for tests.
* :meth:`cron` / :meth:`interval` — run a task on a schedule.

You never import ``taskiq`` in application code: pick the transport with
:meth:`TaskQueue.rabbitmq` / :meth:`redis` / :meth:`memory`. The raw
broker stays at :attr:`broker` for the worker CLI and escape hatches.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Generic, ParamSpec, TypeVar

if TYPE_CHECKING:
    import asyncio

    from taskiq import AsyncBroker

    from tempest_fastapi_sdk.tasks.scheduler import AsyncTaskScheduler

logger = logging.getLogger("tempest_fastapi_sdk.tasks")

P = ParamSpec("P")
R = TypeVar("R")


def _require_taskiq() -> Any:
    """Import ``taskiq`` or raise a helpful error.

    Returns:
        Any: The ``taskiq`` module.

    Raises:
        ImportError: When the ``[tasks]`` extra is not installed.
    """
    try:
        import taskiq
    except ImportError as exc:
        raise ImportError(
            "Background tasks require the optional [tasks] extra. "
            "Install with: pip install tempest-fastapi-sdk[tasks]",
        ) from exc
    return taskiq


class Task(Generic[P, R]):
    """A background-runnable function — the result of :meth:`TaskQueue.task`.

    Wraps a TaskIQ task so the two things you actually do read clearly
    and stay typed against the original signature:

    * ``await my_task.enqueue(...)`` — hand the call to a worker and
      return immediately (the worker runs it out-of-process).
    * ``await my_task.run(...)`` — run the body right here, in-process,
      returning its real value. Handy in tests and for reuse from other
      tasks.

    Attributes:
        taskiq_task (Any): The underlying TaskIQ decorated task — the
            escape hatch for ``.schedule_by_cron`` and friends.
    """

    def __init__(
        self,
        taskiq_task: Any,
        func: Callable[P, Awaitable[R]],
    ) -> None:
        """Wrap a TaskIQ decorated task.

        Args:
            taskiq_task (Any): The object returned by ``broker.task(...)``.
            func (Callable[P, Awaitable[R]]): The original async function,
                kept for :meth:`run` and to preserve the typed signature.
        """
        self.taskiq_task: Any = taskiq_task
        self._func: Callable[P, Awaitable[R]] = func

    async def enqueue(self, *args: P.args, **kwargs: P.kwargs) -> Any:
        """Send this call to a worker and return without waiting for it.

        Args:
            *args (P.args): Positional arguments for the task.
            **kwargs (P.kwargs): Keyword arguments for the task.

        Returns:
            Any: A TaskIQ task handle. ``await handle.wait_result()`` to
            block for the return value when a result backend is wired.
        """
        return await self.taskiq_task.kiq(*args, **kwargs)

    async def run(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Run the task body inline (no broker) and return its value.

        Args:
            *args (P.args): Positional arguments for the task.
            **kwargs (P.kwargs): Keyword arguments for the task.

        Returns:
            R: Whatever the wrapped function returns.
        """
        return await self._func(*args, **kwargs)

    @property
    def task_name(self) -> str:
        """Return the registered task name.

        Returns:
            str: TaskIQ's ``module:function`` name (or the override).
        """
        return str(self.taskiq_task.task_name)


class TaskQueue:
    """Typed facade over a TaskIQ broker plus its periodic scheduler.

    Declare tasks with :meth:`task`, enqueue them with
    :meth:`Task.enqueue`, and schedule periodic ones with :meth:`cron` /
    :meth:`interval` — all on one object::

        from tempest_fastapi_sdk.tasks import TaskQueue

        tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")

        @tq.task
        async def send_welcome(to: str, name: str) -> None:
            await email.send(to, "Welcome", f"Hi {name}")

        @tq.cron("0 9 * * MON-FRI", cron_offset="-03:00")
        async def daily_digest() -> None:
            ...

        # FastAPI lifespan
        await tq.connect()
        await tq.start_scheduler()          # dev / single-process only
        ...
        await tq.stop_scheduler()
        await tq.disconnect()

        # from a request handler
        await send_welcome.enqueue(to=user.email, name=user.name)

    In production run the worker (and one scheduler) as separate
    processes, pointing them at :attr:`broker` / :attr:`scheduler`::

        taskiq worker    myapp.tasks:tq.broker
        taskiq scheduler myapp.tasks:tq.scheduler

    Attributes:
        broker (AsyncBroker): The underlying TaskIQ broker (for the
            worker CLI and escape hatches).
    """

    def __init__(self, broker: AsyncBroker) -> None:
        """Wrap an already-constructed TaskIQ broker.

        Prefer :meth:`rabbitmq` / :meth:`redis` / :meth:`memory`; use
        this to inject a custom or pre-configured broker.

        Args:
            broker (AsyncBroker): A TaskIQ broker instance.
        """
        _require_taskiq()
        self.broker: AsyncBroker = broker
        self._started: bool = False
        self._scheduler: AsyncTaskScheduler | None = None

    # ------------------------------------------------------------------
    # Transport constructors
    # ------------------------------------------------------------------

    @classmethod
    def rabbitmq(cls, url: str, **options: Any) -> TaskQueue:
        """Build a RabbitMQ-backed task queue (``[tasks]`` extra).

        Args:
            url (str): AMQP URL.
            **options (Any): Extra keyword arguments forwarded to
                ``taskiq_aio_pika.AioPikaBroker``.

        Returns:
            TaskQueue: A facade around an ``AioPikaBroker``.
        """
        _require_taskiq()
        try:
            from taskiq_aio_pika import AioPikaBroker
        except ImportError as exc:
            raise ImportError(
                "RabbitMQ tasks require the optional [tasks] extra. "
                "Install with: pip install tempest-fastapi-sdk[tasks]",
            ) from exc
        return cls(AioPikaBroker(url, **options))

    @classmethod
    def redis(cls, url: str, **options: Any) -> TaskQueue:
        """Build a Redis-backed task queue (``taskiq-redis``).

        Args:
            url (str): Redis URL.
            **options (Any): Extra keyword arguments forwarded to
                ``taskiq_redis.RedisStreamBroker``.

        Returns:
            TaskQueue: A facade around a Redis stream broker.
        """
        _require_taskiq()
        try:
            from taskiq_redis import RedisStreamBroker
        except ImportError as exc:
            raise ImportError(
                "Redis tasks require taskiq-redis. "
                "Install with: pip install taskiq-redis",
            ) from exc
        return cls(RedisStreamBroker(url, **options))

    @classmethod
    def memory(cls) -> TaskQueue:
        """Build an in-memory task queue for tests.

        ``enqueue`` runs the task **synchronously in-process**, so tests
        need no worker and no broker connection.

        Returns:
            TaskQueue: A facade around ``taskiq.InMemoryBroker``.
        """
        taskiq = _require_taskiq()
        return cls(taskiq.InMemoryBroker())

    # ------------------------------------------------------------------
    # Task registration
    # ------------------------------------------------------------------

    def task(
        self,
        func: Callable[P, Awaitable[R]] | None = None,
        *,
        name: str | None = None,
        **options: Any,
    ) -> Any:
        """Register an async function as a background task.

        Usable bare or with options::

            @tq.task
            async def a() -> None: ...

            @tq.task(name="reports:nightly", retry_on_error=True)
            async def b() -> None: ...

        Args:
            func (Callable[P, Awaitable[R]] | None): The function, when
                used as a bare ``@tq.task``. ``None`` when called with
                arguments (``@tq.task(...)``).
            name (str | None): Override the auto-generated
                ``module:function`` task name.
            **options (Any): Extra TaskIQ labels / options forwarded to
                ``broker.task``.

        Returns:
            Any: A :class:`Task` (bare form) or a decorator returning one.
        """

        def wrap(fn: Callable[P, Awaitable[R]]) -> Task[P, R]:
            decorator = self.broker.task(task_name=name, **options)
            return Task(decorator(fn), fn)

        if func is not None:
            return wrap(func)
        return wrap

    # ------------------------------------------------------------------
    # Scheduling (periodic tasks)
    # ------------------------------------------------------------------

    @property
    def scheduler(self) -> Any:
        """Return the underlying TaskIQ scheduler (for the CLI).

        Lazily built on first access. Point the standalone scheduler
        process at it: ``taskiq scheduler myapp.tasks:tq.scheduler``.

        Returns:
            Any: The ``taskiq.TaskiqScheduler`` instance.
        """
        return self._ensure_scheduler().scheduler

    def _ensure_scheduler(self) -> AsyncTaskScheduler:
        """Build (once) and return the internal scheduler manager.

        Returns:
            AsyncTaskScheduler: The scheduler manager bound to this broker.
        """
        if self._scheduler is None:
            from tempest_fastapi_sdk.tasks.scheduler import AsyncTaskScheduler

            self._scheduler = AsyncTaskScheduler(self.broker)
        return self._scheduler

    def cron(
        self,
        expr: str,
        *,
        cron_offset: str | timedelta | None = None,
        name: str | None = None,
        **options: Any,
    ) -> Callable[[Callable[P, Awaitable[R]]], Task[P, R]]:
        """Register a task to run on a cron schedule.

        Args:
            expr (str): A cron expression (``"*/5 * * * *"`` = every five
                minutes).
            cron_offset (str | timedelta | None): Timezone offset applied
                to ``expr`` (``"-03:00"`` or a :class:`~datetime.timedelta`).
            name (str | None): Override the task name.
            **options (Any): Extra TaskIQ labels forwarded to the task.

        Returns:
            Callable[[Callable[P, Awaitable[R]]], Task[P, R]]: A decorator
            returning the wrapped :class:`Task`.
        """
        schedule: list[dict[str, Any]] = [{"cron": expr}]
        if cron_offset is not None:
            schedule[0]["cron_offset"] = cron_offset

        def wrap(fn: Callable[P, Awaitable[R]]) -> Task[P, R]:
            decorator = self.broker.task(task_name=name, schedule=schedule, **options)
            return Task(decorator(fn), fn)

        return wrap

    def interval(
        self,
        seconds: float | timedelta,
        *,
        name: str | None = None,
        **options: Any,
    ) -> Callable[[Callable[P, Awaitable[R]]], Task[P, R]]:
        """Register a task to run every ``seconds``.

        Args:
            seconds (float | timedelta): Interval between runs. A number
                is coerced to seconds.
            name (str | None): Override the task name.
            **options (Any): Extra TaskIQ labels forwarded to the task.

        Returns:
            Callable[[Callable[P, Awaitable[R]]], Task[P, R]]: A decorator
            returning the wrapped :class:`Task`.
        """
        delta: timedelta = (
            seconds if isinstance(seconds, timedelta) else timedelta(seconds=seconds)
        )
        schedule: list[dict[str, Any]] = [{"interval": delta}]

        def wrap(fn: Callable[P, Awaitable[R]]) -> Task[P, R]:
            decorator = self.broker.task(task_name=name, schedule=schedule, **options)
            return Task(decorator(fn), fn)

        return wrap

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start the broker so tasks can be enqueued and processed.

        Idempotent — extra calls are no-ops while the broker is alive.
        """
        if self._started:
            return
        await self.broker.startup()
        self._started = True

    async def disconnect(self) -> None:
        """Stop the scheduler (if running) and shut the broker down."""
        if not self._started:
            return
        if self._scheduler is not None and self._scheduler.is_connected:
            await self._scheduler.disconnect()
        await self.broker.shutdown()
        self._started = False

    async def start_scheduler(self) -> asyncio.Task[None]:
        """Start the periodic scheduler in-process (dev / single-process).

        Connects the scheduler and runs its loop as a background asyncio
        task. In production run one standalone ``taskiq scheduler`` process
        instead so a multi-worker deployment doesn't fire each schedule
        N times.

        Returns:
            asyncio.Task[None]: The scheduler loop task (cancelled by
            :meth:`stop_scheduler` / :meth:`disconnect`).

        Raises:
            RuntimeError: When :meth:`connect` has not been called yet.
        """
        if not self._started:
            raise RuntimeError("TaskQueue.connect() must be called first.")
        scheduler = self._ensure_scheduler()
        await scheduler.connect()
        return await scheduler.run_in_background()

    async def stop_scheduler(self) -> None:
        """Stop the in-process scheduler started by :meth:`start_scheduler`."""
        if self._scheduler is not None and self._scheduler.is_connected:
            await self._scheduler.disconnect()

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[TaskQueue]:
        """Connect on entry, disconnect on exit — for scripts and tests.

        Does **not** start the scheduler; call :meth:`start_scheduler`
        explicitly when you need periodic tasks.

        Yields:
            TaskQueue: This connected facade.
        """
        await self.connect()
        try:
            yield self
        finally:
            await self.disconnect()

    @property
    def is_connected(self) -> bool:
        """Return ``True`` once :meth:`connect` has succeeded.

        Returns:
            bool: ``True`` while the broker is started.
        """
        return self._started

    async def health_check(self) -> bool:
        """Return ``True`` while the broker is started.

        Returns:
            bool: ``True`` while the broker is started.
        """
        return self._started


__all__: list[str] = [
    "Task",
    "TaskQueue",
]
