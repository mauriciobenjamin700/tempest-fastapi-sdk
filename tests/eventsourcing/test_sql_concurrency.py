"""``SqlEventStore`` under real concurrency: two connections, two transactions.

The optimistic-concurrency claim is that of two appends with the same
``expected_version`` exactly one lands and the other raises
:class:`~tempest_fastapi_sdk.WrongExpectedVersionException`. Sequential calls
cannot show that — the second call simply reads the first one's commit. So
these tests hold the first transaction **open** while the second append
runs on its own connection, and on PostgreSQL they confirm, through
``pg_stat_activity``, that the second one is actually blocked before the
first commits.

The ``$all`` tests pin the other claim: positions are assigned in commit
order with no gap a projection could skip — and, as the evidence for why
the store takes an advisory lock instead of using a sequence, that a
sequence does leave such a gap.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import text

from tempest_fastapi_sdk import AsyncDatabaseManager, WrongExpectedVersionException
from tempest_fastapi_sdk.eventsourcing import StreamStateEnum
from tests.eventsourcing.support import OrderPlaced, make_store
from tests.eventsourcing.support import db as db
from tests.eventsourcing.support import postgres_url as postgres_url

ROUNDS: int = 20
WRITERS: int = 8


def placed(total: int = 1) -> OrderPlaced:
    """Build an ``OrderPlaced``.

    Args:
        total (int): The total.

    Returns:
        OrderPlaced: The event.
    """
    return OrderPlaced(order_id="x", total_cents=total)


async def wait_for_lock_waiter(
    db: AsyncDatabaseManager, *, deadline: float = 15.0
) -> None:
    """Poll ``pg_stat_activity`` until some backend waits on a lock.

    Args:
        db (AsyncDatabaseManager): A PostgreSQL manager.
        deadline (float): Seconds before giving up.

    Raises:
        AssertionError: When no backend waited within ``deadline``.
    """
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        async with db.engine.connect() as connection:
            waiting = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' "
                        "AND datname = current_database()"
                    )
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the second append never blocked on the first")


async def test_second_writer_with_same_expectation_is_refused(
    db: AsyncDatabaseManager,
) -> None:
    """Forced interleaving: B starts while A is uncommitted; B is refused."""
    store = make_store(db)
    await store.append("s", [placed()], expected_version=StreamStateEnum.NO_STREAM)

    holder = await db.get_session()
    try:
        await store.append("s", [placed(2)], expected_version=0, session=holder)
        contender = asyncio.create_task(
            store.append("s", [placed(3)], expected_version=0)
        )
        if db.engine.dialect.name == "postgresql":
            await wait_for_lock_waiter(db)
        else:
            await asyncio.sleep(0.3)
        assert not contender.done(), "B finished while A held its transaction"
        await holder.commit()
    finally:
        await holder.close()

    with pytest.raises(WrongExpectedVersionException) as caught:
        await asyncio.wait_for(contender, timeout=30)
    assert caught.value.actual_version == 1
    events = await store.read_stream("s")
    assert [e.event.total_cents for e in events] == [1, 2]  # type: ignore[attr-defined]


async def test_racing_writers_one_wins_per_round(db: AsyncDatabaseManager) -> None:
    """Many writers with the same expectation, every round: exactly one lands."""
    store = make_store(db)
    await store.append("s", [placed(0)], expected_version=StreamStateEnum.NO_STREAM)
    for round_number in range(ROUNDS):

        async def attempt(writer: int, expected: int = round_number) -> bool:
            try:
                await store.append("s", [placed(writer)], expected_version=expected)
            except WrongExpectedVersionException:
                return False
            return True

        outcomes = await asyncio.gather(*(attempt(w) for w in range(WRITERS)))
        assert sum(outcomes) == 1, f"round {round_number}: {outcomes}"
    events = await store.read_stream("s")
    assert [e.version for e in events] == list(range(ROUNDS + 1))
    assert await store.stream_version("s") == ROUNDS


async def test_racing_stream_creation_one_wins(db: AsyncDatabaseManager) -> None:
    """Many ``NO_STREAM`` appends to one new stream: exactly one creates it."""
    store = make_store(db)

    async def attempt(writer: int) -> bool:
        try:
            await store.append(
                "new", [placed(writer)], expected_version=StreamStateEnum.NO_STREAM
            )
        except WrongExpectedVersionException:
            return False
        return True

    outcomes = await asyncio.gather(*(attempt(w) for w in range(WRITERS)))
    assert sum(outcomes) == 1
    assert len(await store.read_stream("new")) == 1


async def test_concurrent_appends_to_many_streams_number_all_without_gaps(
    db: AsyncDatabaseManager,
) -> None:
    """Concurrent appends to distinct streams fill ``$all`` as 1..n."""
    store = make_store(db)
    await asyncio.gather(
        *(
            store.append(
                f"s-{n}", [placed(n), placed(n)], expected_version=StreamStateEnum.ANY
            )
            for n in range(WRITERS * 3)
        )
    )
    positions = [e.position for e in await store.read_all(limit=1000)]
    assert positions == list(range(1, WRITERS * 3 * 2 + 1))


@pytest.mark.docker
async def test_positions_follow_commit_order_on_postgres(postgres_url: str) -> None:
    """A later append to another stream waits, so it can never commit first."""
    from tempest_fastapi_sdk import BaseModel
    from tests.eventsourcing.support import TABLES

    db = AsyncDatabaseManager(postgres_url)
    await db.connect()
    async with db.engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
        await connection.run_sync(BaseModel.metadata.create_all, tables=TABLES)
    try:
        store = make_store(db)
        holder = await db.get_session()
        try:
            await store.append(
                "x", [placed(1)], expected_version=StreamStateEnum.ANY, session=holder
            )
            later = asyncio.create_task(
                store.append("y", [placed(2)], expected_version=StreamStateEnum.ANY)
            )
            await wait_for_lock_waiter(db)
            assert not later.done()
            assert await store.read_all() == []
            await holder.commit()
        finally:
            await holder.close()
        await asyncio.wait_for(later, timeout=30)
        log = await store.read_all()
        assert [(e.stream_id, e.position) for e in log] == [("x", 1), ("y", 2)]
    finally:
        async with db.engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
        await db.disconnect()


@pytest.mark.docker
async def test_a_sequence_would_let_a_projection_skip_an_event(
    postgres_url: str,
) -> None:
    """The hazard the advisory lock exists for, reproduced with a sequence.

    Transaction 1 draws position 1 and stays open; transaction 2 draws 2 and
    commits. A reader then sees 2 without 1 — a projection that checkpoints
    2 never reads 1 after transaction 1 commits.
    """
    db = AsyncDatabaseManager(postgres_url)
    await db.connect()
    try:
        async with db.engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS es_seq_probe"))
            await connection.execute(
                text(
                    "CREATE TABLE es_seq_probe (position bigserial PRIMARY KEY, v int)"
                )
            )
        first = await db.engine.connect()
        try:
            await first.begin()
            drawn = (
                await first.execute(
                    text("INSERT INTO es_seq_probe (v) VALUES (1) RETURNING position")
                )
            ).scalar_one()
            async with db.engine.begin() as second:
                await second.execute(text("INSERT INTO es_seq_probe (v) VALUES (2)"))
            async with db.engine.connect() as reader:
                visible = (
                    (await reader.execute(text("SELECT position FROM es_seq_probe")))
                    .scalars()
                    .all()
                )
            await first.commit()
        finally:
            await first.close()
        assert drawn == 1
        assert visible == [2]
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS es_seq_probe"))
        await db.disconnect()
