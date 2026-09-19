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
    for variable in ("REDIS_URL", "RABBITMQ_URL", "SMTP_HOST", "MINIO_ENDPOINT"):
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
