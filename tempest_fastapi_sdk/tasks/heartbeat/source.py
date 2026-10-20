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
from taskiq.exceptions import ScheduledTaskCancelledError
from taskiq.scheduler.scheduled_task import ScheduledTask

from tempest_fastapi_sdk.tasks.heartbeat.files import (
    HEARTBEAT_SCHEDULE_ID,
    HEARTBEAT_TASK_NAME,
    HeartbeatWriter,
)


class HeartbeatScheduleSource(ScheduleSource):
    """Beat the scheduler heartbeat from inside TaskIQ's scheduler loop.

    TaskIQ's scheduler loop (``taskiq.cli.scheduler.run.SchedulerLoop``,
    taskiq 0.12) wakes every ``loop_interval`` (one second by default),
    asks each schedule whether it is due and, for a due one, calls
    ``TaskiqScheduler.on_ready``, whose first step is ``pre_send`` on the
    schedule's source. This source owns exactly one interval schedule;
    its ``pre_send`` writes the heartbeat file and then raises
    ``ScheduledTaskCancelledError``, which ``on_ready`` catches as "do not
    publish". The file is therefore rewritten once per ``every`` while the
    loop is ticking, and **nothing** reaches the broker — no message
    piles up in a queue no worker consumes, and no worker heartbeat
    depends on it (workers beat on their own, see
    :class:`~tempest_fastapi_sdk.tasks.heartbeat.HeartbeatLoop`).

    The trade: a fresh file proves the loop runs, not that the scheduler
    can still publish to the broker. Pair it with a broker check if that
    matters.

    ``get_schedules`` is not the hook, although it looks like one: the
    loop calls it once at startup and then every ``update_interval``
    (sixty seconds by default), on a task of its own, so its period is a
    CLI flag and not the heartbeat period.

    Attributes:
        schedule (ScheduledTask): The heartbeat schedule this source
            returns.
        writer (HeartbeatWriter): Writes the scheduler heartbeat.
    """

    def __init__(self, *, every: timedelta, writer: HeartbeatWriter) -> None:
        """Initialize the source.

        Args:
            every (timedelta): The heartbeat period; whole seconds, at
                least one (TaskIQ refuses anything else).
            writer (HeartbeatWriter): The scheduler heartbeat writer.
        """
        self.schedule: ScheduledTask = ScheduledTask(
            task_name=HEARTBEAT_TASK_NAME,
            labels={},
            args=[],
            kwargs={},
            schedule_id=HEARTBEAT_SCHEDULE_ID,
            interval=every,
        )
        self.writer: HeartbeatWriter = writer

    async def get_schedules(self) -> list[ScheduledTask]:
        """Return the heartbeat schedule.

        Returns:
            list[ScheduledTask]: A single interval schedule.
        """
        return [self.schedule]

    def pre_send(self, task: ScheduledTask) -> None:
        """Write the scheduler heartbeat, then cancel the send.

        Args:
            task (ScheduledTask): The schedule that is due.

        Raises:
            ScheduledTaskCancelledError: Always, for the heartbeat
                schedule, so ``on_ready`` publishes nothing.
        """
        if task.schedule_id != self.schedule.schedule_id:
            return
        self.writer.write()
        raise ScheduledTaskCancelledError


__all__: list[str] = ["HeartbeatScheduleSource"]
