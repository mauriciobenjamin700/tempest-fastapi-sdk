"""Async database manager with engine/session lifecycle helpers."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import Pool

from tempest_fastapi_sdk.db.model import BaseModel


class AsyncDatabaseManager:
    """Manage the async SQLAlchemy engine and session lifecycle.

    Handles engine creation tailored to the database backend (SQLite
    gets ``check_same_thread=False`` by default, everything else
    gets a pooled config), session factory construction, and table
    create/drop helpers. Designed to be instantiated once per
    application and reused across requests.

    Backend detection uses ``sqlalchemy.engine.make_url`` so URLs
    like ``sqlite+aiosqlite://...`` are matched precisely without
    relying on substring tricks.

    Attributes:
        is_sqlite (bool): Whether the URL targets a SQLite backend.

    The connection URL itself is stored on a private attribute so it
    never leaks through ``repr()`` or accidental logging. Use the
    :attr:`db_url_safe` property when a redacted form is needed.
    """

    def __init__(
        self,
        db_url: str,
        *,
        echo: bool = False,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_recycle: int = 3600,
        connect_args: dict[str, Any] | None = None,
        poolclass: type[Pool] | None = None,
        **engine_kwargs: Any,
    ) -> None:
        """Initialize the manager (does not open connections yet).

        Args:
            db_url (str): The database connection URL.
            echo (bool): Whether to emit SQL to stdout.
            pool_size (int): Number of permanent connections in the
                pool. Ignored for SQLite URLs.
            max_overflow (int): Extra connections allowed above the
                pool size. Ignored for SQLite URLs.
            pool_recycle (int): Recycle connections older than this
                many seconds. Ignored for SQLite URLs.
            connect_args (dict[str, Any] | None): Driver-level
                arguments forwarded to ``create_async_engine``
                (e.g. ``{"ssl": "require"}`` for asyncpg). SQLite
                always receives ``check_same_thread=False`` unless
                explicitly overridden here.
            poolclass (type[Pool] | None): Override SQLAlchemy's
                default pool class. Useful for tests
                (``poolclass=NullPool``) or specialized topologies.
            **engine_kwargs: Any additional keyword arguments are
                passed through to ``create_async_engine`` verbatim.
        """
        self._db_url: str = db_url
        self.is_sqlite: bool = make_url(db_url).get_backend_name() == "sqlite"
        self._echo: bool = echo
        self._pool_size: int = pool_size
        self._max_overflow: int = max_overflow
        self._pool_recycle: int = pool_recycle
        self._connect_args: dict[str, Any] = dict(connect_args or {})
        self._poolclass: type[Pool] | None = poolclass
        self._engine_kwargs: dict[str, Any] = engine_kwargs
        self._engine: AsyncEngine | None = None
        self._session_maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def db_url_safe(self) -> str:
        """Return the URL with credentials masked.

        Useful for diagnostics, health payloads or log lines —
        ``postgresql+asyncpg://user:pass@host/db`` becomes
        ``postgresql+asyncpg://***@host/db``.

        Returns:
            str: The URL safe to surface outside the manager.
        """
        url = make_url(self._db_url)
        return url.render_as_string(hide_password=True)

    @property
    def engine(self) -> AsyncEngine:
        """Return the live async engine.

        Useful for instrumentation that attaches to the engine
        directly — e.g.
        :class:`~tempest_fastapi_sdk.db.slow_query.SlowQueryLogger`
        or OpenTelemetry's SQLAlchemy instrumentor.

        Returns:
            AsyncEngine: The initialized engine.

        Raises:
            RuntimeError: If :meth:`connect` has not run yet.
        """
        if self._engine is None:
            raise RuntimeError(
                "AsyncDatabaseManager is not connected. "
                "Call await manager.connect() before accessing the engine."
            )
        return self._engine

    @property
    def is_connected(self) -> bool:
        """Whether the engine is currently initialized.

        Returns:
            bool: ``True`` if :meth:`connect` has been called and
            :meth:`disconnect` has not.
        """
        return self._engine is not None

    async def connect(self) -> None:
        """Create the engine and session factory if they don't exist.

        Idempotent — calling twice is a no-op.
        """
        if self._engine is not None:
            return

        kwargs: dict[str, Any] = {"echo": self._echo, **self._engine_kwargs}
        connect_args = dict(self._connect_args)

        if self.is_sqlite:
            connect_args.setdefault("check_same_thread", False)
        else:
            kwargs.setdefault("pool_pre_ping", True)
            kwargs.setdefault("pool_recycle", self._pool_recycle)
            kwargs.setdefault("pool_size", self._pool_size)
            kwargs.setdefault("max_overflow", self._max_overflow)

        if connect_args:
            kwargs["connect_args"] = connect_args
        if self._poolclass is not None:
            kwargs["poolclass"] = self._poolclass

        self._engine = create_async_engine(self._db_url, **kwargs)
        self._session_maker = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def disconnect(self) -> None:
        """Dispose the engine and clear the session factory.

        Safe to call multiple times.
        """
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_maker = None

    def _require_session_maker(self) -> async_sessionmaker[AsyncSession]:
        """Return the session maker, raising if uninitialized.

        Returns:
            async_sessionmaker[AsyncSession]: The configured factory.

        Raises:
            RuntimeError: If :meth:`connect` has not run yet.
        """
        if self._session_maker is None:
            raise RuntimeError(
                "AsyncDatabaseManager is not connected. "
                "Call await manager.connect() before using sessions."
            )
        return self._session_maker

    async def get_session(self) -> AsyncSession:
        """Return a new ``AsyncSession`` bound to the engine.

        Lazy-connects on first use. The caller is responsible for
        closing the session (use :meth:`get_session_context` for
        managed lifecycle).

        Returns:
            AsyncSession: A new session.
        """
        if self._engine is None:
            await self.connect()
        return self._require_session_maker()()

    @asynccontextmanager
    async def get_session_context(self) -> AsyncGenerator[AsyncSession]:
        """Yield a session that auto-commits on exit and rolls back on error.

        Yields:
            AsyncSession: A managed session.

        Raises:
            Exception: Re-raises whatever the caller raised inside
                the ``async with`` block, after rolling back.
        """
        if self._engine is None:
            await self.connect()
        session = self._require_session_maker()()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def session_dependency(self) -> AsyncGenerator[AsyncSession]:
        """FastAPI dependency yielding one session per request.

        Use as ``Depends(db.session_dependency)``. Differs from
        :meth:`get_session_context` in that it does **not** commit on
        success — commits are the responsibility of the service /
        repository layer. The session is closed when the request
        scope ends; failures bubble up unchanged.

        Yields:
            AsyncSession: A request-scoped session.
        """
        if self._engine is None:
            await self.connect()
        session = self._require_session_maker()()
        try:
            yield session
        finally:
            await session.close()

    async def health_check(self) -> bool:
        """Return whether a trivial ``SELECT 1`` succeeds.

        Suitable for ``/health`` endpoints. Swallows every exception
        and returns ``False`` so callers can branch on the result
        without dealing with driver-specific error types.

        Returns:
            bool: ``True`` when the database responded with ``1``,
            ``False`` on any failure.
        """
        try:
            if self._engine is None:
                await self.connect()
            async with self._require_session_maker()() as session:
                result = await session.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception:
            return False

    async def create_tables(self) -> None:
        """Issue ``CREATE TABLE`` for every model registered on ``BaseModel``.

        Intended for tests and local development. Production schemas
        should be managed by Alembic (see
        :class:`tempest_fastapi_sdk.db.migrations.AlembicHelper`).
        """
        if self._engine is None:
            await self.connect()
        if self._engine is None:
            raise RuntimeError("Engine is not connected.")
        async with self._engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)

    async def drop_tables(self) -> None:
        """Issue ``DROP TABLE`` for every model registered on ``BaseModel``.

        Intended for tests and local development.
        """
        if self._engine is None:
            await self.connect()
        if self._engine is None:
            raise RuntimeError("Engine is not connected.")
        async with self._engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.drop_all)


__all__: list[str] = [
    "AsyncDatabaseManager",
]
