"""Tests for the session and schema helpers of ``AsyncDatabaseManager``.

Covers :func:`session_dependency_for` (request session from a manager
built on demand), :meth:`AsyncDatabaseManager.transaction` (explicit
commit-on-exit alias) and the ``metadata`` argument of
``create_tables`` / ``drop_tables``.
"""

from collections.abc import AsyncGenerator, Callable
from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import Integer, String, event, func, inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, session_dependency_for


class OwnBase(DeclarativeBase):
    """A ``DeclarativeBase`` independent of the SDK's ``BaseModel``."""


class ObjectRow(OwnBase):
    """Row keyed by a natural key, as an S3-style service would declare it."""

    __tablename__ = "own_base_object_row"

    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)


def _table_names(connection: Connection) -> list[str]:
    """Return the table names visible on a sync connection.

    Args:
        connection (Connection): The sync connection ``run_sync`` passes.

    Returns:
        list[str]: The table names.
    """
    return inspect(connection).get_table_names()


async def _tables(manager: AsyncDatabaseManager) -> list[str]:
    """List the tables that exist in the manager's database.

    Args:
        manager (AsyncDatabaseManager): The connected manager.

    Returns:
        list[str]: The table names.
    """
    async with manager.engine.connect() as connection:
        return await connection.run_sync(_table_names)


@pytest.fixture
async def manager() -> AsyncGenerator[AsyncDatabaseManager]:
    """Yield a connected in-memory manager with ``ObjectRow`` created.

    Yields:
        AsyncDatabaseManager: The manager.
    """
    database = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await database.create_tables(OwnBase.metadata)
    yield database
    await database.disconnect()


def _client(app: FastAPI) -> httpx.AsyncClient:
    """Build an in-process client that turns app errors into ``500``.

    Args:
        app (FastAPI): The application under test.

    Returns:
        httpx.AsyncClient: The client.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class _SessionSpy:
    """Record commits and closes of the sessions a request receives."""

    def __init__(self) -> None:
        """Start with no recorded event."""
        self.commits: int = 0
        self.closes: int = 0

    def watch(self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
        """Count ``COMMIT`` and ``close()`` on ``session``.

        Args:
            session (AsyncSession): The request session.
            monkeypatch (pytest.MonkeyPatch): Used to wrap ``close``.
        """
        original_close: Callable[[], Any] = session.close

        def on_commit(_session: Session) -> None:
            """Count one commit.

            Args:
                _session (Session): The committing sync session.
            """
            self.commits += 1

        async def close() -> None:
            """Count the close, then close for real."""
            self.closes += 1
            await original_close()

        event.listen(session.sync_session, "after_commit", on_commit)
        monkeypatch.setattr(session, "close", close)


class TestSessionDependencyFor:
    async def test_manager_is_not_built_at_import(
        self, manager: AsyncDatabaseManager
    ) -> None:
        calls: list[int] = []

        def get_db() -> AsyncDatabaseManager:
            calls.append(1)
            return manager

        get_session = session_dependency_for(get_db)
        app = FastAPI()

        @app.get("/ping")
        async def ping(
            session: Annotated[AsyncSession, Depends(get_session)],
        ) -> dict[str, int]:
            return {"ok": 1}

        assert calls == []
        async with _client(app) as client:
            response = await client.get("/ping")
        assert response.status_code == 200
        assert calls == [1]

    async def test_teardown_does_not_commit(
        self, manager: AsyncDatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _SessionSpy()
        get_session = session_dependency_for(lambda: manager)
        app = FastAPI()

        @app.post("/objects")
        async def create(
            session: Annotated[AsyncSession, Depends(get_session)],
        ) -> dict[str, int]:
            spy.watch(session, monkeypatch)
            session.add(ObjectRow(bucket="b", key="k", size=1))
            await session.flush()
            return {"ok": 1}

        async with _client(app) as client:
            response = await client.post("/objects")

        assert response.status_code == 200
        assert spy.commits == 0
        assert spy.closes == 1
        async with manager.get_session_context() as session:
            count = await session.scalar(select(func.count()).select_from(ObjectRow))
        assert count == 0

    async def test_session_closed_when_endpoint_raises(
        self, manager: AsyncDatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _SessionSpy()
        get_session = session_dependency_for(lambda: manager)
        app = FastAPI()

        @app.post("/boom")
        async def boom(
            session: Annotated[AsyncSession, Depends(get_session)],
        ) -> dict[str, int]:
            spy.watch(session, monkeypatch)
            session.add(ObjectRow(bucket="b", key="boom", size=1))
            await session.flush()
            raise RuntimeError("endpoint failed")

        async with _client(app) as client:
            response = await client.post("/boom")

        assert response.status_code == 500
        assert spy.commits == 0
        assert spy.closes == 1
        async with manager.get_session_context() as session:
            count = await session.scalar(select(func.count()).select_from(ObjectRow))
        assert count == 0

    async def test_explicit_commit_in_endpoint_persists(
        self, manager: AsyncDatabaseManager
    ) -> None:
        get_session = session_dependency_for(lambda: manager)
        app = FastAPI()

        @app.post("/objects")
        async def create(
            session: Annotated[AsyncSession, Depends(get_session)],
        ) -> dict[str, int]:
            session.add(ObjectRow(bucket="b", key="kept", size=3))
            await session.commit()
            return {"ok": 1}

        async with _client(app) as client:
            response = await client.post("/objects")

        assert response.status_code == 200
        async with manager.get_session_context() as session:
            row = await session.get(ObjectRow, ("b", "kept"))
        assert row is not None
        assert row.size == 3


class TestTransactionAlias:
    async def test_commits_on_clean_exit(self, manager: AsyncDatabaseManager) -> None:
        async with manager.transaction() as session:
            session.add(ObjectRow(bucket="t", key="k", size=1))
        async with manager.get_session_context() as session:
            assert await session.get(ObjectRow, ("t", "k")) is not None

    async def test_rolls_back_on_error(self, manager: AsyncDatabaseManager) -> None:
        with pytest.raises(RuntimeError):
            async with manager.transaction() as session:
                session.add(ObjectRow(bucket="t", key="gone", size=1))
                await session.flush()
                raise RuntimeError("abort")
        async with manager.get_session_context() as session:
            assert await session.get(ObjectRow, ("t", "gone")) is None


class TestTablesWithOwnMetadata:
    async def test_default_ignores_own_declarative_base(self) -> None:
        database = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        try:
            await database.create_tables()
            assert ObjectRow.__tablename__ not in await _tables(database)
        finally:
            await database.disconnect()

    async def test_create_and_drop_with_own_metadata(self) -> None:
        database = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        try:
            await database.create_tables(OwnBase.metadata)
            assert await _tables(database) == [ObjectRow.__tablename__]

            async with database.transaction() as session:
                session.add(ObjectRow(bucket="b", key="k", size=7))
            async with database.transaction() as session:
                row = await session.get(ObjectRow, ("b", "k"))
            assert row is not None

            await database.drop_tables(OwnBase.metadata)
            assert await _tables(database) == []
        finally:
            await database.disconnect()
