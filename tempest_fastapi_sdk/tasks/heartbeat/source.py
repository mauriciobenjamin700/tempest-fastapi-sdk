"""The schedule source behind the scheduler heartbeat.

Imports ``taskiq`` at module level, so neither
``tempest_fastapi_sdk.tasks.heartbeat`` nor its ``__main__`` imports it:
only :meth:`tempest_fastapi_sdk.tasks.TaskQueue.enable_heartbeat` does,
because the healthcheck command must keep working without the
``[tasks]`` extra.
"""

from __future__ import annotations

from datetime import timedelta

from taskiq.abc.schedule_source import ScheduleSource
from taskiq.scheduler.scheduled_task import ScheduledTask

from tempest_fastapi_sdk.tasks.heartbeat.files import (
    HEARTBEAT_SCHEDULE_ID,
    HEARTBEAT_TASK_NAME,
    HeartbeatWriter,
)


class HeartbeatScheduleSource(ScheduleSource):
    """Schedule the heartbeat task and record each send.

    TaskIQ's scheduler loop (``taskiq.cli.scheduler.run.SchedulerLoop``,
    taskiq 0.12) wakes every ``loop_interval`` (one second by default),
    asks each schedule whether it is due and, for a due one, calls
    ``TaskiqScheduler.on_ready``: ``pre_send`` on the source, the broker
    publish, then ``post_send``. This source owns exactly one interval
    schedule — the heartbeat task — and writes the scheduler heartbeat in
    ``post_send``, so the file is rewritten once per ``every`` and only
    when the loop is running **and** the publish succeeded. A stuck loop
    or a broker the scheduler can no longer publish to both leave the
    file to age.

    ``get_schedules`` is not the hook, although it looks like one: the
    loop calls it once at startup and then every ``update_interval``
    (sixty seconds by default), on a task of its own, so its period is a
    CLI flag and not the heartbeat period.

    Attributes:
        schedule (ScheduledTask): The heartbeat schedule this source
            returns.
        writer (HeartbeatWriter | None): Writes the scheduler heartbeat;
            ``None`` when only the worker heartbeat is enabled and the
            source exists just to send the task.
    """

    def __init__(
        self,
        *,
        every: timedelta,
        writer: HeartbeatWriter | None,
        task_name: str = HEARTBEAT_TASK_NAME,
    ) -> None:
        """Initialize the source.

        Args:
            every (timedelta): The heartbeat period; whole seconds, at
                least one (TaskIQ refuses anything else).
            writer (HeartbeatWriter | None): The scheduler heartbeat
                writer, or ``None``.
            task_name (str): The task the schedule sends.
        """
        self.schedule: ScheduledTask = ScheduledTask(
            task_name=task_name,
            labels={},
            args=[],
            kwargs={},
            schedule_id=HEARTBEAT_SCHEDULE_ID,
            interval=every,
        )
        self.writer: HeartbeatWriter | None = writer

    async def get_schedules(self) -> list[ScheduledTask]:
        """Return the heartbeat schedule.

        Returns:
            list[ScheduledTask]: A single interval schedule.
        """
        return [self.schedule]

    def post_send(self, task: ScheduledTask) -> None:
        """Write the scheduler heartbeat after the heartbeat was published.

        Args:
            task (ScheduledTask): The schedule that was just sent.
        """
        if self.writer is not None and task.schedule_id == self.schedule.schedule_id:
            self.writer.write()


__all__: list[str] = ["HeartbeatScheduleSource"]
