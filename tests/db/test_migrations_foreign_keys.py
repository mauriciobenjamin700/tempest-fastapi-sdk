"""Migrations stay off SQLite foreign keys (#395).

Alembic batch mode rebuilds a SQLite table by copying it and dropping the
original. With ``PRAGMA foreign_keys=ON`` the ``DROP TABLE`` runs the
foreign-key actions of the dropped parent, so ``ON DELETE CASCADE``
silently deletes every child. The engines the generated ``env.py`` and
:class:`AlembicHelper` build keep enforcement off; a connection handed in
through ``config.attributes["connection"]`` is checked and refused.
"""

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection

from tempest_fastapi_sdk.db import (
    AlembicHelper,
    AsyncDatabaseManager,
    require_sqlite_foreign_keys_off,
)

_PROBE_MODULE: str = "fk_migration_probe"
"""Module the revision appends the pragma it saw to, in this process."""

_REVISION: str = '''"""Rebuild the parent table through batch mode."""

import sqlalchemy as sa
from alembic import op

import fk_migration_probe

revision = "fk0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    fk_migration_probe.seen.append(
        bind.exec_driver_sql("PRAGMA foreign_keys").scalar()
    )
    with op.batch_alter_table("org", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("extra", sa.String(), nullable=True))


def downgrade() -> None:
    pass
'''
"""A revision that recreates the parent table and records the pragma."""


@pytest.fixture
def probe() -> Iterator[list[int]]:
    """Register the probe module the revision imports, and return its list."""
    module = ModuleType(_PROBE_MODULE)
    seen: list[int] = []
    module.seen = seen  # type: ignore[attr-defined]
    sys.modules[_PROBE_MODULE] = module
    yield seen
    del sys.modules[_PROBE_MODULE]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create the parent/child schema with three children on disk."""
    path = tmp_path / "fk.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE org (id INTEGER PRIMARY KEY, name VARCHAR)")
        conn.execute(
            "CREATE TABLE member (id INTEGER PRIMARY KEY, "
            "org_id INTEGER REFERENCES org(id) ON DELETE CASCADE)"
        )
        conn.execute("INSERT INTO org VALUES (1, 'a'), (2, 'b')")
        conn.execute("INSERT INTO member VALUES (1, 1), (2, 1), (3, 2)")
    conn.close()
    return path


@pytest.fixture
def helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> AlembicHelper:
    """Scaffold an SDK Alembic project whose only revision is ``_REVISION``."""
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "fk_models.py").write_text(
        "from sqlalchemy import MetaData\n\n\nclass Base:\n    metadata = MetaData()\n",
        encoding="utf-8",
    )
    instance = AlembicHelper(config_path=str(tmp_path / "alembic.ini"), db_url=url)
    instance.init(
        directory=str(tmp_path / "alembic"),
        metadata_module="fk_models",
        metadata_attr="Base",
        db_url=url,
    )
    (tmp_path / "alembic" / "versions" / "fk0001_rebuild.py").write_text(
        _REVISION, encoding="utf-8"
    )
    return instance


def _members(db_path: Path) -> int:
    """Count child rows straight from the file."""
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT count(*) FROM member").fetchone()[0]
    conn.close()
    assert isinstance(count, int)
    return count


class TestHazard:
    def test_batch_rebuild_of_parent_deletes_children_with_fk_on(
        self, db_path: Path
    ) -> None:
        """Pins what the refusal prevents: 3 children become 0, no error."""
        engine = sa.create_engine(f"sqlite:///{db_path}")

        @sa.event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection: sqlite3.Connection, _record: object) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        with engine.begin() as conn:
            operations = Operations(MigrationContext.configure(conn))
            with operations.batch_alter_table("org", recreate="always") as batch_op:
                batch_op.add_column(sa.Column("extra", sa.String(), nullable=True))
        engine.dispose()
        assert _members(db_path) == 0


class TestOwnEnginesKeepForeignKeysOff:
    def test_cli_path_env_py_engine(
        self, helper: AlembicHelper, probe: list[int], db_path: Path
    ) -> None:
        helper.upgrade("head")
        assert probe == [0]
        assert _members(db_path) == 3

    async def test_upgrade_async(
        self, helper: AlembicHelper, probe: list[int], db_path: Path
    ) -> None:
        await helper.upgrade_async("head")
        assert probe == [0]
        assert _members(db_path) == 3

    def test_helper_sync_read_engine(self, helper: AlembicHelper) -> None:
        pragma = helper._read(
            lambda conn: conn.exec_driver_sql("PRAGMA foreign_keys").scalar(),
            method="current",
        )
        assert pragma == 0

    def test_helper_async_read_engine(self, helper: AlembicHelper) -> None:
        url = helper.config.get_main_option("sqlalchemy.url")
        assert url is not None
        pragma = helper._read_via_async(
            url,
            lambda conn: conn.exec_driver_sql("PRAGMA foreign_keys").scalar(),
            method="current",
        )
        assert pragma == 0


class TestSharedConnection:
    async def test_manager_connection_is_refused_before_migrating(
        self, helper: AlembicHelper, probe: list[int], db_path: Path
    ) -> None:
        """The manager enforces FK by default, so its connection is refused."""
        config = helper.config

        def _upgrade(connection: Connection) -> None:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{db_path}")
        await manager.connect()
        try:
            with pytest.raises(RuntimeError, match=r"PRAGMA foreign_keys=ON"):
                async with manager.engine.begin() as conn:
                    await conn.run_sync(_upgrade)
        finally:
            await manager.disconnect()
        assert probe == []
        assert _members(db_path) == 3

    async def test_connection_without_fk_migrates(
        self, helper: AlembicHelper, probe: list[int], db_path: Path
    ) -> None:
        config = helper.config

        def _upgrade(connection: Connection) -> None:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        manager = AsyncDatabaseManager(
            f"sqlite+aiosqlite:///{db_path}", sqlite_foreign_keys=False
        )
        await manager.connect()
        try:
            async with manager.engine.begin() as conn:
                await conn.run_sync(_upgrade)
        finally:
            await manager.disconnect()
        assert probe == [0]
        assert _members(db_path) == 3


class TestRequireSqliteForeignKeysOff:
    def test_other_backend_is_not_inspected(self) -> None:
        connection = MagicMock()
        connection.dialect.name = "postgresql"
        require_sqlite_foreign_keys_off(connection)
        connection.connection.cursor.assert_not_called()

    def test_check_does_not_open_a_transaction(self, db_path: Path) -> None:
        """Alembic reads ``in_transaction()`` to decide who commits."""
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            require_sqlite_foreign_keys_off(conn)
            assert not conn.in_transaction()
        engine.dispose()
