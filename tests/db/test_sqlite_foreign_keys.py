"""Foreign-key enforcement on the SQLite engines the SDK builds (#395).

SQLite only checks ``REFERENCES`` on a connection that ran
``PRAGMA foreign_keys=ON``. Every engine path the SDK owns —
:class:`AsyncDatabaseManager` and :func:`create_test_engine` — now turns
it on by default, so these tests pin the three behaviours PostgreSQL
already had (orphan rejected, ``ON DELETE CASCADE`` applied, unordered
``add_all`` rejected) across the URL shapes each path handles
differently, plus the opt-outs.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import ForeignKey, Integer, MetaData, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tempest_fastapi_sdk.db import AsyncDatabaseManager, connection
from tempest_fastapi_sdk.settings import DatabaseSettings
from tempest_fastapi_sdk.testing import create_test_engine


class _Base(DeclarativeBase):
    """Isolated declarative base so the SDK metadata stays untouched."""

    metadata = MetaData()


class _Org(_Base):
    """Parent row."""

    __tablename__ = "fk_org"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class _Member(_Base):
    """Child row deleted with its parent."""

    __tablename__ = "fk_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("fk_org.id", ondelete="CASCADE"))


EngineFactory = Callable[
    [Path], Awaitable[tuple[AsyncEngine, Callable[[], Awaitable[None]]]]
]
"""Build an engine for ``tmp_path`` and return it with its teardown."""


async def _manager(
    url: str, **kwargs: Any
) -> tuple[AsyncEngine, Callable[[], Awaitable[None]]]:
    """Connect a manager and return its engine plus ``disconnect``."""
    manager = AsyncDatabaseManager(url, **kwargs)
    await manager.connect()
    return manager.engine, manager.disconnect


async def _test_engine(
    url: str, **kwargs: Any
) -> tuple[AsyncEngine, Callable[[], Awaitable[None]]]:
    """Build a test engine and return it plus ``dispose``."""
    engine = create_test_engine(url, **kwargs)
    return engine, engine.dispose


_ENGINES: dict[str, EngineFactory] = {
    "manager-memory": lambda _p: _manager("sqlite+aiosqlite:///:memory:"),
    "manager-file": lambda p: _manager(f"sqlite+aiosqlite:///{p / 'm.db'}"),
    "test-engine-memory": lambda _p: _test_engine("sqlite+aiosqlite:///:memory:"),
    "test-engine-file": lambda p: _test_engine(f"sqlite+aiosqlite:///{p / 't.db'}"),
}
"""Every SQLite engine shape the SDK builds, with foreign keys on by default.

