"""Every SDK timestamp reads back aware, on both engines.

Measured before the fix, same row and same process on SQLite: the
Python-side default made ``created_at`` aware at ``commit()``, and the
very next ``SELECT`` handed it back naive — so
``datetime.now(UTC) - row.created_at`` raised ``TypeError``. PostgreSQL
returned it aware all along, which is what made the split invisible:
code written against one engine failed on the other, and the suite only
ever ran one of them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import TIMESTAMP, String, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import CreateTable
from sqlalchemy.types import TypeDecorator

from tempest_fastapi_sdk import BaseModel, UtcDateTime


class Stamped(BaseModel):
    """A row with one explicit aware column beside the inherited ones."""

    __tablename__ = "stamped_for_utc_test"

    label: Mapped[str] = mapped_column(String(16), nullable=False)
    happened_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
    )


class TestReadsBackAware:
    """The behaviour the type exists for."""

    async def test_inherited_created_at_is_aware_after_select(
        self,
        session: AsyncSession,
    ) -> None:
        session.add(Stamped(label="a"))
        await session.commit()
        session.expire_all()

        row = (await session.execute(select(Stamped))).scalar_one()

        assert row.created_at.tzinfo is not None

    async def test_arithmetic_against_now_does_not_raise(
        self,
        session: AsyncSession,
    ) -> None:
        """``TypeError: can't subtract offset-naive and offset-aware``."""
        session.add(Stamped(label="b"))
        await session.commit()
        session.expire_all()

        row = (await session.execute(select(Stamped))).scalar_one()

        assert datetime.now(UTC) - row.created_at < timedelta(minutes=5)

    async def test_naive_input_is_stored_and_read_as_utc(
        self,
        session: AsyncSession,
    ) -> None:
        session.add(
            Stamped(label="c", happened_at=datetime(2026, 9, 11, 12, 0)),
        )
        await session.commit()
        session.expire_all()

        row = (
            await session.execute(select(Stamped).where(Stamped.label == "c"))
        ).scalar_one()

        assert row.happened_at == datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    async def test_offset_input_is_normalized_to_utc(
        self,
        session: AsyncSession,
    ) -> None:
        from datetime import timezone

        sao_paulo = timezone(timedelta(hours=-3))
        session.add(
            Stamped(
                label="d",
                happened_at=datetime(2026, 9, 11, 9, 0, tzinfo=sao_paulo),
            ),
        )
        await session.commit()
        session.expire_all()

        row = (
            await session.execute(select(Stamped).where(Stamped.label == "d"))
        ).scalar_one()

        assert row.happened_at == datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    async def test_null_stays_null(self, session: AsyncSession) -> None:
        session.add(Stamped(label="e"))
        await session.commit()
        session.expire_all()

        row = (
            await session.execute(select(Stamped).where(Stamped.label == "e"))
        ).scalar_one()

        assert row.happened_at is None


class TestTheTypeItself:
    """What other code asks the type, and gets wrong answers from."""

    def test_python_type_is_datetime(self) -> None:
        """``TypeDecorator`` raises ``NotImplementedError`` unasked."""
        assert UtcDateTime().python_type is datetime

    def test_timezone_flag_is_readable_through_impl(self) -> None:
        """``impl = TIMESTAMP`` (the class) would answer ``False`` here."""
        assert UtcDateTime().impl_instance.timezone is True

    @pytest.mark.parametrize(
        ("dialect", "expected"),
        [
            (postgresql.dialect(), "TIMESTAMP WITH TIME ZONE"),
            (sqlite.dialect(), "TIMESTAMP"),
        ],
    )
    def test_ddl_is_unchanged(self, dialect: Any, expected: str) -> None:
        """No migration for consumers: the emitted column type is the same."""
        ddl = str(CreateTable(Stamped.__table__).compile(dialect=dialect))
        line = next(line.strip() for line in ddl.splitlines() if "happened_at" in line)

        assert expected in line

    def test_it_is_a_type_decorator_over_timestamp(self) -> None:
        assert isinstance(UtcDateTime(), TypeDecorator)
        assert isinstance(UtcDateTime().impl_instance, TIMESTAMP)


class TestCursorCoercionSeesThroughIt:
    """The decorator must not hide the timezone from the cursor logic."""

    def test_wants_timezone_descends_into_the_decorator(self) -> None:
        from tempest_fastapi_sdk.db.repository import _coerce_cursor_value

        value = _coerce_cursor_value(
            Stamped.__table__.c.happened_at,
            "2026-09-11T12:00:00+00:00",
        )

        assert isinstance(value, datetime)
        assert value.tzinfo is not None


class TestMigrationRendering:
    """What ``alembic revision --autogenerate`` writes for this column.

    Alembic renders a type it does not know as a dotted path into the
    package that defines it — and the generated migration never imports
    that package, so the file is broken the first time it runs. Measured
    on this repo before the fix, a squashed migration came out carrying::

        tempest_fastapi_sdk.db.datetime_type.UtcDateTime(timezone=True)

    with no import, which `tempest db squash` then reported as
    ``F821 Undefined name `tempest_fastapi_sdk```. The same trap
    ``TempestEnum`` had, and the same hook fixes it.
    """

    def test_renders_as_a_plain_sqlalchemy_type(self) -> None:
        from tempest_fastapi_sdk.db.enum_migrations import render_enum_types

        rendered = render_enum_types("type", UtcDateTime(), None)  # type: ignore[arg-type]

        assert rendered == "sa.TIMESTAMP(timezone=True)"

    def test_the_rendered_type_names_no_sdk_module(self) -> None:
        from tempest_fastapi_sdk.db.enum_migrations import render_enum_types

        rendered = render_enum_types("type", UtcDateTime(), None)  # type: ignore[arg-type]

        assert "tempest_fastapi_sdk" not in str(rendered)
