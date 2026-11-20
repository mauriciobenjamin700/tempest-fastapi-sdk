"""Tests for :class:`TrafficAggregator`: one class per privacy rule, then speed."""

from __future__ import annotations

import dataclasses
import json
from collections import deque
from typing import Any

import pytest

from tempest_fastapi_sdk.geo import (
    DEFAULT_TRAFFIC_SNAPSHOT_KEY,
    InMemoryTrafficSnapshotStore,
    RedisTrafficSnapshotStore,
    TrafficAggregator,
    TrafficPrivacy,
    TrafficSnapshotSchema,
)
from tests.geo.test_live import _RecordingBroker
from tests.geo.traffic_helpers import T0, Clock, drive, east, position, straight_street

fakeredis = pytest.importorskip("fakeredis")

PLATE = "plate-ABC1D23"
"""A device id that must never be found anywhere after aggregation."""


def _aggregator(clock: Clock, **privacy: Any) -> TrafficAggregator:
    """An aggregator over a 3 km street.

    Args:
        clock (Clock): Server clock.
        **privacy (Any): :class:`TrafficPrivacy` overrides.

    Returns:
        TrafficAggregator: The aggregator.
    """
    return TrafficAggregator(
        straight_street(), privacy=TrafficPrivacy(**privacy), clock=clock
    )


def _published(aggregator: TrafficAggregator) -> dict[str, int]:
    """Published edge id -> devices.

    Args:
        aggregator (TrafficAggregator): The aggregator.

    Returns:
        dict[str, int]: What the snapshot carries.
    """
    return {e.edge_id: e.devices for e in aggregator.snapshot().edges}


