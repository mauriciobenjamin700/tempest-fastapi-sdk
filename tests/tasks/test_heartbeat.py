"""Worker and scheduler heartbeats, and the healthcheck that reads them."""

from __future__ import annotations

import asyncio
import logging
import math
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from taskiq.exceptions import ScheduledTaskCancelledError
from taskiq.message import BrokerMessage
from taskiq.scheduler.scheduled_task import ScheduledTask

from tempest_fastapi_sdk.settings import TaskIQSettings
from tempest_fastapi_sdk.tasks import (
    HEARTBEAT_FILE_MODE,
    HEARTBEAT_FUTURE_TOLERANCE_SECONDS,
    HEARTBEAT_SCHEDULE_ID,
    HEARTBEAT_TASK_NAME,
    HeartbeatLoop,
    HeartbeatWriter,
    TaskQueue,
    heartbeat_age,
    is_heartbeat_fresh,
    read_heartbeat,
    validate_max_age,
    write_heartbeat,
)
from tempest_fastapi_sdk.tasks.heartbeat import main
from tempest_fastapi_sdk.tasks.heartbeat.source import HeartbeatScheduleSource


class HeartbeatSettings:
    """The fields ``from_settings`` reads, heartbeat included.

    Attributes:
        TASKIQ_BROKER_URL (str): Empty, for the in-memory broker.
        TASKIQ_RESULT_BACKEND_URL (str | None): Unused.
        TASKIQ_STORE_RESULTS (bool): Unused.
        TASKIQ_RESULT_TTL_SECONDS (int): Unused.
        TASKIQ_WORKER_HEARTBEAT_FILE (str): Worker heartbeat file.
        TASKIQ_SCHEDULER_HEARTBEAT_FILE (str): Scheduler heartbeat file.
        TASKIQ_HEARTBEAT_SECONDS (int): Heartbeat period.
    """

    def __init__(
        self, worker: str = "", scheduler: str = "", seconds: int = 30
    ) -> None:
        """Set the fields.

        Args:
            worker (str): Worker heartbeat file.
            scheduler (str): Scheduler heartbeat file.
            seconds (int): Heartbeat period.
        """
        self.TASKIQ_BROKER_URL: str = ""
        self.TASKIQ_RESULT_BACKEND_URL: str | None = None
        self.TASKIQ_STORE_RESULTS: bool = False
        self.TASKIQ_RESULT_TTL_SECONDS: int = 0
        self.TASKIQ_WORKER_HEARTBEAT_FILE: str = worker
        self.TASKIQ_SCHEDULER_HEARTBEAT_FILE: str = scheduler
        self.TASKIQ_HEARTBEAT_SECONDS: int = seconds


def _sources(queue: TaskQueue) -> list[HeartbeatScheduleSource]:
    """Return the heartbeat sources attached to the queue's scheduler.

    Args:
        queue (TaskQueue): The queue.

    Returns:
        list[HeartbeatScheduleSource]: The heartbeat sources.
    """
    return [
        s for s in queue.scheduler.sources if isinstance(s, HeartbeatScheduleSource)
    ]


class TestFiles:
    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        moment = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
        write_heartbeat(tmp_path / "hb", now=moment)
        assert read_heartbeat(tmp_path / "hb") == moment
        assert (tmp_path / "hb").read_text() == "2026-10-20T12:00:00Z\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["hb"]

    def test_write_replaces_the_previous_beat(self, tmp_path: Path) -> None:
        write_heartbeat(tmp_path / "hb", now=datetime(2026, 1, 1, tzinfo=UTC))
        write_heartbeat(tmp_path / "hb", now=datetime(2026, 1, 2, tzinfo=UTC))
        assert read_heartbeat(tmp_path / "hb") == datetime(2026, 1, 2, tzinfo=UTC)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["hb"]

    def test_write_into_a_missing_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            write_heartbeat(tmp_path / "absent" / "hb")

    def test_age_and_freshness(self, tmp_path: Path) -> None:
        now = datetime(2026, 10, 20, 12, 0, 30, tzinfo=UTC)
        write_heartbeat(tmp_path / "hb", now=now - timedelta(seconds=30))
        assert heartbeat_age(tmp_path / "hb", now=now) == 30.0
        assert is_heartbeat_fresh(tmp_path / "hb", 90, now=now) is True
        assert is_heartbeat_fresh(tmp_path / "hb", 29, now=now) is False

    def test_missing_and_garbage_are_not_fresh(self, tmp_path: Path) -> None:
        (tmp_path / "garbage").write_text("not a timestamp\n")
        assert heartbeat_age(tmp_path / "missing") is None
        assert heartbeat_age(tmp_path / "garbage") is None
        assert is_heartbeat_fresh(tmp_path / "missing", 90) is False
        assert is_heartbeat_fresh(tmp_path / "garbage", 90) is False


