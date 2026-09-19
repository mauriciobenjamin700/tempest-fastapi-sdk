"""Tests for ``tempest shell``."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_CLI_ENTRY: str = "from tempest_fastapi_sdk.cli.main import main; main()"
"""Entry point the subprocess runs, avoiding runpy's re-import warning."""

_MODELS_MODULE = """
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel


class WidgetModel(BaseModel):
    __tablename__ = "widget"
    name: Mapped[str] = mapped_column()
"""

_SETTINGS_MODULE = """
from tempest_fastapi_sdk.settings import DatabaseSettings, ServerSettings


class Settings(ServerSettings, DatabaseSettings):
    pass


settings = Settings()
"""


@pytest.fixture
def service(tmp_path: Path) -> Path:
    """Write a project with settings and one mapped model."""
    for package in ("src", "src/db", "src/core"):
        (tmp_path / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "db" / "models.py").write_text(
        _MODELS_MODULE,
        encoding="utf-8",
    )
    (tmp_path / "src" / "core" / "settings.py").write_text(
        _SETTINGS_MODULE,
        encoding="utf-8",
    )
    return tmp_path


def _run_shell(script: str, *, cwd: Path, database_url: str | None) -> str:
    """Feed ``script`` to ``tempest shell`` in a fresh process.

    The console reads stdin, so piping a script exercises the real
    prompt — compilation flags, the loop the session lives on and the
    teardown included. Running it in-process would share this
    interpreter's event loop policy and model registry with the suite.

    Args:
        script (str): Lines typed at the prompt.
        cwd (Path): Project root to run in.
        database_url (str | None): ``DATABASE_URL`` for the child, or
            ``None`` to unset it.

    Returns:
        str: stdout and stderr, concatenated.
    """
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    args = [sys.executable, "-c", _CLI_ENTRY, "shell"]
    if database_url is None:
        args.append("--no-db")
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout + completed.stderr


class TestPrompt:
    def test_top_level_await_runs_on_the_session_loop(self, service: Path) -> None:
        """A session driven from a second loop raises MissingGreenlet."""
        output = _run_shell(
            'rows = await session.execute(text("select 1"))\n'
            'print("SCALAR:", rows.scalar_one())\n',
            cwd=service,
            database_url="sqlite+aiosqlite:///./shell_test.db",
        )
        assert "SCALAR: 1" in output
        assert "MissingGreenlet" not in output

    def test_models_and_settings_are_in_the_namespace(self, service: Path) -> None:
        output = _run_shell(
            'print("TABLE:", WidgetModel.__tablename__)\n'
            'print("URL:", settings.DATABASE_URL)\n',
            cwd=service,
            database_url="sqlite+aiosqlite:///./shell_test.db",
        )
        assert "TABLE: widget" in output
        assert "URL: sqlite+aiosqlite:///./shell_test.db" in output

    def test_the_sdk_base_class_is_not_offered(self, service: Path) -> None:
        """``models.py`` re-exports BaseModel; querying it is never what you meant."""
        output = _run_shell("", cwd=service, database_url="sqlite+aiosqlite:///./x.db")
        available = next(
            line for line in output.splitlines() if line.startswith("available:")
        )
        assert "WidgetModel" in available
        assert "BaseModel" not in available

    def test_session_is_closed_cleanly(self, service: Path) -> None:
        output = _run_shell(
            'await session.execute(text("select 1"))\n',
            cwd=service,
            database_url="sqlite+aiosqlite:///./shell_close.db",
        )
        assert "non-checked-in connection" not in output
        assert "greenlet is being finalized" not in output

    def test_no_db_starts_without_a_session(self, service: Path) -> None:
        output = _run_shell(
            'print("MODEL:", WidgetModel.__tablename__)\n',
            cwd=service,
            database_url=None,
        )
        assert "session: none" in output
        assert "MODEL: widget" in output


class TestFailures:
    def test_missing_url_exits_two(self, tmp_path: Path) -> None:
        env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
        completed = subprocess.run(
            [sys.executable, "-c", _CLI_ENTRY, "shell"],
            cwd=tmp_path,
            env=env,
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 2
        assert "no database URL" in completed.stderr
