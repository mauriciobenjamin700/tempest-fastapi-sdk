"""Map-matching: put a GPS trace on the roads it drove — HMM + Viterbi.

A port of ``tempest-react-sdk``'s ``matchToRoads`` (``src/geo/map-matching.ts``,
0.90.0), the method of Newson & Krumm (2009): each fix's candidates are the
nearest roads within ``radius_m``, scored by a Gaussian on the distance
(``sigma_m``); consecutive candidates are linked by an exponential on how
much the network distance between them differs from the straight line
between the fixes (``beta_m``); Viterbi picks the most likely sequence.

Same defaults (:data:`ROAD_MATCH_DEFAULTS`), same pre-filter, same breaks,
same tie order — ``tests/geo/test_map_matching_parity.py`` checks the output
against fixtures produced by the TypeScript implementation. The fixed-lag
live matcher (``createRoadMatcher``) is not ported: the backend matches a
released stretch of trace at once (see
:class:`~tempest_fastapi_sdk.geo.TrafficAggregator`), and the live marker is
the app's job.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from tempest_fastapi_sdk.geo.road_graph import RoadCandidate, RoadEdge, RoadGraph
from tempest_fastapi_sdk.geo.schemas import Coordinate


@dataclass(frozen=True, slots=True)
class RoadMatchOptions:
    """Tuning of the hidden Markov model behind :func:`match_to_roads`.

    Attributes:
        sigma_m (float): GPS noise, standard deviation in metres.
        beta_m (float): Scale of the transition, in metres: a move costs
            ``|straight - network| / beta_m``.
        radius_m (float): Roads farther than this from a fix are not
            candidates.
        max_candidates (int): At most this many nearest roads per fix.
        max_detour_m (float): A transition whose network distance exceeds the
            straight line by more than this is impossible; bounds the
            shortest-path search.
        min_spacing_m (float | None): A fix closer than this to the previous
            kept fix skips the model. ``None`` is ``2 * sigma_m``.
    """

    sigma_m: float = 7.0
    beta_m: float = 3.0
    radius_m: float = 200.0
    max_candidates: int = 16
    max_detour_m: float = 100.0
    min_spacing_m: float | None = None

    def __post_init__(self) -> None:
        """Validate the numbers.

        Raises:
            ValueError: When a value is not a positive finite number (zero is
                allowed for ``min_spacing_m``), or ``max_candidates`` is not a
                positive integer.
        """
        for name in ("sigma_m", "beta_m", "radius_m", "max_detour_m"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"road matching: invalid {name} ({value})")
        if self.min_spacing_m is not None and (
            not math.isfinite(self.min_spacing_m) or self.min_spacing_m < 0
        ):
            raise ValueError(
                f"road matching: invalid min_spacing_m ({self.min_spacing_m})"
            )
        if isinstance(self.max_candidates, bool) or self.max_candidates < 1:
            raise ValueError("road matching: max_candidates must be a positive int")

    @property
    def spacing_m(self) -> float:
        """The pre-filter distance actually used.

        Returns:
            float: ``min_spacing_m``, or ``2 * sigma_m`` when unset.
        """
        return 2 * self.sigma_m if self.min_spacing_m is None else self.min_spacing_m


ROAD_MATCH_DEFAULTS: Final[RoadMatchOptions] = RoadMatchOptions()
"""Default tuning: sigma 7 m, beta 3 m, radius 200 m, 16 roads, detour 100 m.

