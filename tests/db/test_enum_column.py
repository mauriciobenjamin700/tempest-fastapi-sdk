"""Tests for tempest_fastapi_sdk.db.enums and the BaseModel annotation map.

The DDL assertions are the load-bearing ones. ``TempestEnum`` reaches the
``Mapped[SomeEnum]`` path by overriding a private SQLAlchemy hook
(``_resolve_for_python_type``), and the whole point of the class is the
three defaults it changes. Compiling the DDL for both dialects is what
turns a SQLAlchemy release that moves that hook into a red test instead
of a column that silently reverts to storing member names with no
constraint.
"""

import enum

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import CreateTable

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.db.enums import (
    ENUM_TYPE_SUFFIX,
    TempestEnum,
    enum_column,
    enum_type_name,
    enum_values,
)


class OrderStatus(BaseStrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Priority(enum.Enum):
    """A plain ``enum.Enum`` — the annotation map must cover it too."""

    LOW = 1
    HIGH = 2


class Order(BaseModel):
    __tablename__ = "order_for_enum_test"

    status: Mapped[OrderStatus]
    fallback_status: Mapped[OrderStatus] = enum_column(
        OrderStatus, default=OrderStatus.OPEN
    )
    stock_enum: Mapped[OrderStatus] = mapped_column(
        sa.Enum(OrderStatus, name="stock_enum_type"),
        nullable=False,
        default=OrderStatus.OPEN,
    )


@pytest.fixture
def orders(session: AsyncSession) -> BaseRepository[Order]:
    return BaseRepository(session, model=Order)


def _ddl(dialect: object) -> str:
    """Compile the ``Order`` table for a dialect.

    Args:
        dialect (object): The SQLAlchemy dialect instance.

    Returns:
        str: The rendered ``CREATE TABLE``.
    """
    return str(CreateTable(Order.__table__).compile(dialect=dialect))  # type: ignore[arg-type]


class TestHelpers:
    def test_enum_values_returns_values_in_declaration_order(self) -> None:
        assert enum_values(OrderStatus) == ["open", "in_progress", "done"]

    def test_enum_values_coerces_non_string_members(self) -> None:
        assert enum_values(Priority) == ["1", "2"]

    def test_type_name_is_snake_cased_and_suffixed(self) -> None:
        assert enum_type_name(OrderStatus) == f"order_status{ENUM_TYPE_SUFFIX}"


class TestAnnotationMap:
    def test_mapped_annotation_uses_tempest_enum(self) -> None:
        column_type = Order.__table__.c.status.type
        assert isinstance(column_type, TempestEnum)

    def test_labels_are_values_not_member_names(self) -> None:
        """The defect this class exists to prevent."""
        assert Order.__table__.c.status.type.enums == [
            "open",
            "in_progress",
            "done",
        ]

    def test_type_name_carries_the_suffix(self) -> None:
        assert Order.__table__.c.status.type.name == "order_status_enum"

    def test_enum_column_matches_the_bare_annotation(self) -> None:
        """Both routes must produce the same database type."""
        annotated = Order.__table__.c.status.type
        explicit = Order.__table__.c.fallback_status.type
        assert explicit.name == annotated.name
        assert explicit.enums == annotated.enums

    def test_an_explicit_type_still_wins(self) -> None:
        stock = Order.__table__.c.stock_enum.type
        assert not isinstance(stock, TempestEnum)
        assert stock.name == "stock_enum_type"
        assert stock.enums == ["OPEN", "IN_PROGRESS", "DONE"]


class TestPostgresDdl:
    def test_column_uses_the_native_enum_type(self) -> None:
        assert "status order_status_enum" in _ddl(postgresql.dialect())

    def test_no_redundant_check_constraint(self) -> None:
        """PostgreSQL enforces the type itself; a CHECK would be dead weight."""
        ddl = _ddl(postgresql.dialect())
        assert "CHECK (status IN" not in ddl


class TestSqliteDdl:
    def test_column_falls_back_to_varchar(self) -> None:
        assert "status VARCHAR(11)" in _ddl(sqlite.dialect())

    def test_check_constraint_lists_the_values(self) -> None:
        """Without this the test database accepts data production rejects."""
        ddl = _ddl(sqlite.dialect())
        assert "CHECK (status IN ('open', 'in_progress', 'done'))" in ddl

    def test_constraint_name_follows_the_convention(self) -> None:
        assert "CONSTRAINT ck_order_for_enum_test_order_status_enum" in _ddl(
            sqlite.dialect()
        )


class TestRoundTrip:
    async def test_member_survives_a_write_and_read(
        self, orders: BaseRepository[Order]
    ) -> None:
        created = await orders.add(Order(status=OrderStatus.IN_PROGRESS))
        fetched = await orders.get_by_id(created.id)
        assert fetched.status is OrderStatus.IN_PROGRESS

    async def test_stored_string_is_the_value(
        self, orders: BaseRepository[Order], session: AsyncSession
    ) -> None:
        """Read past the ORM: what a report or a sibling service would see."""
        await orders.add(Order(status=OrderStatus.IN_PROGRESS))
        result = await session.execute(
            text("SELECT status FROM order_for_enum_test"),
        )
        assert result.scalar_one() == "in_progress"

    async def test_filtering_by_member_works(
        self, orders: BaseRepository[Order]
    ) -> None:
        await orders.add(Order(status=OrderStatus.DONE))
        await orders.add(Order(status=OrderStatus.OPEN))
        found = await orders.list(filters={"status": OrderStatus.DONE})
        assert len(found) == 1

    async def test_default_is_applied(self, orders: BaseRepository[Order]) -> None:
        created = await orders.add(Order(status=OrderStatus.OPEN))
        assert created.fallback_status is OrderStatus.OPEN


class TestDatabaseRejectsInvalidValues:
    async def test_check_constraint_blocks_an_unknown_value(
        self, session: AsyncSession
    ) -> None:
        """The safety the SQLite default was missing.

        Written as raw SQL on purpose — going through the ORM would be
        stopped by SQLAlchemy before the database ever saw it, which
        proves nothing about the column.
        """
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO order_for_enum_test "
                    "(id, is_active, status, fallback_status, stock_enum) "
                    "VALUES ('x', 1, 'bogus', 'open', 'OPEN')",
                ),
            )
        await session.rollback()

    async def test_a_valid_value_passes(self, session: AsyncSession) -> None:
        await session.execute(
            text(
                "INSERT INTO order_for_enum_test "
                "(id, is_active, status, fallback_status, stock_enum) "
                "VALUES ('y', 1, 'done', 'open', 'OPEN')",
            ),
        )
        await session.rollback()


class TestStringValuesForm:
    def test_bare_values_still_build_a_type(self) -> None:
        """``sqlalchemy.Enum`` also accepts plain strings; keep that working."""
        column_type = TempestEnum("a", "b", name="letters_enum")
        assert column_type.enums == ["a", "b"]
        assert column_type.name == "letters_enum"

    def test_explicit_keywords_beat_the_defaults(self) -> None:
        column_type = TempestEnum(OrderStatus, name="custom_name")
        assert column_type.name == "custom_name"
