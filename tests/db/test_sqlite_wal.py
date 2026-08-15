"""Tests for SQLite WAL mode and the busy timeout on ``AsyncDatabaseManager``.

The scenario these protect is the one an application meets the day it
grows a worker: the web process and ``taskiq worker`` writing the same
``app.db``. In the default rollback journal a reader and a writer exclude
each other, so one of them dies with ``database is locked`` — a failure
that never appears in a single-process test suite.
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from tempest_fastapi_sdk.db import AsyncDatabaseManager

HOLDER_SCRIPT: str = textwrap.dedent(
    """
    import sqlite3, sys, time

    path, hold = sys.argv[1], float(sys.argv[2])
    conn = sqlite3.connect(path, timeout=2.0, isolation_level=None)
    conn.execute("BEGIN")
    conn.execute("SELECT count(*) FROM t").fetchone()
    print("ready", flush=True)
    time.sleep(hold)
    """
)
"""A second process holding a read transaction open on the database."""


def _seed(path: Path, journal_mode: str) -> None:
    """Create the table and fix the file's journal mode.

    The mode is set before any other connection exists because switching
    to WAL needs the file to itself for a moment.

    Args:
        path (Path): The database file.
        journal_mode (str): ``"wal"`` or ``"delete"``.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"PRAGMA journal_mode={journal_mode}")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


async def _journal_mode(manager: AsyncDatabaseManager) -> str:
    """Return the journal mode the manager's connections see.

    Args:
        manager (AsyncDatabaseManager): A connected manager.

    Returns:
        str: The value ``PRAGMA journal_mode`` answers.
    """
    async with manager.engine.connect() as conn:
        return str((await conn.execute(text("PRAGMA journal_mode"))).scalar())


class TestDefaults:
    """What a plain ``AsyncDatabaseManager`` gives a SQLite file."""

    async def test_wal_is_on_by_default(self, tmp_path: Path) -> None:
        """A file database opens in WAL without being asked."""
        manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
        await manager.connect()
        try:
            assert await _journal_mode(manager) == "wal"
        finally:
            await manager.disconnect()

    async def test_busy_timeout_is_thirty_seconds(self, tmp_path: Path) -> None:
        """The driver's own default is 5s, which is short for a worker."""
        manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
        await manager.connect()
        try:
            async with manager.engine.connect() as conn:
                busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        finally:
            await manager.disconnect()

        assert busy == 30_000

    async def test_memory_database_is_unaffected(self) -> None:
        """SQLite answers ``memory`` for an in-memory file, without error."""
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await manager.connect()
        try:
            assert await _journal_mode(manager) == "memory"
        finally:
            await manager.disconnect()


class TestOptOut:
    """Both knobs turn off, and an explicit ``connect_args`` still wins."""

    async def test_sqlite_wal_false_keeps_the_rollback_journal(
        self, tmp_path: Path
    ) -> None:
        """Needed on filesystems whose shared memory does not work."""
        manager = AsyncDatabaseManager(
            f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
            sqlite_wal=False,
        )
        await manager.connect()
        try:
            assert await _journal_mode(manager) == "delete"
        finally:
            await manager.disconnect()

    async def test_busy_timeout_is_configurable(self, tmp_path: Path) -> None:
        """The value reaches the driver as milliseconds."""
        manager = AsyncDatabaseManager(
            f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
            sqlite_busy_timeout=2.5,
        )
        await manager.connect()
        try:
            async with manager.engine.connect() as conn:
                busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        finally:
            await manager.disconnect()

        assert busy == 2_500

    async def test_explicit_connect_args_timeout_wins(self, tmp_path: Path) -> None:
        """``connect_args`` is the escape hatch and outranks the knob."""
        manager = AsyncDatabaseManager(
            f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
            sqlite_busy_timeout=30.0,
            connect_args={"timeout": 1.0},
        )
        await manager.connect()
        try:
            async with manager.engine.connect() as conn:
                busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        finally:
            await manager.disconnect()

        assert busy == 1_000

    def test_other_backends_get_neither(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PostgreSQL must not receive a SQLite-only connect argument."""
        captured: dict[str, Any] = {}

        def _fake_create_engine(url: str, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return None

        monkeypatch.setattr(
            "tempest_fastapi_sdk.db.connection.create_async_engine",
            _fake_create_engine,
        )
        monkeypatch.setattr(
            "tempest_fastapi_sdk.db.connection.async_sessionmaker",
            lambda *args, **kwargs: None,
        )
        manager = AsyncDatabaseManager("postgresql+asyncpg://u:p@localhost:5432/db")

        asyncio.run(manager.connect())

        assert "timeout" not in captured.get("connect_args", {})


class TestContentionAcrossProcesses:
    """The failure WAL exists to prevent, reproduced between two processes.

    A subprocess holds a read transaction open — the worker chewing on a
    document — while this process writes, the way a request handler would.
    """

    @staticmethod
    def _write_while_held(path: Path) -> str | None:
        """Insert a row while another process holds a read transaction.

        The journal mode is already fixed on the file by the time this
        runs, so the only variable is which mode that is.

        Args:
            path (Path): The database file, already seeded.

        Returns:
            str | None: The driver's error text, or ``None`` on success.
        """
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER_SCRIPT, str(path), "5"],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "ready"
            time.sleep(0.2)

            writer = sqlite3.connect(str(path), timeout=1.0)
            try:
                writer.execute("INSERT INTO t (id) VALUES (1)")
                writer.commit()
                return None
            except sqlite3.OperationalError as exc:
                return str(exc)
            finally:
                writer.close()
        finally:
            holder.kill()
            holder.wait()

    async def test_rollback_journal_locks_the_writer_out(self, tmp_path: Path) -> None:
        """Without WAL the writer waits out the timeout and then fails.

        This is the ``sqlite3.OperationalError: database is locked`` an
        application hits the day it runs a worker beside the web process.
        """
        path = tmp_path / "app.db"
        _seed(path, "delete")
        manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{path}", sqlite_wal=False)
        await manager.connect()
        await manager.disconnect()

        error = self._write_while_held(path)

        assert error is not None
        assert "database is locked" in error

    async def test_wal_lets_the_write_through(self, tmp_path: Path) -> None:
        """With WAL the same write commits while the reader is still open."""
        path = tmp_path / "app.db"
        _seed(path, "wal")
        manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{path}")
        await manager.connect()
        assert await _journal_mode(manager) == "wal"
        await manager.disconnect()

        assert self._write_while_held(path) is None