``manager-memory`` is the shared-cache URL plus keepalive connection;
``test-engine-memory`` is the single ``StaticPool`` connection.
"""


@pytest.fixture(params=sorted(_ENGINES))
async def factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory over one engine shape, with the tables created."""
    engine, teardown = await _ENGINES[request.param](tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await teardown()


async def _pragma(engine: AsyncEngine) -> int:
    """Read ``PRAGMA foreign_keys`` on a fresh connection."""
    async with engine.connect() as conn:
        value = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
    assert isinstance(value, int)
    return value


class TestEnforcedByDefault:
    async def test_orphan_child_is_rejected(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as session:
            session.add(_Member(id=1, org_id=999))
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                await session.commit()

    async def test_on_delete_cascade_deletes_the_children(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as session:
            session.add(_Org(id=1))
            await session.flush()
            session.add_all([_Member(id=1, org_id=1), _Member(id=2, org_id=1)])
            await session.commit()
            await session.execute(text("DELETE FROM fk_org WHERE id = 1"))
            await session.commit()
            left = (
                await session.execute(select(func.count()).select_from(_Member))
            ).scalar_one()
        assert left == 0

    async def test_add_all_without_relationship_is_not_reordered(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """No ``relationship()`` means the unit of work inserts in add order."""
        async with factory() as session:
            session.add_all([_Member(id=1, org_id=5), _Org(id=5)])
            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
                await session.commit()

    async def test_pragma_survives_the_explicit_begin(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The pragma is set on ``connect``, before savepoints emit ``BEGIN``.

        Emitted inside the transaction it would be a silent no-op, so a
        session (which always runs inside one) reading ``1`` pins that the
        listener runs at connect time.
        """
        async with factory() as session:
            assert (await session.execute(text("PRAGMA foreign_keys"))).scalar() == 1
            assert session.in_transaction()


class TestPragmaInsideTransactionIsANoOp:
    async def test_late_pragma_does_not_switch_enforcement_on(
        self, tmp_path: Path
    ) -> None:
        """Why the listener lives on ``connect``: SQLite ignores it in a transaction."""
        engine, teardown = await _manager(
            f"sqlite+aiosqlite:///{tmp_path / 'late.db'}", sqlite_foreign_keys=False
        )
        async with engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 0
        await teardown()


class TestOptOut:
    @pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "file"])
    async def test_manager_flag_turns_it_off(self, url: str, tmp_path: Path) -> None:
        target = f"sqlite+aiosqlite:///{tmp_path / 'off.db'}" if url == "file" else url
        engine, teardown = await _manager(target, sqlite_foreign_keys=False)
        assert await _pragma(engine) == 0
        await teardown()

    @pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "file"])
    async def test_test_engine_flag_turns_it_off(
        self, url: str, tmp_path: Path
    ) -> None:
        target = f"sqlite+aiosqlite:///{tmp_path / 'off.db'}" if url == "file" else url
        engine, teardown = await _test_engine(target, foreign_keys=False)
        assert await _pragma(engine) == 0
        await teardown()

    async def test_settings_flag_reaches_the_manager(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
        monkeypatch.setenv("DATABASE_SQLITE_FOREIGN_KEYS", "false")
        settings = DatabaseSettings()
        assert settings.database_kwargs()["sqlite_foreign_keys"] is False
        manager = AsyncDatabaseManager(**settings.database_kwargs())
        await manager.connect()
        assert await _pragma(manager.engine) == 0
        await manager.disconnect()

    def test_settings_default_is_on(self) -> None:
        assert DatabaseSettings().DATABASE_SQLITE_FOREIGN_KEYS is True


class TestOtherBackendsUntouched:
    async def test_manager_skips_the_listener_off_sqlite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PostgreSQL URL builds its engine without any SQLite listener."""
        calls: list[AsyncEngine] = []
        monkeypatch.setattr(connection, "enable_sqlite_foreign_keys", calls.append)
        manager = AsyncDatabaseManager("postgresql+asyncpg://u:p@127.0.0.1:1/db")
        await manager.connect()
        await manager.disconnect()
        assert calls == []

    async def test_manager_calls_the_listener_on_sqlite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The spy above would pass vacuously if it never fired anywhere."""
        calls: list[AsyncEngine] = []
        monkeypatch.setattr(connection, "enable_sqlite_foreign_keys", calls.append)
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await manager.connect()
        await manager.disconnect()
        assert len(calls) == 1

    def test_test_engine_skips_the_listener_off_sqlite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[AsyncEngine] = []
        monkeypatch.setattr(connection, "enable_sqlite_foreign_keys", calls.append)
        create_test_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db")
        assert calls == []
        create_test_engine()
        assert len(calls) == 1


class TestTestEngineSavepoints:
    async def test_released_savepoint_does_not_commit_the_outer_transaction(
        self,
    ) -> None:
        """``create_test_engine`` now applies ``enable_sqlite_savepoints`` too.

        Without it ``RELEASE SAVEPOINT`` is the outermost commit on
        SQLite, and the row below survives the outer rollback.
        """
        engine = create_test_engine()
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin_nested():
                session.add(_Org(id=7))
            await session.rollback()
        async with factory() as session:
            count = (
                await session.execute(select(func.count()).select_from(_Org))
            ).scalar_one()
        await engine.dispose()
        assert count == 0
