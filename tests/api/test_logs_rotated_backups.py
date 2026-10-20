"""Log readers see rotated backups, size- and time-based alike."""

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    clear_log_files,
    make_logs_router,
    read_log_entries,
    rotated_log_files,
)


def _write(path: Path, *messages: str, mtime: float | None = None) -> None:
    """Write one JSON record per message, optionally pinning the mtime.

    Args:
        path (Path): The file to write.
        *messages (str): One record per message; the timestamp is derived
            from the message order so sorting is deterministic.
        mtime (float | None): Modification time to set afterwards.
    """
    lines = [
        json.dumps(
            {
                "timestamp": f"2026-10-{10 + index:02d}T00:00:00Z",
                "level": "INFO",
                "logger": "t",
                "message": message,
            }
        )
        for index, message in enumerate(messages)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def rotated_dir(tmp_path: Path) -> Path:
    """A log directory holding every backup shape plus decoys.

    Args:
        tmp_path (Path): pytest's temporary directory.

    Returns:
        Path: The directory.
    """
    _write(tmp_path / "info.log", "live", mtime=5_000)
    _write(tmp_path / "info.log.1", "numbered", mtime=4_000)
    _write(tmp_path / "info.log.2026-10-20_13", "hourly", mtime=3_000)
    _write(tmp_path / "info.log.2026-10-20", "daily", mtime=2_000)
    _write(tmp_path / "info.log.2026-10-19_13-05-00", "per-second", mtime=1_000)
    _write(tmp_path / "info.log.gz", "compressed decoy", mtime=6_000)
    _write(tmp_path / "info.log.swp", "editor decoy", mtime=6_000)
    _write(tmp_path / "info.logger", "prefix decoy", mtime=6_000)
    return tmp_path


class TestRotatedLogFiles:
    def test_lists_both_shapes_newest_first(self, rotated_dir: Path) -> None:
        names = [p.name for p in rotated_log_files(rotated_dir / "info.log")]
        assert names == [
            "info.log.1",
            "info.log.2026-10-20_13",
            "info.log.2026-10-20",
            "info.log.2026-10-19_13-05-00",
        ]

    def test_same_mtime_falls_back_to_the_scheme_order(self, tmp_path: Path) -> None:
        for name in (
            "info.log.2",
            "info.log.1",
            "info.log.2026-10-19",
            "info.log.2026-10-20",
        ):
            _write(tmp_path / name, name, mtime=1_000)
        names = [p.name for p in rotated_log_files(tmp_path / "info.log")]
        assert names[:2] == ["info.log.2026-10-20", "info.log.2026-10-19"]
        assert names[2:] == ["info.log.1", "info.log.2"]

    def test_missing_directory_is_empty(self, tmp_path: Path) -> None:
        assert rotated_log_files(tmp_path / "absent" / "info.log") == []


class TestReadLogEntries:
    def test_reads_live_and_every_backup(self, rotated_dir: Path) -> None:
        result = read_log_entries(rotated_dir, "info")
        messages = {entry["message"] for entry in result.entries}
        assert messages == {"live", "numbered", "hourly", "daily", "per-second"}
        assert result.truncated is False

    def test_reads_backups_when_the_live_file_is_gone(self, tmp_path: Path) -> None:
        _write(tmp_path / "error.log.2026-10-20", "kept from yesterday")
        result = read_log_entries(tmp_path, "error")
        assert [e["message"] for e in result.entries] == ["kept from yesterday"]

    def test_cap_covers_the_backups_newest_first(self, rotated_dir: Path) -> None:
        """The per-file bound holds across the live file and its backups."""
        result = read_log_entries(rotated_dir, "info", max_records_per_file=3)
        messages = {entry["message"] for entry in result.entries}
        assert messages == {"live", "numbered", "hourly"}
        assert result.truncated is True

    def test_cap_exactly_met_by_the_last_file_is_not_truncation(
        self, rotated_dir: Path
    ) -> None:
        result = read_log_entries(rotated_dir, "info", max_records_per_file=5)
        assert len(result.entries) == 5
        assert result.truncated is False


class TestClearLogFiles:
    def test_clear_deletes_backups_and_keeps_decoys(self, rotated_dir: Path) -> None:
        cleared = clear_log_files(rotated_dir, "info")
        assert cleared == [
            "info.log",
            "info.log.1",
            "info.log.2026-10-20_13",
            "info.log.2026-10-20",
            "info.log.2026-10-19_13-05-00",
        ]
        assert (rotated_dir / "info.log").read_text() == ""
        assert rotated_log_files(rotated_dir / "info.log") == []
        assert (rotated_dir / "info.log.gz").exists()
        assert read_log_entries(rotated_dir, "info").entries == []


@pytest.mark.asyncio
async def test_logs_endpoint_returns_timed_backups(rotated_dir: Path) -> None:
    app = FastAPI()
    app.include_router(
        make_logs_router(log_dir=rotated_dir, allow_unauthenticated=True)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/logs", params={"source": "info"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert {item["message"] for item in body["items"]} >= {"daily", "hourly"}
