"""The task panel's data source: declared schedule and persisted runs.

Two halves that already existed and had no screen. The **schedule** is read
off the broker's own registry (:func:`~tempest_fastapi_sdk.tasks.task_inventory`),
so it describes what the process would run, not what a queue holds. The
**runs** come from a :class:`~tempest_fastapi_sdk.tasks.JobStore`, one row per
unit of long work, written by the workers themselves.

What this panel deliberately cannot show is **live queue state**. TaskIQ
exposes none, which is already recorded for the dead-letter panel: the screen
answers "what is declared" and "what was persisted", never "how many messages
are sitting in the broker right now".

Either half may be missing. A service given only a ``TaskQueue`` shows the
schedule; given only a ``JobStore`` it shows the runs. A section with no
source does not render, rather than rendering empty and implying there is
nothing to see.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Generic, TypeVar

if TYPE_CHECKING:
    from uuid import UUID

    from tempest_fastapi_sdk.tasks.jobs import BaseJobModel, JobStore
    from tempest_fastapi_sdk.tasks.queue import TaskQueue


JobT = TypeVar("JobT", bound="BaseJobModel")
"""The concrete job model the panel reads."""


_MODELLED_SCHEDULE_KEYS: Final[frozenset[str]] = frozenset(
    {"cron", "cron_offset", "interval", "time"},
)
"""Schedule keys :class:`TaskTrigger` models as named fields.

