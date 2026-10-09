"""Tests for tempest_fastapi_sdk.privacy.SubjectGraph."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
    delete,
    insert,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tempest_fastapi_sdk.privacy import SubjectGraph
from tempest_fastapi_sdk.testing import (
    assert_subject_graph_valid,
    create_test_engine,
)


def _schema() -> MetaData:
    """Build a schema with a users root and a three-level cascade closure.

    Returns:
        MetaData: ``users`` -> ``profiles``/``orders`` -> ``order_items``,
        an ``invoices`` table kept with ``SET NULL``, and an unrelated
        ``categories`` table referenced from inside the closure.
    """
    metadata = MetaData()
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String(120)),
        Column("password_hash", String(120)),
        Column("created_at", DateTime(timezone=True)),
    )
    Table(
        "profiles",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", ForeignKey("users.id", ondelete="CASCADE")),
        Column("bio", String(200)),
        Column("api_token", String(64), info={"secret": True}),
        Column("avatar", LargeBinary),
        Column("public_id", Uuid),
    )
    Table("categories", metadata, Column("id", Integer, primary_key=True))
    Table(
        "orders",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", ForeignKey("users.id", ondelete="cascade")),
        Column("total", Numeric(10, 2)),
    )
    Table(
        "order_items",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("order_id", ForeignKey("orders.id", ondelete="CASCADE")),
        Column("category_id", ForeignKey("categories.id")),
        Column("sku", String(20)),
    )
    Table(
        "invoices",
        metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "user_id",
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    return metadata


RETAINED: dict[str, str] = {"invoices.user_id": "tax law keeps invoices 5 years"}


class TestClosure:
    def test_tables_breadth_first_from_root(self) -> None:
        graph = SubjectGraph(_schema(), root="users", retained=RETAINED)
        assert graph.tables() == ["users", "orders", "profiles", "order_items"]

    def test_root_as_table_object(self) -> None:
        metadata = _schema()
        graph = SubjectGraph(metadata, root=metadata.tables["users"])
        assert graph.tables()[0] == "users"

    def test_new_cascading_table_joins_export_without_config(self) -> None:
        metadata = _schema()
        before = SubjectGraph(metadata, root="users", retained=RETAINED)
        Table(
            "order_notes",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("item_id", ForeignKey("order_items.id", ondelete="CASCADE")),
        )
        after = SubjectGraph(metadata, root="users", retained=RETAINED)
        assert "order_notes" not in before.tables()
        assert after.tables()[-1] == "order_notes"
        assert after.violations() == []

    def test_composite_primary_key_root_rejected(self) -> None:
        metadata = MetaData()
        Table(
            "pairs",
            metadata,
            Column("a", Integer, primary_key=True),
            Column("b", Integer, primary_key=True),
        )
        with pytest.raises(ValueError, match="exactly one"):
            SubjectGraph(metadata, root="pairs")

    def test_unknown_root_raises(self) -> None:
        with pytest.raises(KeyError):
            SubjectGraph(_schema(), root="nope")

    def test_table_outside_closure_raises(self) -> None:
        graph = SubjectGraph(_schema(), root="users")
        with pytest.raises(KeyError, match="categories"):
            graph.exported_columns("categories")


class TestViolations:
    def test_clean_schema_has_none(self) -> None:
        graph = SubjectGraph(_schema(), root="users", retained=RETAINED)
        assert graph.violations() == []
        assert_subject_graph_valid(graph)

    def test_unlisted_set_null_is_a_violation(self) -> None:
        graph = SubjectGraph(_schema(), root="users")
        assert graph.violations() == [
            "invoices.user_id -> users: ON DELETE SET NULL (expected CASCADE, "
            "or SET NULL listed in retained)"
        ]

    def test_no_action_into_closure_is_a_violation(self) -> None:
        metadata = _schema()
        Table(
            "audit",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("order_id", ForeignKey("orders.id")),
        )
        graph = SubjectGraph(metadata, root="users", retained=RETAINED)
        assert graph.violations() == [
            "audit.order_id -> orders: ON DELETE NO ACTION (expected CASCADE, "
            "or SET NULL listed in retained)"
        ]
        with pytest.raises(AssertionError, match="1 erasure violation"):
            assert_subject_graph_valid(graph)

    def test_fk_out_of_closure_is_not_checked(self) -> None:
        graph = SubjectGraph(_schema(), root="users", retained=RETAINED)
        assert not any("categories" in line for line in graph.violations())

    def test_stale_retained_entry_is_reported(self) -> None:
        graph = SubjectGraph(
            _schema(),
            root="users",
            retained={**RETAINED, "ghosts.user_id": "removed table"},
        )
        assert graph.violations() == [
            "ghosts.user_id: listed in retained but no foreign key from it "
            "points into the subject closure"
        ]

    def test_retained_with_wrong_action_is_reported(self) -> None:
        metadata = _schema()
        Table(
            "audit",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("user_id", ForeignKey("users.id", ondelete="RESTRICT")),
        )
        graph = SubjectGraph(
            metadata,
            root="users",
            retained={**RETAINED, "audit.user_id": "kept"},
        )
        assert graph.violations() == [
            "audit.user_id -> users: listed in retained but ON DELETE is "
            "RESTRICT, expected SET NULL"
        ]

    def test_set_null_on_not_null_column_is_reported(self) -> None:
        metadata = _schema()
        Table(
            "audit",
            metadata,
            Column("id", Integer, primary_key=True),
            Column(
                "user_id",
                ForeignKey("users.id", ondelete="SET NULL"),
                nullable=False,
            ),
        )
        graph = SubjectGraph(
            metadata,
            root="users",
            retained={**RETAINED, "audit.user_id": "kept"},
        )
        assert graph.violations() == [
            "audit.user_id -> users: ON DELETE SET NULL on a NOT NULL column"
        ]


class TestColumns:
    def test_secret_columns_are_left_out(self) -> None:
        graph = SubjectGraph(
            _schema(),
            root="users",
            secret_columns={"orders": ["total"]},
        )
        assert graph.exported_columns("users") == ["id", "email", "created_at"]
        assert "api_token" not in graph.exported_columns("profiles")
        assert graph.exported_columns("orders") == ["id", "user_id"]

    def test_markers_are_configurable(self) -> None:
        graph = SubjectGraph(_schema(), root="users", secret_markers=["EMAIL"])
        assert graph.exported_columns("users") == ["id", "password_hash", "created_at"]


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Yield an in-memory SQLite engine with foreign keys enforced."""
    engine = create_test_engine()
    yield engine
    await engine.dispose()


