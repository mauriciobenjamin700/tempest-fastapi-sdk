"""``KurrentDBEventStore`` against a real KurrentDB node in a container.

Runs under ``make test-docker``. ``TEST_KURRENTDB_URI`` points it at a node
you already run; otherwise one ``kurrentplatform/kurrentdb`` container
(insecure, single node) is started for the module.
``test_kurrentdb_matrix.py`` reruns this module under the declared client
floor and under the locked version, passing ``TEMPEST_KURRENTDB_CONTAINER``
and ``TEMPEST_KURRENTDB_PORT`` so each run owns its container.

Every test writes to streams with a fresh suffix and reads ``$all`` from a
position it recorded first, so tests sharing the node do not see each
other's events.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from tempest_fastapi_sdk import WrongExpectedVersionException
from tempest_fastapi_sdk.eventsourcing import (
    SNAPSHOT_EVENT_TYPE,
    AggregateRepository,
    EventMetadata,
    EventRegistry,
    EventStore,
    InMemoryCheckpointStore,
    KurrentDBEventStore,
    ProjectionRunner,
    RecordedEvent,
    StreamStateEnum,
    UnknownEventTypeError,
)
from tests.eventsourcing.support import (
    Order,
    OrderNoted,
    OrderPlaced,
    OrderShipped,
    make_registry,
)

pytestmark = pytest.mark.docker

KURRENTDB_IMAGE: str = "kurrentplatform/kurrentdb:26.2.3"
CONTAINER: str = os.environ.get(
    "TEMPEST_KURRENTDB_CONTAINER", "tempest-eventsourcing-kdb"
)
PORT: int = int(os.environ.get("TEMPEST_KURRENTDB_PORT", "52117"))
WRITERS: int = 8
ROUNDS: int = 10


@pytest.fixture(scope="module")
def kurrentdb_uri() -> Iterator[str]:
    """Yield a connection string: ``TEST_KURRENTDB_URI`` or a fresh container.

    Yields:
        str: A ``kurrentdb://`` URI.
    """
    configured = os.environ.get("TEST_KURRENTDB_URI")
    if configured:
        yield configured
        return
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            "KURRENTDB_INSECURE=true",
            "-e",
            "KURRENTDB_MEM_DB=true",
            "-p",
            f"{PORT}:2113",
            KURRENTDB_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {KURRENTDB_IMAGE}: {started.stderr.strip()}")
    try:
        import httpx

        for _ in range(90):
            try:
                if httpx.get(
                    f"http://127.0.0.1:{PORT}/health/live", timeout=1
                ).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            pytest.fail("KurrentDB never became live")
        yield f"kurrentdb://127.0.0.1:{PORT}?Tls=false"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest_asyncio.fixture
async def store(kurrentdb_uri: str) -> AsyncIterator[KurrentDBEventStore]:
    """Yield a store over the node, closed afterwards.

    Args:
        kurrentdb_uri (str): The connection string.

    Yields:
        KurrentDBEventStore: The store.
    """
    built = KurrentDBEventStore.from_uri(kurrentdb_uri, registry=make_registry())
    try:
        yield built
    finally:
        await built.close()


def stream(prefix: str = "order") -> str:
    """Return a fresh stream name.

    Args:
        prefix (str): The category.

    Returns:
        str: ``prefix-<uuid>``.
    """
    return f"{prefix}-{uuid.uuid4().hex}"


def placed(total: int = 1, order_id: str = "1") -> OrderPlaced:
    """Build an ``OrderPlaced``.

    Args:
        total (int): The total.
        order_id (str): The order id.

    Returns:
        OrderPlaced: The event.
    """
    return OrderPlaced(order_id=order_id, total_cents=total)


async def head_position(store: KurrentDBEventStore) -> int:
    """Return the commit position of the newest event in the node.

    Args:
        store (KurrentDBEventStore): The store.

    Returns:
        int: The position.
    """
    return await store.client.get_commit_position(filter_exclude=())


async def test_store_satisfies_protocol(store: KurrentDBEventStore) -> None:
    """The KurrentDB store is an ``EventStore``."""
    assert isinstance(store, EventStore)


async def test_append_and_read_round_trip(store: KurrentDBEventStore) -> None:
    """Typed events, 0-based versions, increasing positions, metadata kept."""
    name = stream()
    result = await store.append(
        name,
        [placed(500), OrderShipped(order_id="1", carrier="ups")],
        expected_version=StreamStateEnum.NO_STREAM,
        metadata=EventMetadata(
            correlation_id="corr", causation_id="cmd", extra={"u": 1}
        ),
    )
    events = await store.read_stream(name)
    assert [e.version for e in events] == [0, 1]
    assert events[0].position < events[1].position == result.position
    assert result.version == 1
    assert [e.event_id for e in events] == list(result.event_ids)
    assert events[0].event == placed(500)
    assert isinstance(events[1].event, OrderShipped)
    assert events[0].metadata == EventMetadata(
        correlation_id="corr", causation_id="cmd", extra={"u": 1}
    )
    assert events[0].recorded_at is not None
    assert await store.stream_version(name) == 1
    assert await store.stream_version(stream("missing")) is None
    assert await store.read_stream(stream("missing")) == []


async def test_metadata_uses_kurrentdb_correlation_keys(
    store: KurrentDBEventStore,
) -> None:
    """The raw metadata carries ``$correlationId`` / ``$causationId``."""
    name = stream()
    await store.append(
        name,
        [placed()],
        expected_version=StreamStateEnum.ANY,
        metadata=EventMetadata(correlation_id="c-1", causation_id="m-1"),
    )
    raw = (await store.client.get_stream(name))[0]
    assert json.loads(raw.metadata) == {"$correlationId": "c-1", "$causationId": "m-1"}


async def test_read_stream_from_version_and_backwards(
    store: KurrentDBEventStore,
) -> None:
    """``from_version`` is inclusive both ways; ``limit`` caps the read."""
    name = stream()
    await store.append(
        name, [placed(n) for n in range(5)], expected_version=StreamStateEnum.NO_STREAM
    )
    assert [e.version for e in await store.read_stream(name, from_version=2)] == [
        2,
        3,
        4,
    ]
    assert [e.version for e in await store.read_stream(name, backwards=True)] == [
        4,
        3,
        2,
        1,
        0,
    ]
    assert [
        e.version
        for e in await store.read_stream(name, from_version=3, backwards=True, limit=2)
    ] == [3, 2]
    assert [e.version for e in await store.read_stream(name, limit=2)] == [0, 1]
    assert await store.read_stream(name, limit=0) == []


class TestExpectedVersion:
    """The client's ``WrongCurrentVersionError`` becomes our exception."""

    async def test_exact_version(self, store: KurrentDBEventStore) -> None:
        """A stale exact version is refused with the actual version."""
        name = stream()
        await store.append(name, [placed()], expected_version=StreamStateEnum.NO_STREAM)
        assert (await store.append(name, [placed()], expected_version=0)).version == 1
        with pytest.raises(WrongExpectedVersionException) as caught:
            await store.append(name, [placed()], expected_version=0)
        assert (caught.value.stream_id, caught.value.expected_version) == (name, 0)
        assert caught.value.actual_version == 1

    async def test_no_stream_and_missing(self, store: KurrentDBEventStore) -> None:
        """``NO_STREAM`` on an existing stream and a version on a missing one."""
        name = stream()
        await store.append(name, [placed()], expected_version=StreamStateEnum.NO_STREAM)
        with pytest.raises(WrongExpectedVersionException) as existing:
            await store.append(
                name, [placed()], expected_version=StreamStateEnum.NO_STREAM
            )
        assert existing.value.actual_version == 0
        with pytest.raises(WrongExpectedVersionException) as missing:
            await store.append(stream(), [placed()], expected_version=0)
        assert missing.value.actual_version is None

    async def test_exists_and_any(self, store: KurrentDBEventStore) -> None:
        """``EXISTS`` refuses a missing stream; both report the landed version."""
        name = stream()
        with pytest.raises(WrongExpectedVersionException):
            await store.append(
                name, [placed()], expected_version=StreamStateEnum.EXISTS
            )
        first = await store.append(
            name, [placed()], expected_version=StreamStateEnum.ANY
        )
        second = await store.append(
            name, [placed(), placed()], expected_version=StreamStateEnum.EXISTS
        )
        third = await store.append(
            name, [placed()], expected_version=StreamStateEnum.ANY
        )
        assert (first.version, second.version, third.version) == (0, 2, 3)

    async def test_input_validation(self, store: KurrentDBEventStore) -> None:
        """Empty appends, negative versions and unregistered types are refused."""
        with pytest.raises(ValueError):
            await store.append(stream(), [], expected_version=StreamStateEnum.ANY)
        with pytest.raises(ValueError):
            await store.append(stream(), [placed()], expected_version=-1)
        with pytest.raises(UnknownEventTypeError):
            await store.append(
                stream(), [OrderNoted(text="x")], expected_version=StreamStateEnum.ANY
            )


