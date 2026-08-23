"""Async database manager with engine/session lifecycle helpers."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, Pool

from tempest_fastapi_sdk.db.model import BaseModel

_MEMORY_URL_MARKERS: tuple[str, ...] = (":memory:", "mode=memory")
"""Substrings that make a SQLite URL an in-memory database."""


def is_memory_sqlite_url(db_url: str) -> bool:
    """Whether a URL points at an in-memory SQLite database.

    Args:
        db_url (str): The connection URL.

    Returns:
        bool: ``True`` for ``sqlite+aiosqlite:///:memory:`` and for the
        ``mode=memory`` URI form, ``False`` for a file or another backend.
    """
    if make_url(db_url).get_backend_name() != "sqlite":
        return False
    return any(marker in db_url for marker in _MEMORY_URL_MARKERS)


def shared_memory_url(name: str) -> str:
    """Build a shared-cache in-memory SQLite URL.

    Args:
        name (str): Database name, unique per manager so two managers do
            not end up talking to the same in-memory database.

    Returns:
        str: A URL every connection of one engine resolves to the same
        in-memory database, instead of the private one each connection
        gets with plain ``:memory:``.
    """
    return f"sqlite+aiosqlite:///file:{name}?mode=memory&cache=shared&uri=true"


def enable_sqlite_savepoints(engine: AsyncEngine) -> None:
    """Make ``SAVEPOINT`` behave on SQLite the way it does on PostgreSQL.

    The ``pysqlite`` driver that ``aiosqlite`` builds on opens
    transactions implicitly and, by default, never emits a ``BEGIN``.
    SQLite therefore sees the ``SAVEPOINT`` as the outermost transaction,
    and the matching ``RELEASE SAVEPOINT`` **commits** it. The damage is
    invisible on the failure path — a rollback to the savepoint still
    works — and only shows up when a nested block exits *cleanly* and
    the surrounding transaction is later rolled back: the supposedly
    pending rows are already durable.

    That is the difference between the SDK's production backend and its
    test backend silently disagreeing about atomicity, so the manager
    applies SQLAlchemy's documented remedy to every SQLite engine it
    builds: turn off the driver's implicit transaction handling, then
    emit ``BEGIN`` explicitly when SQLAlchemy starts a transaction.

    Idempotent per engine — re-registering the same handlers is
    harmless, and the listeners are attached to ``engine.sync_engine``
    because the driver-level events fire there, not on the async facade.

    Args:
        engine (AsyncEngine): The SQLite engine to configure. Passing a
            non-SQLite engine would break its transaction handling, so
            callers building their own engine must gate on the backend.

    Notes:
        ``tests/db/test_transaction.py`` pins the RELEASE path, which is
        the one that regressed silently before this existed.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _disable_implicit_begin(dbapi_connection: Any, _record: Any) -> None:
        """Stop the driver from managing transactions on its own."""
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _emit_explicit_begin(connection: Connection) -> None:
        """Open a real transaction so SAVEPOINT nests inside one."""
        connection.exec_driver_sql("BEGIN")


