"""Async SQLite-backed helpers for repository/service tests."""

from __future__ import annotations

import warnings
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.schema import MetaData

from tempest_fastapi_sdk.db.connection import _configure_sqlite_engine
from tempest_fastapi_sdk.db.model import BaseModel


def create_test_engine(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    echo: bool = False,
    foreign_keys: bool = True,
) -> AsyncEngine:
    """Build a throwaway async engine for tests.

    The default URL uses an in-memory SQLite database with
    :class:`StaticPool` so every connection shares the same store —
    necessary for tests that span multiple sessions in the same
    asyncio loop.

    A SQLite engine gets the same configuration
    :class:`~tempest_fastapi_sdk.db.AsyncDatabaseManager` applies, minus
    WAL: :func:`~tempest_fastapi_sdk.db.enable_sqlite_savepoints`, so a
    nested ``begin_nested()`` that exits cleanly does not commit the
    outer transaction, and, unless ``foreign_keys=False``,
    :func:`~tempest_fastapi_sdk.db.enable_sqlite_foreign_keys`, so an
    orphan row raises ``IntegrityError`` and ``ON DELETE CASCADE``
    deletes the children — what PostgreSQL does. A test that inserts a
    child must therefore insert its parent first.

    Args:
        database_url (str): SQLAlchemy URL. Defaults to in-memory
            SQLite.
        echo (bool): Echo statements to stdout (useful for debugging
            failing tests).
        foreign_keys (bool): Whether a SQLite engine enforces
            ``FOREIGN KEY`` constraints. Ignored on other backends.

    Returns:
        AsyncEngine: An engine ready to run :func:`init_test_metadata`.
    """
    kwargs: dict[str, object] = {"echo": echo}
    is_sqlite = make_url(database_url).get_backend_name() == "sqlite"
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    engine = create_async_engine(database_url, **kwargs)
    if is_sqlite:
        _configure_sqlite_engine(engine, wal=False, foreign_keys=foreign_keys)
    return engine


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
async def make_test_database(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    metadata: MetaData | None = None,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Yield a session factory backed by a freshly created database.

    Setup creates every table, teardown drops them and disposes the
    engine. Use as an ``async with`` block in test setups that need a
    clean database per scope. The name does not start with ``test``, so
    importing it into a test module does not make pytest collect it as
    a test.

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
async def make_test_session(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    metadata: MetaData | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a single :class:`AsyncSession` backed by a fresh database.

    Convenience wrapper around :func:`make_test_database` for tests that
    only need one session. The name does not start with ``test``, so
    importing it into a test module does not make pytest collect it as
    a test.

    Args:
        database_url (str): SQLAlchemy URL.
        metadata (MetaData | None): The metadata to apply.

    Yields:
        AsyncSession: A live session — closed automatically on exit.
    """
    async with (
        make_test_database(database_url, metadata=metadata) as factory,
        factory() as session,
    ):
        yield session


_RENAME_REASON: str = (
    "pytest collects any imported callable whose name starts with 'test' "
    "as a phantom test"
)
"""Why the ``test_*`` helpers were renamed, quoted by both deprecation warnings."""


def test_database(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    metadata: MetaData | None = None,
) -> AbstractAsyncContextManager[async_sessionmaker[AsyncSession]]:
    """Deprecated alias of :func:`make_test_database`.

    Kept so existing imports keep working. Marked ``__test__ = False``,
    so pytest does not collect it even while a test module imports it.

    Args:
        database_url (str): SQLAlchemy URL.
        metadata (MetaData | None): The metadata to apply.

    Returns:
        AbstractAsyncContextManager[async_sessionmaker[AsyncSession]]:
        The context manager :func:`make_test_database` returns.

    Warns:
        DeprecationWarning: On every call; use :func:`make_test_database`.
    """
    warnings.warn(
        f"test_database() is deprecated, use make_test_database(): {_RENAME_REASON}.",
        DeprecationWarning,
        stacklevel=2,
    )
    return make_test_database(database_url, metadata=metadata)


def test_session(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    *,
    metadata: MetaData | None = None,
) -> AbstractAsyncContextManager[AsyncSession]:
    """Deprecated alias of :func:`make_test_session`.

    Kept so existing imports keep working. Marked ``__test__ = False``,
    so pytest does not collect it even while a test module imports it.

    Args:
        database_url (str): SQLAlchemy URL.
        metadata (MetaData | None): The metadata to apply.

    Returns:
        AbstractAsyncContextManager[AsyncSession]: The context manager
        :func:`make_test_session` returns.

    Warns:
        DeprecationWarning: On every call; use :func:`make_test_session`.
    """
    warnings.warn(
        f"test_session() is deprecated, use make_test_session(): {_RENAME_REASON}.",
        DeprecationWarning,
        stacklevel=2,
    )
    return make_test_session(database_url, metadata=metadata)


test_database.__test__ = False  # type: ignore[attr-defined]
test_session.__test__ = False  # type: ignore[attr-defined]


__all__: list[str] = [
    "create_test_engine",
    "create_test_session_factory",
    "drop_test_metadata",
    "init_test_metadata",
    "make_test_database",
    "make_test_session",
    "test_database",
    "test_session",
]
