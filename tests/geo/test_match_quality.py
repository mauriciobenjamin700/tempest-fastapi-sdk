"""Tests for the map-matching quality of :class:`TrafficAggregator`.

One class per promise: the numbers are right on a trace with known noise and
a forced gap, nothing per trip or device stays or leaves, the dimensions are
k-anonymous, and the histograms have fixed memory.
"""

from __future__ import annotations

import dataclasses
import json
import random
import sys
from collections import deque
from datetime import UTC, datetime
from typing import Any

import pytest

from tempest_fastapi_sdk.geo import (
    DEFAULT_TRAFFIC_SNAPSHOT_KEY,
    MATCH_QUALITY_REFERENCE_BRAZIL_OSM,
    MATCH_QUALITY_REFERENCE_KUBICKA,
    BoundingBox,
    MatchedFix,
    MatchQualityOptions,
    MatchQualityTracker,
    QualityHistogram,
    QualityTrackPoint,
    RedisTrafficSnapshotStore,
    RoadEdge,
    RoadGraph,
    RoadMatchResult,
    TrafficAggregator,
    TrafficPrivacy,
)
from tempest_fastapi_sdk.geo.match_quality import (
    PARALLEL_MAX_DEG,
    PARALLEL_MIN_OFFSET_M,
    SAMPLING_INTERVAL_BUCKETS_S,
    SNAP_DISTANCE_BUCKETS_M,
    STRAIGHT_HALF_M,
    STRAIGHT_MAX_TURN_DEG,
)
from tests.geo.traffic_helpers import T0, Clock, at, position, straight_street

fakeredis = pytest.importorskip("fakeredis")

PLATE = "plate-ABC1D23"
"""A device id that must never be found anywhere after aggregation."""

HOUR = 3600


def _aggregator(
    clock: Clock,
    graph: RoadGraph | None = None,
    *,
    dimensions: tuple[str, ...] = (),
    k: int = 5,
    **kw: Any,
) -> TrafficAggregator:
    """An aggregator measuring quality in 1-hour windows.

    Args:
        clock (Clock): Server clock.
        graph (RoadGraph | None): The network; a 3 km street by default.
        dimensions (tuple[str, ...]): Declared quality dimensions.
        k (int): ``min_devices``.
        **kw (Any): More :class:`TrafficAggregator` arguments.

    Returns:
        TrafficAggregator: The aggregator.
    """
    return TrafficAggregator(
        graph or straight_street(),
        privacy=TrafficPrivacy(min_devices=k),
        quality=MatchQualityOptions(window_seconds=HOUR, dimensions=dimensions),
        clock=clock,
        **kw,
    )


def _feed(
    agg: TrafficAggregator,
    clock: Clock,
    device: str,
    track: list[tuple[float, float, float]],
    *,
    dimensions: dict[str, str] | None = None,
) -> None:
    """Feed one trip, processing every 20 positions, then end it.

    Args:
        agg (TrafficAggregator): The aggregator.
        clock (Clock): Server clock, kept equal to the position time.
        device (str): Device id.
        track (list[tuple[float, float, float]]): ``(t, x, y)`` per position.
        dimensions (dict[str, str] | None): Quality dimensions of the trip.
    """
    for n, (t, x, y) in enumerate(track):
        clock.now = t
        agg.add_position(device, position(x, y, t), dimensions=dimensions)
        if n % 20 == 19:
            agg.process()
    agg.process()
    agg.end_trip(device)


def _east(
    start: float, seconds: int, *, x0: float = 0.0, y: float = 2.0
) -> list[tuple[float, float, float]]:
    """A 1 Hz drive east at 10 m/s.

    Args:
        start (float): Epoch of the first position.
        seconds (int): Duration.
        x0 (float): Start, metres east.
        y (float): Offset from the street, metres.

    Returns:
        list[tuple[float, float, float]]: The track.
    """
    return [(start + s, x0 + 10.0 * s, y) for s in range(seconds + 1)]


def _close_window(agg: TrafficAggregator, clock: Clock) -> Any:
    """Move past the window and read its published quality.

    Args:
        agg (TrafficAggregator): The aggregator.
        clock (Clock): Server clock.

    Returns:
        Any: The :class:`MatchQualitySnapshotSchema`.
    """
    clock.now = T0 + HOUR + 1
    snapshot = agg.quality_snapshot()
    assert snapshot is not None
    return snapshot


