"""Async SQLite-backed helpers for repository/service tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.schema import MetaData

from tempest_fastapi_sdk.db.model import BaseModel


def create_test_engine(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    echo: bool = False,
) -> AsyncEngine:
    """Build a throwaway async engine for tests.

    The default URL uses an in-memory SQLite database with
    :class:`StaticPool` so every connection shares the same store —
    necessary for tests that span multiple sessions in the same
    asyncio loop.

    Args:
        database_url (str): SQLAlchemy URL. Defaults to in-memory
            SQLite.
        echo (bool): Echo statements to stdout (useful for debugging
            failing tests).

    Returns:
        AsyncEngine: An engine ready to run :func:`init_test_metadata`.
    """
    kwargs: dict[str, object] = {"echo": echo}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    return create_async_engine(database_url, **kwargs)


def create_test_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to ``engine``.

    Args:
        engine (AsyncEngine): The engine to bind to.

    Returns:
        async_sessionmaker[AsyncSession]: A session factory with
        ``expire_on_commit=False`` so ORM instances survive past
        commit/rollback boundaries inside the same test body.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_test_metadata(
    engine: AsyncEngine,
    metadata: MetaData | None = None,
) -> None:
    """Create every table tracked by ``metadata`` on ``engine``.

    Args:
        engine (AsyncEngine): The engine to apply the DDL to.
        metadata (MetaData | None): The metadata to create. Defaults
            to :attr:`BaseModel.metadata` — pass an explicit metadata
            object if your tests use a different declarative base.
    """
    md = metadata or BaseModel.metadata
    async with engine.begin() as conn:
        await conn.run_sync(md.create_all)


async def drop_test_metadata(
    engine: AsyncEngine,
    metadata: MetaData | None = None,
) -> None:
    """Drop every table tracked by ``metadata`` on ``engine``.

    Args:
        engine (AsyncEngine): The engine to drop tables from.
        metadata (MetaData | None): Defaults to :attr:`BaseModel.metadata`.
    """
    md = metadata or BaseModel.metadata
    async with engine.begin() as conn:
        await conn.run_sync(md.drop_all)


@asynccontextmanager
async def test_database(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    metadata: MetaData | None = None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory backed by a freshly created database.

    Setup creates every table, teardown drops them and disposes the
    engine. Use as an ``async with`` block in test setups that need a
    clean database per scope.

    Args:
        database_url (str): SQLAlchemy URL.
        metadata (MetaData | None): The metadata to apply. Defaults
            to :attr:`BaseModel.metadata`.

    Yields:
        async_sessionmaker[AsyncSession]: A session factory ready to use.
    """
    engine = create_test_engine(database_url)
    try:
        await init_test_metadata(engine, metadata)
        yield create_test_session_factory(engine)
    finally:
        await drop_test_metadata(engine, metadata)
        await engine.dispose()


@asynccontextmanager
async def test_session(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    metadata: MetaData | None = None,
) -> AsyncIterator[AsyncSession]:
    """Yield a single :class:`AsyncSession` backed by a fresh database.

    Convenience wrapper around :func:`test_database` for tests that
    only need one session.

    Args:
        database_url (str): SQLAlchemy URL.
        metadata (MetaData | None): The metadata to apply.

    Yields:
        AsyncSession: A live session — closed automatically on exit.
    """
    async with (
        test_database(database_url, metadata=metadata) as factory,
        factory() as session,
    ):
        yield session


__all__: list[str] = [
    "create_test_engine",
    "create_test_session_factory",
    "drop_test_metadata",
    "init_test_metadata",
    "test_database",
    "test_session",
]
