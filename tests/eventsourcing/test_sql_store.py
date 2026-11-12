"""``SqlEventStore`` on SQLite (file) and, under ``make test-docker``, PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from tempest_fastapi_sdk import AsyncDatabaseManager, WrongExpectedVersionException
from tempest_fastapi_sdk.db.outbox import OutboxRelay, OutboxStatusEnum
from tempest_fastapi_sdk.eventsourcing import (
    EventMetadata,
    EventRegistry,
    EventStore,
    SqlEventStore,
    StreamStateEnum,
    UnknownEventTypeError,
)
from tests.eventsourcing.support import (
    EsEventModel,
    EsOutboxModel,
    EsStreamModel,
    OrderNoted,
    OrderPlaced,
    OrderShipped,
    make_store,
)
from tests.eventsourcing.support import db as db
from tests.eventsourcing.support import postgres_url as postgres_url


def placed(order_id: str = "1", total: int = 100) -> OrderPlaced:
    """Build an ``OrderPlaced``.

    Args:
        order_id (str): The order id.
        total (int): The total.

    Returns:
        OrderPlaced: The event.
    """
    return OrderPlaced(order_id=order_id, total_cents=total)


async def test_store_satisfies_protocol(db: AsyncDatabaseManager) -> None:
    """The SQL store is an ``EventStore``."""
    assert isinstance(make_store(db), EventStore)


async def test_append_and_read_round_trip(db: AsyncDatabaseManager) -> None:
    """Appended events come back typed, versioned 0..n and positioned 1..n."""
    store = make_store(db)
    result = await store.append(
        "order-1",
        [placed(), OrderShipped(order_id="1", carrier="ups")],
        expected_version=StreamStateEnum.NO_STREAM,
        metadata=EventMetadata(
            correlation_id="corr-1", causation_id="cmd-1", extra={"user": "u1"}
        ),
    )
    assert (result.version, result.position, len(result.event_ids)) == (1, 2, 2)

    events = await store.read_stream("order-1")
    assert [e.version for e in events] == [0, 1]
    assert [e.position for e in events] == [1, 2]
    assert [e.event_id for e in events] == list(result.event_ids)
    assert events[0].event == placed()
    assert isinstance(events[1].event, OrderShipped)
    assert events[1].event.carrier == "ups"
    assert events[0].metadata == EventMetadata(
        correlation_id="corr-1", causation_id="cmd-1", extra={"user": "u1"}
    )
    assert events[0].recorded_at is not None
    assert events[0].recorded_at.tzinfo is not None
    assert await store.stream_version("order-1") == 1
    assert await store.stream_version("missing") is None
    assert await store.read_stream("missing") == []


async def test_read_stream_from_version_and_backwards(db: AsyncDatabaseManager) -> None:
    """``from_version`` is inclusive both ways; ``limit`` caps the read."""
    store = make_store(db)
    await store.append(
        "s",
        [placed(total=n) for n in range(5)],
        expected_version=StreamStateEnum.NO_STREAM,
    )
    forward = await store.read_stream("s", from_version=2)
    assert [e.version for e in forward] == [2, 3, 4]
    backward = await store.read_stream("s", backwards=True)
    assert [e.version for e in backward] == [4, 3, 2, 1, 0]
    from_three_back = await store.read_stream(
        "s", from_version=3, backwards=True, limit=2
    )
    assert [e.version for e in from_three_back] == [3, 2]
    assert [e.version for e in await store.read_stream("s", limit=2)] == [0, 1]
    assert await store.read_stream("s", limit=0) == []


class TestExpectedVersion:
    """Every expectation, accepted and refused."""

    async def test_exact_version(self, db: AsyncDatabaseManager) -> None:
        """An exact version appends once and refuses the stale repeat."""
        store = make_store(db)
        await store.append("s", [placed()], expected_version=StreamStateEnum.NO_STREAM)
        result = await store.append("s", [placed()], expected_version=0)
        assert result.version == 1
        with pytest.raises(WrongExpectedVersionException) as caught:
            await store.append("s", [placed()], expected_version=0)
        error = caught.value
        assert (error.stream_id, error.expected_version, error.actual_version) == (
            "s",
            0,
            1,
        )
        assert error.status_code == 409
        assert error.code == "EVENTSOURCING_WRONG_EXPECTED_VERSION"
        assert await store.stream_version("s") == 1

    async def test_no_stream(self, db: AsyncDatabaseManager) -> None:
        """``NO_STREAM`` creates once and refuses an existing stream."""
        store = make_store(db)
        await store.append("s", [placed()], expected_version=StreamStateEnum.NO_STREAM)
        with pytest.raises(WrongExpectedVersionException) as caught:
            await store.append(
                "s", [placed()], expected_version=StreamStateEnum.NO_STREAM
            )
        assert caught.value.expected_version == "no_stream"
        assert caught.value.actual_version == 0

    async def test_exact_version_on_missing_stream(
        self, db: AsyncDatabaseManager
    ) -> None:
        """An exact version on a stream that does not exist is refused."""
        store = make_store(db)
        with pytest.raises(WrongExpectedVersionException) as caught:
            await store.append("s", [placed()], expected_version=0)
        assert caught.value.actual_version is None
        assert await store.stream_version("s") is None

    async def test_exists(self, db: AsyncDatabaseManager) -> None:
        """``EXISTS`` refuses a missing stream and appends to an existing one."""
        store = make_store(db)
        with pytest.raises(WrongExpectedVersionException):
            await store.append("s", [placed()], expected_version=StreamStateEnum.EXISTS)
        await store.append("s", [placed()], expected_version=StreamStateEnum.NO_STREAM)
        result = await store.append(
            "s", [placed(), placed()], expected_version=StreamStateEnum.EXISTS
        )
        assert result.version == 2

    async def test_any(self, db: AsyncDatabaseManager) -> None:
        """``ANY`` creates a stream and appends to it without a check."""
        store = make_store(db)
        first = await store.append(
            "s", [placed()], expected_version=StreamStateEnum.ANY
        )
        second = await store.append(
            "s", [placed(), placed()], expected_version=StreamStateEnum.ANY
        )
        assert (first.version, second.version) == (0, 2)
        assert [e.version for e in await store.read_stream("s")] == [0, 1, 2]

    async def test_refused_append_writes_nothing(
        self, db: AsyncDatabaseManager
    ) -> None:
        """A refused append leaves no event row behind."""
        store = make_store(db)
        await store.append("s", [placed()], expected_version=StreamStateEnum.NO_STREAM)
        with pytest.raises(WrongExpectedVersionException):
            await store.append("s", [placed(), placed()], expected_version=5)
        async with db.get_session_context() as session:
            count = (
                await session.execute(select(func.count()).select_from(EsEventModel))
            ).scalar_one()
        assert count == 1


async def test_input_validation(db: AsyncDatabaseManager) -> None:
    """Empty appends, negative versions and unregistered types are refused."""
    store = make_store(db)
    with pytest.raises(ValueError, match="at least one event"):
        await store.append("s", [], expected_version=StreamStateEnum.ANY)
    with pytest.raises(ValueError, match="NO_STREAM"):
        await store.append("s", [placed()], expected_version=-1)
    with pytest.raises(UnknownEventTypeError):
        await store.append(
            "s", [OrderNoted(text="x")], expected_version=StreamStateEnum.ANY
        )
    assert await store.stream_version("s") is None


async def test_read_stream_with_unregistered_type_raises(
    db: AsyncDatabaseManager,
) -> None:
    """A stream holding a type this registry lacks fails loudly on read."""
    wide = SqlEventStore(
        db,
        registry=EventRegistry(OrderPlaced, OrderShipped, OrderNoted),
        event_model=EsEventModel,
        stream_model=EsStreamModel,
    )
    await wide.append("s", [OrderNoted(text="x")], expected_version=StreamStateEnum.ANY)
    narrow = make_store(db)
    with pytest.raises(UnknownEventTypeError) as caught:
        await narrow.read_stream("s")
    assert caught.value.event_type == "order.noted"


async def test_read_all_positions_and_type_filter(db: AsyncDatabaseManager) -> None:
    """``$all`` is in position order, after-exclusive, registered types only."""
    wide = SqlEventStore(
        db,
        registry=EventRegistry(OrderPlaced, OrderShipped, OrderNoted),
        event_model=EsEventModel,
        stream_model=EsStreamModel,
    )
    await wide.append("a", [placed("a")], expected_version=StreamStateEnum.ANY)
    await wide.append("b", [OrderNoted(text="x")], expected_version=StreamStateEnum.ANY)
    await wide.append("a", [OrderShipped(order_id="a")], expected_version=0)
    await wide.append("c", [placed("c")], expected_version=StreamStateEnum.ANY)

    store = make_store(db)
    everything = await store.read_all()
    assert [(e.stream_id, e.position) for e in everything] == [
        ("a", 1),
        ("a", 3),
        ("c", 4),
    ]
    assert [e.position for e in await store.read_all(after_position=1)] == [3, 4]
    assert [e.position for e in await store.read_all(after_position=3, limit=5)] == [4]
    assert [e.position for e in await store.read_all(limit=2)] == [1, 3]
    assert await store.read_all(after_position=4) == []
    assert len(await wide.read_all()) == 4


async def test_subscribe_all_yields_backlog_then_new(db: AsyncDatabaseManager) -> None:
    """The polling subscription catches up and then sees a later append."""
    store = make_store(db)
    await store.append("a", [placed("a")], expected_version=StreamStateEnum.ANY)
    seen: list[int] = []
    subscription = store.subscribe_all(poll_interval=0.01)
    seen.append((await anext(subscription)).position)
    await store.append("b", [placed("b")], expected_version=StreamStateEnum.ANY)
    seen.append((await anext(subscription)).position)
    await subscription.aclose()
    assert seen == [1, 2]


async def test_snapshots(db: AsyncDatabaseManager) -> None:
    """The newest snapshot wins; an older save does not move it back."""
    store = make_store(db)
    assert await store.load_snapshot("s") is None
    await store.save_snapshot("s", version=4, state={"n": 4})
    await store.save_snapshot("s", version=9, state={"n": 9})
    await store.save_snapshot("s", version=2, state={"n": 2})
    snapshot = await store.load_snapshot("s")
    assert snapshot is not None
    assert (snapshot.version, snapshot.state) == (9, {"n": 9})


async def test_snapshot_methods_need_a_model(db: AsyncDatabaseManager) -> None:
    """Without ``snapshot_model`` the snapshot methods explain the setup."""
    store = SqlEventStore(
        db,
        registry=EventRegistry(OrderPlaced),
        event_model=EsEventModel,
        stream_model=EsStreamModel,
    )
    with pytest.raises(RuntimeError, match="snapshot_model"):
        await store.load_snapshot("s")
    with pytest.raises(RuntimeError, match="snapshot_model"):
        await store.save_snapshot("s", version=0, state={})


async def test_append_in_caller_session_commits_with_caller(
    db: AsyncDatabaseManager,
) -> None:
    """With ``session=`` the append is part of the caller's transaction."""
    store = make_store(db)
    session = await db.get_session()
    try:
        await store.append(
            "s", [placed()], expected_version=StreamStateEnum.NO_STREAM, session=session
        )
        assert await store.stream_version("s", session=session) == 0
        await session.rollback()
    finally:
        await session.close()
    assert await store.stream_version("s") is None

    session = await db.get_session()
    try:
        await store.append(
            "s", [placed()], expected_version=StreamStateEnum.NO_STREAM, session=session
        )
        await session.commit()
    finally:
        await session.close()
    assert await store.stream_version("s") == 0


