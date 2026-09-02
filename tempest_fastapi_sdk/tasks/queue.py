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

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    ParamSpec,
    Protocol,
    TypeVar,
    runtime_checkable,
)

from tempest_fastapi_sdk.tasks.lock import (
    DEFAULT_LOCK_TTL_SECONDS,
    RedisSchedulerLock,
    SchedulerLock,
)
from tempest_fastapi_sdk.tasks.observability import make_dead_letter_middleware

if TYPE_CHECKING:
    from taskiq import AsyncBroker

    from tempest_fastapi_sdk.tasks.observability import (
        DeadLetterSink,
        RetryPolicy,
        TaskMetrics,
    )
    from tempest_fastapi_sdk.tasks.scheduler import AsyncTaskScheduler

logger = logging.getLogger("tempest_fastapi_sdk.tasks")

P = ParamSpec("P")
R = TypeVar("R")

LifecycleScope = Literal["worker", "client", "both"]
"""Which process a lifecycle hook runs in.

``"worker"`` is the ``taskiq worker`` process, ``"client"`` is whoever
calls :meth:`TaskQueue.connect` (typically the web application's
lifespan), ``"both"`` is either. ``TaskQueue.memory()`` runs both sides
in one process, so a worker-scoped hook fires there as well.
"""

Hook = Callable[[], Awaitable[None] | None]
"""A lifecycle callback taking no arguments. Sync or async."""


@runtime_checkable
class LifecycleResource(Protocol):
    """Anything the queue can open on startup and close on shutdown.

    :class:`~tempest_fastapi_sdk.db.AsyncDatabaseManager`,
    :class:`~tempest_fastapi_sdk.queue.MessageBroker` and
    :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` all satisfy
    it, which is what makes ``resources=[db, broker]`` work without the
    SDK naming any of them.
    """

    async def connect(self) -> None:
        """Open the resource."""
        ...

    async def disconnect(self) -> None:
        """Close the resource."""
        ...


