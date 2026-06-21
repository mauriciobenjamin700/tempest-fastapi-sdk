"""Tests for ``reorder_base_columns_first`` + ``backfill_non_nullable_defaults``."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from alembic.operations import ops

from tempest_fastapi_sdk import (
    BASE_COLUMN_ORDER,
    backfill_non_nullable_defaults,
    compose_hooks,
    reorder_base_columns_first,
)


def _column_names(create_op: ops.CreateTableOp) -> list[str]:
    """Pull ``Column.name`` off the children, skipping constraints."""
    return [item.name for item in create_op.columns if hasattr(item, "type")]


def _build_migration_script(create_op: ops.CreateTableOp) -> ops.MigrationScript:
    """Wrap one CreateTableOp into the directive tree Alembic emits."""
    upgrade_ops = ops.UpgradeOps(ops=[create_op])
    downgrade_ops = ops.DowngradeOps(ops=[])
    return ops.MigrationScript(
        rev_id="test_rev",
        upgrade_ops=upgrade_ops,
        downgrade_ops=downgrade_ops,
        message="test",
    )


class TestReorderBaseColumnsFirst:
    def test_reorders_when_base_columns_at_the_end(self) -> None:
        # Simulate Alembic emitting subclass columns first, base last.
        create_op = ops.CreateTableOp(
            "users",
            columns=[
                sa.Column("email", sa.String(320), nullable=False),
                sa.Column("hashed_password", sa.String(255), nullable=False),
                sa.Column("id", sa.Uuid(), nullable=False),
                sa.Column("is_active", sa.Boolean(), nullable=False),
                sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
                sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
                sa.PrimaryKeyConstraint("id", name="pk_users"),
            ],
        )
        directive = _build_migration_script(create_op)
        reorder_base_columns_first(object(), "rev_id", [directive])

        assert _column_names(create_op)[:4] == list(BASE_COLUMN_ORDER)
        # Subclass columns kept in their original relative order.
        assert _column_names(create_op)[4:] == ["email", "hashed_password"]

    def test_idempotent_when_already_in_order(self) -> None:
        create_op = ops.CreateTableOp(
            "users",
            columns=[
                sa.Column("id", sa.Uuid(), nullable=False),
                sa.Column("is_active", sa.Boolean(), nullable=False),
                sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
                sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
                sa.PrimaryKeyConstraint("id"),
                sa.Column("email", sa.String(320), nullable=False),
            ],
        )
        directive = _build_migration_script(create_op)
        reorder_base_columns_first(object(), "rev_id", [directive])

        assert _column_names(create_op) == [
            "id",
            "is_active",
            "created_at",
            "updated_at",
            "email",
        ]

    def test_constraints_preserved_after_base_columns(self) -> None:
        pk = sa.PrimaryKeyConstraint("id", name="pk_users")
        uq = sa.UniqueConstraint("email", name="uq_users_email")
        create_op = ops.CreateTableOp(
            "users",
            columns=[
                sa.Column("email", sa.String(320), nullable=False),
                pk,
                sa.Column("id", sa.Uuid(), nullable=False),
                sa.Column("is_active", sa.Boolean(), nullable=False),
                sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
                sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
                uq,
            ],
        )
        directive = _build_migration_script(create_op)
        reorder_base_columns_first(object(), "rev_id", [directive])

        # Base columns first, then everything else (including
        # constraints) in original relative order.
        assert _column_names(create_op)[:4] == list(BASE_COLUMN_ORDER)
        constraints = [item for item in create_op.columns if not hasattr(item, "type")]
        assert constraints == [pk, uq]

    def test_table_without_base_columns_untouched(self) -> None:
        create_op = ops.CreateTableOp(
            "join_table",
            columns=[
                sa.Column("left_id", sa.Uuid(), nullable=False),
                sa.Column("right_id", sa.Uuid(), nullable=False),
                sa.PrimaryKeyConstraint("left_id", "right_id"),
            ],
        )
        directive = _build_migration_script(create_op)
        reorder_base_columns_first(object(), "rev_id", [directive])

        assert _column_names(create_op) == ["left_id", "right_id"]

    def test_non_create_table_ops_untouched(self) -> None:
        drop_op = ops.DropTableOp("old_table")
        directive = _build_migration_script(drop_op)  # type: ignore[arg-type]
        reorder_base_columns_first(object(), "rev_id", [directive])
        # No assertion — just verify it didn't raise.


class _Status(StrEnum):
    PENDING = "pending"
    DONE = "done"


def _add_column_script(column: sa.Column[Any]) -> ops.MigrationScript:
    """Wrap a single AddColumnOp into the directive tree Alembic emits."""
    add_op = ops.AddColumnOp("users", column)
    return ops.MigrationScript(
        rev_id="rev",
        upgrade_ops=ops.UpgradeOps(ops=[add_op]),
        downgrade_ops=ops.DowngradeOps(ops=[]),
        message="add column",
    )


def _server_default_sql(column: sa.Column[Any]) -> str | None:
    """Return the rendered SQL of a column's ``server_default``, if any."""
    sd = column.server_default
    if sd is None:
        return None
    return sd.arg.text  # DefaultClause.arg is the TextClause