async def test_racing_writers_one_wins_per_round(store: KurrentDBEventStore) -> None:
    """Concurrent appends with one expectation: exactly one lands per round."""
    name = stream()
    await store.append(name, [placed(0)], expected_version=StreamStateEnum.NO_STREAM)
    for round_number in range(ROUNDS):

        async def attempt(writer: int, expected: int = round_number) -> bool:
            try:
                await store.append(name, [placed(writer)], expected_version=expected)
            except WrongExpectedVersionException:
                return False
            return True

        outcomes = await asyncio.gather(*(attempt(w) for w in range(WRITERS)))
        assert sum(outcomes) == 1, f"round {round_number}: {outcomes}"
    assert await store.stream_version(name) == ROUNDS


async def test_read_all_is_exclusive_and_filtered(store: KurrentDBEventStore) -> None:
    """``after_position`` is exclusive; only exactly-registered types come back."""
    from kurrentdbclient import NewEvent, StreamState

    start = await head_position(store)
    a, b = stream(), stream()
    first = await store.append(a, [placed(1)], expected_version=StreamStateEnum.ANY)
    await store.client.append_to_stream(
        b,
        events=[
            NewEvent(type="order.placed.v2", data=b"{}"),
            NewEvent(type="xorder.placed", data=b"{}"),
            NewEvent(type="order.shipped.extra", data=b"{}"),
        ],
        current_version=StreamState.ANY,
    )
    second = await store.append(a, [OrderShipped(order_id="1")], expected_version=0)
    third = await store.append(b, [placed(3)], expected_version=StreamStateEnum.ANY)

    ours = {a, b}
    log = [e for e in await store.read_all(after_position=start) if e.stream_id in ours]
    assert [e.position for e in log] == [
        first.position,
        second.position,
        third.position,
    ]
    assert {e.event_type for e in log} == {"order.placed", "order.shipped"}
    after_first = await store.read_all(after_position=first.position, limit=2)
    assert [e.position for e in after_first] == [second.position, third.position]
    assert await store.read_all(after_position=third.position) == []


