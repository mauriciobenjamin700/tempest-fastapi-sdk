"""Time-based rotation in ``configure_logging`` and its settings wiring."""

import logging
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    LOG_ROTATE_WHEN_CHOICES,
    LogUtils,
    configure_logging,
    read_log_entries,
    validate_rotate_when,
)
from tempest_fastapi_sdk.settings import LogSettings


def _file_handlers() -> list[logging.FileHandler]:
    """Return the root logger's file handlers.

    Returns:
        list[logging.FileHandler]: Every file handler on the root logger.
    """
    return [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.FileHandler)
    ]


class TestRotationMode:
    """``rotate_when`` picks the handler class."""

    def test_rotate_when_selects_timed_handlers(self, tmp_path: Path) -> None:
        configure_logging(
            level="INFO",
            log_dir=tmp_path,
            stdout=False,
            rotate_when="midnight",
            backup_count=7,
        )
        handlers = _file_handlers()
        assert len(handlers) == 6
        for handler in handlers:
            assert isinstance(handler, TimedRotatingFileHandler)
            assert handler.when == "MIDNIGHT"
            assert handler.backupCount == 7
            assert handler.utc is True
            assert handler.encoding == "utf-8"

    def test_max_bytes_is_ignored_under_time_rotation(self, tmp_path: Path) -> None:
        """A tiny ``max_bytes`` must not cut a file mid-period."""
        log = configure_logging(
            level="INFO",
            log_dir=tmp_path,
            stdout=False,
            rotate_when="H",
            max_bytes=100,
        )
        for index in range(50):
            log.info("line %s with enough padding to pass max_bytes", index)
        assert sorted(p.name for p in tmp_path.glob("info.log*")) == ["info.log"]
        assert (tmp_path / "info.log").stat().st_size > 100

    def test_interval_is_forwarded(self, tmp_path: Path) -> None:
        configure_logging(
            level="INFO",
            log_dir=tmp_path,
            stdout=False,
            rotate_when="h",
            rotate_interval=6,
        )
        handler = _file_handlers()[0]
        assert isinstance(handler, TimedRotatingFileHandler)
        assert handler.interval == 6 * 3600

    def test_empty_rotate_when_keeps_size_rotation(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_dir=tmp_path, stdout=False, rotate_when="")
        handlers = _file_handlers()
        assert handlers
        assert all(type(h) is RotatingFileHandler for h in handlers)

    def test_rollover_writes_a_dated_backup_that_readers_see(
        self, tmp_path: Path
    ) -> None:
        """The backup name is the period's UTC date, and it stays readable."""
        log = configure_logging(
            level="INFO",
            log_dir=tmp_path,
            stdout=False,
            rotate_when="midnight",
            backup_count=7,
        )
        log.info("yesterday's record")
        handler = next(
            h for h in _file_handlers() if h.baseFilename.endswith("info.log")
        )
        assert isinstance(handler, TimedRotatingFileHandler)
        forced_rollover_at = int(time.time()) - 1
        handler.rolloverAt = forced_rollover_at
        log.info("today's record")

        period_start = datetime.fromtimestamp(
            forced_rollover_at - handler.interval, tz=UTC
        )
        backups = sorted(p.name for p in tmp_path.glob("info.log.*"))
        assert backups == [f"info.log.{period_start:%Y-%m-%d}"]

        messages = [e["message"] for e in read_log_entries(tmp_path, "info").entries]
        assert messages == ["today's record", "yesterday's record"]