class TaskIQSettingsLike(Protocol):
    """The two settings fields :meth:`TaskQueue.from_settings` reads.

    Typed as a protocol rather than as
    :class:`~tempest_fastapi_sdk.settings.TaskIQSettings` so a service
    composing its own ``Settings`` from the mixins passes without an
    import cycle, and so a test can hand over a plain object.

    Attributes:
        TASKIQ_BROKER_URL (str): Broker URL. Empty selects the
            in-memory broker, which is the shape a test suite and a dev
            box without Redis actually run in.
        TASKIQ_RESULT_BACKEND_URL (str | None): Where task results go.
            ``None`` means "same place as the broker" when the broker
            can store results, and "nowhere" when it cannot.
    """

    TASKIQ_BROKER_URL: str
    TASKIQ_RESULT_BACKEND_URL: str | None


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
    processes. The TaskIQ CLI resolves ``module:attr`` with a plain
    ``getattr``, so bind :attr:`broker` / :attr:`scheduler` to names of
    their own — ``myapp.tasks:tq.broker`` raises ``AttributeError:
    module 'myapp.tasks' has no attribute 'tq.broker'``::

        # myapp/tasks.py
        broker = tq.broker
        scheduler = tq.scheduler

        # shell
        taskiq worker    myapp.tasks:broker
        taskiq scheduler myapp.tasks:scheduler

    Attributes:
        broker (AsyncBroker): The underlying TaskIQ broker (for the
            worker CLI and escape hatches).
    """

    def __init__(
        self,
        broker: AsyncBroker,
        *,
        resources: Sequence[LifecycleResource] = (),
        lock_url: str | None = None,
    ) -> None:
        """Wrap an already-constructed TaskIQ broker.

        Prefer :meth:`from_settings`, or :meth:`rabbitmq` / :meth:`redis`
        / :meth:`memory`; use this to inject a custom or pre-configured
        broker.

        Args:
            broker (AsyncBroker): A TaskIQ broker instance.
            resources (Sequence[LifecycleResource]): Resources the
                worker opens on startup and closes on shutdown, in
                order — see :meth:`use`.
            lock_url (str | None): Redis URL the scheduler lease is
                derived from when :meth:`lifespan` is asked for a
                scheduler. The transport constructors set this for you;
                pass it when you build the broker yourself, or pass
                ``scheduler_lock=`` to ``lifespan`` instead.
        """
        _require_taskiq()
        self.broker: AsyncBroker = broker
        self._started: bool = False
        self._scheduler: AsyncTaskScheduler | None = None
        self._lock_url: str | None = lock_url
        if resources:
            self.use(*resources)

    # ------------------------------------------------------------------
    # Transport constructors
    # ------------------------------------------------------------------

    @classmethod
    def rabbitmq(
        cls,
        url: str,
        *,
        resources: Sequence[LifecycleResource] = (),
        **options: Any,
    ) -> TaskQueue:
        """Build a RabbitMQ-backed task queue (``[tasks]`` extra).

        Args:
            url (str): AMQP URL.
            resources (Sequence[LifecycleResource]): Resources the
                worker opens on startup and closes on shutdown — see
                :meth:`use`.
            **options (Any): Extra keyword arguments forwarded to
                ``taskiq_aio_pika.AioPikaBroker``.

        Returns:
            TaskQueue: A facade around an ``AioPikaBroker``.

        Raises:
            ImportError: When the ``[tasks]`` extra is not installed, so
                ``taskiq_aio_pika`` is unavailable.
        """
        _require_taskiq()
        try:
            from taskiq_aio_pika import AioPikaBroker
        except ImportError as exc:
            raise ImportError(
                "RabbitMQ tasks require the optional [tasks] extra. "
                "Install with: pip install tempest-fastapi-sdk[tasks]",
            ) from exc
        return cls(AioPikaBroker(url, **options), resources=resources)

    @classmethod
    def redis(
        cls,
        url: str,
        *,
        results: bool | str = True,
        resources: Sequence[LifecycleResource] = (),
        **options: Any,
    ) -> TaskQueue:
        """Build a Redis-backed task queue (``[tasks-redis]`` extra).

        Args:
            url (str): Redis URL.
            results (bool | str): Where task results are stored.
                ``True`` (default) uses ``url``; a string uses that URL
                instead; ``False`` stores nothing, which leaves TaskIQ's
                ``DummyResultBackend`` in place and makes reading a
                result impossible. Until v0.282.0 there was no result
                backend at all and no way to ask for one, so every
                service rebuilt the broker by hand to get ``.kiq`` +
                result to work.
            resources (Sequence[LifecycleResource]): Resources the
                worker opens on startup and closes on shutdown — see
                :meth:`use`.
            **options (Any): Extra keyword arguments forwarded to
                ``taskiq_redis.RedisStreamBroker``.

        Returns:
            TaskQueue: A facade around a Redis stream broker, carrying
            the URL its scheduler lease can be derived from — see
            :meth:`lifespan`.

        Raises:
            ImportError: When ``taskiq_redis`` is not installed.
        """
        _require_taskiq()
        try:
            from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker
        except ImportError as exc:
            raise ImportError(
                "Redis tasks require the optional [tasks-redis] extra. "
                "Install with: pip install tempest-fastapi-sdk[tasks-redis]",
            ) from exc
        broker: AsyncBroker = RedisStreamBroker(url, **options)
        result_url: str | None = (
            url if results is True else (results if isinstance(results, str) else None)
        )
        if result_url is not None:
            broker = broker.with_result_backend(RedisAsyncResultBackend(result_url))
        return cls(broker, resources=resources, lock_url=url)

    @classmethod
    def from_settings(
        cls,
        settings: TaskIQSettingsLike,
        *,
        resources: Sequence[LifecycleResource] = (),
        **options: Any,
    ) -> TaskQueue:
        """Build the queue the settings describe, transport included.

        The branch this replaces was written once per service, and each
        copy decided the same three things again: which transport the
        URL means, whether results are stored, and what an empty URL
        does. The last one is the load-bearing case — a test suite and a
        dev box without Redis run with ``TASKIQ_BROKER_URL`` empty, and
        the answer has to be a broker that still registers
        ``@tq.task``, not a parse error on an empty string.

        Example:

            >>> from tempest_fastapi_sdk.settings import TaskIQSettings
            >>> from tempest_fastapi_sdk.tasks import TaskQueue
            >>>
            >>> class Settings(TaskIQSettings):
            ...     '''Service settings.'''
            >>>
            >>> tq: TaskQueue = TaskQueue.from_settings(Settings())

        Args:
            settings (TaskIQSettingsLike): Anything carrying
                ``TASKIQ_BROKER_URL`` and ``TASKIQ_RESULT_BACKEND_URL``
                — the :class:`~tempest_fastapi_sdk.settings.TaskIQSettings`
                mixin, or a service ``Settings`` composing it.
            resources (Sequence[LifecycleResource]): Resources the
                worker opens on startup and closes on shutdown.
            **options (Any): Extra keyword arguments forwarded to the
                selected broker.

        Returns:
            TaskQueue: A facade over the transport the URL scheme names —
            ``redis``/``rediss`` for Redis Streams, ``amqp``/``amqps``
            for RabbitMQ, and the in-memory broker for an empty URL.

        Raises:
            ValueError: When the URL carries a scheme none of the
                transports handles. Falling back to memory here would
                turn a typo in a deployed environment variable into a
                queue that silently runs every task in the web process.
        """
        url: str = (settings.TASKIQ_BROKER_URL or "").strip()
        if not url:
            logger.warning(
                "TASKIQ_BROKER_URL is empty; using the in-memory broker, whose"
                " enqueue runs the task synchronously in this process instead"
                " of handing it to a worker.",
            )
            return cls.memory(resources=resources)
        scheme: str = url.split("://", 1)[0].lower()
        if scheme in {"redis", "rediss", "unix"}:
            return cls.redis(
                url,
                results=settings.TASKIQ_RESULT_BACKEND_URL or True,
                resources=resources,
                **options,
            )
        if scheme in {"amqp", "amqps"}:
            queue = cls.rabbitmq(url, resources=resources, **options)
            result_url = settings.TASKIQ_RESULT_BACKEND_URL
            if result_url:
                queue._attach_redis_results(result_url)
            return queue
        raise ValueError(
            f"TASKIQ_BROKER_URL has an unsupported scheme {scheme!r}. "
            f"Use redis:// or rediss:// for Redis Streams, amqp:// or "
            f"amqps:// for RabbitMQ, or leave it empty for the in-memory "
            f"broker.",
        )

    def _attach_redis_results(self, url: str) -> None:
        """Route results to Redis on a broker that cannot store them.

        ``taskiq-aio-pika`` ships no result backend, so a RabbitMQ
        deployment that wants to read results keeps them somewhere else.
        The lease URL is set from the same place, since a service with a
        Redis result backend already has the one thing leader election
        needs.

        Args:
            url (str): Redis URL for the result backend.

        Raises:
            ImportError: When ``taskiq_redis`` is not installed.
        """
        try:
            from taskiq_redis import RedisAsyncResultBackend
        except ImportError as exc:
            raise ImportError(
                "TASKIQ_RESULT_BACKEND_URL points at Redis, which requires the "
                "optional [tasks-redis] extra. Install with: "
                "pip install tempest-fastapi-sdk[tasks-redis]",
            ) from exc
        self.broker = self.broker.with_result_backend(RedisAsyncResultBackend(url))
        self._lock_url = url

    @classmethod
    def memory(
        cls,
        *,
        resources: Sequence[LifecycleResource] = (),
    ) -> TaskQueue:
        """Build an in-memory task queue for tests.

        ``enqueue`` runs the task **synchronously in-process**, so tests
        need no worker and no broker connection. The in-memory broker
        runs the client **and** worker lifecycle events on
        :meth:`connect`, so worker-scoped hooks fire here too — which is
        what makes them testable without a worker process.

        Args:
            resources (Sequence[LifecycleResource]): Resources opened on
                startup and closed on shutdown — see :meth:`use`.

        Returns:
            TaskQueue: A facade around ``taskiq.InMemoryBroker``.
        """
        taskiq = _require_taskiq()
        return cls(taskiq.InMemoryBroker(), resources=resources)

    # ------------------------------------------------------------------
    # Task registration
    # ------------------------------------------------------------------

    def task(
        self,
        func: Callable[P, Awaitable[R]] | None = None,
        *,
        name: str | None = None,
        retry: RetryPolicy | None = None,
        **options: Any,
    ) -> Any:
        """Register an async function as a background task.

        Usable bare or with options::

            @tq.task
            async def a() -> None: ...

            @tq.task(name="reports:nightly", retry=RetryPolicy(max_retries=5))
            async def b() -> None: ...

        Args:
            func (Callable[P, Awaitable[R]] | None): The function, when
                used as a bare ``@tq.task``. ``None`` when called with
                arguments (``@tq.task(...)``).
            name (str | None): Override the auto-generated
                ``module:function`` task name.
            retry (RetryPolicy | None): Per-task retry configuration; its
                labels are merged into ``options``. Needs
                :meth:`enable_retries` to have installed the retry middleware.
            **options (Any): Extra TaskIQ labels / options forwarded to
                ``broker.task``.

        Returns:
            Any: A :class:`Task` (bare form) or a decorator returning one.
        """
        if retry is not None:
            options = {**retry.as_labels(), **options}

        def wrap(fn: Callable[P, Awaitable[R]]) -> Task[P, R]:
            decorator = self.broker.task(task_name=name, **options)
            return Task(decorator(fn), fn)

        if func is not None:
            return wrap(func)
        return wrap

    def register(self, definition: Any) -> Any:
        """Register a class-based :class:`~tempest_fastapi_sdk.tasks.TaskDef`.

        Reads the definition's task bindings (constructor form or grouped
        ``@task_method`` methods) and registers each with the broker.

        A binding's ``retry`` is rendered into labels here, exactly as
        :meth:`task` does — TaskIQ's middleware reads
        ``retry_on_error`` / ``max_retries``, so a ``RetryPolicy``
        forwarded as a label would be ignored and the task would never
        retry, silently.

        Args:
            definition (TaskDef): The task-definition instance.

        Returns:
            Task | dict[str, Task]: A single :class:`Task` for the
            constructor form, or a ``dict`` keyed by method name for the
            grouped form.

        Notes:
            TaskIQ needs a plain function with a settable ``__name__``,
            and a bound method has neither, so the method is wrapped.
            Deliberately without ``functools.wraps``: that would make
            ``inspect.signature`` follow ``__wrapped__`` back to a
            signature including ``self``.
        """
        grouped = definition.is_grouped
        wrapped: dict[str, Task[Any, Any]] = {}
        for binding in definition.task_bindings():
            bound = binding.func

            async def _entry(*args: Any, _call: Any = bound, **kwargs: Any) -> Any:
                return await _call(*args, **kwargs)

            _entry.__name__ = binding.key
            _entry.__qualname__ = f"{type(definition).__name__}.{binding.key}"
            options: dict[str, Any] = binding.options
            if binding.retry is not None:
                options = {**binding.retry.as_labels(), **options}
            decorator = self.broker.task(task_name=binding.name, **options)
            wrapped[binding.key] = Task(decorator(_entry), bound)
        if grouped:
            return wrapped
        return wrapped["run"]

    # ------------------------------------------------------------------
    # Reliability + observability (opt-in middleware)
    # ------------------------------------------------------------------

    def enable_retries(self, *, default_max_retries: int = 3) -> None:
        """Install TaskIQ's retry middleware so failing tasks are re-run.

        A task opts in with a :class:`~tempest_fastapi_sdk.tasks.RetryPolicy`
        (``@tq.task(retry=RetryPolicy(...))``); this installs the middleware
        that honours it. Call it **before** :meth:`connect`.

        Args:
            default_max_retries (int): Attempt cap for a retrying task that
                sets no ``max_retries`` of its own.
        """
        _require_taskiq()
        from taskiq.middlewares import SimpleRetryMiddleware

        self.broker.add_middlewares(
            SimpleRetryMiddleware(default_retry_count=default_max_retries)
        )

    def enable_metrics(self, metrics: TaskMetrics) -> None:
        """Record Prometheus run count + duration for every task.

        Call it **before** :meth:`connect`.

        Args:
            metrics (TaskMetrics): The metric bundle to record into.
        """
        self.broker.add_middlewares(metrics.middleware())

    def dead_letter(
        self,
        sink: DeadLetterSink,
        *,
        default_max_retries: int = 3,
    ) -> None:
        """Route terminally-failed tasks to ``sink``.

        A task that fails with no retry configured, or after its retries are
        exhausted, is handed to ``sink`` exactly once. Call it **before**
        :meth:`connect`.

        Args:
            sink (DeadLetterSink): Where dead letters go (a channel publisher,
                a DB write, an alert — the target is yours).
            default_max_retries (int): Must match the value passed to
                :meth:`enable_retries` so the "retries exhausted" point lines
                up for tasks that set no explicit ``max_retries``.
        """
        self.broker.add_middlewares(
            make_dead_letter_middleware(sink, default_max_retries=default_max_retries)
        )

    # ------------------------------------------------------------------
    # Scheduling (periodic tasks)
    # ------------------------------------------------------------------

    @property
    def scheduler(self) -> Any:
        """Return the underlying TaskIQ scheduler (for the CLI).

        Lazily built on first access. Point the standalone scheduler
        process at it. The CLI resolves ``module:attr`` with a plain
        ``getattr``, so bind it to a module-level name first —
        ``scheduler = tq.scheduler``, then ``taskiq scheduler
        myapp.tasks:scheduler``.

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

        For readable schedules without hand-writing cron syntax, pass a
        :class:`tempest_fastapi_sdk.tasks.Cron` member or a builder result
        (:func:`~tempest_fastapi_sdk.tasks.daily`,
        :func:`~tempest_fastapi_sdk.tasks.weekdays`, …) as ``expr``, and a
        :class:`~tempest_fastapi_sdk.tasks.CronOffset` member as
        ``cron_offset``.

        Args:
            expr (str): A cron expression (``"*/5 * * * *"`` = every five
                minutes), a :class:`Cron` member, or a builder result.
            cron_offset (str | timedelta | None): Timezone offset applied
                to ``expr`` — ``"-03:00"``, a :class:`CronOffset` member,
                or a :class:`~datetime.timedelta`.
            name (str | None): Override the task name.
            **options (Any): Extra TaskIQ labels forwarded to the task.

        Returns:
            Callable[[Callable[P, Awaitable[R]]], Task[P, R]]: A decorator
            returning the wrapped :class:`Task`.
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
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _register_hook(
        self,
        moment: Literal["startup", "shutdown"],
        handler: Hook,
        scope: LifecycleScope,
    ) -> None:
        """Attach a zero-argument callback to the broker's own events.

        TaskIQ hands its event handlers the broker's ``TaskiqState``;
        the SDK's hooks take nothing, because a hook that needs the
        state can read ``queue.broker.state`` and one that does not
        should not have to name a parameter it ignores.

        Args:
            moment (Literal["startup", "shutdown"]): Which end of the
                process lifetime to attach to.
            handler (Hook): The callback, sync or async.
            scope (LifecycleScope): Which process runs it.
        """
        taskiq = _require_taskiq()
        events = taskiq.TaskiqEvents
        chosen: list[Any] = []
        if scope in ("worker", "both"):
            chosen.append(
                events.WORKER_STARTUP if moment == "startup" else events.WORKER_SHUTDOWN
            )
        if scope in ("client", "both"):
            chosen.append(
                events.CLIENT_STARTUP if moment == "startup" else events.CLIENT_SHUTDOWN
            )

        def _ignore_state(_state: Any) -> Awaitable[None] | None:
            """Drop TaskIQ's state argument and call the SDK hook."""
            return handler()

        for event in chosen:
            self.broker.add_event_handler(event, _ignore_state)

    def on_startup(
        self,
        handler: Hook | None = None,
        *,
        scope: LifecycleScope = "worker",
    ) -> Any:
        """Run a callback when the process this queue lives in starts.

        The worker has no FastAPI ``lifespan``, so without this there is
        nowhere to open the database, the message broker or an HTTP
        client — and nowhere to close them either. This is that place::

            queue = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)

            @queue.on_startup
            async def _open() -> None:
                await db.connect()

            @queue.on_shutdown
            async def _close() -> None:
                await db.disconnect()

        For the common case of resources that already speak
        ``connect`` / ``disconnect``, :meth:`use` does both in one line.

        Args:
            handler (Hook | None): The callback, when used as a bare
                ``@queue.on_startup``. ``None`` when called with
                arguments.
            scope (LifecycleScope): Which process runs it. The default
                ``"worker"`` deliberately leaves the web process alone —
                it has its own lifespan — so a module imported by both
                does not open the same resource twice.

        Returns:
            Any: The handler (bare form) or a decorator returning it.
        """

        def wrap(fn: Hook) -> Hook:
            self._register_hook("startup", fn, scope)
            return fn

        if handler is not None:
            return wrap(handler)
        return wrap

    def on_shutdown(
        self,
        handler: Hook | None = None,
        *,
        scope: LifecycleScope = "worker",
    ) -> Any:
        """Run a callback when the process this queue lives in stops.

        The mirror of :meth:`on_startup`; see it for the example.

        Args:
            handler (Hook | None): The callback, when used as a bare
                ``@queue.on_shutdown``. ``None`` when called with
                arguments.
            scope (LifecycleScope): Which process runs it.

        Returns:
            Any: The handler (bare form) or a decorator returning it.
        """

        def wrap(fn: Hook) -> Hook:
            self._register_hook("shutdown", fn, scope)
            return fn

        if handler is not None:
            return wrap(handler)
        return wrap

    def use(
        self,
        *resources: LifecycleResource,
        scope: LifecycleScope = "worker",
    ) -> None:
        """Open resources when the process starts, close them when it stops.

        The shortcut behind ``TaskQueue.rabbitmq(url, resources=[db,
        broker])``. Anything with ``connect`` / ``disconnect`` fits —
        :class:`~tempest_fastapi_sdk.db.AsyncDatabaseManager`,
        :class:`~tempest_fastapi_sdk.queue.MessageBroker`,
        :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient`::

            queue = TaskQueue.rabbitmq(url, resources=[db, broker])

        They are opened left to right and closed right to left, so a
        resource that depends on an earlier one still has it while
        closing.

        Args:
            *resources (LifecycleResource): The resources to manage.
            scope (LifecycleScope): Which process manages them; see
                :meth:`on_startup`.
        """
        for resource in resources:
            self._register_hook("startup", resource.connect, scope)
        for resource in reversed(resources):
            self._register_hook("shutdown", resource.disconnect, scope)

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
        """Start the periodic scheduler loop in this process, unguarded.

        This is the raw loop: it fires every schedule it owns, in every
        process that calls it. With more than one replica that means
        every schedule fires once per replica, and nothing says so —
        prefer ``lifespan(scheduler=True)``, which holds a lease so
        exactly one replica runs the loop.

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

    def _resolve_scheduler_lock(
        self,
        explicit: SchedulerLock | None,
        *,
        lease_name: str | None,
        lease_ttl_seconds: float,
    ) -> SchedulerLock | None:
        """Pick the lease that guarantees a single scheduler.

        Args:
            explicit (SchedulerLock | None): A lease the caller passed.
            lease_name (str | None): Key to lock, when one is derived.
            lease_ttl_seconds (float): Lease duration, when derived.

        Returns:
            SchedulerLock | None: The lease to use, or ``None`` when the
            broker is in-memory — a single process by construction, so
            there is nothing to elect.

        Raises:
            ValueError: When a lease is required, none was given, and
                none can be derived. Guessing here is what ships the
                N-fold firing this parameter exists to prevent.
        """
        if explicit is not None:
            return explicit
        if type(self.broker).__name__ == "InMemoryBroker":
            return None
        if self._lock_url is not None:
            return RedisSchedulerLock.from_url(
                self._lock_url,
                **({"name": lease_name} if lease_name else {}),
                ttl_seconds=lease_ttl_seconds,
            )
        raise ValueError(
            "lifespan(scheduler=True) needs a lease so only one replica runs "
            "the schedule, and none could be derived from this broker. Pass "
            "scheduler_lock=RedisSchedulerLock.from_url(...), build the queue "
            "with TaskQueue.from_settings(...) or TaskQueue.redis(...), or ask "
            'for scheduler="unlocked" if you accept every replica firing every '
            "schedule.",
        )

    async def _run_scheduler_under_lease(
        self,
        lock: SchedulerLock,
        *,
        poll_seconds: float,
    ) -> None:
        """Run the scheduler loop only while this process holds the lease.

        The replica that takes the lease runs the loop and renews on a
        timer; the others poll and take over when the holder dies or
        stands down. Losing the lease stops the loop before the next
        tick, because the point is that two loops never overlap.

        Args:
            lock (SchedulerLock): The lease to hold.
            poll_seconds (float): Interval between renew attempts, and
                between take-over attempts while standing by. Must be
                well under the lease TTL.
        """
        loop_task: asyncio.Task[None] | None = None
        try:
            while True:
                if loop_task is None:
                    if await lock.acquire():
                        loop_task = await self.start_scheduler()
                        logger.info("scheduler lease acquired; running schedules")
                elif not await lock.renew():
                    logger.warning("scheduler lease lost; stopping the schedule")
                    loop_task.cancel()
                    await self.stop_scheduler()
                    loop_task = None
                await asyncio.sleep(poll_seconds)
        except asyncio.CancelledError:
            if loop_task is not None:
                loop_task.cancel()
            await lock.release()
            raise

    @asynccontextmanager
    async def lifespan(
        self,
        *,
        scheduler: bool | Literal["unlocked"] = False,
        scheduler_lock: SchedulerLock | None = None,
        lease_name: str | None = None,
        lease_ttl_seconds: float = DEFAULT_LOCK_TTL_SECONDS,
    ) -> AsyncGenerator[TaskQueue, None]:
        """Connect on entry, disconnect on exit — and optionally schedule.

        Plugs straight into FastAPI, which is what removes the second
        process from a small deployment::

            from contextlib import asynccontextmanager
            from collections.abc import AsyncGenerator

            from fastapi import FastAPI

            tq = TaskQueue.from_settings(settings)


            @asynccontextmanager
            async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
                '''Open the queue and run periodic tasks in-process.'''
                async with tq.lifespan(scheduler=True):
                    yield


            app = FastAPI(lifespan=lifespan)

        Args:
            scheduler (bool | Literal["unlocked"]): Whether to run the
                periodic scheduler here. ``False`` (default) keeps the
                pre-v0.282.0 behavior — broker only. ``True`` runs it
                behind a lease, so scaling the API to N replicas still
                fires each schedule once. ``"unlocked"`` runs it in
                every replica, which is a choice and not a default: a
                sweep that expires charges would expire them N times,
                and nothing raises.
            scheduler_lock (SchedulerLock | None): The lease to use.
                ``None`` derives a
                :class:`~tempest_fastapi_sdk.tasks.RedisSchedulerLock`
                from the broker's Redis URL, and needs none at all for
                the in-memory broker, which is one process by
                construction.
            lease_name (str | None): Redis key for the derived lease.
                Two services sharing one Redis instance need different
                names, or only one of them ever schedules.
            lease_ttl_seconds (float): How long the derived lease
                survives unrenewed. Renewals run at a third of it.

        Yields:
            TaskQueue: This connected facade.

        Raises:
            ValueError: When ``scheduler=True`` and no lease could be
                found or derived — see :meth:`_resolve_scheduler_lock`.
        """
        lock: SchedulerLock | None = None
        if scheduler is True:
            lock = self._resolve_scheduler_lock(
                scheduler_lock,
                lease_name=lease_name,
                lease_ttl_seconds=lease_ttl_seconds,
            )
        await self.connect()
        supervisor: asyncio.Task[None] | None = None
        try:
            if scheduler is not False:
                if lock is None:
                    await self.start_scheduler()
                else:
                    supervisor = asyncio.create_task(
                        self._run_scheduler_under_lease(
                            lock,
                            poll_seconds=lease_ttl_seconds / 3,
                        ),
                    )
            yield self
        finally:
            if supervisor is not None:
                supervisor.cancel()
                with suppress(asyncio.CancelledError):
                    await supervisor
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
    "Hook",
    "LifecycleResource",
    "LifecycleScope",
    "Task",
    "TaskQueue",
]
