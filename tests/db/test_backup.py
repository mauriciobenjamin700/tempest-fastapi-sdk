"""Tests for tempest_fastapi_sdk.db.backup.DatabaseBackup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tempest_fastapi_sdk.db import (
    BackupToolMissingError,
    DatabaseBackup,
    UnsupportedBackupBackendError,
)
from tempest_fastapi_sdk.db import backup as backup_mod


def _make_sqlite_db(path: Path, rows: int) -> None:
    """Create a SQLite file with a ``thing`` table holding ``rows`` rows."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE thing (id INTEGER PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO thing (id) VALUES (?)", [(i,) for i in range(rows)]
        )
        conn.commit()
    finally:
        conn.close()


def _count_rows(path: Path) -> int:
    """Return the row count of the ``thing`` table in a SQLite file."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT count(*) FROM thing").fetchone()[0]
    finally:
        conn.close()


class TestBackendDetection:
    def test_strips_async_driver(self) -> None:
        helper = DatabaseBackup("postgresql+asyncpg://u:p@h:5432/db")
        assert helper.backend == "postgresql"
        assert "+asyncpg" not in helper.url

    def test_sqlite_backend(self, tmp_path: Path) -> None:
        helper = DatabaseBackup(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
        assert helper.backend == "sqlite"


class TestSqliteBackupRestore:
    def test_backup_copies_file(self, tmp_path: Path) -> None:
        db = tmp_path / "app.db"
        _make_sqlite_db(db, rows=3)
        helper = DatabaseBackup(f"sqlite+aiosqlite:///{db}")
        out = tmp_path / "snapshot.sqlite"

        written = helper.backup(out)

        assert written == out
        assert out.is_file()
        assert _count_rows(out) == 3

    def test_restore_overwrites_target(self, tmp_path: Path) -> None:
        db = tmp_path / "app.db"
        _make_sqlite_db(db, rows=3)
        helper = DatabaseBackup(f"sqlite+aiosqlite:///{db}")
        snapshot = helper.backup(tmp_path / "snap.sqlite")

        # Mutate the live database, then restore the snapshot over it.
        db.unlink()
        _make_sqlite_db(db, rows=99)
        assert _count_rows(db) == 99

        helper.restore(snapshot)
        assert _count_rows(db) == 3

    def test_default_output_under_backups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The default output is ``backups/`` relative to the cwd — run in
        # tmp_path so the repo root is never polluted.
        monkeypatch.chdir(tmp_path)
        db = tmp_path / "app.db"
        _make_sqlite_db(db, rows=1)
        helper = DatabaseBackup(f"sqlite+aiosqlite:///{db}")

        written = helper.backup()
        assert written.parent.name == "backups"
        assert written.name.startswith("app_")
        assert written.suffix == ".sqlite"
        assert written.is_file()

    def test_in_memory_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        helper = DatabaseBackup("sqlite+aiosqlite:///:memory:")
        with pytest.raises(RuntimeError, match="in-memory"):
            helper.backup()

    def test_restore_missing_source_raises(self, tmp_path: Path) -> None:
        helper = DatabaseBackup(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
        with pytest.raises(FileNotFoundError):
            helper.restore(tmp_path / "nope.sqlite")


class TestPostgresCommandBuilding:
    """Postgres needs no live server here — we capture the built argv."""

    @pytest.fixture
    def captured(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            backup_mod, "_require_tool", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(
            backup_mod,
            "_run",
            lambda args, env=None: calls.append(args),
        )
        return calls

    def test_backup_custom_format_by_default(
        self, captured: list[list[str]], tmp_path: Path
    ) -> None:
        helper = DatabaseBackup("postgresql+asyncpg://u:p@h:5432/shop")
        helper.backup(tmp_path / "dump.dump")
        argv = captured[0]
        assert argv[0].endswith("pg_dump")
        assert "-Fc" in argv
        assert "-d" in argv and "shop" in argv

    def test_backup_plain_inferred_from_sql_extension(
        self, captured: list[list[str]], tmp_path: Path
    ) -> None:
        helper = DatabaseBackup("postgresql+asyncpg://u:p@h:5432/shop")
        helper.backup(tmp_path / "dump.sql")
        argv = captured[0]
        assert argv[0].endswith("pg_dump")
        assert "-Fc" not in argv

    def test_restore_custom_uses_pg_restore_clean(
        self, captured: list[list[str]], tmp_path: Path
    ) -> None:
        src = tmp_path / "dump.dump"
        src.write_bytes(b"x")
        helper = DatabaseBackup("postgresql+asyncpg://u:p@h:5432/shop")
        helper.restore(src)
        argv = captured[-1]
        assert argv[0].endswith("pg_restore")
        assert "--clean" in argv and "--if-exists" in argv

    def test_restore_plain_drops_schema_then_applies(
        self, captured: list[list[str]], tmp_path: Path
    ) -> None:
        src = tmp_path / "dump.sql"
        src.write_text("SELECT 1;", encoding="utf-8")
        helper = DatabaseBackup("postgresql+asyncpg://u:p@h:5432/shop")
        helper.restore(src)
        # First psql call drops/recreates public, second applies the file.
        assert (
            "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
            in (captured[0])
        )
        assert "-f" in captured[1] and str(src) in captured[1]

    def test_restore_no_clean_skips_drop(
        self, captured: list[list[str]], tmp_path: Path
    ) -> None:
        src = tmp_path / "dump.dump"
        src.write_bytes(b"x")
        helper = DatabaseBackup("postgresql+asyncpg://u:p@h:5432/shop")
        helper.restore(src, clean=False)
        argv = captured[-1]
        assert "--clean" not in argv


class TestErrors:
    def test_unsupported_backend(self) -> None:
        helper = DatabaseBackup("mysql://u:p@h/db")
        with pytest.raises(UnsupportedBackupBackendError):
            helper.backup()

    def test_missing_tool_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(backup_mod.shutil, "which", lambda name: None)
        helper = DatabaseBackup("postgresql://u:p@h:5432/db")
        with pytest.raises(BackupToolMissingError):
            helper.backup(tmp_path / "x.dump")
