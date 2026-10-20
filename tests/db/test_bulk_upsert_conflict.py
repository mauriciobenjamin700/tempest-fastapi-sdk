"""``bulk_upsert(on_conflict="nothing")`` and the real row counts, per dialect.

Issue #581. Every behavior runs on a SQLite **file** and on a real
PostgreSQL: the concurrency case needs two connections with their own
transactions, and a shared in-memory database behind ``StaticPool`` is a
single connection, so the "concurrent" sessions would be one transaction
and prove nothing.

PostgreSQL comes from ``TEST_POSTGRES_URL`` when it is set, otherwise
from a ``postgres:16-alpine`` container started once per module under
its own name and port, so it never collides with the wallet guard's
container. The ``postgres`` parameter carries the ``docker`` marker: the
default run exercises SQLite and ``make test-docker`` adds PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Date, String, Table, UniqueConstraint
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    ConflictException,
    enable_sqlite_savepoints,
)

POSTGRES_IMAGE: str = "postgres:16-alpine"
POSTGRES_CONTAINER: str = "tempest-bulk-upsert-live"
POSTGRES_PORT: int = 55481


class _Occurrence(BaseModel):
    """One materialized occurrence of a task on a day (the #581 use case)."""

    __tablename__ = "_bulk_occurrences"
    __table_args__ = (
        UniqueConstraint("task_ref", "occurs_on", name="uq_bulk_occurrence"),
    )

    task_ref: Mapped[str] = mapped_column(String(32))
    occurs_on: Mapped[date] = mapped_column(Date)
    label: Mapped[str] = mapped_column(String(32), unique=True)


TABLES: list[Table] = [_Occurrence.__table__]  # type: ignore[list-item]


class _OccurrenceRepo(BaseRepository[_Occurrence]):
    """Repository under test."""

    def __init__(self, session: AsyncSession, *, autocommit: bool = True) -> None:
        """Build the repository.

        Args:
            session (AsyncSession): The session.
            autocommit (bool): Forwarded to :class:`BaseRepository`.
        """
        super().__init__(session=session, model=_Occurrence, autocommit=autocommit)


def _row(task_ref: str, day: int, label: str | None = None) -> dict[str, Any]:
    """Build one occurrence row.

    Args:
        task_ref (str): The task half of the natural key.
        day (int): Day of October 2026, the date half of the key.
        label (str | None): The separately unique label; derived from the
            key when omitted.

    Returns:
        dict[str, Any]: The row mapping, with a fresh primary key.
    """
    return {
        "id": uuid4(),
        "task_ref": task_ref,
        "occurs_on": date(2026, 10, day),
        "label": label or f"{task_ref}-{day}",
    }


KEY: list[str] = ["task_ref", "occurs_on"]


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """Yield a PostgreSQL URL: ``TEST_POSTGRES_URL`` or a fresh container.

    Yields:
        str: An async SQLAlchemy URL.
    """
    configured = os.environ.get("TEST_POSTGRES_URL")
    if configured:
        yield configured
        return
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", POSTGRES_CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            POSTGRES_CONTAINER,
            "-e",
            "POSTGRES_PASSWORD=probe",
            "-e",
            "POSTGRES_DB=probe",
            "-p",
            f"{POSTGRES_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {POSTGRES_IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", POSTGRES_CONTAINER, "pg_isready", "-U", "postgres"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.skip("postgres never became ready")
        time.sleep(1)
        yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{POSTGRES_PORT}/probe"
    finally:
        subprocess.run(["docker", "rm", "-f", POSTGRES_CONTAINER], capture_output=True)


@pytest_asyncio.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.docker)],
)
async def engine(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[AsyncEngine]:
    """Yield an engine with the occurrence table, on each dialect.

    Args:
        request (pytest.FixtureRequest): Carries the dialect parameter.
        tmp_path (Path): Directory for the SQLite file.

    Yields:
        AsyncEngine: The engine; the table is dropped afterwards.
    """
    if request.param == "sqlite":
        built = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bulk.db'}")
        enable_sqlite_savepoints(built)
    else:
        built = create_async_engine(request.getfixturevalue("postgres_url"))
    async with built.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
        await connection.run_sync(BaseModel.metadata.create_all, tables=TABLES)
    yield built
    async with built.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
    await built.dispose()


@pytest.fixture
def maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory on the parametrized engine.

    Args:
        engine (AsyncEngine): The engine.

    Returns:
        async_sessionmaker[AsyncSession]: The factory.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield one session on the parametrized engine.

    Args:
        maker (async_sessionmaker[AsyncSession]): The factory.

    Yields:
        AsyncSession: The session.
    """
    async with maker() as opened:
        yield opened


class TestOnConflictNothing:
    """``on_conflict="nothing"`` inserts new keys and skips existing ones."""

    async def test_counts_only_inserted_rows(self, session: AsyncSession) -> None:
        repo = _OccurrenceRepo(session)
        assert (
            await repo.bulk_upsert(
                [_row("t1", 1), _row("t1", 2)],
                conflict_columns=KEY,
                on_conflict="nothing",
            )
            == 2
        )
        inserted = await repo.bulk_upsert(
            [_row("t1", 1, "other-label"), _row("t1", 3)],
            conflict_columns=KEY,
            on_conflict="nothing",
        )
        assert inserted == 1
        assert await repo.count() == 3
        kept = await repo.get(
            filters={"task_ref": "t1", "occurs_on": date(2026, 10, 1)}
        )
        assert kept.label == "t1-1"

    async def test_all_conflicts_return_a_real_zero(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        rows = [_row("t1", 1), _row("t1", 2)]
        await repo.bulk_upsert(rows, conflict_columns=KEY, on_conflict="nothing")
        again = [{**row, "id": uuid4()} for row in rows]
        assert (
            await repo.bulk_upsert(again, conflict_columns=KEY, on_conflict="nothing")
            == 0
        )
        assert await repo.count() == 2

    async def test_duplicate_key_inside_one_batch_is_inserted_once(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        inserted = await repo.bulk_upsert(
            [_row("t1", 1), _row("t1", 1, "dup-label")],
            conflict_columns=KEY,
            on_conflict="nothing",
        )
        assert inserted == 1
        assert await repo.count() == 1

    async def test_returning_ids_names_exactly_the_created_rows(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        first = _row("t1", 1)
        await repo.bulk_upsert([first], conflict_columns=KEY, on_conflict="nothing")
        new_a, new_b = _row("t1", 2), _row("t1", 3)
        ids = await repo.bulk_upsert(
            [_row("t1", 1, "x"), new_a, new_b],
            conflict_columns=KEY,
            on_conflict="nothing",
            returning_ids=True,
        )
        assert sorted(ids, key=str) == sorted([new_a["id"], new_b["id"]], key=str)
        assert all(isinstance(created, UUID) for created in ids)

    async def test_returning_ids_is_empty_when_nothing_is_created(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        await repo.bulk_upsert(
            [_row("t1", 1)], conflict_columns=KEY, on_conflict="nothing"
        )
        ids = await repo.bulk_upsert(
            [_row("t1", 1)],
            conflict_columns=KEY,
            on_conflict="nothing",
            returning_ids=True,
        )
        assert ids == []

    async def test_expected_conflict_keeps_the_callers_transaction(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        await repo.bulk_upsert(
            [_row("t1", 1)], conflict_columns=KEY, on_conflict="nothing"
        )
        async with repo.transaction():
            await repo.bulk_create_values([_row("t2", 1)])
            skipped = await repo.bulk_upsert(
                [_row("t1", 1, "y")],
                conflict_columns=KEY,
                on_conflict="nothing",
            )
            await repo.bulk_create_values([_row("t3", 1)])
        assert skipped == 0
        assert await repo.count() == 3

    async def test_other_unique_constraint_still_raises(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        await repo.bulk_upsert(
            [_row("t1", 1)], conflict_columns=KEY, on_conflict="nothing"
        )
        with pytest.raises(ConflictException):
            await repo.bulk_upsert(
                [_row("t9", 9, "t1-1")],
                conflict_columns=KEY,
                on_conflict="nothing",
            )
        assert await repo.count() == 1

    async def test_update_columns_with_nothing_is_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        with pytest.raises(ValueError, match="update_columns has no effect"):
            await repo.bulk_upsert(
                [_row("t1", 1)],
                conflict_columns=KEY,
                update_columns=["label"],
                on_conflict="nothing",
            )
        assert await repo.count() == 0


class TestOnConflictUpdate:
    """The default mode keeps its count and gains ``returning_ids``."""

    async def test_count_is_inserted_plus_updated(self, session: AsyncSession) -> None:
        repo = _OccurrenceRepo(session)
        await repo.bulk_create_values([_row("t1", 1)])
        touched = await repo.bulk_upsert(
            [_row("t1", 1, "renamed"), _row("t1", 2)],
            conflict_columns=KEY,
        )
        assert touched == 2
        assert await repo.count() == 2
        renamed = await repo.get(
            filters={"task_ref": "t1", "occurs_on": date(2026, 10, 1)}
        )
        assert renamed.label == "renamed"

    async def test_returning_ids_lists_inserted_and_updated_rows(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        existing = _row("t1", 1)
        await repo.bulk_create_values([existing])
        new = _row("t1", 2)
        ids = await repo.bulk_upsert(
            [_row("t1", 1, "renamed"), new],
            conflict_columns=KEY,
            update_columns=["label"],
            returning_ids=True,
        )
        assert sorted(ids, key=str) == sorted([existing["id"], new["id"]], key=str)

    async def test_empty_update_columns_points_to_nothing(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _OccurrenceRepo(session)
        with pytest.raises(ValueError, match="on_conflict='nothing'"):
            await repo.bulk_upsert(
                [_row("t1", 1)], conflict_columns=KEY, update_columns=[]
            )

    async def test_unknown_mode_is_rejected(self, session: AsyncSession) -> None:
        repo = _OccurrenceRepo(session)
        with pytest.raises(ValueError, match="must be 'update' or 'nothing'"):
            await repo.bulk_upsert(
                [_row("t1", 1)],
                conflict_columns=KEY,
                on_conflict="ignore",  # type: ignore[call-overload]
            )


class TestConcurrentIdempotentInsert:
    """Two sessions inserting overlapping keys neither fail nor duplicate."""

    async def test_second_session_waits_then_skips(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """The second writer blocks on the first's open transaction.

        The interleaving is forced: session A inserts and holds its
        transaction open; session B's insert of an overlapping batch is
        started and must still be pending; A commits; B then finishes,
        skipping the keys A created and inserting only its own.
        """
        async with maker() as first_session, maker() as second_session:
            first = _OccurrenceRepo(first_session, autocommit=False)
            second = _OccurrenceRepo(second_session, autocommit=False)
            first_ids = await first.bulk_upsert(
                [_row("t1", day) for day in (1, 2, 3)],
                conflict_columns=KEY,
                on_conflict="nothing",
                returning_ids=True,
            )
            pending = asyncio.create_task(
                second.bulk_upsert(
                    [_row("t1", day) for day in (2, 3, 4)],
                    conflict_columns=KEY,
                    on_conflict="nothing",
                    returning_ids=True,
                ),
            )
            await asyncio.sleep(0.3)
            assert not pending.done()
            await first.commit()
            second_ids = await asyncio.wait_for(pending, timeout=10)
            await second.commit()
        assert len(first_ids) == 3
        assert len(second_ids) == 1
        async with maker() as check:
            repo = _OccurrenceRepo(check)
            assert await repo.count() == 4
            assert {row.id for row in await repo.list()} == {*first_ids, *second_ids}

    async def test_racing_rounds_create_each_key_once(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Several rounds racing over the same keys create each key once.

        Every round opens its own session and inserts the same 20 keys;
        the inserted counts across rounds add up to exactly 20.
        """
        keys = [("t1", day) for day in range(1, 21)]

        async def round_trip() -> int:
            async with maker() as opened:
                return await _OccurrenceRepo(opened).bulk_upsert(
                    [_row(task_ref, day) for task_ref, day in keys],
                    conflict_columns=KEY,
                    on_conflict="nothing",
                )

        counts = await asyncio.gather(*(round_trip() for _ in range(5)))
        assert sum(counts) == len(keys)
        async with maker() as check:
            assert await _OccurrenceRepo(check).count() == len(keys)


class TestRowcountFallback:
    """A real ``0`` from the driver is reported as ``0``."""

    def test_zero_is_kept_and_unknown_falls_back(self) -> None:
        class _Result:
            def __init__(self, rowcount: int) -> None:
                self.rowcount = rowcount

        rowcount = BaseRepository._reported_rowcount
        assert rowcount(_Result(0), fallback=5) == 0  # type: ignore[arg-type]
        assert rowcount(_Result(3), fallback=5) == 3  # type: ignore[arg-type]
        assert rowcount(_Result(-1), fallback=5) == 5  # type: ignore[arg-type]