The keys TaskIQ itself reads off a schedule entry are listed in
``taskiq/schedule_sources/label_based.py``; the rest of them
(``labels``, ``schedule_id``, ``args``, ``kwargs``) describe *what* is
sent rather than *when*, so they reach :attr:`TaskTrigger.extra` instead
of a column. ``tests/test_schedule_projection_guard.py`` fails when
TaskIQ grows a key this set neither models nor exempts.
"""


def _format_offset(value: str | timedelta | None) -> str | None:
    """Return ``value`` as the offset text a panel row shows.

    Args:
        value (str | timedelta | None): A ``"±HH:MM"`` offset, an IANA
            key, a :class:`~datetime.timedelta`, or ``None``.

    Returns:
        str | None: ``"±HH:MM"`` for a timedelta, the string unchanged
        for a key, or ``None`` when no offset was declared.
    """
    if value is None:
        return None
    if not isinstance(value, timedelta):
        return str(value)
    total: int = int(value.total_seconds())
    sign: str = "-" if total < 0 else "+"
    hours, remainder = divmod(abs(total), 3600)
    return f"{sign}{hours:02d}:{remainder // 60:02d}"


@dataclass(frozen=True, slots=True)
class TaskTrigger:
    """One declared trigger of a registered task.

    A task may declare several, and a trigger is not always a cron: TaskIQ
    accepts ``cron``, ``interval`` and ``time`` (one-shot) entries in the
    same list. Projecting that list onto a fixed pair of columns has lost
    a declaration three times in this panel's history, so the row carries
    the triggers as the registry declares them.

    Attributes:
        cron (str | None): The cron expression, for a cron trigger.
        cron_offset (str | timedelta | None): The timezone the expression
            is anchored to. A cron expression without one reads as
            complete while meaning UTC, which is why it is shown rather
            than dropped.
        interval_seconds (float | None): The interval in seconds, for an
            interval trigger.
        run_at (datetime | None): The instant, for a one-shot ``time``
            trigger.
        extra (Mapping[str, Any]): Any other key on the entry, kept so a
            key TaskIQ adds later is reachable rather than silently
            dropped. Excluded from equality, and not rendered.
    """

    cron: str | None = None
    cron_offset: str | timedelta | None = None
    interval_seconds: float | None = None
    run_at: datetime | None = None
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def cron_offset_label(self) -> str | None:
        """The offset as display text.

        Returns:
            str | None: ``"±HH:MM"`` for a fixed offset, the IANA key for
            a named zone, or ``None`` when the trigger declares none.
        """
        return _format_offset(self.cron_offset)


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """One registered task, as the schedule source will read it.

    :attr:`triggers` is the whole declaration; the flat fields summarize
    the first trigger of each kind, so a caller that only needs "the
    cron" keeps reading one attribute.

    Attributes:
        name (str): The registered task name.
        cron (str | None): The first cron expression the task declares.
        interval_seconds (float | None): The first interval in seconds the
            task declares.
        retry_on_error (bool): Whether the task opts into retries.
        max_retries (int | None): The attempt cap, when the task set one.
        cron_offset (str | timedelta | None): The timezone paired with
            :attr:`cron`. Without it the expression reads as complete
            while meaning UTC.
        run_at (datetime | None): The instant, when the task declares a
            one-shot ``time`` trigger.
        triggers (tuple[TaskTrigger, ...]): Every declared trigger, in
            registry order. Empty for an on-demand task.
    """

    name: str
    cron: str | None
    interval_seconds: float | None
    retry_on_error: bool
    max_retries: int | None
    cron_offset: str | timedelta | None = None
    run_at: datetime | None = None
    triggers: tuple[TaskTrigger, ...] = ()

    @property
    def is_scheduled(self) -> bool:
        """Whether the task runs on its own, rather than on demand.

        Returns:
            bool: ``True`` when the task declares any trigger — a cron, an
            interval, or a one-shot instant.
        """
        return bool(self.triggers) or any(
            value is not None
            for value in (self.cron, self.interval_seconds, self.run_at)
        )

    @property
    def cron_offset_label(self) -> str | None:
        """The offset paired with :attr:`cron`, as display text.

        Returns:
            str | None: ``"±HH:MM"`` for a fixed offset, the IANA key for
            a named zone, or ``None`` when none was declared.
        """
        return _format_offset(self.cron_offset)


def _trigger_from_entry(entry: Mapping[str, Any]) -> TaskTrigger:
    """Return the :class:`TaskTrigger` one registry entry declares.

    Args:
        entry (Mapping[str, Any]): One entry of a task's ``schedule``
            label, carrying one of ``cron``, ``interval`` or ``time``.

    Returns:
        TaskTrigger: The trigger, with every key this class does not
        model kept in :attr:`TaskTrigger.extra`.
    """
    interval: Any = entry.get("interval")
    interval_seconds: float | None = None
    if isinstance(interval, timedelta):
        interval_seconds = interval.total_seconds()
    elif isinstance(interval, (int, float)):
        interval_seconds = float(interval)

    cron: Any = entry.get("cron")
    run_at: Any = entry.get("time")
    return TaskTrigger(
        cron=str(cron) if cron is not None else None,
        cron_offset=entry.get("cron_offset"),
        interval_seconds=interval_seconds,
        run_at=run_at if isinstance(run_at, datetime) else None,
        extra={
            key: value
            for key, value in entry.items()
            if key not in _MODELLED_SCHEDULE_KEYS
        },
    )


class TaskPanelService(Generic[JobT]):
    """Reads what the admin task panel renders.

    Neither half is required, and neither is invented: the schedule is the
    broker's registry and the runs are rows a worker wrote.

    Example:
        >>> panel = TaskPanelService(queue=tq, job_store=jobs)
        >>> make_admin_router(site, db=db, auth_backend=backend,
        ...                   secret_key=secret, tasks=panel)

    Attributes:
        recent_limit (int): How many runs the list shows.
    """

    def __init__(
        self,
        *,
        queue: TaskQueue | None = None,
        job_store: JobStore[JobT] | None = None,
        recent_limit: int = 25,
    ) -> None:
        """Configure the panel.

        Args:
            queue (TaskQueue | None): The queue whose registry the
                schedule section reads. ``None`` hides that section.
            job_store (JobStore[JobT] | None): The store the runs section
                reads. ``None`` hides that section.
            recent_limit (int): Rows in the runs list. Defaults to ``25``.

        Raises:
            ValueError: When neither half is given — a panel with no
                source would render two empty sections and say nothing.
        """
        if queue is None and job_store is None:
            raise ValueError(
                "TaskPanelService needs a queue, a job_store, or both — "
                "with neither there is nothing to show."
            )
        self._queue: TaskQueue | None = queue
        self._job_store: JobStore[JobT] | None = job_store
        self.recent_limit: int = recent_limit

    @property
    def shows_schedule(self) -> bool:
        """Whether the schedule section has a source.

        Returns:
            bool: ``True`` when a queue was given.
        """
        return self._queue is not None

    @property
    def shows_runs(self) -> bool:
        """Whether the runs section has a source.

        Returns:
            bool: ``True`` when a job store was given.
        """
        return self._job_store is not None

    def schedule(self) -> list[ScheduledTask]:
        """Return every registered task, scheduled ones first.

        Reads the broker's registry, so it is the declared task set — no
        queue is contacted and nothing is executed.

        The row carries the cron **expression**, not the next run time.
        Computing that is not free with what the SDK already depends on:
        ``pycron``, which arrives with TaskIQ, exposes ``is_now`` and
        ``has_been`` and no ``next()``, so a next-run column would mean
        sweeping candidate minutes on every render — up to ~44k iterations
        for a monthly cron — or taking a new dependency for one column.

        Every declared trigger reaches the row. Reading a chosen pair of
        keys off the registry has dropped a declaration three times here
        — an interval shown as ``on demand``, a one-shot ``time`` doing
        the same, and a ``cron_offset`` that made the panel state an
        hour three hours off what fires — so :attr:`ScheduledTask.triggers`
        carries the entries as declared and the flat fields summarize
        them.

        Returns:
            list[ScheduledTask]: Scheduled tasks first, then on-demand
            ones, each group by name. Empty when no queue was given.
        """
        if self._queue is None:
            return []
        from tempest_fastapi_sdk.tasks.dead_letter import task_inventory

        rows: list[ScheduledTask] = []
        for info in task_inventory(self._queue):
            triggers: tuple[TaskTrigger, ...] = tuple(
                _trigger_from_entry(entry) for entry in info.schedule
            )
            cron_trigger: TaskTrigger | None = next(
                (item for item in triggers if item.cron is not None),
                None,
            )
            rows.append(
                ScheduledTask(
                    name=info.name,
                    cron=cron_trigger.cron if cron_trigger is not None else None,
                    interval_seconds=next(
                        (
                            item.interval_seconds
                            for item in triggers
                            if item.interval_seconds is not None
                        ),
                        None,
                    ),
                    retry_on_error=info.retry_on_error,
                    max_retries=info.max_retries,
                    cron_offset=(
                        cron_trigger.cron_offset if cron_trigger is not None else None
                    ),
                    run_at=next(
                        (item.run_at for item in triggers if item.run_at is not None),
                        None,
                    ),
                    triggers=triggers,
                )
            )
        return sorted(rows, key=lambda row: (not row.is_scheduled, row.name))

    async def recent_runs(self, *, status: str | None = None) -> list[JobT]:
        """Return the most recent job rows, newest first.

        Args:
            status (str | None): Restrict to one
                :class:`~tempest_fastapi_sdk.tasks.JobStatus` value.

        Returns:
            list[JobT]: The rows, newest first. Empty when no job
            store was given, and empty when nothing matches — "no runs
            yet" is an answer, not a 404.
        """
        if self._job_store is None:
            return []
        return list(
            await self._job_store.list_recent(
                status=status,
                limit=self.recent_limit,
            )
        )

    async def run(self, job_id: UUID) -> JobT | None:
        """Return one job row, or ``None`` when it is gone.

        Args:
            job_id (UUID): The row to read.

        Returns:
            JobT | None: The job, or ``None`` when no store was
            given or the row no longer exists.
        """
        if self._job_store is None:
            return None
        from tempest_fastapi_sdk.tasks.jobs import JobNotFoundError

        try:
            return await self._job_store.get(job_id)
        except JobNotFoundError:
            return None

    async def cancel(self, job_id: UUID) -> bool:
        """Ask a queued or running job to stop.

        Cancelling is cooperative: the store flips the row, and the worker
        notices at its next progress tick. A job already finished is left
        alone.

        Args:
            job_id (UUID): The row to cancel.

        Returns:
            bool: ``True`` when the row moved to ``CANCELLED``, ``False``
            when there was nothing to stop.
        """
        if self._job_store is None:
            return False
        cancelled = await self._job_store.cancel(job_id)
        return cancelled is not None


__all__: list[str] = [
    "JobT",
    "ScheduledTask",
    "TaskPanelService",
]