def enable_sqlite_wal(engine: AsyncEngine) -> None:
    """Put every connection of a SQLite engine in WAL mode.

    In the default rollback journal a reader and a writer exclude each
    other, so the moment an application grows a worker — web process and
    ``taskiq worker`` on one ``app.db`` — the second one fails. Measured
    across two processes, one holding a read transaction open while the
    other inserts:

    ==================  ==========================================
    ``journal_mode``    Result for the writer
    ==================  ==========================================
    ``delete``          waits the whole ``busy_timeout``, then
                        ``sqlite3.OperationalError: database is
                        locked``
    ``wal``             commits immediately
    ==================  ==========================================

    WAL is a property of the **database file**, not of the connection:
    setting it once is enough, it survives the process, and every later
    connection from any process opens the file already in WAL. Emitting
    the pragma per connection is therefore idempotent, and it is done
    per connection only so the very first connection of a brand-new file
    switches it too. On an in-memory database the pragma is a no-op —
    SQLite answers ``memory`` and keeps its own journal, without error.

    Args:
        engine (AsyncEngine): The SQLite engine to configure. Emitting
            this pragma against another backend would fail, so callers
            building their own engine must gate on the backend.

    Notes:
        WAL admits one writer at a time; the others queue (see
        ``sqlite_busy_timeout``). What no amount of waiting fixes is a
        transaction that **reads first and writes later** — promoting
        the lock fails at once when another connection wrote in
        between, and ``busy_timeout`` does not apply because there is
        nothing to wait for. Claim the row, do the long work with no
        session open, then persist.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_wal(dbapi_connection: Any, _record: Any) -> None:
        """Switch the file this connection just opened into WAL."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


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

    SQLite engines are additionally put in WAL mode with a 30-second
    busy timeout, which is what makes "web process plus worker on the
    same file" work at all — see :func:`enable_sqlite_wal` for the
    measurement and for the one contention WAL does **not** fix.

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
        sqlite_wal: bool = True,
        sqlite_busy_timeout: float = 30.0,
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
            sqlite_wal (bool): Whether SQLite engines are put in WAL
                mode (see :func:`enable_sqlite_wal`). Ignored on every
                other backend. Turn it off only for a file on a
                filesystem without working shared memory — WAL needs
                ``mmap``, so some network mounts reject it.
            sqlite_busy_timeout (float): Seconds a SQLite connection
                waits for a lock held by another connection before
                giving up with ``database is locked``. Forwarded to the
                driver as ``connect_args["timeout"]``; the driver's own
                default is 5 seconds, which is short for a worker that
                writes in bursts. Ignored on every other backend, and
                ignored here when ``connect_args`` already carries a
                ``timeout``.
            **engine_kwargs: Any additional keyword arguments are
                passed through to ``create_async_engine`` verbatim.
        """
        self._db_url: str = db_url
        self.is_sqlite: bool = make_url(db_url).get_backend_name() == "sqlite"
        self.is_memory_sqlite: bool = is_memory_sqlite_url(db_url)
        self._memory_keepalive: AsyncConnection | None = None
        self._sqlite_wal: bool = sqlite_wal
        self._sqlite_busy_timeout: float = sqlite_busy_timeout
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
            connect_args.setdefault("timeout", self._sqlite_busy_timeout)
        else:
            kwargs.setdefault("pool_pre_ping", True)
            kwargs.setdefault("pool_recycle", self._pool_recycle)
            kwargs.setdefault("pool_size", self._pool_size)
            kwargs.setdefault("max_overflow", self._max_overflow)

        if connect_args:
            kwargs["connect_args"] = connect_args
        if self._poolclass is not None:
            kwargs["poolclass"] = self._poolclass

        url = self._db_url
        share_memory = self.is_memory_sqlite and self._poolclass is None
        if share_memory:
            url = shared_memory_url(f"tempest_mem_{uuid4().hex}")
            kwargs["poolclass"] = AsyncAdaptedQueuePool

        self._engine = create_async_engine(url, **kwargs)
        if self.is_sqlite:
            enable_sqlite_savepoints(self._engine)
            if self._sqlite_wal:
                enable_sqlite_wal(self._engine)
        if share_memory:
            self._memory_keepalive = await self._engine.connect()
        self._session_maker = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def disconnect(self) -> None:
        """Dispose the engine and clear the session factory.

        Safe to call multiple times.
        """
        if self._memory_keepalive is not None:
            await self._memory_keepalive.close()
            self._memory_keepalive = None
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

        Raises:
            RuntimeError: When the engine is not connected.
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

        Raises:
            RuntimeError: When the engine is not connected.
        """
        if self._engine is None:
            await self.connect()
        if self._engine is None:
            raise RuntimeError("Engine is not connected.")
        async with self._engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.drop_all)


__all__: list[str] = [
    "AsyncDatabaseManager",
    "enable_sqlite_savepoints",
    "enable_sqlite_wal",
    "is_memory_sqlite_url",
    "shared_memory_url",
]
