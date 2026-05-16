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
