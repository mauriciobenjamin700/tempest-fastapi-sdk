"""Tests for the quiet, bind-aware revision read of AlembicHelper (issue #805).

``current()`` used to build an Alembic ``MigrationContext`` per call, whose
``configure`` logs ``Context impl ...`` and ``Will assume ... DDL.`` at INFO,
and to create and dispose of an engine per call. Backing a readiness probe,
that was two log lines and a fresh connection handshake every few seconds.
These tests pin the quiet read, that real migrations keep logging, and that
a caller-owned engine or connection is used without creating or disposing
of another.
"""

import logging
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy.ext.asyncio
from alembic.runtime.migration import MigrationContext
from alembic.util import CommandError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tempest_fastapi_sdk.db import AlembicHelper, AsyncDatabaseManager, migrations

_MIGRATION_LOGGER: str = "alembic.runtime.migration"
"""Logger Alembic's ``MigrationContext`` announces itself and each step on."""

_ANNOUNCEMENTS: tuple[str, ...] = ("Context impl ", "Will assume ")
"""Prefixes of the two lines ``MigrationContext.configure`` logs per call."""


def _announcements(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the captured context-announcement messages.

    Args:
        caplog (pytest.LogCaptureFixture): The log capture.

    Returns:
        list[str]: Every message on the migration logger starting with one
        of :data:`_ANNOUNCEMENTS`.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _MIGRATION_LOGGER
        and record.getMessage().startswith(_ANNOUNCEMENTS)
    ]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return the SQLite file the project migrates."""
    return tmp_path / "current_read.db"


@pytest.fixture
def helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> AlembicHelper:
    """Scaffold an SDK Alembic project with two empty revisions.

    Two revisions give ``upgrade`` two ``Running upgrade`` lines and a
    longer window for the concurrent reader to overlap with.
    """
    monkeypatch.chdir(tmp_path)
    url = f"sqlite+aiosqlite:///{db_path}"
    instance = AlembicHelper(config_path=str(tmp_path / "alembic.ini"), db_url=url)
    instance.init(directory=str(tmp_path / "alembic"), db_url=url)
    instance.revision("first", autogenerate=False)
    instance.revision("second", autogenerate=False)
    return instance


@pytest.fixture
def sync_engine(db_path: Path) -> Iterator[Engine]:
    """Yield a caller-owned sync engine on the project database."""
    engine = create_engine(f"sqlite:///{db_path}")
    yield engine
    engine.dispose()


@pytest.fixture
async def async_engine(db_path: Path) -> AsyncIterator[AsyncEngine]:
    """Yield a caller-owned async engine on the project database."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield engine
    await engine.dispose()


def _forbid_engine_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every engine factory the helper could reach raise when called.

    Args:
        monkeypatch (pytest.MonkeyPatch): The test's monkeypatch.
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an engine was created despite bind=")

    monkeypatch.setattr(migrations, "create_engine", _refuse)
    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", _refuse)