def _contains(value: object, needle: bytes, seen: set[int]) -> bool:
    """Whether ``needle`` appears anywhere reachable from ``value``.

    Args:
        value (object): Root object.
        needle (bytes): The bytes to look for (UTF-8 of the raw id).
        seen (set[int]): Ids already visited.

    Returns:
        bool: Found.
    """
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, str):
        return needle in value.encode()
    if isinstance(value, bytes | bytearray):
        return needle in value
    if isinstance(value, dict):
        return any(
            _contains(k, needle, seen) or _contains(v, needle, seen)
            for k, v in value.items()
        )
    if isinstance(value, list | tuple | set | frozenset | deque):
        return any(_contains(v, needle, seen) for v in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return any(
            _contains(getattr(value, f.name), needle, seen)
            for f in dataclasses.fields(value)
        )
    if hasattr(value, "__dict__") and type(value).__module__.startswith(
        "tempest_fastapi_sdk.geo.traffic"
    ):
        return _contains(vars(value), needle, seen)
    return False


class TestTrimsStartAndEnd:
    """Nothing within ``trim_radius_m`` of a trip's start or end is aggregated."""

    def test_edges_near_origin_and_destination_never_published(self) -> None:
        """Two devices, same 1.5 km trip: the first and last 200 m stay dark."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=2)
        for n in range(2):
            drive(agg, clock, f"bus-{n}", east(0.0), 150, start=T0 + n * 5)

        published = _published(agg)

        assert {"s-0", "s-1", "s-13", "s-14"}.isdisjoint(published)
        assert {f"s-{i}" for i in range(3, 12)} <= set(published)

    def test_position_near_origin_is_refused(self) -> None:
        """A position within the radius of the trip start is not kept."""
        clock = Clock()
        agg = _aggregator(clock)

        assert agg.add_position(PLATE, position(0, 0, T0)) is False
        assert agg.add_position(PLATE, position(150, 0, T0 + 15)) is False
        assert agg.add_position(PLATE, position(250, 0, T0 + 25)) is True

    def test_coming_back_near_the_start_stays_dark(self) -> None:
        """Returning home: the stretch within the radius of the origin is cut."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=2)

        def there_and_back(s: float) -> tuple[float, float]:
            """East for 100 s, then back west.

            Args:
                s (float): Seconds.

            Returns:
                tuple[float, float]: Position.
            """
            return (10.0 * s if s <= 100 else 2000.0 - 10.0 * s, 2.0)

        for n in range(2):
            drive(agg, clock, f"car-{n}", there_and_back, 195, start=T0 + n * 3)

        published = _published(agg)
        assert not any(e in published for e in ("s-0", "s-0~", "s-1", "s-1~"))
        assert "s-5" in published

    def test_pending_tail_is_dropped_when_the_trip_goes_silent(self) -> None:
        """A trip that stops reporting is ended by ``process`` and its tail lost."""
        clock = Clock()
        agg = _aggregator(clock, trip_gap_seconds=60)
        drive(agg, clock, PLATE, east(0.0), 80, start=T0, end_trip=False)
        assert agg.active_trips == 1

        clock.now += 61
        agg.process()

        assert agg.active_trips == 0


class TestKAnonymity:
    """An edge is published only with ``min_devices`` distinct devices."""

    def test_below_k_nothing_is_published(self) -> None:
        """k - 1 devices over the same street publish nothing."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=3)
        for n in range(2):
            drive(agg, clock, f"bus-{n}", east(0.0), 150, start=T0 + n * 5)

        assert agg.snapshot().edges == []

    def test_at_k_the_edge_is_published_with_its_count(self) -> None:
        """The k-th device publishes, and ``devices`` says how many."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=3)
        for n in range(3):
            drive(agg, clock, f"bus-{n}", east(0.0), 150, start=T0 + n * 5)

        published = _published(agg)

        assert published["s-6"] == 3

    def test_one_device_twice_counts_once(self) -> None:
        """Two trips of one device in the window are one device."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=2)
        drive(agg, clock, PLATE, east(0.0), 150, start=T0)
        drive(agg, clock, PLATE, east(0.0), 150, start=T0 + 160)

        assert agg.snapshot().edges == []

    def test_epoch_boundary_never_double_counts(self) -> None:
        """The same device on both sides of a salt rotation is not two.

        Hashes of two epochs never match, so the window takes the largest
        per-epoch count instead of adding them.
        """
        clock = Clock(T0 - T0 % 300)
        agg = _aggregator(clock, min_devices=2, window_seconds=300)
        epoch_start = clock.now
        drive(agg, clock, PLATE, east(0.0), 150, start=epoch_start + 100)
        drive(agg, clock, PLATE, east(0.0), 150, start=epoch_start + 300)

        assert agg.snapshot().edges == []

    def test_k_below_two_is_refused(self) -> None:
        """``min_devices=1`` is a trace, not traffic."""
        with pytest.raises(ValueError, match="min_devices"):
            TrafficPrivacy(min_devices=1)

    def test_old_samples_leave_the_window(self) -> None:
        """Past the window, a published edge disappears."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=2, window_seconds=300)
        for n in range(2):
            drive(agg, clock, f"bus-{n}", east(0.0), 150, start=T0 + n * 5)
        assert agg.snapshot().edges

        clock.now += 301

        assert agg.snapshot().edges == []


class TestNoIdentifierKept:
    """The raw id never stays; samples carry rotating per-edge hashes."""

    def test_raw_id_is_nowhere_in_memory(self) -> None:
        """After driving, the aggregator holds no copy of the plate."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=2)
        drive(agg, clock, PLATE, east(0.0), 150, start=T0, end_trip=False)
        drive(agg, clock, "other", east(0.0), 150, start=T0 + 2)

        assert not _contains(agg, PLATE.encode(), set())

    def test_hashes_differ_per_edge_and_per_window(self) -> None:
        """One device's samples on two edges, or two windows, do not link."""
        clock = Clock(T0 - T0 % 300)
        agg = _aggregator(clock, window_seconds=300)
        epoch_start = clock.now
        drive(agg, clock, PLATE, east(0.0), 150, start=epoch_start + 140)
        drive(agg, clock, PLATE, east(0.0), 150, start=epoch_start + 300)

        tokens = [
            (directed, sample.epoch, sample.token)
            for directed, queue in agg._samples.items()
            for sample in queue
        ]
        by_token: dict[bytes, set[tuple[int, int]]] = {}
        for directed, epoch, token in tokens:
            by_token.setdefault(token, set()).add((directed, epoch))

        assert len({epoch for _, epoch, _ in tokens}) == 2
        assert all(len(places) == 1 for places in by_token.values())

    def test_salts_are_forgotten_after_the_window(self) -> None:
        """A salt no sample in the window can use is deleted."""
        clock = Clock(T0 - T0 % 300)
        agg = _aggregator(clock, window_seconds=300)
        drive(agg, clock, PLATE, east(0.0), 150, start=clock.now + 10)
        first = set(agg._salts)

        clock.now += 900
        agg.process()

        assert first and first.isdisjoint(agg._salts)

    async def test_store_gets_the_snapshot_and_nothing_else(self) -> None:
        """Redis ends with one key: the k-anonymous snapshot, no id, no trace.

        ``quality`` joined the snapshot with the map-matching quality
        (aggregate only, tested in ``test_match_quality.py``); without
        ``quality=`` it is ``None``.
        """
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        clock = Clock()
        agg = TrafficAggregator(
            straight_street(),
            privacy=TrafficPrivacy(min_devices=2),
            store=RedisTrafficSnapshotStore(redis),
            clock=clock,
        )
        drive(agg, clock, PLATE, east(0.0), 150, start=T0, end_trip=False)
        drive(agg, clock, "other", east(0.0), 150, start=T0 + 2)

        await agg.flush()

        assert await redis.keys("*") == [DEFAULT_TRAFFIC_SNAPSHOT_KEY]
        raw = await redis.get(DEFAULT_TRAFFIC_SNAPSHOT_KEY)
        assert PLATE not in raw
        assert set(json.loads(raw)) == {
            "generated_at",
            "window_seconds",
            "min_devices",
            "edges",
            "quality",
        }
        assert json.loads(raw)["quality"] is None
        loaded = await RedisTrafficSnapshotStore(redis).load()
        assert loaded is not None and loaded.edges


class TestCleaning:
    """Positions that would poison the speed are dropped."""

    def test_bad_accuracy_is_dropped(self) -> None:
        """``accuracy_m`` above ``max_accuracy_m`` is refused."""
        clock = Clock()
        agg = _aggregator(clock)
        agg.add_position(PLATE, position(0, 0, T0))

        assert agg.add_position(PLATE, position(400, 0, T0 + 40, 80.0)) is False

    def test_teleport_is_dropped(self) -> None:
        """A 1 km jump in one second is an outlier."""
        clock = Clock()
        agg = _aggregator(clock)
        agg.add_position(PLATE, position(0, 0, T0))
        agg.add_position(PLATE, position(300, 0, T0 + 30))

        assert agg.add_position(PLATE, position(1300, 0, T0 + 31)) is False

    def test_old_or_repeated_time_is_dropped(self) -> None:
        """A position not newer than the previous one is refused."""
        clock = Clock()
        agg = _aggregator(clock)
        agg.add_position(PLATE, position(0, 0, T0))
        agg.add_position(PLATE, position(300, 0, T0 + 30))

        assert agg.add_position(PLATE, position(310, 0, T0 + 30)) is False


class TestSpeed:
    """What gets published is the median speed of the passes."""

    def test_median_speed_of_three_devices(self) -> None:
        """8, 10 and 15 m/s on the same street publish the median, 36 km/h."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=3)
        for n, speed in enumerate((8.0, 10.0, 15.0)):
            drive(agg, clock, f"bus-{n}", east(0.0, speed), 120, start=T0 + n * 3)

        edge = next(e for e in agg.snapshot().edges if e.edge_id == "s-6")

        assert edge.speed_kmh == pytest.approx(36.0, abs=1.5)

    def test_direction_is_kept_apart(self) -> None:
        """Westbound passes are reported on the reversed edge id ``s-<n>~``."""
        clock = Clock()
        agg = _aggregator(clock, min_devices=2)
        for n in range(2):
            drive(agg, clock, f"bus-{n}", east(2900.0, -10.0), 150, start=T0 + n * 3)

        published = _published(agg)

        assert "s-20~" in published
        assert "s-20" not in published


class TestPublishing:
    """``flush`` saves the snapshot and tells only the audience."""

    def test_broker_needs_audience(self) -> None:
        """A broker without an audience is refused, as in the live store."""
        with pytest.raises(ValueError, match="audience"):
            TrafficAggregator(straight_street(), broker=_RecordingBroker())

    async def test_flush_announces_to_the_audience(self) -> None:
        """Each recipient gets one ``traffic_updated`` with the edge count."""
        broker = _RecordingBroker()

        async def audience() -> list[str]:
            """Two viewers, one repeated.

            Returns:
                list[str]: Channels.
            """
            return ["ops", "ops", "dispatch"]

        clock = Clock()
        store = InMemoryTrafficSnapshotStore()
        agg = TrafficAggregator(
            straight_street(),
            privacy=TrafficPrivacy(min_devices=2),
            store=store,
            broker=broker,
            audience=audience,
            clock=clock,
        )
        for n in range(2):
            drive(agg, clock, f"bus-{n}", east(0.0), 150, start=T0 + n * 5)

        snapshot = await agg.flush()

        assert [(c, e) for c, _, e in broker.sent] == [
            ("ops", "traffic_updated"),
            ("dispatch", "traffic_updated"),
        ]
        assert broker.sent[0][1]["edges"] == len(snapshot.edges) > 0
        assert await store.load() == snapshot

    async def test_in_memory_store_expires(self) -> None:
        """The in-memory store forgets the snapshot after its TTL."""
        now = [0.0]
        store = InMemoryTrafficSnapshotStore(clock=lambda: now[0])
        await store.save(TrafficSnapshotSchema(), 10)
        assert await store.load() is not None

        now[0] = 10.0

        assert await store.load() is None

    async def test_undecodable_value_reads_as_absent(self) -> None:
        """A corrupt Redis value is ignored, not raised."""
        redis = fakeredis.aioredis.FakeRedis()
        await redis.set(DEFAULT_TRAFFIC_SNAPSHOT_KEY, b"{not json")

        assert await RedisTrafficSnapshotStore(redis).load() is None
