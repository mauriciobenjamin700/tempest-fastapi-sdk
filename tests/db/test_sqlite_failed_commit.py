"""A ``COMMIT`` SQLite refuses must not leave the pooled connection dirty.

SQLAlchemy marks its transaction inactive the moment ``COMMIT`` raises,
so closing the connection afterwards neither rolls back nor lets the
pool do it. Under :func:`~tempest_fastapi_sdk.enable_sqlite_savepoints`
the driver connection then goes back to the pool still inside the
explicit ``BEGIN``, and the next checkout fails with ``cannot start a
transaction within a transaction`` (issue #411).

The refusal is forced, not raced: a stdlib ``sqlite3`` connection holds
a read transaction open on the same file in the rollback journal, so the
writer's ``COMMIT`` cannot take the exclusive lock and fails after a
``busy_timeout`` of 0.1 s. Every engine here has one connection in use at
a time, so the next checkout is the connection that failed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tempest_fastapi_sdk import enable_sqlite_savepoints
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.db.transaction import transaction

BUSY_TIMEOUT: float = 0.1
"""Seconds the refused ``COMMIT`` waits before SQLite gives up."""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a rollback-journal database with one row.

    Args:
        tmp_path (Path): pytest's per-test directory.

    Returns:
        Path: The database file.
    """
    path = tmp_path / "app.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=delete")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
        conn.execute("INSERT INTO t VALUES (1, 0)")
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
async def engine(db_path: Path) -> AsyncIterator[AsyncEngine]:
    """Yield a hand-built engine configured with the savepoint recipe.

    Args:
        db_path (Path): The database file.

    Yields:
        AsyncEngine: The engine, disposed afterwards.
    """
    built = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": BUSY_TIMEOUT},
    )
    enable_sqlite_savepoints(built)
    yield built
    await built.dispose()


@pytest.fixture
async def manager(db_path: Path) -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a connected manager on the file, without WAL.

    Args:
        db_path (Path): The database file.

    Yields:
        AsyncDatabaseManager: The manager, disconnected afterwards.
    """
    built = AsyncDatabaseManager(
        f"sqlite+aiosqlite:///{db_path}",
        sqlite_wal=False,
        sqlite_busy_timeout=BUSY_TIMEOUT,
    )
    await built.connect()
    yield built
    await built.disconnect()


@contextmanager
def held_read(db_path: Path) -> Iterator[None]:
    """Hold a read transaction open on the file from a second connection.

    Args:
        db_path (Path): The database file.

    Yields:
        None: While the shared lock is held.
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute("BEGIN")
        conn.execute("SELECT v FROM t").fetchall()
        yield
        conn.execute("ROLLBACK")
    finally:
        conn.close()


def committed_value(db_path: Path) -> int:
    """Read the row's committed value from a fresh connection.

    Args:
        db_path (Path): The database file.

    Returns:
        int: The value of ``t.v``.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("SELECT v FROM t").fetchone()[0])
    finally:
        conn.close()


async def assert_next_transaction_works(engine: AsyncEngine) -> None:
    """Open, use and commit a transaction on the engine's pooled connection.

    Args:
        engine (AsyncEngine): The engine whose COMMIT was refused.
    """
    async with engine.begin() as conn:
        assert (await conn.execute(text("SELECT v FROM t"))).scalar() == 0


class TestFailedCommitLeavesConnectionClean:
    """The connection that failed to commit is usable by the next caller."""

    async def test_core_connection(self, engine: AsyncEngine, db_path: Path) -> None:
        """``Connection.commit()`` refused, then the connection is closed."""
        with held_read(db_path), pytest.raises(OperationalError, match="locked"):
            async with engine.connect() as conn:
                await conn.begin()
                await conn.execute(text("UPDATE t SET v = 1"))
                await conn.commit()
        await assert_next_transaction_works(engine)
        assert committed_value(db_path) == 0

    async def test_transaction_helper(
        self,
        engine: AsyncEngine,
        db_path: Path,
    ) -> None:
        """The SDK's ``transaction()`` commits on exit and does not roll back."""
        maker = async_sessionmaker(engine)
        with held_read(db_path), pytest.raises(OperationalError, match="locked"):
            async with maker() as session, transaction(session):
                await session.execute(text("UPDATE t SET v = 1"))
        await assert_next_transaction_works(engine)
        assert committed_value(db_path) == 0

    async def test_manager_without_wal(
        self,
        manager: AsyncDatabaseManager,
        db_path: Path,
    ) -> None:
        """The manager's own engine gets the listener through the configurator.

        ``manager.transaction()`` rolls the session back after a failed
        commit and so never hit the bug; a session from
        ``manager.get_session()`` scoped with the ``transaction()``
        helper — the repository path — did.
        """
        session = await manager.get_session()
        with held_read(db_path), pytest.raises(OperationalError, match="locked"):
            async with session, transaction(session):
                await session.execute(text("UPDATE t SET v = 1"))
        await assert_next_transaction_works(manager.engine)
        assert committed_value(db_path) == 0
