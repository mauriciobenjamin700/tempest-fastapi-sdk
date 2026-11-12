"""``Aggregate`` + ``AggregateRepository`` over the SQL store."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk import AsyncDatabaseManager, WrongExpectedVersionException
from tempest_fastapi_sdk.eventsourcing import (
    Aggregate,
    AggregateRepository,
    DomainEvent,
    EventMetadata,
)
from tests.eventsourcing.support import Order, make_store
from tests.eventsourcing.support import db as db
from tests.eventsourcing.support import postgres_url as postgres_url


async def test_save_then_load_replays(db: AsyncDatabaseManager) -> None:
    """A saved aggregate loads back with the same state and version."""
    store = make_store(db)
    orders = AggregateRepository(store, Order, stream_prefix="order-")
    assert await orders.load("1") is None

    order = Order("1")
    order.place(500)
    order.ship()
    result = await orders.save(order, metadata=EventMetadata(correlation_id="c"))
    assert result is not None
    assert (result.version, order.version, order.pending_events) == (1, 1, ())
    assert await orders.save(order) is None

    loaded = await orders.load("1")
    assert loaded is not None
    assert (loaded.total_cents, loaded.shipped, loaded.version) == (500, True, 1)
    events = await store.read_stream("order-1")
    assert [e.metadata.correlation_id for e in events] == ["c", "c"]


async def test_stale_aggregate_is_refused_and_keeps_its_events(
    db: AsyncDatabaseManager,
) -> None:
    """Two copies loaded at the same version: the second save conflicts."""
    orders = AggregateRepository(make_store(db), Order)
    first = Order("1")
    first.place(1)
    await orders.save(first)

    a = await orders.load("1")
    b = await orders.load("1")
    assert a is not None and b is not None
    a.place(2)
    b.place(3)
    await orders.save(a)
    with pytest.raises(WrongExpectedVersionException):
        await orders.save(b)
    assert len(b.pending_events) == 1


async def test_creating_an_existing_aggregate_conflicts(
    db: AsyncDatabaseManager,
) -> None:
    """A fresh aggregate saves with ``NO_STREAM``; a duplicate id is refused."""
    orders = AggregateRepository(make_store(db), Order)
    for _ in range(2):
        order = Order("dup")
        order.place(1)
        if _ == 0:
            await orders.save(order)
        else:
            with pytest.raises(WrongExpectedVersionException):
                await orders.save(order)


async def test_snapshots_skip_the_replayed_prefix(db: AsyncDatabaseManager) -> None:
    """With ``snapshot_every=3`` the load applies only events after it."""
    store = make_store(db)
    orders = AggregateRepository(store, Order, snapshot_every=3)
    order = Order("s")
    for _ in range(4):
        order.place(10)
        await orders.save(order)
    snapshot = await store.load_snapshot("s")
    assert snapshot is not None
    assert (snapshot.version, snapshot.state) == (
        2,
        {"total_cents": 30, "shipped": False},
    )

    loaded = await orders.load("s")
    assert loaded is not None
    assert (loaded.total_cents, loaded.version, loaded.applied) == (40, 3, 1)


async def test_snapshot_marks_crossed_by_one_append(db: AsyncDatabaseManager) -> None:
    """One append crossing a multiple saves the snapshot at its last version."""
    store = make_store(db)
    orders = AggregateRepository(store, Order, snapshot_every=2)
    order = Order("b")
    for _ in range(5):
        order.place(1)
    await orders.save(order)
    snapshot = await store.load_snapshot("b")
    assert snapshot is not None
    assert snapshot.version == 4


def test_invalid_snapshot_every() -> None:
    """``snapshot_every`` must be positive."""
    with pytest.raises(ValueError):
        AggregateRepository(object(), Order, snapshot_every=0)  # type: ignore[arg-type]


def test_base_aggregate_requires_apply_and_restore() -> None:
    """The base class names what a subclass must implement."""

    class Bare(Aggregate):
        pass

    bare = Bare("x")
    assert bare.snapshot() is None
    with pytest.raises(NotImplementedError, match="apply"):
        bare.record(DomainEvent())
    with pytest.raises(NotImplementedError, match="restore"):
        bare.restore({})
