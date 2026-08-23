"""Tests for tempest_fastapi_sdk.db.connection.AsyncDatabaseManager."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from tempest_fastapi_sdk.db import AsyncDatabaseManager


class TestConnectAndDisconnect:
    async def test_connect_is_idempotent(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await manager.connect()
        await manager.connect()
        await manager.disconnect()

    async def test_disconnect_clears_engine(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await manager.connect()
        await manager.disconnect()
        assert manager._engine is None

    async def test_is_connected_flag(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        assert manager.is_connected is False
        await manager.connect()
        assert manager.is_connected is True
        await manager.disconnect()
        assert manager.is_connected is False


class TestBackendDetection:
    def test_sqlite_url_flagged(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        assert manager.is_sqlite is True

    def test_postgres_url_not_flagged(self) -> None:
        manager = AsyncDatabaseManager(
            "postgresql+asyncpg://user:pass@localhost:5432/db"
        )
        assert manager.is_sqlite is False

    def test_misleading_substring_url_not_flagged(self) -> None:
        # Older `"sqlite" in db_url` check would false-positive here.
        manager = AsyncDatabaseManager(
            "postgresql+asyncpg://user:pass@my-sqlite-backup-host/db"
        )
        assert manager.is_sqlite is False


class TestEngineKwargs:
    async def test_poolclass_override(self) -> None:
        manager = AsyncDatabaseManager(
            "sqlite+aiosqlite:///:memory:",
            poolclass=NullPool,
        )
        await manager.connect()
        try:
            assert isinstance(manager._engine.pool, NullPool)  # type: ignore[union-attr]
        finally:
            await manager.disconnect()


class TestSessionContext:
    async def test_session_commits_on_success(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        async with manager.get_session_context() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        await manager.disconnect()

    async def test_session_rolls_back_on_error(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        with pytest.raises(RuntimeError):
            async with manager.get_session_context() as session:
                await session.execute(text("SELECT 1"))
                raise RuntimeError("boom")
        await manager.disconnect()


class TestSessionDependency:
    async def test_yields_async_session(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        gen = manager.session_dependency()
        session = await anext(gen)
        try:
            assert isinstance(session, AsyncSession)
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        finally:
            with pytest.raises(StopAsyncIteration):
                await anext(gen)
            await manager.disconnect()

    async def test_dependency_exits_cleanly(self) -> None:
        # The dependency should yield one session and exit without
        # raising. Mirrors how FastAPI consumes it via ``Depends``.
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        gen = manager.session_dependency()
        session = await anext(gen)
        await session.execute(text("SELECT 1"))
        with pytest.raises(StopAsyncIteration):
            await anext(gen)
        await manager.disconnect()


class TestHealthCheck:
    async def test_returns_true_when_db_responds(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        assert await manager.health_check() is True
        await manager.disconnect()

    async def test_returns_false_when_url_is_bad(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:////nonexistent/path.db")
        # File-based SQLite with invalid path → connection error.
        # We expect health_check to swallow it and return False.
        result = await manager.health_check()
        assert result is False
        await manager.disconnect()


class TestUrlMasking:
    def test_db_url_safe_hides_password(self) -> None:
        manager = AsyncDatabaseManager(
            "postgresql+asyncpg://alice:supersecret@db.internal:5432/app",
        )
        safe = manager.db_url_safe
        assert "supersecret" not in safe
        assert "alice" in safe
        assert "db.internal" in safe

    def test_db_url_safe_handles_url_without_password(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        assert manager.db_url_safe.startswith("sqlite+aiosqlite://")

    def test_db_url_no_public_attribute(self) -> None:
        manager = AsyncDatabaseManager(
            "postgresql+asyncpg://alice:supersecret@db.internal:5432/app",
        )
        # Credentials must not be reachable via a public attribute.
        assert not hasattr(manager, "db_url") or "supersecret" not in str(
            getattr(manager, "db_url", "")
        )


class TestRequireConnected:
    async def test_session_methods_lazy_connect(self) -> None:
        # get_session / get_session_context / session_dependency all
        # lazy-connect on first use, so calling them on a brand-new
        # manager must work without an explicit connect().
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        session = await manager.get_session()
        await session.close()
        assert manager.is_connected is True
        await manager.disconnect()


class TestInMemoryOverlappingSessions:
    """``:memory:`` has to behave like a database, not like one connection.

    Plain ``sqlite+aiosqlite:///:memory:`` makes SQLAlchemy pick
    ``StaticPool``: every session shares **one** DBAPI connection. Since
    v0.200.0 the manager also emits an explicit ``BEGIN`` per transaction —
    needed so ``RELEASE SAVEPOINT`` stops committing on SQLite — and the two
    together broke every overlapping session with ``cannot start a
    transaction within a transaction``.

    Both properties are measured here, because the obvious fix (drop the
    ``BEGIN`` when the connection is shared) trades one defect for the other:
    measured on this repository, it makes the nested-block rows durable
    through an outer rollback.
    """

    async def test_two_overlapping_sessions_work(self) -> None:
        """The shape the issue reported: a session opened inside another."""
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await manager.connect()
        await manager.create_tables()
        try:
            async with manager.get_session_context() as first:
                await first.execute(text("SELECT 1"))
                async with manager.get_session_context() as second:
                    await second.execute(text("SELECT 1"))
        finally:
            await manager.disconnect()

    async def test_a_released_savepoint_is_still_not_durable(self) -> None:
        """The property the explicit ``BEGIN`` exists for, on ``:memory:``.

        A nested block that exits cleanly must not survive the outer
        rollback. Without the ``BEGIN``, SQLite treats the ``SAVEPOINT`` as
        the outermost transaction and ``RELEASE`` commits it.
        """
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await manager.connect()
        await manager.create_tables()
        try:
            async with manager.get_session_context() as session:
                await session.execute(
                    text("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
                )
                await session.commit()
                async with session.begin_nested():
                    await session.execute(text("INSERT INTO probe (id) VALUES (1)"))
                await session.rollback()
            async with manager.get_session_context() as check:
                rows = (await check.execute(text("SELECT id FROM probe"))).all()
            assert rows == []
        finally:
            await manager.disconnect()

    async def test_writes_are_visible_across_sessions(self) -> None:
        """One in-memory database per manager, not one per connection.

        Sharing is what plain ``:memory:`` gives up when it stops using a
        single connection, so the manager asks for a shared cache instead.
        """
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await manager.connect()
        try:
            async with manager.get_session_context() as writer:
                await writer.execute(text("CREATE TABLE probe (id INTEGER)"))
                await writer.execute(text("INSERT INTO probe (id) VALUES (7)"))
                await writer.commit()
            async with manager.get_session_context() as reader:
                rows = (await reader.execute(text("SELECT id FROM probe"))).all()
            assert rows == [(7,)]
        finally:
            await manager.disconnect()

    async def test_two_managers_do_not_share_a_database(self) -> None:
        """The shared cache is named per manager, so isolation is kept."""
        first = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        second = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await first.connect()
        await second.connect()
        try:
            async with first.get_session_context() as session:
                await session.execute(text("CREATE TABLE only_here (id INTEGER)"))
                await session.commit()
            async with second.get_session_context() as other:
                found = (
                    await other.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='table' AND name='only_here'"
                        )
                    )
                ).all()
            assert found == []
        finally:
            await first.disconnect()
            await second.disconnect()

    async def test_an_explicit_poolclass_is_left_alone(self) -> None:
        """The escape hatch: a caller who wants one connection still gets it.

        ``poolclass=StaticPool`` restores the pre-fix topology — including
        its failure on overlapping sessions — because a caller passing a pool
        explicitly has a reason, and the manager should not override it.
        """
        from sqlalchemy.pool import StaticPool

        manager = AsyncDatabaseManager(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        await manager.connect()
        try:
            assert isinstance(manager.engine.pool, StaticPool)
        finally:
            await manager.disconnect()

    async def test_a_file_database_keeps_its_url(self) -> None:
        """Only the in-memory case is rewritten."""
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///./probe-url.db")
        assert manager.is_memory_sqlite is False
        assert manager.is_sqlite is True

    def test_memory_detection_covers_both_spellings(self) -> None:
        """``:memory:`` and the ``mode=memory`` URI form."""
        from tempest_fastapi_sdk.db.connection import is_memory_sqlite_url

        assert is_memory_sqlite_url("sqlite+aiosqlite:///:memory:") is True
        assert (
            is_memory_sqlite_url(
                "sqlite+aiosqlite:///file:x?mode=memory&cache=shared&uri=true"
            )
            is True
        )
        assert is_memory_sqlite_url("sqlite+aiosqlite:///./file.db") is False
        assert is_memory_sqlite_url("postgresql+asyncpg://h/db") is False