class TestValidation:
    """Unknown units fail at the call, before handlers are swapped."""

    @pytest.mark.parametrize("when", ["S", "m", "H", "d", "midnight", "W0", "w6"])
    def test_accepted_units(self, when: str) -> None:
        assert validate_rotate_when(when) == when

    @pytest.mark.parametrize("when", ["daily", "W7", "hourly", "1d", " "])
    def test_unknown_unit_is_rejected(self, when: str) -> None:
        with pytest.raises(ValueError, match="invalid log rotation when"):
            validate_rotate_when(when)

    def test_choices_match_the_stdlib(self, tmp_path: Path) -> None:
        """Every listed unit builds a handler; one past the list does not."""
        for when in LOG_ROTATE_WHEN_CHOICES:
            handler = TimedRotatingFileHandler(tmp_path / "x.log", when=when)
            handler.close()
        with pytest.raises(ValueError, match="Invalid rollover interval"):
            TimedRotatingFileHandler(tmp_path / "x.log", when="X")

    def test_configure_logging_rejects_before_touching_handlers(
        self, tmp_path: Path
    ) -> None:
        configure_logging(level="INFO", log_dir=tmp_path, stdout=False)
        before = list(logging.getLogger().handlers)
        with pytest.raises(ValueError, match="invalid log rotation when"):
            configure_logging(
                level="INFO", log_dir=tmp_path, stdout=False, rotate_when="daily"
            )
        assert logging.getLogger().handlers == before

    def test_interval_below_one_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="rotate_interval must be at least 1"):
            configure_logging(
                level="INFO",
                log_dir=tmp_path,
                stdout=False,
                rotate_when="H",
                rotate_interval=0,
            )


class TestSettingsWiring:
    """``LogSettings`` carries the two knobs into ``configure_logging``."""

    def test_defaults_keep_size_rotation(self) -> None:
        settings = LogSettings()
        kwargs = settings.logging_kwargs()
        assert kwargs["rotate_when"] is None
        assert kwargs["rotate_interval"] == 1

    def test_one_week_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("LOG_ROTATE_WHEN", "midnight")
        monkeypatch.setenv("LOG_BACKUP_COUNT", "7")
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        settings = LogSettings()
        kwargs = settings.logging_kwargs()
        assert kwargs["rotate_when"] == "midnight"
        assert kwargs["backup_count"] == 7

        configure_logging(stdout=True, **kwargs)
        handler = _file_handlers()[0]
        assert isinstance(handler, TimedRotatingFileHandler)
        assert handler.when == "MIDNIGHT"
        assert handler.backupCount == 7

    def test_unknown_unit_fails_at_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_ROTATE_WHEN", "daily")
        with pytest.raises(ValidationError, match="invalid log rotation when"):
            LogSettings()

    def test_interval_below_one_fails_at_boot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_ROTATE_INTERVAL", "0")
        with pytest.raises(ValidationError):
            LogSettings()


class TestLogUtilsForwarding:
    """The facade forwards the new knobs like the old ones."""

    def test_logger_scope_forwards_rotation(self, tmp_path: Path) -> None:
        log = LogUtils(
            "tempest.rotation.facade",
            log_dir=tmp_path,
            stdout=False,
            rotate_when="midnight",
            scope="logger",
        )
        handlers = [
            h for h in log.logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert handlers
        assert all(isinstance(h, TimedRotatingFileHandler) for h in handlers)

    def test_configure_forwards_rotation(self, tmp_path: Path) -> None:
        logger = LogUtils.configure(
            logger_name="tempest.rotation.configure",
            log_dir=tmp_path,
            stdout=False,
            rotate_when="H",
            rotate_interval=2,
        )
        handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert isinstance(handler, TimedRotatingFileHandler)
        assert handler.interval == 2 * 3600


class TestOneWeek:
    """``midnight`` + ``backup_count=7`` is one week of history."""

    def test_keeps_the_seven_newest_days(self, tmp_path: Path) -> None:
        log = configure_logging(
            level="INFO",
            log_dir=tmp_path,
            stdout=False,
            rotate_when="midnight",
            backup_count=7,
        )
        handler = next(
            h for h in _file_handlers() if h.baseFilename.endswith("info.log")
        )
        assert isinstance(handler, TimedRotatingFileHandler)
        today = int(time.time()) // 86_400 * 86_400
        for days_ago in range(10, 0, -1):
            handler.rolloverAt = today - days_ago * 86_400 + 86_400
            log.info("record from %s days ago", days_ago)
        backups = sorted(p.name for p in tmp_path.glob("info.log.*"))
        expected = [
            "info.log."
            + datetime.fromtimestamp(today - days * 86_400, tz=UTC).strftime("%Y-%m-%d")
            for days in range(7, 0, -1)
        ]
        assert len(backups) == 7
        assert backups == expected
