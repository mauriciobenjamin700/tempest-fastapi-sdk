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

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from uuid import UUID

    from tempest_fastapi_sdk.tasks.jobs import BaseJobModel, JobStore
    from tempest_fastapi_sdk.tasks.queue import TaskQueue


JobT = TypeVar("JobT", bound="BaseJobModel")
"""The concrete job model the panel reads."""


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """One registered task, as the schedule source will read it.

    Attributes:
        name (str): The registered task name.
        cron (str | None): The cron expression, when the task declares
            one.
        interval_seconds (float | None): The interval in seconds, when the
            task declares one instead of a cron.
        retry_on_error (bool): Whether the task opts into retries.
        max_retries (int | None): The attempt cap, when the task set one.
    """

    name: str
    cron: str | None
    interval_seconds: float | None
    retry_on_error: bool
    max_retries: int | None

    @property
    def is_scheduled(self) -> bool:
        """Whether the task runs on its own, rather than on demand.

        Returns:
            bool: ``True`` when the task declares a cron or an interval.
        """
        return self.cron is not None or self.interval_seconds is not None


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

        Returns:
            list[ScheduledTask]: Scheduled tasks first, then on-demand
            ones, each group by name. Empty when no queue was given.
        """
        if self._queue is None:
            return []
        from tempest_fastapi_sdk.tasks.dead_letter import task_inventory

        rows: list[ScheduledTask] = []
        for info in task_inventory(self._queue):
            cron: str | None = None
            interval_seconds: float | None = None
            for entry in info.schedule:
                if entry.get("cron") is not None:
                    cron = str(entry["cron"])
                interval = entry.get("interval")
                if isinstance(interval, timedelta):
                    interval_seconds = interval.total_seconds()
                elif isinstance(interval, (int, float)):
                    interval_seconds = float(interval)
            rows.append(
                ScheduledTask(
                    name=info.name,
                    cron=cron,
                    interval_seconds=interval_seconds,
                    retry_on_error=info.retry_on_error,
                    max_retries=info.max_retries,
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
