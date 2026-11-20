"""Tests for :class:`OffRoadDensity` and :func:`infer_road_candidates`."""

from __future__ import annotations

import json

import pytest

from tempest_fastapi_sdk.geo import (
    OffRoadDensity,
    RoadEdge,
    RoadGraph,
    TrafficAggregator,
    TrafficPrivacy,
    infer_road_candidates,
    road_candidates_geojson,
)
from tests.geo.traffic_helpers import BASE, T0, Clock, at, drive

DAY = 86_400


def _grid(*, with_cross_street: bool) -> RoadGraph:
    """Two east-west streets 400 m apart, joined at both ends.

    The middle north-south street at x = 300 m is the one the fleet reveals;
    ``with_cross_street`` puts it in the graph.

    Args:
        with_cross_street (bool): Include the x = 300 m street.

    Returns:
        RoadGraph: The graph.
    """
    nodes = [at(0, 0), at(300, 0), at(600, 0), at(0, 400), at(300, 400), at(600, 400)]
    edges = [
        RoadEdge(0, 1, True, "south-w"),
        RoadEdge(1, 2, True, "south-e"),
        RoadEdge(3, 4, True, "north-w"),
        RoadEdge(4, 5, True, "north-e"),
        RoadEdge(0, 3, True, "west"),
        RoadEdge(2, 5, True, "east"),
    ]
    if with_cross_street:
        edges.append(RoadEdge(1, 4, True, "cross"))
    return RoadGraph(nodes, edges)


def _route(s: float) -> tuple[float, float]:
    """West end -> x = 300 m -> north through the unmapped street -> east.

    Args:
        s (float): Seconds at 10 m/s.

    Returns:
        tuple[float, float]: Position, metres.
    """
    d = 10.0 * s
    if d <= 300:
        return d, 0.0
    if d <= 700:
        return 300.0, d - 300
    return 300.0 + d - 700, 400.0


def _feed(graph: RoadGraph, devices: int, *, min_devices: int = 5) -> OffRoadDensity:
    """Run ``devices`` trips through the middle street on day 0, then roll.

    Args:
        graph (RoadGraph): What the aggregator matches against.
        devices (int): Trips (one per device).
        min_devices (int): k of the density.

    Returns:
        OffRoadDensity: The committed density.
    """
    clock = Clock()
    density = OffRoadDensity(BASE, min_devices=min_devices)
    agg = TrafficAggregator(
        graph, privacy=TrafficPrivacy(window_seconds=DAY), density=density, clock=clock
    )
    for n in range(devices):
        drive(agg, clock, f"bus-{n}", _route, 100, start=T0 + n * 60)
    density.roll(T0 + 2 * DAY)
    return density


class TestDensityPrivacy:
    """Only k-anonymous blocks are committed; hashes die with the epoch."""

    def test_below_k_nothing_is_committed(self) -> None:
        """Four devices with k = 5 leave no cell."""
        density = _feed(_grid(with_cross_street=False), 4)

        assert density.cells == {}

    def test_at_k_cells_are_committed_and_hashes_dropped(self) -> None:
        """Five devices commit cells; the open epoch and its salt are gone."""
        density = _feed(_grid(with_cross_street=False), 5)

        assert density.cells
        assert max(devices for _, devices in density.cells.values()) == 5
        assert density._open == {}
        assert density._salts == {}

    def test_persisted_form_has_counts_only(self) -> None:
        """``to_dict`` carries grid parameters and counts, and round-trips."""
        density = _feed(_grid(with_cross_street=False), 5)

        raw = density.to_dict()
        restored = OffRoadDensity.from_dict(json.loads(json.dumps(raw)))

        assert set(raw) == {
            "reference",
            "cell_m",
            "off_road_m",
            "min_devices",
            "block_cells",
            "epoch_seconds",
            "cells",
        }
        assert all(len(row) == 4 for row in raw["cells"])
        assert restored.cells == density.cells

    def test_late_position_for_a_closed_epoch_is_ignored(self) -> None:
        """Once an epoch is rolled, nothing more lands in it."""
        density = OffRoadDensity(BASE)
        density.roll(T0 + DAY)

        density.add(b"k", at(0, 0), T0)

        assert density._open == {}

    def test_k_below_two_is_refused(self) -> None:
        """A density of one device is a trace."""
        with pytest.raises(ValueError, match="min_devices"):
            OffRoadDensity(BASE, min_devices=1)


class TestInference:
    """The unmapped street comes back as a candidate; mapped ones do not."""

    def test_removed_street_is_recovered(self) -> None:
        """Twenty trips: one candidate along x = 300 m, joined to both streets."""
        graph = _grid(with_cross_street=False)
        density = _feed(graph, 20)

        candidates = infer_road_candidates(density, graph)

        assert len(candidates) == 1
        candidate = candidates[0]
        xs = [
            graph.to_plane(p)[0] - graph.to_plane(at(300, 0))[0]
            for p in candidate.coordinates
        ]
        assert max(abs(x) for x in xs) < 10
        assert candidate.length_m == pytest.approx(400, abs=25)
        assert candidate.status == "candidate"
        assert candidate.devices >= 5

    def test_mapped_street_yields_nothing(self) -> None:
        """With the street in the graph its positions are on-road."""
        graph = _grid(with_cross_street=True)
        density = _feed(graph, 20)

        assert infer_road_candidates(density, graph) == []

    def test_geojson_for_review(self) -> None:
        """One LineString feature per candidate, lon/lat order."""
        graph = _grid(with_cross_street=False)
        candidates = infer_road_candidates(_feed(graph, 20), graph)

        doc = road_candidates_geojson(candidates)

        feature = doc["features"][0]
        assert feature["geometry"]["type"] == "LineString"
        first = candidates[0].coordinates[0]
        assert feature["geometry"]["coordinates"][0] == [
            first.longitude,
            first.latitude,
        ]
        assert feature["properties"]["status"] == "candidate"
