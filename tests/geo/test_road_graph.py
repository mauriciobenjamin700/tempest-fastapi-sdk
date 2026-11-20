"""Tests for :class:`RoadGraph`, :class:`RoadEdge` and :func:`match_to_roads`."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.geo import (
    BoundingBox,
    RoadEdge,
    RoadGraph,
    RoadMatchOptions,
    match_to_roads,
)
from tests.geo.traffic_helpers import at


class TestRoadGraph:
    """Building, serializing and querying the network."""

    def test_round_trips_the_react_sdk_shape(self) -> None:
        """``to_dict``/``from_dict`` keep ``from``/``to``/``twoWay``/id/extras."""
        raw = {
            "nodes": [
                {"latitude": -5.09, "longitude": -42.8},
                {"latitude": -5.09, "longitude": -42.799},
            ],
            "edges": [
                {"from": 0, "to": 1, "twoWay": True, "id": "way/1:0", "name": "Rua A"}
            ],
            "twoWay": False,
        }

        graph = RoadGraph.from_dict(raw)

        assert graph.to_dict() == raw
        assert graph.edges[0].data == {"name": "Rua A"}
        assert graph.directed_edge_count == 2

    def test_edge_to_unknown_node_is_refused(self) -> None:
        """An index past the node list raises instead of corrupting the index."""
        with pytest.raises(ValueError, match="no node 5"):
            RoadGraph([at(0, 0), at(10, 0)], [RoadEdge(start=0, end=5)])

    def test_edge_without_integer_ends_is_refused(self) -> None:
        """``from``/``to`` must be integers."""
        with pytest.raises(ValueError, match="integer"):
            RoadEdge.from_dict({"from": "a", "to": 1})

    def test_one_way_has_no_reverse(self) -> None:
        """``reverse_of`` answers ``-1`` on a one-way edge."""
        graph = RoadGraph(
            [at(0, 0), at(100, 0), at(200, 0)],
            [RoadEdge(0, 1), RoadEdge(1, 2, two_way=True, id="b")],
        )

        assert graph.reverse_of(0) == -1
        assert graph.reverse_of(graph.reverse_of(1)) == 1
        assert graph.edge_key(0) == "#0"
        assert graph.edge_key(2) == "b~"

    def test_distance_to_road(self) -> None:
        """Distance to the nearest edge, ``None`` beyond the radius."""
        graph = RoadGraph([at(0, 0), at(100, 0)], [RoadEdge(0, 1)])

        near = graph.distance_to_road_m(at(50, 12), 50)

        assert near == pytest.approx(12, abs=0.01)
        assert graph.distance_to_road_m(at(50, 80), 50) is None

    def test_extract_keeps_ids_and_reindexes(self) -> None:
        """A tile carries the edges touching the box, renumbered."""
        graph = RoadGraph(
            [at(0, 0), at(100, 0), at(1000, 0), at(1100, 0)],
            [RoadEdge(0, 1, id="near"), RoadEdge(2, 3, id="far")],
            two_way=True,
        )
        a, b = at(-10, -10), at(150, 10)
        box = BoundingBox(
            min_latitude=a.latitude,
            max_latitude=b.latitude,
            min_longitude=a.longitude,
            max_longitude=b.longitude,
        )

        tile = graph.extract(box)

        assert [e.id for e in tile.edges] == ["near"]
        assert (tile.edges[0].start, tile.edges[0].end) == (0, 1)
        assert tile.two_way is True


class TestMatchToRoads:
    """Behaviour the parity fixture does not isolate."""

    def test_options_are_validated(self) -> None:
        """A non-positive sigma or candidate count is refused."""
        with pytest.raises(ValueError, match="sigma_m"):
            RoadMatchOptions(sigma_m=0)
        with pytest.raises(ValueError, match="max_candidates"):
            RoadMatchOptions(max_candidates=0)
        with pytest.raises(ValueError, match="min_spacing_m"):
            RoadMatchOptions(min_spacing_m=-1)

    def test_one_way_is_never_driven_backwards(self) -> None:
        """The nearer one-way road against traffic loses to the far one."""
        graph = RoadGraph(
            [at(0, 0), at(0, 600), at(12, 600), at(12, 0)],
            [RoadEdge(0, 1, id="north"), RoadEdge(2, 3, id="south")],
        )
        fixes = [at(4, y) for y in range(580, 0, -20)]

        result = match_to_roads(fixes, graph)

        assert {m.edge.id for m in result.matches if m is not None} == {"south"}

    def test_close_fix_is_inferred_and_far_fix_unmatched(self) -> None:
        """A fix within the spacing is projected; one out of reach is ``None``."""
        graph = RoadGraph([at(0, 0), at(1000, 0)], [RoadEdge(0, 1, id="road")])
        fixes = [at(100, 2), at(103, 2), at(200, 900), at(300, 2)]

        result = match_to_roads(fixes, graph)

        assert result.matches[1] is not None and result.matches[1].inferred
        assert result.matches[2] is None
        assert (result.dropped, result.unmatched, result.breaks) == (1, 1, 0)