def _two_streets() -> RoadGraph:
    """Two parallel two-way streets 400 m apart, never connected.

    Returns:
        RoadGraph: Street ``a`` along y = 0 and ``b`` along y = 400.
    """
    nodes = [at(x * 100.0, 0.0) for x in range(31)]
    nodes += [at(x * 100.0, 400.0) for x in range(31)]
    edges = [RoadEdge(i, i + 1, True, f"a:{i}") for i in range(30)]
    edges += [RoadEdge(31 + i, 32 + i, True, f"b:{i}") for i in range(30)]
    return RoadGraph(nodes, edges)


def _contains(value: object, needle: bytes, seen: set[int]) -> bool:
    """Whether ``needle`` appears anywhere reachable from ``value``.

    Args:
        value (object): Root object.
        needle (bytes): The bytes to look for.
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
    module = type(value).__module__
    if module.startswith("tempest_fastapi_sdk.geo"):
        if hasattr(value, "__dict__"):
            return _contains(vars(value), needle, seen)
        slots = getattr(type(value), "__slots__", ())
        return any(_contains(getattr(value, s, None), needle, seen) for s in slots)
    return False


class TestKnownNoise:
    """A trace with known noise gives the expected indicators."""

    def test_lateral_gaussian_noise_gives_the_half_normal_quantiles(self) -> None:
        """|N(0, 5 m)| has median 3.37 m and p90 8.22 m; 1 Hz reads 1 s."""
        clock = Clock(T0)
        agg = _aggregator(clock)
        rand = random.Random(667)
        for n in range(10):
            start = T0 + 300 * n
            track = [(start + s, 10.0 * s, rand.gauss(0.0, 5.0)) for s in range(281)]
            _feed(agg, clock, f"bus-{n}", track)

        overall = _close_window(agg, clock).overall

        assert overall is not None and overall.devices == 10
        assert overall.positions > 2000
        assert overall.snap_distance_median_m == pytest.approx(3.37, abs=0.35)
        assert overall.snap_distance_p90_m == pytest.approx(8.22, abs=0.6)
        assert overall.matched_share == 1.0
        assert overall.breaks_per_1000_positions == 0.0
        assert overall.sampling_interval_median_s == 1.0
        assert overall.sampling_interval_p90_s == 1.0
        assert 0 < (overall.inferred_share or 0) < 1

    def test_forced_gap_is_one_break_per_trip(self) -> None:
        """A jump to a street with no path to the first one breaks once."""
        clock = Clock(T0)
        agg = _aggregator(clock, _two_streets())
        for n in range(5):
            start = T0 + 400 * n
            track = _east(start, 120, y=3.0)
            track += [(t + 180, x + 600.0, 403.0) for t, x, _ in _east(start, 120)]
            _feed(agg, clock, f"bus-{n}", track)

        overall = _close_window(agg, clock).overall

        assert overall is not None
        expected = round(1000 * 5 / overall.positions, 3)
        assert overall.breaks_per_1000_positions == expected
        assert overall.sampling_interval_p90_s == 1.0

    def test_no_gap_no_break(self) -> None:
        """The same drive on one street never breaks."""
        clock = Clock(T0)
        agg = _aggregator(clock, _two_streets())
        for n in range(5):
            _feed(agg, clock, f"bus-{n}", _east(T0 + 400 * n, 250, y=3.0))

        overall = _close_window(agg, clock).overall

        assert overall is not None and overall.breaks_per_1000_positions == 0.0
        assert overall.snap_distance_median_m == 3.0
        assert overall.parallel_switches_per_km == 0.0
        assert overall.straight_km > 0

    def test_positions_off_every_road_lower_the_matched_share(self) -> None:
        """Positions past the end of the street (and 200 m on) are unmatched."""
        clock = Clock(T0)
        street = straight_street(1000.0)
        agg = _aggregator(clock, street)
        for n in range(5):
            _feed(agg, clock, f"bus-{n}", _east(T0 + 400 * n, 200))

        overall = _close_window(agg, clock).overall

        assert overall is not None
        assert overall.matched_share is not None and 0 < overall.matched_share < 1


def _matched(graph: RoadGraph, edge_index: int, x: float, y: float) -> MatchedFix:
    """A match of the plane point ``(x, y)`` on an undirected edge.

    Args:
        graph (RoadGraph): The network.
        edge_index (int): The edge.
        x (float): Plane x, metres.
        y (float): Plane y, metres.

    Returns:
        MatchedFix: The match, as ``match_to_roads`` would give it.
    """
    directed = next(d for d, e in enumerate(graph.dir_edge) if e == edge_index)
    c = graph.project_onto(directed, x, y)
    return MatchedFix(
        edge=graph.edges[edge_index],
        edge_index=edge_index,
        reversed=False,
        directed=directed,
        point=graph.from_plane(c.px, c.py),
        distance_m=c.distance,
        offset_m=c.offset,
        inferred=False,
    )


class TestParallelSwitch:
    """A switch to a parallel road on straight track is counted, nothing else."""

    @staticmethod
    def _measure(ids: list[str], ys: list[float], on: list[int]) -> Any:
        """Record one straight stretch whose matches follow ``on``.

        Args:
            ids (list[str]): Ids of three edges: two collinear along y = 0
                (x 0..500 and 500..1000) and one along y = ``ys[2]``.
            ys (list[float]): y of each edge.
            on (list[int]): Edge each of the 21 positions is matched on.

        Returns:
            Any: The published overall cell.
        """
        nodes = [
            at(0.0, ys[0]),
            at(500.0, ys[0]),
            at(1000.0, ys[1]),
            at(0.0, ys[2]),
            at(1000.0, ys[2]),
        ]
        graph = RoadGraph(
            nodes,
            [
                RoadEdge(0, 1, True, ids[0]),
                RoadEdge(1, 2, True, ids[1]),
                RoadEdge(3, 4, True, ids[2]),
            ],
        )
        points = [QualityTrackPoint(50.0 * i, 5.0, float(i)) for i in range(21)]
        result = RoadMatchResult(
            matches=[
                _matched(graph, edge, p.x, p.y)
                for edge, p in zip(on, points, strict=True)
            ],
            routes=[],
            breaks=0,
            dropped=0,
            unmatched=0,
        )
        tracker = MatchQualityTracker(MatchQualityOptions(), min_devices=2)
        for device in (b"a", b"b"):
            tracker.record(T0, device, (), graph, points, result, skip=0)
        snapshot = tracker.snapshot(T0 + 2 * HOUR)
        return snapshot.overall

    def test_jump_to_the_parallel_road_counts_once(self) -> None:
        """y = 0 to y = 20 in the middle of a straight 1 km track: 1 per device.

        Straight positions need 75 m of track on both sides, so of the 1 km
        only x 100..900 is straight: 0.8 km per device.
        """
        overall = self._measure(["s:0", "s:1", "p:0"], [0, 0, 20], [0] * 10 + [2] * 11)

        assert overall.straight_km == pytest.approx(2 * 0.8, abs=1e-6)
        assert overall.parallel_switches_per_km == pytest.approx(2 / 1.6, abs=1e-3)

    def test_collinear_continuation_under_a_new_id_is_not_one(self) -> None:
        """``s-0`` to ``s-1`` along the same line is a new road, not a parallel."""
        overall = self._measure(["s-0", "s-1", "p-0"], [0, 0, 20], [0] * 10 + [1] * 11)

        assert overall.parallel_switches_per_km == 0.0

    def test_segments_of_one_osm_way_are_one_road(self) -> None:
        """``way/7:0`` and ``way/7:1`` are the same road even when offset."""
        overall = self._measure(
            ["way/7:0", "way/7:1", "way/8:0"], [0, 20, 40], [0] * 10 + [1] * 11
        )

        assert overall.parallel_switches_per_km == 0.0


class TestNoTripDataKept:
    """Nothing per trip or device stays, and only aggregates leave."""

    def test_raw_id_is_nowhere_in_memory(self) -> None:
        """While the window is open, the plate exists nowhere."""
        clock = Clock(T0)
        agg = _aggregator(clock, k=2)
        _feed(agg, clock, PLATE, _east(T0, 250))
        _feed(agg, clock, "other", _east(T0 + 300, 250))

        assert not _contains(agg, PLATE.encode(), set())

    def test_closed_window_keeps_no_hash_no_salt_no_counter(self) -> None:
        """After the window closes only the published aggregate remains."""
        clock = Clock(T0)
        agg = _aggregator(clock, k=2)
        _feed(agg, clock, PLATE, _east(T0, 250))
        _feed(agg, clock, "other", _east(T0 + 300, 250))
        tracker = agg._quality
        assert tracker is not None and tracker.open_cells == 1

        _close_window(agg, clock)

        assert tracker._cells == {}
        assert tracker._salt is None
        assert tracker._epoch is None

    def test_a_dimension_value_of_one_device_is_forgotten(self) -> None:
        """An app that sends an id as a dimension: never published, then gone."""
        clock = Clock(T0)
        agg = _aggregator(clock, dimensions=("os",), k=2)
        _feed(agg, clock, "a", _east(T0, 250), dimensions={"os": "android"})
        _feed(agg, clock, "b", _east(T0 + 300, 250), dimensions={"os": "android"})
        _feed(agg, clock, "c", _east(T0 + 600, 250), dimensions={"os": PLATE})

        snapshot = _close_window(agg, clock)

        assert PLATE not in snapshot.model_dump_json()
        assert not _contains(agg, PLATE.encode(), set())

    def test_published_fields_are_aggregates_only(self) -> None:
        """Each published cell has the indicator fields and nothing else."""
        clock = Clock(T0)
        agg = _aggregator(clock, k=2)
        _feed(agg, clock, PLATE, _east(T0, 250))
        _feed(agg, clock, "other", _east(T0 + 300, 250))

        cell = _close_window(agg, clock).overall.model_dump()

        assert set(cell) == {
            "dimensions",
            "devices",
            "positions",
            "straight_km",
            "breaks_per_1000_positions",
            "matched_share",
            "inferred_share",
            "snap_distance_median_m",
            "snap_distance_p90_m",
            "sampling_interval_median_s",
            "sampling_interval_p90_s",
            "parallel_switches_per_km",
        }

    async def test_store_gets_the_aggregate_and_nothing_else(self) -> None:
        """Redis holds one key; its quality part names no device."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        clock = Clock(T0)
        agg = _aggregator(clock, k=2, store=RedisTrafficSnapshotStore(redis))
        _feed(agg, clock, PLATE, _east(T0, 250))
        _feed(agg, clock, "other", _east(T0 + 300, 250))
        clock.now = T0 + HOUR + 1

        await agg.flush()

        assert await redis.keys("*") == [DEFAULT_TRAFFIC_SNAPSHOT_KEY]
        raw = await redis.get(DEFAULT_TRAFFIC_SNAPSHOT_KEY)
        assert PLATE not in raw
        quality = json.loads(raw)["quality"]
        assert set(quality) == {
            "window_start",
            "window_end",
            "min_devices",
            "dimensions",
            "overall",
            "cells",
            "other",
        }
        assert quality["overall"]["devices"] == 2

    def test_traffic_box_answer_leaves_quality_out(self) -> None:
        """``within`` (the traffic route) never carries the quality."""
        clock = Clock(T0)
        agg = _aggregator(clock, k=2)
        snapshot = agg.snapshot()
        assert snapshot.quality is not None

        box = snapshot.within(
            BoundingBox(
                min_latitude=-6, max_latitude=-4, min_longitude=-43, max_longitude=-42
            )
        )

        assert box.quality is None