class TestGuards:
    """Future timestamps, impossible ``max_age`` and file permissions."""

    def test_written_file_is_world_readable(self, tmp_path: Path) -> None:
        """mkstemp's 0600 would lock out a checker running as another user."""
        write_heartbeat(tmp_path / "hb")
        assert (tmp_path / "hb").stat().st_mode & 0o777 == HEARTBEAT_FILE_MODE

    def test_small_future_skew_is_tolerated(self, tmp_path: Path) -> None:
        now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
        write_heartbeat(tmp_path / "hb", now=now + timedelta(seconds=3))
        assert is_heartbeat_fresh(tmp_path / "hb", 90, now=now) is True

    def test_far_future_is_not_fresh(self, tmp_path: Path) -> None:
        now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
        ahead = HEARTBEAT_FUTURE_TOLERANCE_SECONDS + 1
        write_heartbeat(tmp_path / "hb", now=now + timedelta(seconds=ahead))
        assert is_heartbeat_fresh(tmp_path / "hb", 90, now=now) is False

    def test_checker_refuses_a_future_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_heartbeat(tmp_path / "hb", now=datetime.now(UTC) + timedelta(hours=2))
        assert main([str(tmp_path / "hb"), "--max-age", "90"]) == 1
        assert "in the future" in capsys.readouterr().err

    @pytest.mark.parametrize("max_age", [0.0, -1.0, math.nan, math.inf])
    def test_impossible_max_age_raises(self, tmp_path: Path, max_age: float) -> None:
        write_heartbeat(tmp_path / "hb")
        with pytest.raises(ValueError, match="max_age must be"):
            is_heartbeat_fresh(tmp_path / "hb", max_age)
        with pytest.raises(ValueError, match="max_age must be"):
            validate_max_age(max_age)

    @pytest.mark.parametrize("max_age", ["0", "-5", "nan", "inf"])
    def test_checker_refuses_impossible_max_age(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        max_age: str,
    ) -> None:
        write_heartbeat(tmp_path / "hb")
        assert main([str(tmp_path / "hb"), f"--max-age={max_age}"]) == 1
        assert "invalid --max-age" in capsys.readouterr().err