async def test_conflict_in_caller_session_keeps_the_transaction(
    db: AsyncDatabaseManager,
) -> None:
    """A refused ``NO_STREAM`` does not poison the caller's transaction."""
    store = make_store(db)
    await store.append("s", [placed()], expected_version=StreamStateEnum.NO_STREAM)
    session = await db.get_session()
    try:
        with pytest.raises(WrongExpectedVersionException):
            await store.append(
                "s",
                [placed()],
                expected_version=StreamStateEnum.NO_STREAM,
                session=session,
            )
        await store.append(
            "t", [placed()], expected_version=StreamStateEnum.NO_STREAM, session=session
        )
        await session.commit()
    finally:
        await session.close()
    assert await store.stream_version("t") == 0


async def test_outbox_rows_commit_with_the_events(db: AsyncDatabaseManager) -> None:
    """Each appended event gets an outbox row the relay publishes."""
    store = make_store(db, outbox=True)
    result = await store.append(
        "order-7",
        [placed("7"), OrderShipped(order_id="7")],
        expected_version=StreamStateEnum.NO_STREAM,
        metadata=EventMetadata(correlation_id="c-7"),
    )
    with pytest.raises(WrongExpectedVersionException):
        await store.append("order-7", [placed("7")], expected_version=0)

    published: list[tuple[str, dict[str, object]]] = []

    async def publish(event: EsOutboxModel) -> None:
        published.append((event.topic, event.payload))

    relay = OutboxRelay(db, model=EsOutboxModel, publish=publish)
    assert await relay.drain_once() == 2
    assert [topic for topic, _ in published] == ["order.placed", "order.shipped"]
    first = published[0][1]
    assert first["event_id"] == str(result.event_ids[0])
    assert first["stream_id"] == "order-7"
    assert first["version"] == 0
    assert first["position"] == 1
    assert first["data"] == {"order_id": "7", "total_cents": 100}
    assert first["metadata"] == {
        "correlation_id": "c-7",
        "causation_id": None,
        "extra": {},
    }
    async with db.get_session_context() as session:
        statuses = (await session.execute(select(EsOutboxModel.status))).scalars().all()
    assert statuses == [OutboxStatusEnum.SENT.value] * 2
