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


def _seed_model_module(target: Path) -> None:
    """Seed a ``src/db/models.py`` with one concrete table for autogenerate."""
    (target / "src" / "db").mkdir(parents=True, exist_ok=True)
    (target / "src" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "db" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "db" / "models.py").write_text(
        "from sqlalchemy.orm import Mapped, mapped_column\n"
        "from tempest_fastapi_sdk import BaseModel\n\n\n"
        "class WidgetModel(BaseModel):\n"
        '    __tablename__ = "widget"\n'
        "    name: Mapped[str] = mapped_column()\n",
        encoding="utf-8",
    )


class TestDbStamp:
    def test_stamp_marks_current_without_running(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_model_module(project_root)
        db_url = f"sqlite+aiosqlite:///{project_root / 'stamp_test.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        runner.invoke(app, ["db", "init", "--metadata-import", "src.db.models"])
        runner.invoke(app, ["db", "revision", "-m", "init"])

        # No upgrade — stamp jumps the DB straight to head.
        stamp = runner.invoke(app, ["db", "stamp", "head"])
        assert stamp.exit_code == 0, stamp.stdout + stamp.stderr

        cur = runner.invoke(app, ["db", "current"])
        assert "(no revision applied)" not in cur.stdout


class TestDbSquash:
    def test_requires_yes_flag(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_model_module(project_root)
        db_url = f"sqlite+aiosqlite:///{project_root / 'squash_guard.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        runner.invoke(app, ["db", "init", "--metadata-import", "src.db.models"])

        result = runner.invoke(app, ["db", "squash"])
        assert result.exit_code == 2
        assert "--yes" in (result.stdout + result.stderr)

    def test_collapses_history_into_single_root(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_model_module(project_root)
        db_url = f"sqlite+aiosqlite:///{project_root / 'squash_test.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        runner.invoke(app, ["db", "init", "--metadata-import", "src.db.models"])

        # Build a multi-revision history, then apply it.
        runner.invoke(app, ["db", "revision", "-m", "init"])
        runner.invoke(app, ["db", "revision", "-m", "more", "--manual"])
        runner.invoke(app, ["db", "upgrade"])

        versions = project_root / "alembic" / "versions"
        before = [p for p in versions.glob("*.py") if p.name != "__init__.py"]
        assert len(before) == 2

        squash = runner.invoke(app, ["db", "squash", "-m", "init", "--yes"])
        assert squash.exit_code == 0, squash.stdout + squash.stderr

        # Exactly one root revision file remains in versions/.
        after = [p for p in versions.glob("*.py") if p.name != "__init__.py"]
        assert len(after) == 1

        # Old files were moved into the recoverable backup subdirectory.
        backups = list(versions.glob("_squashed_*"))
        assert len(backups) == 1
        assert len(list(backups[0].glob("*.py"))) == 2

        # The DB is left stamped at the new head.
        cur = runner.invoke(app, ["db", "current"])
        assert "(no revision applied)" not in cur.stdout


class TestDbBackupRestore:
    def test_backup_then_restore_roundtrip(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sqlite3

        db = project_root / "data.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
        conn.commit()
        conn.close()
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")

        out = project_root / "snap.sqlite"
        backup = runner.invoke(app, ["db", "backup", "-o", str(out)])
        assert backup.exit_code == 0, backup.stdout + backup.stderr
        assert out.is_file()

        # Wipe the table, then restore.
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM t")
        conn.commit()
        conn.close()

        restore = runner.invoke(app, ["db", "restore", str(out), "--yes"])
        assert restore.exit_code == 0, restore.stdout + restore.stderr

        conn = sqlite3.connect(db)
        count = conn.execute("SELECT count(*) FROM t").fetchone()[0]
        conn.close()
        assert count == 2

    def test_restore_requires_yes(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "DATABASE_URL", f"sqlite+aiosqlite:///{project_root / 'data.db'}"
        )
        result = runner.invoke(app, ["db", "restore", "whatever.sqlite"])
        assert result.exit_code == 2
        assert "--yes" in (result.stdout + result.stderr)


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