class TestDimensionKAnonymity:
    """A cell is published only with k devices, and never by difference."""

    @staticmethod
    def _fleet(per_os: dict[str, int]) -> Any:
        """Drive ``per_os[os]`` devices per OS and close the window.

        Args:
            per_os (dict[str, int]): Devices per OS value.

        Returns:
            Any: The published snapshot.
        """
        clock = Clock(T0)
        agg = _aggregator(clock, dimensions=("os",), k=5)
        n = 0
        for os_name, count in per_os.items():
            for _ in range(count):
                _feed(
                    agg,
                    clock,
                    f"bus-{n}",
                    _east(T0 + 60 * n, 250),
                    dimensions={"os": os_name},
                )
                n += 1
        return _close_window(agg, clock)

    def test_both_cells_at_k_are_published(self) -> None:
        """5 Android and 5 iOS devices: two cells, no ``other``."""
        snapshot = self._fleet({"android": 5, "ios": 5})

        assert [(c.dimensions, c.devices) for c in snapshot.cells] == [
            ({"os": "android"}, 5),
            ({"os": "ios"}, 5),
        ]
        assert snapshot.other is None
        assert snapshot.overall.devices == 10
        assert snapshot.overall.positions == sum(c.positions for c in snapshot.cells)

    def test_small_cell_drags_the_smallest_published_one_into_other(self) -> None:
        """2 devices of a third OS cannot be isolated as total minus cells."""
        snapshot = self._fleet({"android": 5, "ios": 6, "harmony": 2})

        assert [c.dimensions for c in snapshot.cells] == [{"os": "ios"}]
        assert snapshot.other is not None and snapshot.other.devices == 7
        assert "harmony" not in snapshot.model_dump_json()
        assert snapshot.overall.positions == (
            snapshot.cells[0].positions + snapshot.other.positions
        )

    def test_no_cell_when_the_rest_cannot_reach_k(self) -> None:
        """5 Android and 2 iOS: only the total, no cell, no ``other``."""
        snapshot = self._fleet({"android": 5, "ios": 2})

        assert snapshot.overall.devices == 7
        assert snapshot.cells == []
        assert snapshot.other is None

    def test_below_k_overall_nothing_is_published(self) -> None:
        """Four devices: the window publishes no indicator at all."""
        snapshot = self._fleet({"android": 4})

        assert snapshot.window_start is not None
        assert snapshot.overall is None and snapshot.cells == []

    def test_one_device_on_many_trips_counts_once(self) -> None:
        """Five trips of one device are one device, under k."""
        clock = Clock(T0)
        agg = _aggregator(clock, k=5)
        for n in range(5):
            _feed(agg, clock, "bus-0", _east(T0 + 400 * n, 250))

        assert _close_window(agg, clock).overall is None

    def test_overflowing_cells_are_never_published_alone(self) -> None:
        """Past ``max_cells`` a new combination goes to the total only."""
        clock = Clock(T0)
        agg = TrafficAggregator(
            straight_street(),
            privacy=TrafficPrivacy(min_devices=2),
            quality=MatchQualityOptions(
                window_seconds=HOUR, dimensions=("os",), max_cells=1
            ),
            clock=clock,
        )
        for n in range(4):
            os_name = "android" if n < 2 else "ios"
            _feed(
                agg,
                clock,
                f"bus-{n}",
                _east(T0 + 300 * n, 250),
                dimensions={"os": os_name},
            )

        snapshot = _close_window(agg, clock)

        assert [c.dimensions for c in snapshot.cells] == [{"os": "android"}]
        assert snapshot.other is not None and snapshot.other.devices == 2
        assert "ios" not in snapshot.model_dump_json()


