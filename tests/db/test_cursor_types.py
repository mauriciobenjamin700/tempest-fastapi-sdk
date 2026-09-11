"""The cursor value reaches the WHERE clause in the column's type.

``encode_cursor`` serializes through ``json.dumps(default=str)``, so a
``datetime`` sort key leaves the first page as an ISO string. SQLite
compares that string against a timestamp column without complaining —
dynamic typing, and the ISO layout even sorts correctly — so the whole
suite stayed green while PostgreSQL answered the second page with::

    operator does not exist: timestamp with time zone < character varying

This module pins both halves: the compiled statement (which needs no
server, so it runs in ``make check``) and a real PostgreSQL walk behind
the ``docker`` marker.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import DateTime, Numeric, String, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import Select

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.db.repository import _coerce_cursor_value


class Priority(StrEnum):
    """Sortable enum used to exercise enum cursor columns."""

    LOW = "low"
    HIGH = "high"


class Reading(BaseModel):
    """Row with the column types a cursor is realistically ordered by."""

    __tablename__ = "reading_for_cursor_type_test"

    label: Mapped[str] = mapped_column(String(32), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


@pytest.fixture
def repo(session: AsyncSession) -> BaseRepository[Reading]:
    """Return a repository over the probe model.

    Args:
        session (AsyncSession): The suite's in-memory session.

    Returns:
        BaseRepository[Reading]: The repository under test.
    """
    return BaseRepository(session, model=Reading)


def _capture_statements(session: AsyncSession) -> list[Any]:
    """Collect every ORM statement the session executes.

    Args:
        session (AsyncSession): The session to listen on.

    Returns:
        list[Any]: The list that fills as statements run.
    """
    captured: list[Any] = []

    @event.listens_for(session.sync_session, "do_orm_execute")
    def _record(state: Any) -> None:
        captured.append(state.statement)

    return captured


def _compile_for_postgres(statement: Select[Any]) -> Any:
    """Compile a statement the way asyncpg would send it.

    Args:
        statement (Select[Any]): The statement to compile.

    Returns:
        Any: The compiled statement, with its bound parameters.
    """
    return statement.compile(dialect=postgresql.asyncpg.dialect())


class TestCoerceCursorValue:
    """The conversion table, one column type at a time."""

    def test_timestamptz_column_gets_an_aware_datetime(self) -> None:
        value = _coerce_cursor_value(
            Reading.__table__.c.created_at,
            "2026-09-11T12:56:02.786758+00:00",
        )

        assert isinstance(value, datetime)
        assert value.tzinfo is not None
        assert value == datetime(2026, 9, 11, 12, 56, 2, 786758, tzinfo=UTC)

    def test_naive_column_gets_a_naive_datetime(self) -> None:
        """A ``timestamp`` column rejects an aware value on PostgreSQL."""
        value = _coerce_cursor_value(
            Reading.__table__.c.measured_at,
            "2026-09-11T12:56:02+00:00",
        )

        assert isinstance(value, datetime)
        assert value.tzinfo is None
        assert value == datetime(2026, 9, 11, 12, 56, 2)

    def test_uuid_column(self) -> None:
        identifier = uuid4()

        assert _coerce_cursor_value(Reading.__table__.c.id, str(identifier)) == (
            identifier
        )

    def test_numeric_column(self) -> None:
        assert _coerce_cursor_value(Reading.__table__.c.amount, "10.50") == (
            Decimal("10.50")
        )

    def test_string_column_is_left_alone(self) -> None:
        assert _coerce_cursor_value(Reading.__table__.c.label, "10.50") == "10.50"

    def test_non_string_payload_is_left_alone(self) -> None:
        moment = datetime(2026, 9, 11, tzinfo=UTC)

        assert _coerce_cursor_value(Reading.__table__.c.created_at, moment) is moment

    def test_tampered_value_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid cursor value"):
            _coerce_cursor_value(Reading.__table__.c.created_at, "not-a-timestamp")


class TestCompiledCursorPredicate:
    """What asyncpg would receive, without needing a server."""

    async def test_second_page_binds_a_timestamp_not_a_string(
        self,
        repo: BaseRepository[Reading],
        session: AsyncSession,
    ) -> None:
        """The defect in one assertion: the bind type of the cursor value.

        Before the fix the same compile rendered ``created_at < $2::VARCHAR``
        and PostgreSQL refused the operator.
        """
        base = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        await repo.add_all(
            [
                Reading(
                    label=f"r{index}",
                    measured_at=base.replace(tzinfo=None),
                    amount=Decimal("1.00"),
                    created_at=base + timedelta(minutes=index),
                )
                for index in range(4)
            ],
        )
        first = await repo.cursor_paginate(limit=2, order_by="created_at")
        assert first["next_cursor"] is not None

        captured = _capture_statements(session)
        await repo.cursor_paginate(
            limit=2,
            order_by="created_at",
            cursor=first["next_cursor"],
        )

        compiled = _compile_for_postgres(captured[-1])
        bound = [
            value for value in compiled.params.values() if isinstance(value, datetime)
        ]
        assert bound, f"no datetime bind in {compiled.params}"
        assert "::VARCHAR" not in str(compiled)
        assert "::TIMESTAMP WITH TIME ZONE" in str(compiled)

    async def test_uuid_tiebreaker_binds_a_uuid(
        self,
        repo: BaseRepository[Reading],
        session: AsyncSession,
    ) -> None:
        base = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        await repo.add_all(
            [
                Reading(
                    label=f"r{index}",
                    measured_at=base.replace(tzinfo=None),
                    amount=Decimal("1.00"),
                    created_at=base,
                )
                for index in range(4)
            ],
        )
        first = await repo.cursor_paginate(limit=2, order_by="created_at")
        assert first["next_cursor"] is not None

        captured = _capture_statements(session)
        await repo.cursor_paginate(
            limit=2,
            order_by="created_at",
            cursor=first["next_cursor"],
        )

        compiled = _compile_for_postgres(captured[-1])
        assert any(isinstance(value, UUID) for value in compiled.params.values())


class TestCursorWalkStillWorks:
    """The behaviour the coercion must not change on SQLite."""

    async def test_pages_do_not_overlap_or_skip(
        self,
        repo: BaseRepository[Reading],
    ) -> None:
        base = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        await repo.add_all(
            [
                Reading(
                    label=f"r{index:02d}",
                    measured_at=base.replace(tzinfo=None),
                    amount=Decimal("1.00"),
                    created_at=base + timedelta(minutes=index),
                )
                for index in range(7)
            ],
        )

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            page = await repo.cursor_paginate(
                limit=3,
                order_by="created_at",
                ascending=True,
                cursor=cursor,
            )
            seen.extend(row.label for row in page["items"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break

        assert seen == [f"r{index:02d}" for index in range(7)]

    async def test_malformed_cursor_id_raises_value_error(
        self,
        repo: BaseRepository[Reading],
    ) -> None:
        from tempest_fastapi_sdk import encode_cursor

        cursor = encode_cursor({"value": "2026-09-11T12:00:00+00:00", "id": "nope"})

        with pytest.raises(ValueError, match="Invalid cursor id"):
            await repo.cursor_paginate(order_by="created_at", cursor=cursor)


@pytest.mark.docker
class TestPostgresCursorWalk:
    """The engine that actually refused the comparison."""

    IMAGE: str = "postgres:16-alpine"
    CONTAINER: str = "tempest-cursor-probe"
    PORT: int = 55434

    @pytest.fixture
    def postgres_url(self) -> Iterator[str]:
        """Start a PostgreSQL container and yield its URL.

        Yields:
            str: An async SQLAlchemy URL for the container.
        """
        if shutil.which("docker") is None:
            pytest.skip("docker CLI not installed")
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon not reachable")

        subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)
        started = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                self.CONTAINER,
                "-e",
                "POSTGRES_PASSWORD=probe",
                "-e",
                "POSTGRES_DB=probe",
                "-p",
                f"{self.PORT}:5432",
                self.IMAGE,
            ],
            capture_output=True,
            text=True,
        )
        if started.returncode != 0:
            pytest.skip(f"could not start {self.IMAGE}: {started.stderr.strip()}")
        try:
            for _ in range(60):
                ready = subprocess.run(
                    ["docker", "exec", self.CONTAINER, "pg_isready", "-U", "postgres"],
                    capture_output=True,
                )
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                pytest.skip("postgres never became ready")
            yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{self.PORT}/probe"
        finally:
            subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)

    @pytest_asyncio.fixture
    async def pg_session(self, postgres_url: str) -> AsyncIterator[AsyncSession]:
        """Yield a session against the container with the probe table created.

        Args:
            postgres_url (str): URL from the container fixture.

        Yields:
            AsyncSession: A session bound to the container.
        """
        engine: AsyncEngine = create_async_engine(postgres_url)
        async with engine.begin() as connection:
            await connection.run_sync(Reading.__table__.create, checkfirst=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            yield session
        async with engine.begin() as connection:
            await connection.run_sync(Reading.__table__.drop, checkfirst=True)
        await engine.dispose()

    async def test_second_page_does_not_raise(
        self,
        pg_session: AsyncSession,
    ) -> None:
        """Page 2 by ``created_at`` answered 500 before the coercion."""
        repo: BaseRepository[Reading] = BaseRepository(pg_session, model=Reading)
        base = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        await repo.add_all(
            [
                Reading(
                    label=f"r{index:02d}",
                    measured_at=base.replace(tzinfo=None),
                    amount=Decimal("1.00"),
                    created_at=base + timedelta(minutes=index),
                )
                for index in range(7)
            ],
        )

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            page = await repo.cursor_paginate(
                limit=3,
                order_by="created_at",
                ascending=True,
                cursor=cursor,
            )
            seen.extend(row.label for row in page["items"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break

        assert seen == [f"r{index:02d}" for index in range(7)]