async def test_subscribe_all_is_exclusive_and_live(store: KurrentDBEventStore) -> None:
    """The catch-up subscription starts after the position and sees new appends."""
    name = stream()
    first = await store.append(name, [placed(1)], expected_version=StreamStateEnum.ANY)
    subscription = store.subscribe_all(after_position=first.position)
    later = await store.append(name, [placed(2)], expected_version=0)

    async def next_ours() -> RecordedEvent:
        async for event in subscription:
            if event.stream_id == name:
                return event
        raise AssertionError("subscription ended")

    received = await asyncio.wait_for(next_ours(), timeout=15)
    assert received.position == later.position
    assert received.version == 1
    await subscription.aclose()


async def test_snapshots_keep_only_the_newest(store: KurrentDBEventStore) -> None:
    """Older snapshots are ignored and the snapshot stream holds one event."""
    name = stream()
    assert await store.load_snapshot(name) is None
    await store.save_snapshot(name, version=4, state={"n": 4})
    await store.save_snapshot(name, version=9, state={"n": 9})
    await store.save_snapshot(name, version=2, state={"n": 2})
    snapshot = await store.load_snapshot(name)
    assert snapshot is not None
    assert (snapshot.version, snapshot.state) == (9, {"n": 9})
    raw = await store.client.get_stream(f"snapshot-{name}")
    assert [e.type for e in raw] == [SNAPSHOT_EVENT_TYPE]


async def test_unregistered_type_in_stream_raises(store: KurrentDBEventStore) -> None:
    """A stream holding a type the registry lacks fails loudly on read."""
    name = stream()
    wide = KurrentDBEventStore(
        store.client, registry=EventRegistry(OrderPlaced, OrderShipped, OrderNoted)
    )
    await wide.append(
        name, [OrderNoted(text="x")], expected_version=StreamStateEnum.ANY
    )
    with pytest.raises(UnknownEventTypeError):
        await store.read_stream(name)


async def test_aggregate_and_projection(store: KurrentDBEventStore) -> None:
    """The backend-agnostic helpers run unchanged on KurrentDB."""
    start = await head_position(store)
    orders = AggregateRepository(store, Order, stream_prefix="order-", snapshot_every=2)
    order_id = uuid.uuid4().hex
    order = Order(order_id)
    order.place(70)
    order.ship()
    await orders.save(order)
    loaded = await orders.load(order_id)
    assert loaded is not None
    assert (loaded.total_cents, loaded.shipped, loaded.version, loaded.applied) == (
        70,
        True,
        1,
        0,
    )

    seen: list[str] = []

    async def handler(event: RecordedEvent) -> None:
        if event.stream_id == f"order-{order_id}":
            seen.append(event.event_type)

    checkpoints = InMemoryCheckpointStore()
    await checkpoints.save("p", start)
    runner = ProjectionRunner(store, name="p", handler=handler, checkpoints=checkpoints)
    while await runner.run_once():
        pass
    assert seen == ["order.placed", "order.shipped"]