class TestHistogram:
    """Fixed buckets: memory independent of the count, exact on bounds."""

    def test_memory_does_not_grow_with_the_values(self) -> None:
        """100 000 values later the histogram is the same size."""
        histogram = QualityHistogram(SNAP_DISTANCE_BUCKETS_M)
        size = sys.getsizeof(histogram.counts)
        rand = random.Random(1)
        for _ in range(100_000):
            histogram.add(abs(rand.gauss(0.0, 10.0)))

        assert sys.getsizeof(histogram.counts) == size
        assert len(histogram.counts) == len(SNAP_DISTANCE_BUCKETS_M) + 1
        assert histogram.total == 100_000
        assert not hasattr(histogram, "__dict__")

    def test_nearest_rank_on_bounds_is_exact(self) -> None:
        """1..40 m: median 21 m, p90 37 m (rank floor(q * n))."""
        histogram = QualityHistogram(SNAP_DISTANCE_BUCKETS_M)
        for value in range(1, 41):
            histogram.add(float(value))

        assert histogram.quantile(0.5) == 21.0
        assert histogram.quantile(0.9) == 37.0

    def test_off_bound_value_is_within_one_bucket(self) -> None:
        """3.33 m reads 3.4 m (0.1 m buckets up to 5 m), or itself when largest."""
        histogram = QualityHistogram(SNAP_DISTANCE_BUCKETS_M)
        histogram.add(3.33)

        assert histogram.quantile(0.5) == 3.33
        histogram.add(10.0)
        assert histogram.quantile(0.0) == pytest.approx(3.4)
        assert histogram.quantile(0.9) == 10.0

    def test_overflow_reads_the_largest_value(self) -> None:
        """A value past the last bound answers itself."""
        histogram = QualityHistogram(SAMPLING_INTERVAL_BUCKETS_S)
        histogram.add(900.0)

        assert histogram.quantile(0.5) == 900.0

    def test_empty_is_none_and_bad_input_is_refused(self) -> None:
        """No value, no quantile; bad bounds, q and merges raise."""
        histogram = QualityHistogram((1.0, 2.0))
        histogram.add(float("nan"))

        assert histogram.quantile(0.5) is None
        with pytest.raises(ValueError):
            histogram.quantile(1.5)
        with pytest.raises(ValueError):
            QualityHistogram((2.0, 1.0))
        with pytest.raises(ValueError):
            histogram.merge(QualityHistogram((1.0, 3.0)))