class TestBackfillNonNullableDefaults:
    def test_bool_default_becomes_server_default(self) -> None:
        column = sa.Column(
            "is_professional", sa.Boolean(), nullable=False, default=False
        )
        directive = _add_column_script(column)
        backfill_non_nullable_defaults(object(), "rev", [directive])
        assert _server_default_sql(column) == "false"

    def test_true_default(self) -> None:
        column = sa.Column("enabled", sa.Boolean(), nullable=False, default=True)
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) == "true"

    def test_int_default(self) -> None:
        column = sa.Column("retries", sa.Integer(), nullable=False, default=0)
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) == "0"

    def test_str_default_is_quoted_and_escaped(self) -> None:
        column = sa.Column("label", sa.String(50), nullable=False, default="it's")
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) == "'it''s'"

    def test_enum_default_uses_value(self) -> None:
        column = sa.Column(
            "status", sa.String(20), nullable=False, default=_Status.PENDING
        )
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) == "'pending'"

    def test_nullable_column_untouched(self) -> None:
        column = sa.Column("nick", sa.String(50), nullable=True, default="x")
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) is None

    def test_existing_server_default_untouched(self) -> None:
        column = sa.Column(
            "flag",
            sa.Boolean(),
            nullable=False,
            default=False,
            server_default=sa.text("true"),
        )
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        # The pre-existing server_default wins — we don't override it.
        assert _server_default_sql(column) == "true"

    def test_callable_default_untouched(self) -> None:
        column = sa.Column("token", sa.Uuid(), nullable=False, default=uuid4)
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) is None

    def test_no_default_untouched(self) -> None:
        column = sa.Column("email", sa.String(320), nullable=False)
        backfill_non_nullable_defaults(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) is None

    def test_create_table_columns_untouched(self) -> None:
        # A brand-new table has no rows — NOT NULL is already safe, so the
        # hook must NOT inject a server_default on CreateTableOp columns.
        column = sa.Column("flag", sa.Boolean(), nullable=False, default=False)
        create_op = ops.CreateTableOp("brand_new", columns=[column])
        directive = ops.MigrationScript(
            rev_id="rev",
            upgrade_ops=ops.UpgradeOps(ops=[create_op]),
            downgrade_ops=ops.DowngradeOps(ops=[]),
            message="create",
        )
        backfill_non_nullable_defaults(object(), "rev", [directive])
        assert _server_default_sql(column) is None

    def test_composes_with_reorder(self) -> None:
        column = sa.Column("flag", sa.Boolean(), nullable=False, default=False)
        combined = compose_hooks(
            reorder_base_columns_first, backfill_non_nullable_defaults
        )
        combined(object(), "rev", [_add_column_script(column)])
        assert _server_default_sql(column) == "false"


class TestComposeHooks:
    def test_runs_hooks_in_order(self) -> None:
        calls: list[str] = []

        def hook_a(_c: Any, _r: Any, _d: list[Any]) -> None:
            calls.append("a")

        def hook_b(_c: Any, _r: Any, _d: list[Any]) -> None:
            calls.append("b")

        combined = compose_hooks(hook_a, hook_b)
        combined(object(), "rev", [])

        assert calls == ["a", "b"]
