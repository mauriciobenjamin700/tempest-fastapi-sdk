"""Reliability + observability for :class:`~tempest_fastapi_sdk.tasks.TaskQueue`.

Three opt-in layers a background-task system always ends up needing, wired onto
TaskIQ so application code never touches the broker middleware API:

* :class:`RetryPolicy` — typed retry config (max attempts + toggle) that a task
  carries as labels; :meth:`TaskQueue.enable_retries` installs TaskIQ's
  ``SimpleRetryMiddleware`` that reads them.
* Dead-letter — :class:`DeadLetter` + :class:`DeadLetterSink` protocol +
  :meth:`TaskQueue.dead_letter`: when a task fails **terminally** (no retry
  configured, or retries exhausted) the failed call is handed to your sink
  (publish to a channel, write a row, page someone). The sink target is yours —
  the SDK never assumes a backend.
* :class:`TaskMetrics` — Prometheus run count (by status) + duration histogram,
  labelled by task, recorded from a middleware; composes with the SDK's
  existing ``/metrics`` endpoint via an explicit ``registry``.

Everything imports without the ``[tasks]`` extra: TaskIQ is only touched inside
the middleware factories (called at wiring time, when the extra is present) and
Prometheus only inside :class:`TaskMetrics`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from taskiq import TaskiqMessage, TaskiqResult


def _require_prometheus() -> Any:
    """Import ``prometheus_client`` or raise a helpful error.

    Returns:
        Any: The imported ``prometheus_client`` module.

    Raises:
        ImportError: When the ``[prometheus]`` extra is not installed.
    """
    try:
        import prometheus_client
    except ImportError as exc:
        raise ImportError(
            "Task metrics require the optional [prometheus] extra. "
            "Install with: pip install tempest-fastapi-sdk[prometheus]",
        ) from exc
    return prometheus_client


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


@dataclass(frozen=True)
class RetryPolicy:
    """Typed retry configuration for a background task.

    Attributes:
        max_retries (int): Total attempts before the task is given up on
            (matches TaskIQ's ``max_retries`` label semantics).
        on_error (bool): Whether the task is retried on failure at all.
    """

    max_retries: int = 3
    on_error: bool = True

    def as_labels(self) -> dict[str, Any]:
        """Render this policy as the TaskIQ labels the retry middleware reads.

        Returns:
            dict[str, Any]: ``{"retry_on_error": ..., "max_retries": ...}``.
        """
        return {"retry_on_error": self.on_error, "max_retries": self.max_retries}


@dataclass(frozen=True)
class DeadLetter:
    """A task call that failed terminally, handed to a :class:`DeadLetterSink`.

    Attributes:
        task_name (str): The registered task name.
        task_id (str): The failed invocation's id.
        args (list[Any]): Positional arguments of the failed call.
        kwargs (dict[str, Any]): Keyword arguments of the failed call.
        exception (BaseException): The exception that ended the last attempt.
        retries (int): Number of attempts made before giving up.
    """

    task_name: str
    task_id: str
    exception: BaseException
    retries: int
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DeadLetterSink(Protocol):
    """Where terminally-failed tasks go.

    Implement this to route dead letters wherever you want — a
    :class:`~tempest_fastapi_sdk.queue.MessageBroker` channel, a database
    table, a log line, an alert. The sink is called at most once per task
    invocation, only after retries (if any) are exhausted.
    """

    async def __call__(self, dead_letter: DeadLetter) -> None:
        """Handle one terminally-failed task call.

        Args:
            dead_letter (DeadLetter): The failed call and its exception.
        """
        ...


def _is_terminal_failure(labels: dict[str, Any], default_max_retries: int) -> bool:
    """Return whether a failing task will NOT be retried again.

    Mirrors ``SimpleRetryMiddleware``'s decision: a task with retries disabled
    is terminal on its first failure; a retrying task is terminal once the
    attempt count reaches ``max_retries``.

    Args:
        labels (dict[str, Any]): The task message labels.
        default_max_retries (int): Fallback when the task sets no
            ``max_retries`` label.

    Returns:
        bool: ``True`` when this failure is final (dead-letter it).
    """
    retry_on_error = labels.get("retry_on_error")
    if isinstance(retry_on_error, str):
        retry_on_error = retry_on_error.lower() == "true"
    if not retry_on_error:
        return True
    retries = int(labels.get("_retries", 0)) + 1
    max_retries = int(labels.get("max_retries", default_max_retries))
    return retries >= max_retries


class TaskMetrics:
    """Prometheus run count + duration histogram for background tasks.

    Example:

        >>> from tempest_fastapi_sdk.tasks import TaskMetrics, TaskQueue
        >>> tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost/")
        >>> tq.enable_metrics(TaskMetrics())

    Attributes:
        namespace (str): Metric name prefix.
    """

    def __init__(self, *, namespace: str = "tasks", registry: Any = None) -> None:
        """Build the metric objects.

        Args:
            namespace (str): Prefix for every metric name.
            registry (Any): A ``prometheus_client.CollectorRegistry`` to
                register on; ``None`` uses the client's default registry (the
                same one the SDK's ``/metrics`` endpoint scrapes).
        """
        prometheus = _require_prometheus()
        self.namespace = namespace
        kwargs: dict[str, Any] = {} if registry is None else {"registry": registry}
        self._runs = prometheus.Counter(
            f"{namespace}_runs_total",
            "Total background task executions.",
            ["task", "status"],
            **kwargs,
        )
        self._duration = prometheus.Histogram(
            f"{namespace}_duration_seconds",
            "Background task execution time in seconds.",
            ["task"],
            **kwargs,
        )

    def record(self, task: str, *, status: str, duration_seconds: float) -> None:
        """Record one completed task execution.

        Args:
            task (str): The task name label.
            status (str): ``"success"`` or ``"error"``.
            duration_seconds (float): Wall-clock execution time.
        """
        self._runs.labels(task=task, status=status).inc()
        self._duration.labels(task=task).observe(duration_seconds)

    def middleware(self) -> Any:
        """Build the TaskIQ middleware that records this bundle.

        Returns:
            Any: A ``TaskiqMiddleware`` instance (needs the ``[tasks]`` extra).
        """
        taskiq = _require_taskiq()
        metrics = self

        class _TaskMetricsMiddleware(taskiq.TaskiqMiddleware):  # type: ignore[misc,name-defined]
            """Records run count + duration on every task's ``post_execute``."""

            async def post_execute(
                self,
                message: TaskiqMessage,
                result: TaskiqResult[Any],
            ) -> None:
                status = "error" if result.is_err else "success"
                metrics.record(
                    message.task_name,
                    status=status,
                    duration_seconds=float(result.execution_time),
                )

        return _TaskMetricsMiddleware()


def make_dead_letter_middleware(
    sink: DeadLetterSink,
    *,
    default_max_retries: int = 3,
) -> Any:
    """Build a TaskIQ middleware routing terminally-failed tasks to ``sink``.

    Args:
        sink (DeadLetterSink): Where dead letters go. Its own failures are
            swallowed (logged) so a broken sink never crashes the worker.
        default_max_retries (int): Fallback attempt cap for tasks that enable
            retries without setting their own ``max_retries``.

    Returns:
        Any: A ``TaskiqMiddleware`` instance (needs the ``[tasks]`` extra).
    """
    taskiq = _require_taskiq()

    class _DeadLetterMiddleware(taskiq.TaskiqMiddleware):  # type: ignore[misc,name-defined]
        """Calls ``sink`` once, on a task's final (non-retried) failure."""

        async def on_error(
            self,
            message: TaskiqMessage,
            result: TaskiqResult[Any],
            exception: BaseException,
        ) -> None:
            if not _is_terminal_failure(message.labels, default_max_retries):
                return
            dead_letter = DeadLetter(
                task_name=message.task_name,
                task_id=message.task_id,
                exception=exception,
                retries=int(message.labels.get("_retries", 0)) + 1,
                args=list(message.args),
                kwargs=dict(message.kwargs),
            )
            try:
                await sink(dead_letter)
            except Exception:
                import logging

                logging.getLogger("tempest_fastapi_sdk.tasks").exception(
                    "Dead-letter sink failed for task '%s'.", message.task_name
                )

    return _DeadLetterMiddleware()


__all__: list[str] = [
    "DeadLetter",
    "DeadLetterSink",
    "RetryPolicy",
    "TaskMetrics",
    "make_dead_letter_middleware",
]
