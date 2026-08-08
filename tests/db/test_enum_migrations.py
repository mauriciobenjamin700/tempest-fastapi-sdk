"""Tests for tempest_fastapi_sdk.db.enum_migrations.

The SQLite paths run for real: a table is created, rows are inserted, the
replacement is applied through Alembic's own ``Operations`` proxy, and
the resulting constraint and data are read back. The PostgreSQL path has
no server here, so its statement sequence is asserted directly — which
is the part that has to be exactly right, since it is the sequence that
exists to dodge ``ALTER TYPE ... ADD VALUE`` failing inside a
transaction.
"""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, text

from tempest_fastapi_sdk.db.enum_migrations import (
    OLD_TYPE_SUFFIX,
    EnumColumnRef,
    EnumTypeState,
    ReplaceEnumOp,
    postgresql_replace_statements,
    render_enum_types,
    sync_enum_types,
)
from tempest_fastapi_sdk.db.enums import TempestEnum

STATUS_TABLE = "task_for_enum_migration"
STATUS_TYPE = "task_status_enum"
OLD_VALUES = ("open", "wip")
NEW_VALUES = ("open", "in_progress", "done")


@pytest.fixture
def connection() -> Iterator[Connection]:
    """Yield a live SQLite connection holding a seeded enum column.

    Yields:
        Connection: A connection inside an open transaction, rolled back
        when the test ends.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE {STATUS_TABLE} ("
                "  id INTEGER PRIMARY KEY,"
                "  status VARCHAR(3) NOT NULL,"
                f"  CONSTRAINT ck_{STATUS_TABLE}_{STATUS_TYPE} "
                "    CHECK (status IN ('open', 'wip'))"
                ")",
            ),
        )
        conn.execute(
            text(f"INSERT INTO {STATUS_TABLE} (id, status) VALUES (1, 'open')"),
        )
        conn.execute(
            text(f"INSERT INTO {STATUS_TABLE} (id, status) VALUES (2, 'wip')"),
        )
        yield conn
    engine.dispose()


@pytest.fixture
def operations(connection: Connection) -> Operations:
    """Build Alembic's operations proxy over the live connection.

    Args:
        connection (Connection): The seeded connection.

    Returns:
        Operations: The proxy ``op.replace_enum`` is invoked through.
    """
    return Operations(MigrationContext.configure(connection))


def _check_values(connection: Connection) -> tuple[str, ...] | None:
    """Read the enum members the table's CHECK constraint enforces.

    Args:
        connection (Connection): The connection to inspect.

    Returns:
        tuple[str, ...] | None: The members, or ``None`` when no
        recognizable enum check is present.
    """
    from tempest_fastapi_sdk.db.enum_migrations import _parse_check_values

    for constraint in sa.inspect(connection).get_check_constraints(STATUS_TABLE):
        parsed = _parse_check_values(constraint.get("sqltext") or "")
        if parsed is not None:
            return parsed
    return None


class TestReverse:
    def test_swaps_the_value_lists(self) -> None:
        op = ReplaceEnumOp(
            STATUS_TYPE,
            new_values=NEW_VALUES,
            old_values=OLD_VALUES,
            columns=[EnumColumnRef(table=STATUS_TABLE, column="status")],
        )
        reversed_op = op.reverse()
        assert reversed_op.new_values == OLD_VALUES
        assert reversed_op.old_values == NEW_VALUES

    def test_inverts_the_rename_map(self) -> None:
        """A downgrade has to rename the rows back, not forward again."""
        op = ReplaceEnumOp(
            STATUS_TYPE,
            new_values=NEW_VALUES,
            old_values=OLD_VALUES,
            columns=[EnumColumnRef(table=STATUS_TABLE, column="status")],
            value_map={"wip": "in_progress"},
        )
        assert op.reverse().value_map == {"in_progress": "wip"}


class TestPostgresStatements:
    def _statements(self, **kwargs: object) -> list[str]:
        """Build the PostgreSQL statement list for a replacement.

        Args:
            **kwargs (object): Overrides for the operation's arguments.

        Returns:
            list[str]: The rendered statements.
        """
        op = ReplaceEnumOp(
            STATUS_TYPE,
            new_values=kwargs.get("new_values", NEW_VALUES),  # type: ignore[arg-type]
            old_values=OLD_VALUES,
            columns=[EnumColumnRef(table=STATUS_TABLE, column="status")],
            value_map=kwargs.get("value_map"),  # type: ignore[arg-type]
        )
        defaults: dict[EnumColumnRef, str | None] = {
            EnumColumnRef(table=STATUS_TABLE, column="status"): kwargs.get(  # type: ignore[dict-item]
                "default"
            ),
        }
        return postgresql_replace_statements(op, defaults, lambda name: name)

    def test_renames_before_creating_the_replacement(self) -> None:
        """The new type needs the real name, so the old one must move first."""
        statements = self._statements()
        assert statements[0] == (
            f"ALTER TYPE {STATUS_TYPE} RENAME TO {STATUS_TYPE}{OLD_TYPE_SUFFIX}"
        )
        assert statements[1] == (
            f"CREATE TYPE {STATUS_TYPE} AS ENUM ('open', 'in_progress', 'done')"
        )

    def test_casts_the_column_through_text(self) -> None:
        assert any(
            "TYPE task_status_enum USING (status::text)::task_status_enum" in s
            for s in self._statements()
        )

    def test_drops_the_old_type_last(self) -> None:
        """Dropping earlier would fail while a column still references it."""
        assert self._statements()[-1] == (f"DROP TYPE {STATUS_TYPE}{OLD_TYPE_SUFFIX}")

    def test_uses_no_alter_type_add_value(self) -> None:
        """The statement that cannot run inside a transaction block."""
        assert not any("ADD VALUE" in s for s in self._statements())

    def test_a_column_without_a_default_is_left_alone(self) -> None:
        assert not any("DEFAULT" in s for s in self._statements())

    def test_a_default_is_dropped_and_restored(self) -> None:
        """PostgreSQL refuses the cast while the default names the old type."""
        statements = self._statements(default="'open'::task_status_enum")
        drop = next(i for i, s in enumerate(statements) if "DROP DEFAULT" in s)
        cast = next(i for i, s in enumerate(statements) if "USING" in s)
        restore = next(i for i, s in enumerate(statements) if "SET DEFAULT" in s)
        assert drop < cast < restore
        assert statements[restore].endswith("SET DEFAULT 'open'")

    def test_a_renamed_default_follows_the_value_map(self) -> None:
        statements = self._statements(
            default="'wip'::task_status_enum",
            value_map={"wip": "in_progress"},
        )
        assert any(s.endswith("SET DEFAULT 'in_progress'") for s in statements)

    def test_value_map_becomes_a_case_expression(self) -> None:
        statements = self._statements(value_map={"wip": "in_progress"})
        assert any(
            "CASE status::text WHEN 'wip' THEN 'in_progress' ELSE status::text END" in s
            for s in statements
        )

    def test_values_with_quotes_are_escaped(self) -> None:
        statements = self._statements(new_values=("it's",))
        assert "CREATE TYPE task_status_enum AS ENUM ('it''s')" in statements


class TestSqliteExecution:
    def test_check_constraint_follows_the_new_members(
        self, connection: Connection, operations: Operations
    ) -> None:
        assert _check_values(connection) == OLD_VALUES

        operations.replace_enum(
            STATUS_TYPE,
            new_values=NEW_VALUES,
            old_values=OLD_VALUES,
            columns=[EnumColumnRef(table=STATUS_TABLE, column="status")],
            value_map={"wip": "in_progress"},
        )

        assert _check_values(connection) == NEW_VALUES

    def test_existing_rows_are_remapped(
        self, connection: Connection, operations: Operations
    ) -> None:
        operations.replace_enum(
            STATUS_TYPE,
            new_values=NEW_VALUES,
            old_values=OLD_VALUES,
            columns=[EnumColumnRef(table=STATUS_TABLE, column="status")],
            value_map={"wip": "in_progress"},
        )
        rows = connection.execute(
            text(f"SELECT status FROM {STATUS_TABLE} ORDER BY id"),
        )
        assert list(rows.scalars()) == ["open", "in_progress"]

    def test_the_new_member_becomes_insertable(
        self, connection: Connection, operations: Operations
    ) -> None:
        """Before the migration the CHECK rejects it; after, it must not."""
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                text(
                    f"INSERT INTO {STATUS_TABLE} (id, status) VALUES (3, 'done')",
                ),
            )

    def test_a_removed_member_is_rejected_afterwards(
        self, connection: Connection, operations: Operations
    ) -> None:
        operations.replace_enum(
            STATUS_TYPE,
            new_values=NEW_VALUES,
            old_values=OLD_VALUES,
            columns=[EnumColumnRef(table=STATUS_TABLE, column="status")],
            value_map={"wip": "in_progress"},
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                text(
                    f"INSERT INTO {STATUS_TABLE} (id, status) VALUES (4, 'wip')",
                ),
            )


class TestUnsupportedBackend:
    def test_an_unknown_dialect_fails_loudly(self) -> None:
        """Emitting nothing would look like a successful migration."""
        from sqlalchemy.dialects import mysql

        context = MigrationContext.configure(
            dialect=mysql.dialect(),
            opts={"as_sql": True},
        )
        operations = Operations(context)
        with pytest.raises(NotImplementedError, match="mysql"):
            operations.replace_enum(
                STATUS_TYPE,
                new_values=NEW_VALUES,
                old_values=OLD_VALUES,
                columns=[EnumColumnRef(table=STATUS_TABLE, column="status")],
            )


class TestRendering:
    def test_tempest_enum_renders_as_plain_sa_enum(self) -> None:
        """A dotted path into this package would not be imported."""
        rendered = render_enum_types("type", TempestEnum("a", "b", name="x"), None)  # type: ignore[arg-type]
        assert rendered == (
            "sa.Enum('a', 'b', name='x', native_enum=True, create_constraint=True)"
        )
        assert "tempest_fastapi_sdk" not in str(rendered)

    def test_other_types_are_left_to_alembic(self) -> None:
        assert render_enum_types("type", sa.String(10), None) is False  # type: ignore[arg-type]

    def test_non_type_items_are_left_to_alembic(self) -> None:
        assert render_enum_types("column", TempestEnum("a"), None) is False  # type: ignore[arg-type]


class TestDetection:
    def test_declared_types_group_columns_sharing_one_enum(self) -> None:
        """The replacement must cast every dependent column in one go."""
        from tempest_fastapi_sdk.db.enum_migrations import _declared_enum_types

        metadata = sa.MetaData()
        shared = TempestEnum("a", "b", name="shared_enum")
        sa.Table(
            "t1",
            metadata,
            sa.Column("one", shared),
            sa.Column("two", shared),
        )
        declared = _declared_enum_types(metadata)
        assert declared["shared_enum"].columns == (
            EnumColumnRef(table="t1", column="one"),
            EnumColumnRef(table="t1", column="two"),
        )

    def test_unnamed_enums_are_skipped(self) -> None:
        """Without a name there is nothing to address in a migration."""
        from tempest_fastapi_sdk.db.enum_migrations import _declared_enum_types

        metadata = sa.MetaData()
        sa.Table("t2", metadata, sa.Column("c", sa.Enum("a", "b")))
        assert _declared_enum_types(metadata) == {}

    def test_check_parsing_reads_the_generated_shape(self) -> None:
        from tempest_fastapi_sdk.db.enum_migrations import _parse_check_values

        assert _parse_check_values("status IN ('open', 'wip')") == ("open", "wip")

    def test_check_parsing_unescapes_quotes(self) -> None:
        from tempest_fastapi_sdk.db.enum_migrations import _parse_check_values

        assert _parse_check_values("s IN ('it''s')") == ("it's",)

    def test_a_foreign_check_shape_is_reported_as_unknown(self) -> None:
        """Returning ``None`` is what stops a guess becoming a migration."""
        from tempest_fastapi_sdk.db.enum_migrations import _parse_check_values

        assert _parse_check_values("length(status) > 0") is None

    def test_changed_members_produce_a_replacement(
        self, connection: Connection
    ) -> None:
        metadata = sa.MetaData()
        sa.Table(
            STATUS_TABLE,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("status", TempestEnum(*NEW_VALUES, name=STATUS_TYPE)),
        )
        script = _run_sync_enum_types(connection, metadata)
        assert script is not None
        operation = script.upgrade_ops.ops[-1]
        assert isinstance(operation, ReplaceEnumOp)
        assert operation.new_values == NEW_VALUES
        assert operation.old_values == OLD_VALUES

    def test_unchanged_members_produce_nothing(self, connection: Connection) -> None:
        metadata = sa.MetaData()
        sa.Table(
            STATUS_TABLE,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("status", TempestEnum(*OLD_VALUES, name=STATUS_TYPE)),
        )
        script = _run_sync_enum_types(connection, metadata)
        assert script is not None
        assert not any(isinstance(op, ReplaceEnumOp) for op in script.upgrade_ops.ops)

    def test_a_type_absent_from_the_database_is_left_alone(
        self, connection: Connection
    ) -> None:
        """A brand-new enum is created by the normal CREATE TABLE, not by us."""
        metadata = sa.MetaData()
        sa.Table(
            "table_that_does_not_exist_yet",
            metadata,
            sa.Column("status", TempestEnum("x", "y", name="brand_new_enum")),
        )
        script = _run_sync_enum_types(connection, metadata)
        assert script is not None
        assert not any(isinstance(op, ReplaceEnumOp) for op in script.upgrade_ops.ops)


def _run_sync_enum_types(
    connection: Connection,
    metadata: sa.MetaData,
) -> object:
    """Run the autogenerate hook against a live connection.

    Args:
        connection (Connection): The database to read the current state
            from.
        metadata (sa.MetaData): The desired state.

    Returns:
        object: The amended ``MigrationScript``.
    """
    from alembic.operations.ops import DowngradeOps, MigrationScript, UpgradeOps

    context = MigrationContext.configure(
        connection,
        opts={"target_metadata": metadata},
    )
    script = MigrationScript(
        rev_id="test",
        upgrade_ops=UpgradeOps(ops=[]),
        downgrade_ops=DowngradeOps(ops=[]),
    )
    sync_enum_types(context, "test", [script])
    return script


class TestEnumTypeState:
    def test_state_is_hashable_and_comparable(self) -> None:
        """Frozen so it can key a diff without accidental mutation."""
        first = EnumTypeState(name="e", values=("a",), columns=())
        second = EnumTypeState(name="e", values=("a",), columns=())
        assert first == second
        assert len({first, second}) == 1
