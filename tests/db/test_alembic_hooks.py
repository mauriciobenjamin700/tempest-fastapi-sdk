"""Tests for ``reorder_base_columns_first``."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic.operations import ops

from tempest_fastapi_sdk import (
    BASE_COLUMN_ORDER,
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