class TestWriter:
    def test_failure_warns_once_and_never_raises(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        writer = HeartbeatWriter(tmp_path / "absent" / "hb")
        with caplog.at_level(logging.WARNING):
            assert writer.write() is False
            assert writer.write() is False
        warnings = [r for r in caplog.records if "heartbeat" in r.getMessage()]
        assert len(warnings) == 1

    def test_success_rearms_the_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        directory = tmp_path / "later"
        writer = HeartbeatWriter(directory / "hb")
        with caplog.at_level(logging.WARNING):
            assert writer.write() is False
            directory.mkdir()
            assert writer.write() is True
            (directory / "hb").unlink()
            directory.rmdir()
            assert writer.write() is False
        warnings = [r for r in caplog.records if "heartbeat" in r.getMessage()]
        assert len(warnings) == 2


class TestChecker:
    def test_fresh_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_heartbeat(tmp_path / "hb")
        assert main([str(tmp_path / "hb"), "--max-age", "90"]) == 0
        assert capsys.readouterr().out.startswith("healthy:")

    def test_stale_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_heartbeat(tmp_path / "hb", now=datetime.now(UTC) - timedelta(seconds=120))
        assert main([str(tmp_path / "hb"), "--max-age", "90"]) == 1
        assert "unhealthy" in capsys.readouterr().err

    def test_missing_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main([str(tmp_path / "hb"), "--max-age", "90"]) == 1
        assert "does not exist" in capsys.readouterr().err

    def test_unreadable_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "hb").write_text("garbage")
        assert main([str(tmp_path / "hb"), "--max-age", "90"]) == 1
        assert "no readable timestamp" in capsys.readouterr().err

    def test_usage_error_exits_one_not_two(self) -> None:
        """Docker reserves exit code 2 in a HEALTHCHECK."""
        assert main([]) == 1

    def test_python_dash_m_in_a_child_process(self, tmp_path: Path) -> None:
        """The command the healthcheck runs, run the way it runs it."""
        write_heartbeat(tmp_path / "hb")
        fresh = subprocess.run(
            [
                sys.executable,
                "-m",
                "tempest_fastapi_sdk.tasks.heartbeat",
                str(tmp_path / "hb"),
                "--max-age",
                "90",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        missing = subprocess.run(
            [
                sys.executable,
                "-m",
                "tempest_fastapi_sdk.tasks.heartbeat",
                str(tmp_path / "missing"),
                "--max-age",
                "90",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert fresh.returncode == 0
        assert fresh.stdout.startswith("healthy:")
        assert "RuntimeWarning" not in fresh.stderr
        assert missing.returncode == 1
        assert missing.stderr.startswith("unhealthy:")


class KickRecorder:
    """Stand-in for ``broker.kick`` that records every publish.

    Attributes:
        sent (list[str]): Task names published.
    """

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent: list[str] = []

    async def __call__(self, message: BrokerMessage) -> None:
        """Record one publish.

        Args:
            message (BrokerMessage): The message the kicker built.
        """
        self.sent.append(message.task_name)


async def wait_for_files(*paths: Path, timeout: float = 10) -> None:
    """Poll until every path exists, or the deadline passes.

    Args:
        *paths (Path): The files to wait for.
        timeout (float): Seconds before giving up.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(path.exists() for path in paths):
            return
        await asyncio.sleep(0.05)


class TestWorkerHeartbeat:
    """Each worker process beats on its own, through no broker."""

    def test_registers_no_task(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=tmp_path / "w")
        assert queue.broker.find_task(HEARTBEAT_TASK_NAME) is None
        assert queue.worker_heartbeat is not None
        assert _sources(queue) == []

    @pytest.mark.asyncio
    async def test_beats_on_worker_startup_without_any_scheduler(
        self, tmp_path: Path
    ) -> None:
        """No scheduler runs here; the worker file still stays fresh."""
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=tmp_path / "w", every=timedelta(seconds=1))
        await queue.connect()
        try:
            await wait_for_files(tmp_path / "w")
            first = read_heartbeat(tmp_path / "w")
            deadline = time.monotonic() + 5
            while (
                read_heartbeat(tmp_path / "w") == first and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.05)
            second = read_heartbeat(tmp_path / "w")
        finally:
            await queue.disconnect()
        assert first is not None
        assert second is not None
        assert second > first

    @pytest.mark.asyncio
    async def test_shutdown_stops_the_loop(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=tmp_path / "w", every=timedelta(seconds=1))
        await queue.connect()
        await wait_for_files(tmp_path / "w")
        await queue.disconnect()
        loop = queue.worker_heartbeat
        assert loop is not None
        assert loop._task is None
        (tmp_path / "w").unlink()
        await asyncio.sleep(1.5)
        assert not (tmp_path / "w").exists()

    @pytest.mark.asyncio
    async def test_a_failed_write_keeps_the_loop_running(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        directory = tmp_path / "later"
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=directory / "w", every=timedelta(seconds=1))
        with caplog.at_level(logging.WARNING):
            await queue.connect()
            try:
                await asyncio.sleep(0.2)
                directory.mkdir()
                await wait_for_files(directory / "w")
            finally:
                await queue.disconnect()
        assert is_heartbeat_fresh(directory / "w", 10)
        assert len([r for r in caplog.records if "heartbeat" in r.getMessage()]) == 1

    @pytest.mark.asyncio
    async def test_loop_start_is_idempotent(self, tmp_path: Path) -> None:
        loop = HeartbeatLoop(HeartbeatWriter(tmp_path / "w"), 60)
        await loop.start()
        task = loop._task
        await loop.start()
        assert loop._task is task
        await loop.stop()
        await loop.stop()
        assert loop._task is None


class TestSchedulerHeartbeat:
    """The scheduler beats from its loop and publishes nothing."""

    def test_attaches_the_source(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(
            scheduler_file=tmp_path / "s", every=timedelta(seconds=5)
        )
        sources = _sources(queue)
        assert len(sources) == 1
        assert sources[0] in queue.scheduler.sources
        assert sources[0].schedule.interval == timedelta(seconds=5)
        assert queue.worker_heartbeat is None

    def test_pre_send_writes_then_cancels(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(scheduler_file=tmp_path / "s")
        source = _sources(queue)[0]
        with pytest.raises(ScheduledTaskCancelledError):
            source.pre_send(source.schedule)
        assert is_heartbeat_fresh(tmp_path / "s", 60)

    def test_pre_send_ignores_other_schedules(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(scheduler_file=tmp_path / "s")
        other = ScheduledTask(
            task_name="x",
            labels={},
            args=[],
            kwargs={},
            interval=5,
            schedule_id="other",
        )
        _sources(queue)[0].pre_send(other)
        assert not (tmp_path / "s").exists()

    def test_a_failed_write_still_cancels_and_does_not_raise_oserror(
        self, tmp_path: Path
    ) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(scheduler_file=tmp_path / "absent" / "s")
        source = _sources(queue)[0]
        with pytest.raises(ScheduledTaskCancelledError):
            source.pre_send(source.schedule)
        assert not (tmp_path / "absent").exists()

    @pytest.mark.asyncio
    async def test_schedule_is_the_heartbeat_interval(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(
            scheduler_file=tmp_path / "s", every=timedelta(seconds=7)
        )
        schedules = await _sources(queue)[0].get_schedules()
        assert [(s.schedule_id, s.interval) for s in schedules] == [
            (HEARTBEAT_SCHEDULE_ID, timedelta(seconds=7))
        ]

    @pytest.mark.asyncio
    async def test_the_scheduler_loop_writes_and_publishes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TaskIQ's own loop drives the beat; the broker never sees it."""
        queue = TaskQueue.memory()
        queue.enable_heartbeat(
            worker_file=tmp_path / "w",
            scheduler_file=tmp_path / "s",
            every=timedelta(seconds=1),
        )
        recorder = KickRecorder()
        monkeypatch.setattr(queue.broker, "kick", recorder)
        await queue.connect()
        await queue.start_scheduler()
        try:
            await wait_for_files(tmp_path / "w", tmp_path / "s")
            first = read_heartbeat(tmp_path / "s")
            deadline = time.monotonic() + 5
            while (
                read_heartbeat(tmp_path / "s") == first and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.05)
            second = read_heartbeat(tmp_path / "s")
        finally:
            await queue.disconnect()
        assert first is not None
        assert second is not None
        assert second > first
        assert is_heartbeat_fresh(tmp_path / "w", 10)
        assert recorder.sent == []


class TestEnableHeartbeatArguments:
    def test_needs_a_file(self) -> None:
        with pytest.raises(ValueError, match="worker_file, scheduler_file or both"):
            TaskQueue.memory().enable_heartbeat()

    @pytest.mark.parametrize("every", [timedelta(seconds=0), timedelta(seconds=1.5)])
    def test_needs_whole_seconds(self, tmp_path: Path, every: timedelta) -> None:
        with pytest.raises(ValueError, match="whole number of seconds"):
            TaskQueue.memory().enable_heartbeat(worker_file=tmp_path / "w", every=every)

    def test_cannot_be_enabled_twice(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=tmp_path / "w")
        with pytest.raises(RuntimeError, match="already called"):
            queue.enable_heartbeat(worker_file=tmp_path / "w")


class TestFromSettings:
    def test_empty_files_leave_the_heartbeat_off(self) -> None:
        queue = TaskQueue.from_settings(HeartbeatSettings())
        assert queue.worker_heartbeat is None
        assert _sources(queue) == []

    def test_scheduler_file_attaches_the_source(self, tmp_path: Path) -> None:
        queue = TaskQueue.from_settings(
            HeartbeatSettings(scheduler=str(tmp_path / "s"), seconds=12)
        )
        sources = _sources(queue)
        assert len(sources) == 1
        assert sources[0].schedule.interval == timedelta(seconds=12)
        assert sources[0].writer.path == tmp_path / "s"
        assert queue.worker_heartbeat is None

    def test_pydantic_settings_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("TASKIQ_BROKER_URL", "")
        monkeypatch.setenv("TASKIQ_WORKER_HEARTBEAT_FILE", str(tmp_path / "w"))
        monkeypatch.setenv("TASKIQ_HEARTBEAT_SECONDS", "15")
        settings = TaskIQSettings()
        assert settings.TASKIQ_SCHEDULER_HEARTBEAT_FILE == ""
        queue = TaskQueue.from_settings(settings)
        assert _sources(queue) == []
        loop = queue.worker_heartbeat
        assert loop is not None
        assert loop.every == 15
        assert loop.writer.path == tmp_path / "w"

    def test_settings_defaults(self) -> None:
        settings = TaskIQSettings()
        assert settings.TASKIQ_WORKER_HEARTBEAT_FILE == ""
        assert settings.TASKIQ_SCHEDULER_HEARTBEAT_FILE == ""
        assert settings.TASKIQ_HEARTBEAT_SECONDS == 30
