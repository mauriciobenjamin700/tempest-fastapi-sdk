"""Tests for ``tempest doctor``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_SETTINGS_MODULE = """
from tempest_fastapi_sdk.settings import (
    DatabaseSettings,
    EmailSettings,
    ServerSettings,
)


class Settings(ServerSettings, DatabaseSettings, EmailSettings):
    pass


settings = Settings()
"""


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand inside a project whose settings compose three mixins."""
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "settings.py").write_text(
        _SETTINGS_MODULE,
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for variable in (
        "REDIS_URL",
        "RABBITMQ_URL",
        "SMTP_HOST",
        "MINIO_ENDPOINT",
        "STORAGE_ENDPOINT",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'doctor.db'}")
    yield tmp_path
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]


def _outcomes(output: str) -> dict[str, dict[str, str]]:
    """Index a ``--json`` report by check name.

    Args:
        output (str): The command's stdout.

    Returns:
        dict[str, dict[str, str]]: ``name -> outcome``.
    """
    return {entry["name"]: entry for entry in json.loads(output)}


class TestReport:
    def test_reaches_a_real_database(self, service: Path) -> None:
        """The point of the command: the line is a connection, not a guess."""
        result = runner.invoke(app, ["doctor", "--json"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert _outcomes(result.stdout)["database"]["status"] == "ok"

    def test_unconfigured_dependency_is_skipped_not_ok(self, service: Path) -> None:
        """A green line for a Redis nobody set up is the costly kind of lie."""
        outcomes = _outcomes(runner.invoke(app, ["doctor", "--json"]).stdout)
        assert outcomes["redis"]["status"] == "skip"
        assert outcomes["rabbitmq"]["status"] == "skip"

    def test_mixin_default_counts_as_unconfigured(self, service: Path) -> None:
        """``EmailSettings`` defaults SMTP_HOST to localhost, which is not a setup."""
        outcomes = _outcomes(runner.invoke(app, ["doctor", "--json"]).stdout)
        assert outcomes["smtp"]["status"] == "skip"
        assert "default" in outcomes["smtp"]["detail"]

    def test_configured_but_unreachable_dependency_fails(
        self,
        service: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", "9")
        result = runner.invoke(app, ["doctor", "--json", "--timeout", "1"])
        assert result.exit_code == 1
        outcomes = _outcomes(result.stdout)
        assert outcomes["smtp"]["status"] == "fail"
        assert "127.0.0.1:9" in outcomes["smtp"]["detail"]
        assert "check(s) failed" in result.stderr

    def test_broken_database_url_fails_with_the_driver_message(
        self,
        service: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The failure is reported, not raised at the operator as a traceback."""
        monkeypatch.setenv("DATABASE_URL", "sqlite+nosuchdriver:///app.db")
        result = runner.invoke(app, ["doctor", "--json", "--timeout", "1"])
        assert result.exit_code == 1
        assert _outcomes(result.stdout)["database"]["status"] == "fail"

    def test_table_output_lists_every_check(self, service: Path) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.stdout + result.stderr
        for name in ("python", "sdk", "settings", "database", "config checks"):
            assert name in result.stdout

    def test_outside_a_project_settings_is_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        outcomes = _outcomes(runner.invoke(app, ["doctor", "--json"]).stdout)
        assert outcomes["settings"]["status"] == "skip"
        assert outcomes["config checks"]["status"] == "skip"


class _OkRedisManager:
    """Pretend a Redis connection that answers ``PING``."""

    def __init__(self, url: str, **kwargs: object) -> None:
        """Keep the URL the check would connect to."""
        self.url: str = url

    async def connect(self) -> None:
        """No-op."""

    async def health_check(self) -> bool:
        """Always healthy."""
        return True

    async def disconnect(self) -> None:
        """No-op."""


class _FailingRedisManager(_OkRedisManager):
    """Pretend a Redis connection that raises with the raw URL."""

    async def connect(self) -> None:
        """Refuse, quoting the URL the check passed in."""
        raise ConnectionError(f"cannot reach {self.url}")


class _OkRabbitBroker:
    """Pretend a FastStream broker bound to a URL."""

    def __init__(self, url: str) -> None:
        """Keep the URL the check would bind to."""
        self.url: str = url


class _OkQueueManager:
    """Pretend a queue manager that starts and answers the ping."""

    def __init__(self, broker: _OkRabbitBroker) -> None:
        """Keep the broker."""
        self.broker: _OkRabbitBroker = broker

    async def connect(self) -> None:
        """No-op."""

    async def health_check(self) -> bool:
        """Always healthy."""
        return True

    async def disconnect(self) -> None:
        """No-op."""


class _FailingQueueManager(_OkQueueManager):
    """Pretend a queue manager whose connect raises with the raw URL."""

    async def connect(self) -> None:
        """Refuse, quoting the URL the broker bound to."""
        raise ConnectionError(f"cannot reach {self.broker.url}")


class _OkDatabaseManager:
    """Pretend an async DB manager that answers the health check."""

    def __init__(self, url: str, **kwargs: object) -> None:
        """Keep the URL and its mask."""
        self.url: str = url
        self.db_url_safe: str = _mask(url)

    async def connect(self) -> None:
        """No-op."""

    async def health_check(self) -> bool:
        """Always healthy."""
        return True

    async def disconnect(self) -> None:
        """No-op."""


class _FailingDatabaseManager(_OkDatabaseManager):
    """Pretend an async DB manager whose connect raises with the raw URL."""

    async def connect(self) -> None:
        """Refuse, quoting the URL the check passed in."""
        raise ConnectionError(f"cannot reach {self.url}")


def _mask(url: str) -> str:
    """Mask ``url`` the way the helpers under test must."""
    from tempest_fastapi_sdk.utils._url import mask_url_password

    return mask_url_password(url)


def _wire(monkeypatch: pytest.MonkeyPatch, failing: bool) -> None:
    """Export credential-carrying URLs and fake the connection layer."""
    monkeypatch.setenv("DATABASE_URL", TestCredentialMasking._DB)
    monkeypatch.setenv("REDIS_URL", TestCredentialMasking._REDIS)
    monkeypatch.setenv("RABBITMQ_URL", TestCredentialMasking._RABBIT)
    from tempest_fastapi_sdk import AsyncDatabaseManager
    from tempest_fastapi_sdk.cache import AsyncRedisManager
    from tempest_fastapi_sdk.queue import AsyncQueueManager

    database = _FailingDatabaseManager if failing else _OkDatabaseManager
    redis = _FailingRedisManager if failing else _OkRedisManager
    queue = _FailingQueueManager if failing else _OkQueueManager
    monkeypatch.setattr(AsyncDatabaseManager, "__init__", database.__init__)
    monkeypatch.setattr(AsyncDatabaseManager, "connect", database.connect)
    monkeypatch.setattr(AsyncDatabaseManager, "health_check", database.health_check)
    monkeypatch.setattr(AsyncDatabaseManager, "disconnect", database.disconnect)
    monkeypatch.setattr(AsyncRedisManager, "connect", redis.connect)
    monkeypatch.setattr(AsyncRedisManager, "health_check", redis.health_check)
    monkeypatch.setattr(AsyncRedisManager, "disconnect", redis.disconnect)
    monkeypatch.setattr(AsyncQueueManager, "__init__", queue.__init__)
    monkeypatch.setattr(AsyncQueueManager, "connect", queue.connect)
    monkeypatch.setattr(AsyncQueueManager, "health_check", queue.health_check)
    monkeypatch.setattr(AsyncQueueManager, "disconnect", queue.disconnect)
    monkeypatch.setattr("faststream.rabbit.RabbitBroker", _OkRabbitBroker)


class TestCredentialMasking:
    """No check may print a URL password, in any mode or format."""

    _DB = "postgresql+asyncpg://dbuser:db-secret-pw-491@127.0.0.1:5432/app"
    _REDIS = "redis://:redis-secret-pw-491@127.0.0.1:6379/0"
    _RABBIT = "amqp://guest:rabbit-secret-pw-491@127.0.0.1:5672/"
    _SECRETS = ("db-secret-pw-491", "redis-secret-pw-491", "rabbit-secret-pw-491")

    def _invoke(self, extra: list[str]) -> str:
        """Run ``tempest doctor`` with ``extra`` flags and join both streams."""
        result = runner.invoke(app, ["doctor", *extra])
        assert result.exit_code in (0, 1), result.stdout + result.stderr
        return result.stdout + result.stderr

    def test_ok_table_never_prints_a_password(
        self,
        service: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every ok ``CheckOutcome`` detail hides the URL passwords."""
        _wire(monkeypatch, failing=False)
        output = self._invoke([])
        for secret in self._SECRETS:
            assert secret not in output

    def test_ok_json_never_prints_a_password(
        self,
        service: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The ``--json`` report hides the URL passwords."""
        _wire(monkeypatch, failing=False)
        output = self._invoke(["--json"])
        for secret in self._SECRETS:
            assert secret not in output

    def test_fail_table_never_prints_a_password(
        self,
        service: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing probe that quotes the raw URL still hides its password."""
        _wire(monkeypatch, failing=True)
        output = self._invoke([])
        for secret in self._SECRETS:
            assert secret not in output

    def test_fail_json_never_prints_a_password(
        self,
        service: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing probe that quotes the raw URL still hides it from JSON."""
        _wire(monkeypatch, failing=True)
        output = self._invoke(["--json"])
        for secret in self._SECRETS:
            assert secret not in output