class TestOptions:
    """Bad configuration fails at construction or at the call."""

    def test_dimensions_need_quality(self) -> None:
        """Passing dimensions to an aggregator without ``quality=`` raises."""
        agg = TrafficAggregator(straight_street())
        with pytest.raises(ValueError, match="quality"):
            agg.add_position("a", position(0, 0, T0), dimensions={"os": "ios"})

    def test_undeclared_dimension_and_long_value_raise(self) -> None:
        """Only declared names, and values up to ``max_value_length``."""
        agg = _aggregator(Clock(T0), dimensions=("os",))
        with pytest.raises(ValueError, match="undeclared"):
            agg.add_position("a", position(0, 0, T0), dimensions={"plate": "x"})
        with pytest.raises(ValueError, match="characters"):
            agg.add_position("a", position(0, 0, T0), dimensions={"os": "x" * 33})

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"window_seconds": 0},
            {"max_cells": 0},
            {"max_value_length": 0},
            {"dimensions": ("a", "a")},
            {"dimensions": ("",)},
            {"dimensions": ("a", "b", "c", "d", "e")},
        ],
    )
    def test_bad_options_raise(self, kwargs: dict[str, Any]) -> None:
        """Each invalid option is refused."""
        with pytest.raises(ValueError):
            MatchQualityOptions(**kwargs)

    def test_tracker_needs_k_of_two(self) -> None:
        """One device per cell is not anonymity."""
        with pytest.raises(ValueError):
            MatchQualityTracker(MatchQualityOptions(), min_devices=1)

    def test_missing_dimension_is_unknown(self) -> None:
        """A declared dimension the trip did not give is ``"unknown"``."""
        tracker = MatchQualityTracker(
            MatchQualityOptions(dimensions=("os", "app")), min_devices=2
        )

        assert tracker.cell_of({"os": "ios"}) == ("ios", "unknown")

    def test_without_quality_the_snapshot_has_none(self) -> None:
        """No ``quality=``, no quality anywhere."""
        agg = TrafficAggregator(straight_street())

        assert agg.quality_snapshot() is None
        assert agg.snapshot().quality is None

    def test_empty_before_the_first_window_closes(self) -> None:
        """Nothing is published from an open window."""
        clock = Clock(T0)
        agg = _aggregator(clock, k=2)
        _feed(agg, clock, "a", _east(T0, 250))
        _feed(agg, clock, "b", _east(T0 + 300, 250))

        snapshot = agg.quality_snapshot()

        assert snapshot is not None and snapshot.window_start is None
        assert _close_window(agg, clock).window_start == datetime.fromtimestamp(T0, UTC)


