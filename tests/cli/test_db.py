"""Tests for ``tempest db`` migration commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


@pytest.fixture
def project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Move into a fresh tmp dir + clear DATABASE_URL.

    The CLI walks ``./src/core/settings.py`` to resolve the URL when
    nothing is set on the environment. Each test runs in an empty
    directory so previous test state doesn't bleed in.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return tmp_path


class TestDbInit:
    def test_creates_alembic_layout(self, project_root: Path) -> None:
        result = runner.invoke(app, ["db", "init"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert (project_root / "alembic.ini").is_file()
        assert (project_root / "alembic" / "env.py").is_file()
        assert (project_root / "alembic" / "versions").is_dir()


def _seed_metadata_module(target: Path) -> None:
    """Seed a tiny ``src/db/models.py`` exposing ``BaseModel`` for env.py."""
    (target / "src" / "db").mkdir(parents=True, exist_ok=True)
    (target / "src" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "db" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "db" / "models.py").write_text(
        "from tempest_fastapi_sdk import BaseModel\n",
        encoding="utf-8",
    )


class TestDbWorkflow:
    def test_revision_then_upgrade_then_current(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_metadata_module(project_root)
        # The SDK alembic.ini ships with ``sqlalchemy.url`` empty; env.py
        # resolves the URL at runtime from DATABASE_URL or settings.
        db_url = f"sqlite+aiosqlite:///{project_root / 'cli_test.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        runner.invoke(app, ["db", "init", "--metadata-import", "src.db.models"])

        # Manual revision (no model diff) — just exercises the
        # revision → upgrade → current chain end-to-end.
        rev = runner.invoke(
            app,
            ["db", "revision", "-m", "empty", "--manual"],
        )
        assert rev.exit_code == 0, rev.stdout + rev.stderr

        up = runner.invoke(app, ["db", "upgrade"])
        assert up.exit_code == 0, up.stdout + up.stderr

        cur = runner.invoke(app, ["db", "current"])
        assert cur.exit_code == 0
        assert "(no revision applied)" not in cur.stdout

    def test_missing_alembic_ini_errors(
        self,
        project_root: Path,
    ) -> None:
        # No `tempest db init` here → alembic.ini doesn't exist.
        result = runner.invoke(app, ["db", "upgrade"])
        assert result.exit_code == 2
        assert "alembic.ini" in (result.stdout + result.stderr)


class TestResolveDatabaseUrl:
    def test_env_overrides_settings(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "DATABASE_URL",
            "sqlite+aiosqlite:///./from_env.db",
        )
        from tempest_fastapi_sdk.cli.db import _resolve_database_url

        assert _resolve_database_url(None) == "sqlite+aiosqlite:///./from_env.db"

    def test_explicit_overrides_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./env.db")
        from tempest_fastapi_sdk.cli.db import _resolve_database_url

        assert (
            _resolve_database_url("sqlite+aiosqlite:///./flag.db")
            == "sqlite+aiosqlite:///./flag.db"
        )