Ported from ``tempest-react-sdk``'s ``ROAD_MATCH_DEFAULTS``, chosen there by
sweeping every knob on the Kubicka et al. dataset (100 real drives, 247 251
fixes, ``node scripts/bench-map-matching.mjs``): 92.33% of fixes on the right
road, 98.53% without the three drives through yards missing from OSM.
"""


@dataclass(frozen=True, slots=True)
class RoadTraversal:
    """A directed pass over one of the graph's edges.

    Attributes:
        edge (RoadEdge): The edge object.
        edge_index (int): Index of the edge in ``graph.edges``.
        reversed (bool): ``True`` when a two-way edge is driven
            ``end → start``.
    """

    edge: RoadEdge
    edge_index: int
    reversed: bool


@dataclass(frozen=True, slots=True)
class MatchedFix:
    """Where one fix landed on the network.

    Attributes:
        edge (RoadEdge): The edge object.
        edge_index (int): Index of the edge in ``graph.edges``.
        reversed (bool): Driven ``end → start``.
        directed (int): Directed edge index in the graph.
        point (Coordinate): The fix projected onto the edge.
        distance_m (float): Metres from the fix to ``point``.
        offset_m (float): Metres from the start of the edge, in the direction
            of travel, to ``point``.
        inferred (bool): ``True`` for a fix the model skipped (within the
            spacing of the previous kept fix), projected onto that fix's edge.
    """

    edge: RoadEdge
    edge_index: int
    reversed: bool
    directed: int
    point: Coordinate
    distance_m: float
    offset_m: float
    inferred: bool


@dataclass(slots=True)
class MatchedRoute:
    """One unbroken run of the matched route.

    Attributes:
        edges (list[RoadTraversal]): Edges driven, in order, consecutive
            repeats collapsed, including the ones recovered between fixes.
        first_fix (int): Index of the first fix of this run.
        last_fix (int): Index of the last fix of this run.
    """

    edges: list[RoadTraversal]
    first_fix: int
    last_fix: int


@dataclass(slots=True)
class RoadMatchResult:
    """Output of :func:`match_to_roads`.

    Attributes:
        matches (list[MatchedFix | None]): One per input fix, same order;
            ``None`` when no road was in reach.
        routes (list[MatchedRoute]): One per unbroken chain.
        breaks (int): Times the chain broke and restarted.
        dropped (int): Fixes skipped by the spacing rule.
        unmatched (int): Fixes with no road within ``radius_m``.
    """

    matches: list[MatchedFix | None]
    routes: list[MatchedRoute]
    breaks: int
    dropped: int
    unmatched: int


@dataclass(slots=True)
class _Follower:
    """A fix attached to the column before it instead of getting its own.

    Attributes:
        fix (int): Fix index.
        x (float): Plane x.
        y (float): Plane y.
        dropped (bool): Skipped by spacing (``True``) or out of reach.
    """

    fix: int
    x: float
    y: float
    dropped: bool


@dataclass(slots=True)
class _Column:
    """One Viterbi column.

    Attributes:
        fix (int): Fix index.
        x (float): Plane x.
        y (float): Plane y.
        cands (list[RoadCandidate]): Candidates.
        score (list[float]): Log-probability per candidate.
        back (list[int]): Best previous candidate per candidate.
        start (bool): Whether this column (re)starts a chain.
        followers (list[_Follower]): Fixes attached to it.
    """

    fix: int
    x: float
    y: float
    cands: list[RoadCandidate]
    score: list[float] = field(default_factory=list)
    back: list[int] = field(default_factory=list)
    start: bool = True
    followers: list[_Follower] = field(default_factory=list)


def _advance(
    graph: RoadGraph, prev: _Column | None, col: _Column, p: RoadMatchOptions
) -> None:
    """Fill one Viterbi column from the previous one.

    Emission ``-0.5(d/sigma)^2``; transition ``-|straight - network| / beta``. One
    bounded Dijkstra per distinct end node of the previous candidates. When
    no previous candidate reaches any current one, the column restarts the
    chain from its emissions alone — the HMM break.

    Args:
        graph (RoadGraph): The network.
        prev (_Column | None): Previous column, or ``None`` at the start.
        col (_Column): The column to fill.
        p (RoadMatchOptions): Model parameters.
    """
    m = len(col.cands)
    emission = [-0.5 * (c.distance / p.sigma_m) ** 2 for c in col.cands]
    score = [-math.inf] * m
    back = [-1] * m
    if prev is not None:
        straight = math.hypot(col.x - prev.x, col.y - prev.y)
        limit = straight + p.max_detour_m

        def consider(a: int, j: int, route: float) -> None:
            """Offer the transition ``a → j`` over ``route`` metres.

            Args:
                a (int): Previous candidate.
                j (int): Current candidate.
                route (float): Network distance, metres.
            """
            if route > limit:
                return
            value = prev.score[a] - abs(route - straight) / p.beta_m
            if value > score[j]:
                score[j] = value
                back[j] = a

        by_dir: dict[int, list[int]] = {}
        for j, cb in enumerate(col.cands):
            by_dir.setdefault(cb.dir, []).append(j)
        by_end: dict[int, list[int]] = {}
        for a, ca in enumerate(prev.cands):
            if prev.score[a] == -math.inf:
                continue
            for j in by_dir.get(ca.dir, ()):
                cb = col.cands[j]
                if cb.offset >= ca.offset:
                    consider(a, j, cb.offset - ca.offset)
            by_end.setdefault(graph.dir_to[ca.dir], []).append(a)
        targets = [graph.dir_from[c.dir] for c in col.cands]
        for end, group in by_end.items():
            rest = min(
                graph.dir_length[prev.cands[a].dir] - prev.cands[a].offset
                for a in group
            )
            if rest > limit:
                continue
            dist, _ = graph.shortest_paths(end, targets, limit - rest)
            for j in range(m):
                via = dist.get(targets[j])
                if via is None:
                    continue
                for a in group:
                    ca = prev.cands[a]
                    consider(
                        a,
                        j,
                        graph.dir_length[ca.dir]
                        - ca.offset
                        + via
                        + col.cands[j].offset,
                    )
    alive = False
    for j in range(m):
        if score[j] > -math.inf:
            score[j] += emission[j]
            alive = True
    col.start = not alive
    col.score = score if alive else emission
    col.back = back


def _best(col: _Column) -> int:
    """Index of the best state of a column (first on ties).

    Args:
        col (_Column): A filled column.

    Returns:
        int: The argmax of its scores.
    """
    at = 0
    for j in range(1, len(col.score)):
        if col.score[j] > col.score[at]:
            at = j
    return at


def _backtrack(cols: Sequence[_Column]) -> list[int]:
    """Viterbi backtrack, newest to oldest; a chain start cuts the walk.

    Args:
        cols (Sequence[_Column]): Columns, oldest first.

    Returns:
        list[int]: The chosen state of each column.
    """
    states = [0] * len(cols)
    if not cols:
        return states
    s = _best(cols[-1])
    for i in range(len(cols) - 1, -1, -1):
        states[i] = s
        if i == 0:
            break
        s = _best(cols[i - 1]) if cols[i].start else cols[i].back[s]
    return states


def _to_match(graph: RoadGraph, c: RoadCandidate, inferred: bool) -> MatchedFix:
    """Turn a candidate into the public match.

    Args:
        graph (RoadGraph): The network.
        c (RoadCandidate): The candidate.
        inferred (bool): Whether the fix was skipped by spacing.

    Returns:
        MatchedFix: The match.
    """
    edge_index = graph.dir_edge[c.dir]
    return MatchedFix(
        edge=graph.edges[edge_index],
        edge_index=edge_index,
        reversed=graph.dir_reversed[c.dir],
        directed=c.dir,
        point=graph.from_plane(c.px, c.py),
        distance_m=c.distance,
        offset_m=c.offset,
        inferred=inferred,
    )


def _traversal(graph: RoadGraph, directed: int) -> RoadTraversal:
    """The public traversal of a directed edge.

    Args:
        graph (RoadGraph): The network.
        directed (int): Directed edge index.

    Returns:
        RoadTraversal: The traversal.
    """
    edge_index = graph.dir_edge[directed]
    return RoadTraversal(
        edge=graph.edges[edge_index],
        edge_index=edge_index,
        reversed=graph.dir_reversed[directed],
    )


def match_to_roads(
    fixes: Sequence[Coordinate],
    graph: RoadGraph,
    options: RoadMatchOptions = ROAD_MATCH_DEFAULTS,
) -> RoadMatchResult:
    """Match a GPS trace to a road network (Newson & Krumm 2009).

    * **Breaks**: when no transition is possible (a gap, a missing road),
      the chain restarts at that fix instead of failing; ``routes`` gets one
      entry per chain and ``breaks`` counts them.
    * **Close fixes** (within the spacing of the last kept one) skip the
      model and are projected onto that fix's edge, ``inferred=True``.
    * **No road in reach**: the match is ``None`` and the fix counts in
      ``unmatched``; it does not break the chain.

    Synchronous and CPU-bound; from async code run it with
    ``asyncio.to_thread`` when the trace is long.

    Args:
        fixes (Sequence[Coordinate]): The trace, in time order.
        graph (RoadGraph): The network.
        options (RoadMatchOptions): Model tuning; defaults in
            :data:`ROAD_MATCH_DEFAULTS`.

    Returns:
        RoadMatchResult: The match of every fix, the route, and what was
        skipped.
    """
    p = options
    spacing = p.spacing_m
    cols: list[_Column] = []
    last_x = 0.0
    last_y = 0.0
    breaks = 0
    dropped = 0
    unmatched = 0
    for index, fix in enumerate(fixes):
        x, y = graph.to_plane(fix)
        last = cols[-1] if cols else None
        if last is not None and math.hypot(x - last_x, y - last_y) < spacing:
            last.followers.append(_Follower(index, x, y, True))
            dropped += 1
            continue
        cands = graph.candidates_near(x, y, p.radius_m, p.max_candidates)
        if not cands:
            unmatched += 1
            if last is not None:
                last.followers.append(_Follower(index, x, y, False))
            continue
        col = _Column(fix=index, x=x, y=y, cands=cands)
        _advance(graph, last, col, p)
        if col.start and last is not None:
            breaks += 1
        cols.append(col)
        last_x, last_y = x, y

    matches: list[MatchedFix | None] = [None] * len(fixes)
    states = _backtrack(cols)
    for col, state in zip(cols, states, strict=True):
        chosen = col.cands[state]
        matches[col.fix] = _to_match(graph, chosen, False)
        for f in col.followers:
            matches[f.fix] = (
                _to_match(graph, graph.project_onto(chosen.dir, f.x, f.y), True)
                if f.dropped
                else None
            )

    routes: list[MatchedRoute] = []
    route: MatchedRoute | None = None
    last_dir = -1
    for i, col in enumerate(cols):
        c = col.cands[states[i]]
        if route is None or col.start:
            route = MatchedRoute(edges=[], first_fix=col.fix, last_fix=col.fix)
            routes.append(route)
            last_dir = -1
        else:
            a = cols[i - 1].cands[states[i - 1]]
            if not (a.dir == c.dir and c.offset >= a.offset):
                target = graph.dir_from[c.dir]
                _, prev = graph.shortest_paths(graph.dir_to[a.dir], [target], math.inf)
                for directed in graph.path_to(prev, target):
                    if directed != last_dir:
                        last_dir = directed
                        route.edges.append(_traversal(graph, directed))
        if c.dir != last_dir:
            last_dir = c.dir
            route.edges.append(_traversal(graph, c.dir))
        route.last_fix = max([col.fix, *(f.fix for f in col.followers)])

    return RoadMatchResult(
        matches=matches,
        routes=routes,
        breaks=breaks,
        dropped=dropped,
        unmatched=unmatched,
    )


__all__: list[str] = [
    "ROAD_MATCH_DEFAULTS",
    "MatchedFix",
    "MatchedRoute",
    "RoadMatchOptions",
    "RoadMatchResult",
    "RoadTraversal",
    "match_to_roads",
]