async def _seed(engine: AsyncEngine, metadata: MetaData) -> None:
    """Create the schema and insert two subjects with nested rows.

    Args:
        engine (AsyncEngine): The test engine.
        metadata (MetaData): The schema from :func:`_schema`.
    """
    tables = metadata.tables
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(tables["users"]),
            [
                {
                    "id": 1,
                    "email": "a@x.io",
                    "password_hash": "h1",
                    "created_at": datetime(2026, 1, 2, tzinfo=UTC),
                },
                {
                    "id": 2,
                    "email": "b@x.io",
                    "password_hash": "h2",
                    "created_at": None,
                },
            ],
        )
        await conn.execute(
            insert(tables["profiles"]),
            [
                {
                    "id": 10,
                    "user_id": 1,
                    "bio": "hi",
                    "api_token": "t",
                    "avatar": b"\x00\xff",
                    "public_id": uuid.UUID(int=7),
                },
                {
                    "id": 20,
                    "user_id": 2,
                    "bio": "other",
                    "api_token": None,
                    "avatar": None,
                    "public_id": None,
                },
            ],
        )
        await conn.execute(insert(tables["categories"]), [{"id": 1}])
        await conn.execute(
            insert(tables["orders"]),
            [
                {"id": 100, "user_id": 1, "total": Decimal("19.90")},
                {"id": 200, "user_id": 2, "total": Decimal("5.00")},
            ],
        )
        await conn.execute(
            insert(tables["order_items"]),
            [
                {"id": 1000, "order_id": 100, "category_id": 1, "sku": "A"},
                {"id": 1001, "order_id": 100, "category_id": 1, "sku": "B"},
                {"id": 2000, "order_id": 200, "category_id": 1, "sku": "C"},
            ],
        )
        await conn.execute(insert(tables["invoices"]), [{"id": 1, "user_id": 1}])


