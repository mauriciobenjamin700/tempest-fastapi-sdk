"""``ProjectionRunner`` and the checkpoint stores."""

from __future__ import annotations

import asyncio

import pytest

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.eventsourcing import (
    CheckpointStore,
    InMemoryCheckpointStore,
    ProjectionRunner,
    RecordedEvent,
    SqlCheckpointStore,
    StreamStateEnum,
)
from tests.eventsourcing.support import EsCheckpointModel, OrderPlaced, make_store
from tests.eventsourcing.support import db as db
from tests.eventsourcing.support import postgres_url as postgres_url


async def seed(db: AsyncDatabaseManager, count: int) -> None:
    """Append ``count`` single-event streams.

    Args:
        db (AsyncDatabaseManager): The database.
        count (int): How many events.
    """
    store = make_store(db)
    for n in range(count):
        await store.append(
            f"order-{n}",
            [OrderPlaced(order_id=str(n), total_cents=n)],
            expected_version=StreamStateEnum.NO_STREAM,
        )


async def test_runs_in_batches_from_the_checkpoint(db: AsyncDatabaseManager) -> None:
    """Each run resumes after the saved checkpoint; nothing is seen twice."""
    await seed(db, 5)
    seen: list[int] = []

    async def handler(event: RecordedEvent) -> None:
        seen.append(event.position)

    checkpoints = SqlCheckpointStore(db, model=EsCheckpointModel)
    assert isinstance(checkpoints, CheckpointStore)
    runner = ProjectionRunner(
        make_store(db),
        name="totals",
        handler=handler,
        checkpoints=checkpoints,
        batch_size=2,
    )
    assert [await runner.run_once() for _ in range(4)] == [2, 2, 1, 0]
    assert seen == [1, 2, 3, 4, 5]
    assert await checkpoints.load("totals") == 5
    assert await checkpoints.load("other") is None


async def test_failure_checkpoints_the_last_success(db: AsyncDatabaseManager) -> None:
    """A handler error saves the position before it; the next run retries it."""
    await seed(db, 3)
    seen: list[int] = []
    fail_at = {2}

    async def handler(event: RecordedEvent) -> None:
        if event.position in fail_at:
            fail_at.clear()
            raise RuntimeError("boom")
        seen.append(event.position)

    checkpoints = InMemoryCheckpointStore()
    runner = ProjectionRunner(
        make_store(db), name="p", handler=handler, checkpoints=checkpoints
    )
    with pytest.raises(RuntimeError):
        await runner.run_once()
    assert await checkpoints.load("p") == 1
    assert await runner.run_once() == 2
    assert seen == [1, 2, 3]


async def test_run_loop_stops_on_event(db: AsyncDatabaseManager) -> None:
    """``run`` drains, survives a failing batch and exits on ``stop_event``."""
    await seed(db, 3)
    seen: list[int] = []
    failures = {"left": 1}
    done = asyncio.Event()

    async def handler(event: RecordedEvent) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("transient")
        seen.append(event.position)
        if len(seen) == 3:
            done.set()

    stop = asyncio.Event()
    runner = ProjectionRunner(
        make_store(db),
        name="loop",
        handler=handler,
        checkpoints=InMemoryCheckpointStore(),
    )
    task = asyncio.create_task(runner.run(poll_interval=0.01, stop_event=stop))
    await asyncio.wait_for(done.wait(), timeout=10)
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert seen == [1, 2, 3]


async def test_sql_checkpoint_overwrites(db: AsyncDatabaseManager) -> None:
    """Saving twice keeps one row with the latest position."""
    checkpoints = SqlCheckpointStore(db, model=EsCheckpointModel)
    await checkpoints.save("p", 3)
    await checkpoints.save("p", 7)
    assert await checkpoints.load("p") == 7


def test_batch_size_must_be_positive() -> None:
    """A zero batch would never advance."""

    async def handler(event: RecordedEvent) -> None:
        return None

    with pytest.raises(ValueError):
        ProjectionRunner(
            object(),  # type: ignore[arg-type]
            name="p",
            handler=handler,
            checkpoints=InMemoryCheckpointStore(),
            batch_size=0,
        )