class TestQuietRead:
    def test_current_does_not_announce_a_migration_context(
        self, helper: AlembicHelper, caplog: pytest.LogCaptureFixture
    ) -> None:
        helper.upgrade()
        caplog.set_level(logging.INFO, logger=_MIGRATION_LOGGER)
        caplog.clear()

        assert helper.current() == helper.heads()[0]

        assert _announcements(caplog) == []

    async def test_current_async_does_not_announce(
        self,
        helper: AlembicHelper,
        async_engine: AsyncEngine,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await helper.upgrade_async()
        caplog.set_level(logging.INFO, logger=_MIGRATION_LOGGER)
        caplog.clear()

        assert await helper.current_async() == helper.heads()[0]
        assert await helper.current_async(bind=async_engine) == helper.heads()[0]

        assert _announcements(caplog) == []

    def test_the_old_read_did_announce(
        self,
        helper: AlembicHelper,
        sync_engine: Engine,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Prove the capture sees the two lines the previous read emitted.

        Without this, the quiet-read assertions would also pass against a
        capture that is not wired to the migration logger at all.
        """
        caplog.set_level(logging.INFO, logger=_MIGRATION_LOGGER)
        with sync_engine.connect() as connection:
            MigrationContext.configure(connection).get_current_revision()

        assert len(_announcements(caplog)) == 2

    def test_upgrade_keeps_logging_during_concurrent_reads(
        self, helper: AlembicHelper, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=_MIGRATION_LOGGER)
        stop = threading.Event()
        reads: list[str | None] = []
        errors: list[BaseException] = []

        def _poll() -> None:
            while not stop.is_set():
                try:
                    reads.append(helper.current())
                except BaseException as exc:
                    errors.append(exc)
                    return

        reader = threading.Thread(target=_poll)
        reader.start()
        try:
            helper.upgrade()
        finally:
            stop.set()
            reader.join()

        assert errors == []
        assert reads
        running = [
            record.getMessage()
            for record in caplog.records
            if record.name == _MIGRATION_LOGGER
            and record.getMessage().startswith("Running upgrade")
        ]
        assert len(running) == 2
        assert len(_announcements(caplog)) == 2


class TestReadContract:
    def test_version_table_matches_alembic_default(self, sync_engine: Engine) -> None:
        with sync_engine.connect() as connection:
            context = MigrationContext.configure(connection)

        assert context.version_table == migrations._VERSION_TABLE
        assert context.version_table_schema is None

    def test_missing_version_table_is_none(self, helper: AlembicHelper) -> None:
        assert helper.current() is None

    def test_empty_version_table_is_none(self, helper: AlembicHelper) -> None:
        helper.upgrade()
        helper.downgrade("base")
        assert helper.current() is None

    def test_several_rows_raise_like_alembic(
        self, helper: AlembicHelper, sync_engine: Engine
    ) -> None:
        helper.upgrade()
        with sync_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('other')")
            )
        with (
            sync_engine.connect() as connection,
            pytest.raises(CommandError) as alembic_error,
        ):
            MigrationContext.configure(connection).get_current_revision()

        with pytest.raises(CommandError) as sdk_error:
            helper.current()

        assert str(sdk_error.value) == str(alembic_error.value)


class TestBind:
    def test_current_on_sync_engine_and_connection(
        self,
        helper: AlembicHelper,
        sync_engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        helper.upgrade()
        head = helper.heads()[0]
        _forbid_engine_creation(monkeypatch)
        pool = sync_engine.pool

        assert helper.current(bind=sync_engine) == head
        with sync_engine.connect() as connection:
            assert helper.current(bind=connection) == head
            assert helper.has_existing_schema(bind=connection) is False
        assert helper.has_existing_schema(bind=sync_engine) is False
        assert sync_engine.pool is pool

    async def test_current_async_on_engine_creates_and_disposes_nothing(
        self,
        helper: AlembicHelper,
        async_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await helper.upgrade_async()
        head = helper.heads()[0]
        _forbid_engine_creation(monkeypatch)
        pool = async_engine.sync_engine.pool

        assert await helper.current_async(bind=async_engine) == head
        assert await helper.has_existing_schema_async(bind=async_engine) is False
        async with async_engine.connect() as connection:
            assert await helper.current_async(bind=connection) == head
            assert await helper.has_existing_schema_async(bind=connection) is False

        assert async_engine.sync_engine.pool is pool
        async with async_engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar() == 1

    async def test_current_async_with_bind_stays_on_the_loop(
        self,
        helper: AlembicHelper,
        async_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("bind= read went through a worker thread")

        monkeypatch.setattr(migrations.asyncio, "to_thread", _refuse)

        assert await helper.current_async(bind=async_engine) is None

    async def test_bind_skips_the_configured_url(
        self, async_engine: AsyncEngine, tmp_path: Path
    ) -> None:
        unconfigured = AlembicHelper(config_path=str(tmp_path / "missing.ini"))

        assert await unconfigured.current_async(bind=async_engine) is None


class TestSchemaAtHeadCheck:
    async def test_tracks_the_stamp_over_the_app_pool(
        self,
        helper: AlembicHelper,
        db_path: Path,
    ) -> None:
        db = AsyncDatabaseManager(f"sqlite+aiosqlite:///{db_path}")
        check = helper.schema_at_head_check(db)
        await db.connect()
        try:
            outcomes: list[bool] = []
            for target in (None, "+1", "head"):
                if target is not None:
                    await helper.upgrade_async(target)
                with pytest.MonkeyPatch.context() as guarded:
                    _forbid_engine_creation(guarded)
                    outcomes.append(await check())
        finally:
            await db.disconnect()

        assert outcomes == [False, False, True]

    async def test_empty_tree_is_not_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        url = f"sqlite+aiosqlite:///{db_path}"
        bare = AlembicHelper(config_path=str(tmp_path / "alembic.ini"), db_url=url)
        bare.init(directory=str(tmp_path / "alembic"), db_url=url)
        db = AsyncDatabaseManager(url)
        await db.connect()
        try:
            assert await bare.schema_at_head_check(db)() is False
        finally:
            await db.disconnect()