class TestReferences:
    """The reference numbers and ported constants are pinned to their source."""

    def test_brazil_osm_reference(self) -> None:
        """tempest-react-sdk#667, Brazil validation, column 'Brasil (todas)'."""
        ref = MATCH_QUALITY_REFERENCE_BRAZIL_OSM

        assert (
            ref.positions,
            ref.straight_km,
            ref.breaks_per_1000_positions,
            ref.matched_share,
            ref.inferred_share,
            ref.snap_distance_median_m,
            ref.snap_distance_p90_m,
            ref.sampling_interval_median_s,
            ref.sampling_interval_p90_s,
            ref.parallel_switches_per_km,
        ) == (19_806, 119.7, 1.06, 1.0, 0.551, 3.7, 11.6, 1.0, 4.0, 0.109)

    def test_kubicka_reference(self) -> None:
        """tempest-react-sdk#667, phase 2: 48 breaks in 247 251 fixes."""
        ref = MATCH_QUALITY_REFERENCE_KUBICKA

        assert ref.positions == 247_251
        assert ref.breaks_per_1000_positions == round(1000 * 48 / 247_251, 2)
        assert ref.snap_distance_median_m == 3.7

    def test_ported_thresholds(self) -> None:
        """``scripts/bench-br-osm.mjs``: 75 m, 10 deg, 20 deg, 8 m."""
        assert (
            STRAIGHT_HALF_M,
            STRAIGHT_MAX_TURN_DEG,
            PARALLEL_MAX_DEG,
            PARALLEL_MIN_OFFSET_M,
        ) == (75.0, 10.0, 20.0, 8.0)
