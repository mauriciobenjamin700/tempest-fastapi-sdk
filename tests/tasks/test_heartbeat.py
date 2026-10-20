"""Worker and scheduler heartbeats, and the healthcheck that reads them."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from taskiq.scheduler.scheduled_task import ScheduledTask

from tempest_fastapi_sdk.settings import TaskIQSettings
from tempest_fastapi_sdk.tasks import (
    HEARTBEAT_SCHEDULE_ID,
    HEARTBEAT_TASK_NAME,
    HeartbeatWriter,
    TaskQueue,
    heartbeat_age,
    is_heartbeat_fresh,
    read_heartbeat,
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


class TestEnableHeartbeat:
    def test_registers_the_task_and_attaches_the_source(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(
            worker_file=tmp_path / "w",
            scheduler_file=tmp_path / "s",
            every=timedelta(seconds=5),
        )
        assert queue.broker.find_task(HEARTBEAT_TASK_NAME) is not None
        sources = _sources(queue)
        assert len(sources) == 1
        assert sources[0].schedule.interval == timedelta(seconds=5)
        assert sources[0].schedule.task_name == HEARTBEAT_TASK_NAME

    @pytest.mark.asyncio
    async def test_the_worker_task_writes_the_worker_file(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=tmp_path / "w")
        task = queue.broker.find_task(HEARTBEAT_TASK_NAME)
        assert task is not None
        await queue.connect()
        try:
            await (await task.kiq()).wait_result(timeout=5)
        finally:
            await queue.disconnect()
        assert is_heartbeat_fresh(tmp_path / "w", 60)
        assert not (tmp_path / "s").exists()

    @pytest.mark.asyncio
    async def test_a_failed_worker_write_does_not_fail_the_task(
        self, tmp_path: Path
    ) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=tmp_path / "read-only" / "w")
        task = queue.broker.find_task(HEARTBEAT_TASK_NAME)
        assert task is not None
        await queue.connect()
        try:
            result = await (await task.kiq()).wait_result(timeout=5)
        finally:
            await queue.disconnect()
        assert result.is_err is False

    def test_post_send_writes_the_scheduler_file(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(scheduler_file=tmp_path / "s")
        source = _sources(queue)[0]
        source.post_send(source.schedule)
        assert is_heartbeat_fresh(tmp_path / "s", 60)

    def test_post_send_ignores_other_schedules(self, tmp_path: Path) -> None:
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
        _sources(queue)[0].post_send(other)
        assert not (tmp_path / "s").exists()

    def test_a_failed_scheduler_write_does_not_raise(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(scheduler_file=tmp_path / "absent" / "s")
        source = _sources(queue)[0]
        source.post_send(source.schedule)
        assert not (tmp_path / "absent").exists()

    @pytest.mark.asyncio
    async def test_schedule_is_the_heartbeat_interval(self, tmp_path: Path) -> None:
        queue = TaskQueue.memory()
        queue.enable_heartbeat(worker_file=tmp_path / "w", every=timedelta(seconds=7))
        schedules = await _sources(queue)[0].get_schedules()
        assert [(s.schedule_id, s.interval) for s in schedules] == [
            (HEARTBEAT_SCHEDULE_ID, timedelta(seconds=7))
        ]

    @pytest.mark.asyncio
    async def test_the_scheduler_loop_drives_both_files(self, tmp_path: Path) -> None:
        """TaskIQ's own loop sends the beat; worker and scheduler both write."""
        queue = TaskQueue.memory()
        queue.enable_heartbeat(
            worker_file=tmp_path / "w",
            scheduler_file=tmp_path / "s",
            every=timedelta(seconds=1),
        )
        await queue.connect()
        await queue.start_scheduler()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if (tmp_path / "w").exists() and (tmp_path / "s").exists():
                    break
                await asyncio.sleep(0.05)
        finally:
            await queue.disconnect()
        assert is_heartbeat_fresh(tmp_path / "w", 10)
        assert is_heartbeat_fresh(tmp_path / "s", 10)

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
        assert queue.broker.find_task(HEARTBEAT_TASK_NAME) is None
        assert _sources(queue) == []

    def test_a_file_enables_it(self, tmp_path: Path) -> None:
        queue = TaskQueue.from_settings(
            HeartbeatSettings(scheduler=str(tmp_path / "s"), seconds=12)
        )
        assert queue.broker.find_task(HEARTBEAT_TASK_NAME) is not None
        sources = _sources(queue)
        assert len(sources) == 1
        assert sources[0].schedule.interval == timedelta(seconds=12)
        assert sources[0].writer is not None
        assert sources[0].writer.path == tmp_path / "s"

    def test_pydantic_settings_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("TASKIQ_BROKER_URL", "")
        monkeypatch.setenv("TASKIQ_WORKER_HEARTBEAT_FILE", str(tmp_path / "w"))
        monkeypatch.setenv("TASKIQ_HEARTBEAT_SECONDS", "15")
        settings = TaskIQSettings()
        assert settings.TASKIQ_SCHEDULER_HEARTBEAT_FILE == ""
        queue = TaskQueue.from_settings(settings)
        sources = _sources(queue)
        assert len(sources) == 1
        assert sources[0].schedule.interval == timedelta(seconds=15)
        assert sources[0].writer is None

    def test_settings_defaults(self) -> None:
        settings = TaskIQSettings()
        assert settings.TASKIQ_WORKER_HEARTBEAT_FILE == ""
        assert settings.TASKIQ_SCHEDULER_HEARTBEAT_FILE == ""
        assert settings.TASKIQ_HEARTBEAT_SECONDS == 30
