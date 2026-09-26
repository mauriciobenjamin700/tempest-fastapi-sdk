"""Tests for AlembicHelper from async code (issue #323).

The SDK ``env.py`` drives migrations with ``asyncio.run``, so every sync
command that executes it fails from a running event loop — a FastAPI
lifespan. These tests pin the ``*_async`` counterparts, the refusal the
sync methods raise instead of the nested ``asyncio.run`` error, and the
template's connection-sharing path.
"""

import asyncio
import gc
import inspect
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from tempest_fastapi_sdk.db import AlembicHelper, SchemaSyncOutcome, migrations

_ENV_RUNNING: tuple[str, ...] = (
    "upgrade",
    "safe_upgrade",
    "downgrade",
    "stamp",
    "revision",
    "check",
    "adopt",
    "sync_schema",
    "squash",
)
"""Sync methods that execute ``env.py`` and must refuse a running loop."""

_ASYNC_PAIRS: tuple[str, ...] = (
    *_ENV_RUNNING,
    "current",
    "has_existing_schema",
    "pending_destructive_ops",
)
"""Every sync method that has an ``_async`` counterpart."""

_LEGACY_ENV_PY: str = """
import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
target_metadata = None


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


asyncio.run(run_async_migrations())
"""
"""The online path of every SDK ``env.py`` generated before #323.

It ends in ``asyncio.run`` and never reads ``config.attributes``, so it
stands in for the files consumers already have on disk.
"""


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    """Return a file-backed aiosqlite URL inside ``tmp_path``."""
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_url: str
) -> AlembicHelper:
    """Scaffold an SDK Alembic project with one empty revision.

    The metadata module is empty so autogenerate (``revision``, ``check``,
    ``squash``) has a target and finds nothing to diff.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "async_helper_models.py").write_text(
        "from sqlalchemy import MetaData\n\n\nclass Base:\n    metadata = MetaData()\n",
        encoding="utf-8",
    )
    instance = AlembicHelper(config_path=str(tmp_path / "alembic.ini"), db_url=db_url)
    instance.init(
        directory=str(tmp_path / "alembic"),
        metadata_module="async_helper_models",
        metadata_attr="Base",
        db_url=db_url,
    )
    instance.revision("first", autogenerate=False)
    return instance


def _call_sync(helper: AlembicHelper, name: str) -> Any:
    """Invoke the sync method ``name`` with arguments that reach ``env.py``."""
    method: Callable[..., Any] = getattr(helper, name)
    if name == "revision":
        return method("second", autogenerate=True)
    if name == "squash":
        return method(force=True)
    return method()


def _unawaited(caught: list[warnings.WarningMessage]) -> list[str]:
    """Return the "coroutine ... was never awaited" warnings in ``caught``."""
    return [str(w.message) for w in caught if "never awaited" in str(w.message)]


class TestSyncMethodsRefuseRunningLoop:
    @pytest.mark.parametrize("name", _ENV_RUNNING)
    async def test_raises_naming_async_counterpart(
        self, helper: AlembicHelper, name: str
    ) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match=rf"use `await helper\.{name}_async"):
                _call_sync(helper, name)
            gc.collect()
        assert _unawaited(caught) == []

    async def test_check_no_longer_reports_false_from_loop(
        self, helper: AlembicHelper
    ) -> None:
        """``check`` used to swallow the ``asyncio.run`` error as drift."""
        with pytest.raises(RuntimeError, match="check_async"):
            helper.check()

    async def test_current_still_works_with_sync_driver(
        self, helper: AlembicHelper
    ) -> None:
        assert helper.current() is None

    async def test_current_async_only_fallback_names_current_async(
        self, helper: AlembicHelper, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _missing_dbapi(url: str) -> object:
            raise ModuleNotFoundError("No module named 'psycopg2'")

        monkeypatch.setattr(migrations, "create_engine", _missing_dbapi)
        with pytest.raises(RuntimeError, match="current_async"):
            helper.current()

    def test_sync_methods_still_work_without_loop(self, helper: AlembicHelper) -> None:
        helper.upgrade()
        assert helper.current() == helper.heads()[0]


class TestAsyncCounterparts:
    @pytest.mark.parametrize("name", _ASYNC_PAIRS)
    def test_signature_mirrors_sync_method(self, name: str) -> None:
        sync_params = inspect.signature(getattr(AlembicHelper, name)).parameters
        async_method = getattr(AlembicHelper, f"{name}_async")
        assert inspect.iscoroutinefunction(async_method)
        assert inspect.signature(async_method).parameters == sync_params

    async def test_upgrade_current_downgrade(self, helper: AlembicHelper) -> None:
        head = helper.heads()[0]
        await helper.upgrade_async()
        assert await helper.current_async() == head
        await helper.downgrade_async("base")
        assert await helper.current_async() is None

    async def test_stamp_and_safe_upgrade(self, helper: AlembicHelper) -> None:
        head = helper.heads()[0]
        await helper.stamp_async(head)
        assert await helper.current_async() == head
        await helper.stamp_async("base", purge=True)
        assert await helper.pending_destructive_ops_async() == []
        await helper.safe_upgrade_async()
        assert await helper.current_async() == head

    async def test_revision_and_check(self, helper: AlembicHelper) -> None:
        await helper.upgrade_async()
        assert await helper.check_async() is True
        script = await helper.revision_async("second", autogenerate=False)
        assert script.revision in helper.heads()

    async def test_sync_schema_from_lifespan_shape(self, helper: AlembicHelper) -> None:
        outcome = await helper.sync_schema_async()
        assert outcome is SchemaSyncOutcome.SYNCED
        assert await helper.current_async() == helper.heads()[0]

    async def test_adopt_stamps_base_of_pre_alembic_schema(
        self, helper: AlembicHelper, db_url: str
    ) -> None:
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE legacy (id INTEGER PRIMARY KEY)"))
        await engine.dispose()
        assert await helper.has_existing_schema_async() is True
        assert await helper.adopt_async() is True
        assert await helper.current_async() == helper.base_revision()

    async def test_squash(self, helper: AlembicHelper) -> None:
        await helper.upgrade_async()
        new_root = await helper.squash_async(force=True)
        assert helper.heads() == [new_root]

    async def test_works_with_env_py_that_only_calls_asyncio_run(
        self, helper: AlembicHelper, tmp_path: Path
    ) -> None:
        """An ``env.py`` generated before this release must keep working."""
        (tmp_path / "alembic" / "env.py").write_text(_LEGACY_ENV_PY, encoding="utf-8")

        await helper.upgrade_async()
        assert await helper.current_async() == helper.heads()[0]


class TestEnvTemplate:
    async def test_old_shape_fails_inside_loop(
        self, helper: AlembicHelper, tmp_path: Path
    ) -> None:
        """Reproduce the error of issue #323 with an ``asyncio.run`` env.py."""
        (tmp_path / "alembic" / "env.py").write_text(_LEGACY_ENV_PY, encoding="utf-8")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("ignore")
            with pytest.raises(
                RuntimeError, match=r"asyncio\.run\(\) cannot be called"
            ):
                command.upgrade(helper.config, "head")
            gc.collect()

    async def test_raises_clearly_inside_loop_without_connection(
        self, helper: AlembicHelper
    ) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(
                RuntimeError, match=r"config\.attributes\[\"connection\"\]"
            ):
                command.upgrade(helper.config, "head")
            gc.collect()
        assert _unawaited(caught) == []

    async def test_runs_on_shared_connection_inside_loop(
        self, helper: AlembicHelper, db_url: str
    ) -> None:
        config = helper.config

        def _upgrade(connection: Connection) -> None:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(_upgrade)
        await engine.dispose()
        assert await helper.current_async() == helper.heads()[0]

    def test_running_loop_is_the_only_refusal(self, helper: AlembicHelper) -> None:
        """Outside a loop the template still builds its own engine."""
        command.upgrade(helper.config, "head")
        assert asyncio.run(helper.current_async()) == helper.heads()[0]
