"""Parity of :func:`match_to_roads` with ``tempest-react-sdk``'s ``matchToRoads``.

The fixture is produced by the TypeScript implementation
(``node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist``,
react SDK 0.90.0): same graph, same fixes, its answer. The Python port has to
give the same edge, direction, projection and inferred flag for every fix,
the same routes and the same counters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.geo import (
    ROAD_MATCH_DEFAULTS,
    Coordinate,
    RoadGraph,
    match_to_roads,
)

FIXTURE: Path = Path(__file__).parent / "fixtures" / "map_matching_parity.json"
DATA: dict[str, Any] = json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def graph() -> RoadGraph:
    """The fixture's graph, built by the Python port.

    Returns:
        RoadGraph: The graph.
    """
    return RoadGraph.from_dict(DATA["graph"])


def test_defaults_match_the_react_sdk() -> None:
    """``ROAD_MATCH_DEFAULTS`` are the TypeScript ``ROAD_MATCH_DEFAULTS``."""
    assert ROAD_MATCH_DEFAULTS.sigma_m == 7
    assert ROAD_MATCH_DEFAULTS.beta_m == 3
    assert ROAD_MATCH_DEFAULTS.radius_m == 200
    assert ROAD_MATCH_DEFAULTS.max_candidates == 16
    assert ROAD_MATCH_DEFAULTS.max_detour_m == 100
    assert ROAD_MATCH_DEFAULTS.spacing_m == 14


@pytest.mark.parametrize("name", sorted(DATA["scenarios"]))
def test_same_answer_as_the_typescript_matcher(graph: RoadGraph, name: str) -> None:
    """Every fix, route and counter equals the TypeScript output.

    Args:
        graph (RoadGraph): The fixture graph.
        name (str): Scenario name.
    """
    scenario = DATA["scenarios"][name]
    fixes = [Coordinate.model_validate(f) for f in scenario["fixes"]]
    expected = scenario["expected"]

    result = match_to_roads(fixes, graph)

    assert result.breaks == expected["breaks"]
    assert result.dropped == expected["dropped"]
    assert result.unmatched == expected["unmatched"]
    assert len(result.matches) == len(expected["matches"])
    for got, want in zip(result.matches, expected["matches"], strict=True):
        if want is None:
            assert got is None
            continue
        assert got is not None
        assert (got.edge_index, got.reversed, got.inferred) == (
            want["edgeIndex"],
            want["reversed"],
            want["inferred"],
        )
        assert got.distance_m == pytest.approx(want["distanceM"], abs=1e-6)
        assert got.offset_m == pytest.approx(want["offsetM"], abs=1e-6)
        assert got.point.latitude == pytest.approx(want["point"]["latitude"], abs=1e-9)
        assert got.point.longitude == pytest.approx(
            want["point"]["longitude"], abs=1e-9
        )
    routes = [
        {
            "edges": [[t.edge_index, t.reversed] for t in r.edges],
            "firstFix": r.first_fix,
            "lastFix": r.last_fix,
        }
        for r in result.routes
    ]
    assert routes == expected["routes"]