class TestExport:
    async def test_export_selects_only_the_subject(self, engine: AsyncEngine) -> None:
        metadata = _schema()
        await _seed(engine, metadata)
        graph = SubjectGraph(metadata, root="users", retained=RETAINED)
        async with AsyncSession(engine) as session:
            payload = await graph.export(session, 1)
        assert list(payload) == ["users", "orders", "profiles", "order_items"]
        assert payload["users"] == [
            {"id": 1, "email": "a@x.io", "created_at": "2026-01-02T00:00:00"}
        ]
        assert payload["profiles"] == [
            {
                "id": 10,
                "user_id": 1,
                "bio": "hi",
                "avatar": "AP8=",
                "public_id": "00000000-0000-0000-0000-000000000007",
            }
        ]
        assert payload["orders"] == [{"id": 100, "user_id": 1, "total": "19.90"}]
        assert [row["sku"] for row in payload["order_items"]] == ["A", "B"]
        json.dumps(payload)

    async def test_unknown_subject_exports_empty_lists(
        self, engine: AsyncEngine
    ) -> None:
        metadata = _schema()
        await _seed(engine, metadata)
        graph = SubjectGraph(metadata, root="users", retained=RETAINED)
        async with AsyncSession(engine) as session:
            payload = await graph.export(session, 999)
        assert payload == {
            "users": [],
            "orders": [],
            "profiles": [],
            "order_items": [],
        }

    async def test_root_delete_erases_the_closure(self, engine: AsyncEngine) -> None:
        metadata = _schema()
        await _seed(engine, metadata)
        graph = SubjectGraph(metadata, root="users", retained=RETAINED)
        factory = async_sessionmaker(engine)
        async with factory() as session:
            assert await graph.count(session, 1) == {
                "users": 1,
                "orders": 1,
                "profiles": 1,
                "order_items": 2,
            }
            await session.execute(
                delete(metadata.tables["users"]).where(graph.condition("users", 1))
            )
            await session.commit()
        async with factory() as session:
            assert set((await graph.count(session, 1)).values()) == {0}
            assert (await graph.count(session, 2))["order_items"] == 1
            invoice = await session.execute(metadata.tables["invoices"].select())
            assert [tuple(row) for row in invoice] == [(1, None)]


class TestShapes:
    async def test_composite_foreign_key(self, engine: AsyncEngine) -> None:
        metadata = MetaData()
        Table("users", metadata, Column("id", Integer, primary_key=True))
        Table(
            "slots",
            metadata,
            Column(
                "user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
            ),
            Column("n", Integer, primary_key=True),
        )
        Table(
            "bookings",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("user_id", Integer),
            Column("n", Integer),
            ForeignKeyConstraint(
                ["user_id", "n"],
                ["slots.user_id", "slots.n"],
                ondelete="CASCADE",
            ),
        )
        graph = SubjectGraph(metadata, root="users")
        assert graph.tables() == ["users", "slots", "bookings"]
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(insert(metadata.tables["users"]), [{"id": 1}, {"id": 2}])
            await conn.execute(
                insert(metadata.tables["slots"]),
                [{"user_id": 1, "n": 1}, {"user_id": 2, "n": 1}],
            )
            await conn.execute(
                insert(metadata.tables["bookings"]),
                [{"id": 5, "user_id": 1, "n": 1}, {"id": 6, "user_id": 2, "n": 1}],
            )
        async with AsyncSession(engine) as session:
            payload = await graph.export(session, 1)
        assert payload["bookings"] == [{"id": 5, "user_id": 1, "n": 1}]

    async def test_self_reference_does_not_recurse(self, engine: AsyncEngine) -> None:
        metadata = MetaData()
        Table("users", metadata, Column("id", Integer, primary_key=True))
        Table(
            "comments",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("user_id", ForeignKey("users.id", ondelete="CASCADE")),
            Column(
                "parent_id",
                ForeignKey("comments.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        graph = SubjectGraph(metadata, root="users")
        assert graph.violations() == []
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(insert(metadata.tables["users"]), [{"id": 1}, {"id": 2}])
            await conn.execute(
                insert(metadata.tables["comments"]),
                [
                    {"id": 1, "user_id": 1, "parent_id": None},
                    {"id": 2, "user_id": 2, "parent_id": 1},
                ],
            )
        async with AsyncSession(engine) as session:
            payload = await graph.export(session, 1)
        assert payload["comments"] == [{"id": 1, "user_id": 1, "parent_id": None}]
