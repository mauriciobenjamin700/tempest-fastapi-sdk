"""Tests for tempest_fastapi_sdk.db.backup.DatabaseBackup."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import time
from collections.abc import Iterator
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


class TestDockerCommandBuilding:
    """The argv the Docker mode builds, without needing a daemon."""

    @pytest.fixture
    def captured(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[list[str]]]:
        calls: dict[str, list[list[str]]] = {"run": [], "to_file": [], "from_file": []}
        monkeypatch.setattr(
            backup_mod, "_require_tool", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(
            backup_mod, "_run", lambda args, env=None: calls["run"].append(args)
        )
        monkeypatch.setattr(
            backup_mod,
            "_run_to_file",
            lambda args, dest, env=None: calls["to_file"].append(args),
        )
        monkeypatch.setattr(
            backup_mod,
            "_run_from_file",
            lambda args, source, env=None: calls["from_file"].append(args),
        )
        return calls

    def test_backup_runs_pg_dump_inside_the_container(
        self, captured: dict[str, list[list[str]]], tmp_path: Path
    ) -> None:
        helper = DatabaseBackup(
            "postgresql+asyncpg://u:p@db-host:5432/shop",
            docker_container="app-db",
        )
        helper.backup(tmp_path / "dump.dump")
        argv = captured["to_file"][0]
        assert argv[0].endswith("docker")
        assert argv[1:3] == ["exec", "-i"]
        assert "app-db" in argv
        assert "pg_dump" in argv
        assert "-Fc" in argv

    def test_password_crosses_by_name_not_by_value(
        self, captured: dict[str, list[list[str]]], tmp_path: Path
    ) -> None:
        """`-e PGPASSWORD` (no value) — Docker copies it from our env.

        Spelling it `-e PGPASSWORD=hunter2` would put the password in the
        container's command line, where any `ps` on the host reads it.
        """
        helper = DatabaseBackup(
            "postgresql+asyncpg://u:hunter2@db-host:5432/shop",
            docker_container="app-db",
        )
        helper.backup(tmp_path / "dump.dump")
        argv = captured["to_file"][0]
        assert "PGPASSWORD" in argv
        assert not any("hunter2" in part for part in argv)

    def test_host_and_port_are_dropped_inside_the_container(
        self, captured: dict[str, list[list[str]]], tmp_path: Path
    ) -> None:
        """`db-host:5432` is how the *app* reaches it, not a route in there."""
        helper = DatabaseBackup(
            "postgresql+asyncpg://u:p@db-host:5432/shop",
            docker_container="app-db",
        )
        helper.backup(tmp_path / "dump.dump")
        argv = captured["to_file"][0]
        assert "-h" not in argv
        assert "db-host" not in argv
        assert "-U" in argv and "u" in argv
        assert "-d" in argv and "shop" in argv

    def test_restore_streams_the_dump_into_the_container(
        self, captured: dict[str, list[list[str]]], tmp_path: Path
    ) -> None:
        """Streamed on stdin — no `docker cp`, nothing to clean up in there."""
        dump = tmp_path / "dump.dump"
        dump.write_bytes(b"PGDMP")
        helper = DatabaseBackup(
            "postgresql+asyncpg://u:p@db-host:5432/shop",
            docker_container="app-db",
        )
        helper.restore(dump)
        argv = captured["from_file"][0]
        assert argv[0].endswith("docker")
        assert "pg_restore" in argv
        assert "--clean" in argv

    def test_plain_restore_drops_the_schema_first(
        self, captured: dict[str, list[list[str]]], tmp_path: Path
    ) -> None:
        dump = tmp_path / "dump.sql"
        dump.write_text("SELECT 1;", encoding="utf-8")
        helper = DatabaseBackup(
            "postgresql+asyncpg://u:p@db-host:5432/shop",
            docker_container="app-db",
        )
        helper.restore(dump)
        assert any("DROP SCHEMA" in " ".join(argv) for argv in captured["run"])
        assert "psql" in captured["from_file"][0]

    def test_without_the_container_nothing_changes(
        self, captured: dict[str, list[list[str]]], tmp_path: Path
    ) -> None:
        helper = DatabaseBackup("postgresql+asyncpg://u:p@db-host:5432/shop")
        helper.backup(tmp_path / "dump.dump")
        assert captured["to_file"] == []
        argv = captured["run"][0]
        assert argv[0].endswith("pg_dump")
        assert "-h" in argv


@pytest.mark.docker
class TestDockerRoundTrip:
    """Backup and restore across a real container, not a mocked argv.

    The argv tests above cannot answer the question that matters — whether
    `pg_dump` inside the container produces a dump this host can hand back
    to `pg_restore` inside it. This starts a real `postgres:16-alpine`,
    writes rows, crosses the container boundary both ways, and reads the
    rows back out.
    """

    IMAGE: str = "postgres:16-alpine"
    CONTAINER: str = "tempest-backup-roundtrip"

    @pytest.fixture
    def postgres(self) -> Iterator[str]:
        if shutil.which("docker") is None:
            pytest.skip("docker CLI not installed")
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon not reachable")

        subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)
        started = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                self.CONTAINER,
                "-e",
                "POSTGRES_PASSWORD=secret",
                "-e",
                "POSTGRES_USER=app",
                "-e",
                "POSTGRES_DB=shop",
                self.IMAGE,
            ],
            capture_output=True,
            text=True,
        )
        if started.returncode != 0:
            pytest.skip(f"could not start {self.IMAGE}: {started.stderr.strip()}")
        try:
            for _ in range(60):
                ready = subprocess.run(
                    ["docker", "exec", self.CONTAINER, "pg_isready", "-U", "app"],
                    capture_output=True,
                )
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                pytest.skip("postgres never became ready")
            yield self.CONTAINER
        finally:
            subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)

    def _psql(self, container: str, sql: str) -> str:
        done = subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                "PGPASSWORD=secret",
                container,
                "psql",
                "-U",
                "app",
                "-d",
                "shop",
                "-t",
                "-A",
                "-c",
                sql,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def test_dump_and_restore_cross_the_container_boundary(
        self, postgres: str, tmp_path: Path
    ) -> None:
        self._psql(
            postgres,
            "CREATE TABLE thing (id serial primary key, total int);"
            "INSERT INTO thing (total) VALUES (1042), (77);",
        )
        helper = DatabaseBackup(
            "postgresql+asyncpg://app:secret@127.0.0.1:5432/shop",
            docker_container=postgres,
        )

        dump = helper.backup(tmp_path / "shop.dump")
        assert dump.read_bytes().startswith(b"PGDMP")

        self._psql(postgres, "DROP TABLE thing;")
        helper.restore(dump)
        assert (
            self._psql(postgres, "SELECT count(*), sum(total) FROM thing;") == "2|1119"
        )

    def test_plain_dump_round_trip(self, postgres: str, tmp_path: Path) -> None:
        self._psql(
            postgres,
            "CREATE TABLE plain_thing (id serial primary key, total int);"
            "INSERT INTO plain_thing (total) VALUES (5);",
        )
        helper = DatabaseBackup(
            "postgresql+asyncpg://app:secret@127.0.0.1:5432/shop",
            docker_container=postgres,
        )
        dump = helper.backup(tmp_path / "shop.sql")
        assert b"plain_thing" in dump.read_bytes()

        self._psql(postgres, "INSERT INTO plain_thing (total) VALUES (999);")
        helper.restore(dump)
        assert (
            self._psql(postgres, "SELECT count(*), sum(total) FROM plain_thing;")
            == "1|5"
        )

    def test_a_missing_container_fails_without_leaving_a_dump(
        self, postgres: str, tmp_path: Path
    ) -> None:
        """A truncated file that looks like a backup is worse than no file."""
        helper = DatabaseBackup(
            "postgresql+asyncpg://app:secret@127.0.0.1:5432/shop",
            docker_container="tempest-backup-does-not-exist",
        )
        dest = tmp_path / "never.dump"
        with pytest.raises(RuntimeError, match="No such container"):
            helper.backup(dest)
        assert not dest.exists()
