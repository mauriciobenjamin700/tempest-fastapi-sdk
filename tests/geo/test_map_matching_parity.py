"""Parity of :func:`match_to_roads` with ``tempest-react-sdk``'s ``matchToRoads``.

The fixture is produced by the TypeScript implementation
(``node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist``,
react SDK 0.90.0 plus PR #678): same graph, same fixes, its answer. The
Python port has to give the same edge, direction, projection and inferred flag
for every fix, the same routes and the same counters.

The timed scenarios (tempest-react-sdk#678) carry a capture time per fix, in
epoch milliseconds, and may bring their own graph and options; the port takes
the times in seconds through ``timestamps=``. A scenario that re-times an
untimed trace names it (``fixesFrom``) and lists the times, and one whose answer
equals another's names it (``expectedFrom``).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.geo import (
    ROAD_MATCH_DEFAULTS,
    Coordinate,
    RoadGraph,
    RoadMatchOptions,
    RoadMatchResult,
    match_to_roads,
)

FIXTURE: Path = Path(__file__).parent / "fixtures" / "map_matching_parity.json"
DATA: dict[str, Any] = json.loads(FIXTURE.read_text())
SCENARIOS: dict[str, Any] = DATA["scenarios"]

OPTION_NAMES: dict[str, str] = {
    "radiusM": "radius_m",
    "detourPerSecondM": "detour_per_second_m",
    "maxIntervalS": "max_interval_s",
}
"""TypeScript option name → ``RoadMatchOptions`` field, for the ones the
fixture uses."""


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
    assert ROAD_MATCH_DEFAULTS.detour_per_second_m == 2
    assert ROAD_MATCH_DEFAULTS.max_interval_s == 60
    assert ROAD_MATCH_DEFAULTS.spacing_m == 14


def run_scenario(name: str, shared: RoadGraph) -> RoadMatchResult:
    """Run the Python port on one fixture scenario.

    Args:
        name (str): Scenario name.
        shared (RoadGraph): The fixture's shared graph.

    Returns:
        RoadMatchResult: What ``match_to_roads`` answers.
    """
    scenario = SCENARIOS[name]
    graph = RoadGraph.from_dict(scenario["graph"]) if "graph" in scenario else shared
    raw = (
        SCENARIOS[scenario["fixesFrom"]]["fixes"]
        if "fixesFrom" in scenario
        else scenario["fixes"]
    )
    millis: list[float | None] = (
        scenario["timestamps"]
        if "timestamps" in scenario
        else [f.get("timestamp") for f in raw]
    )
    options = RoadMatchOptions(
        **{OPTION_NAMES[k]: v for k, v in scenario.get("options", {}).items()}
    )
    timed = any(t is not None for t in millis)
    return match_to_roads(
        [Coordinate.model_validate(f) for f in raw],
        graph,
        options,
        timestamps=[None if t is None else t / 1000 for t in millis] if timed else None,
    )


def expected_of(name: str) -> dict[str, Any]:
    """The TypeScript answer of a scenario, following ``expectedFrom``.

    Args:
        name (str): Scenario name.

    Returns:
        dict[str, Any]: Matches, routes and counters.
    """
    scenario = SCENARIOS[name]
    if "expectedFrom" in scenario:
        return expected_of(scenario["expectedFrom"])
    expected: dict[str, Any] = scenario["expected"]
    return expected


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_same_answer_as_the_typescript_matcher(graph: RoadGraph, name: str) -> None:
    """Every fix, route and counter equals the TypeScript output.

    Args:
        graph (RoadGraph): The fixture graph.
        name (str): Scenario name.
    """
    expected = expected_of(name)

    result = run_scenario(name, graph)

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


def edge_ids(name: str, result: RoadMatchResult) -> list[str | None]:
    """Edge ``id`` of every match of a block scenario.

    Args:
        name (str): Scenario name (one with its own graph).
        result (RoadMatchResult): Its result.

    Returns:
        list[str | None]: The id of each matched edge, ``None`` when unmatched.
    """
    ids = [e["id"] for e in SCENARIOS[name]["graph"]["edges"]]
    return [None if m is None else ids[m.edge_index] for m in result.matches]


class TestTimedFixes:
    """What each timed scenario shows, asserted on the port (tempest-react-sdk#678).

    The parity test above already pins the output to the TypeScript one; these
    name the behaviour, so a fixture regenerated from a matcher that lost it
    fails here with the reason instead of passing on a new expected answer.
    """

    def test_thirty_seconds_round_a_block_chains_where_untimed_breaks(
        self, graph: RoadGraph
    ) -> None:
        """80 m legs, 150 m wide, ``radius_m`` 50: 30 s chains, no time breaks.

        Args:
            graph (RoadGraph): The shared graph (unused by block scenarios).
        """
        assert run_scenario("block80_untimed", graph).breaks == 1
        timed = run_scenario("block80_30s", graph)
        assert timed.breaks == 0
        assert edge_ids("block80_30s", timed) == ["west", "east"]

    def test_five_seconds_is_too_short_for_the_detour(self, graph: RoadGraph) -> None:
        """The same block 5 s apart still breaks.

        Args:
            graph (RoadGraph): The shared graph.
        """
        assert run_scenario("block80_5s", graph).breaks == 1

    def test_the_cap_bounds_a_long_gap(self, graph: RoadGraph) -> None:
        """130 m legs, 600 s: breaks at the 60 s cap, chains with 120 s or 3 m/s.

        Args:
            graph (RoadGraph): The shared graph.
        """
        assert run_scenario("block130_600s", graph).breaks == 1
        assert run_scenario("block130_600s_cap120", graph).breaks == 0
        assert run_scenario("block130_600s_rate3", graph).breaks == 0

    @pytest.mark.parametrize(
        "name",
        [
            "block80_repeated_clock",
            "block80_clock_back",
            "block80_only_first_timed",
            "block80_only_second_timed",
            "block80_30s_rate0",
        ],
    )
    def test_no_usable_interval_keeps_the_fixed_bound(
        self, graph: RoadGraph, name: str
    ) -> None:
        """Repeated clock, clock going back, one side untimed, rate 0: as untimed.

        Args:
            graph (RoadGraph): The shared graph.
            name (str): Scenario name.
        """
        assert run_scenario(name, graph) == run_scenario("block80_untimed", graph)

    def test_a_sparse_fix_stays_on_its_own_road(self, graph: RoadGraph) -> None:
        """Four fixes on the 100 m block, default radius: time keeps fix 2 on west.

        Args:
            graph (RoadGraph): The shared graph.
        """
        untimed = run_scenario("block100_four_untimed", graph)
        timed = run_scenario("block100_four_timed", graph)
        assert edge_ids("block100_four_untimed", untimed) == [
            "west",
            "top",
            "east",
            "east",
        ]
        assert edge_ids("block100_four_timed", timed) == [
            "west",
            "west",
            "east",
            "east",
        ]

    @pytest.mark.parametrize(
        "trace",
        sorted(n for n in SCENARIOS if f"{n}_1hz_rate0" in SCENARIOS),
    )
    def test_rate_zero_gives_the_untimed_answer(
        self, graph: RoadGraph, trace: str
    ) -> None:
        """The original traces, timed at 1 Hz with rate 0, answer as before.

        Args:
            graph (RoadGraph): The shared graph.
            trace (str): Untimed scenario name.
        """
        assert run_scenario(f"{trace}_1hz_rate0", graph) == run_scenario(trace, graph)

    def test_timestamps_must_match_the_fixes(self, graph: RoadGraph) -> None:
        """One timestamp per fix, or ``ValueError``.

        Args:
            graph (RoadGraph): The shared graph.
        """
        fixes = [Coordinate.model_validate(f) for f in SCENARIOS["grid_tour"]["fixes"]]
        with pytest.raises(ValueError, match="timestamps"):
            match_to_roads(fixes, graph, timestamps=[0.0])

    def test_nan_counts_as_untimed(self, graph: RoadGraph) -> None:
        """A ``nan`` time grants no slack, like ``None``.

        Args:
            graph (RoadGraph): The shared graph.
        """
        block = RoadGraph.from_dict(SCENARIOS["block80_30s"]["graph"])
        raw = SCENARIOS["block80_30s"]["fixes"]
        fixes = [Coordinate.model_validate(f) for f in raw]
        options = RoadMatchOptions(radius_m=50)
        result = match_to_roads(
            fixes, block, options, timestamps=[math.nan, raw[1]["timestamp"] / 1000]
        )
        assert result.breaks == 1


class TestOptionValidation:
    """``detour_per_second_m`` may be 0, never negative; ``max_interval_s`` > 0."""

    def test_negative_rate_is_refused(self) -> None:
        """A negative rate raises."""
        with pytest.raises(ValueError, match="detour_per_second_m"):
            RoadMatchOptions(detour_per_second_m=-1)

    @pytest.mark.parametrize("value", [0.0, -1.0, math.inf, math.nan])
    def test_non_positive_cap_is_refused(self, value: float) -> None:
        """A cap that is not a positive finite number raises.

        Args:
            value (float): The cap.
        """
        with pytest.raises(ValueError, match="max_interval_s"):
            RoadMatchOptions(max_interval_s=value)

    def test_rate_zero_is_allowed(self) -> None:
        """Zero turns the slack off."""
        assert RoadMatchOptions(detour_per_second_m=0).detour_per_second_m == 0
