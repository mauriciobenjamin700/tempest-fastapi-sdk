"""Quality of the map-matching on the real fleet, published only in aggregate.

:class:`~tempest_fastapi_sdk.geo.TrafficAggregator` already matches every
released stretch of every trip to the roads. With ``quality=`` it also
measures, on that same match, the indicators tempest-react-sdk#667 used to
validate the matcher on the Kubicka et al. dataset and on public Brazilian
OSM traces (``scripts/bench-br-osm.mjs`` there):

* breaks per 1000 positions;
* distance from each position to its matched point (median and p90);
* share of positions matched, and share ``inferred``;
* sampling interval (median and p90);
* switches to a parallel road on straight stretches, per km.

Nothing per trip or per device is kept or published. Each stretch is folded
at once into the counters and fixed-size histograms of one **cell** of the
current window — the cell is the combination of the coarse dimensions the
app gave the trip (``{"os": "android"}``), or the single empty cell. The
distinct-device count of a cell is a set of HMACs under a salt drawn for the
window; when the window closes, the published aggregate is computed and the
salt, the hashes and the counters are dropped. A cell is published only with
``min_devices`` distinct devices (the k of
:class:`~tempest_fastapi_sdk.geo.TrafficPrivacy`), and cells are suppressed
together so that what the published ones leave of the total is either
nothing or itself k-anonymous (``other``): subtracting published cells from
the total never isolates a small group.
"""

from __future__ import annotations

import bisect
import hashlib
import hmac
import itertools
import math
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from pydantic import AwareDatetime, Field

from tempest_fastapi_sdk.geo.map_matching import MatchedFix, RoadMatchResult
from tempest_fastapi_sdk.geo.road_graph import RoadGraph
from tempest_fastapi_sdk.schemas.base import BaseSchema

STRAIGHT_HALF_M: Final[float] = 75.0
"""Track length before and after a position that decides if it is straight.

Ported from tempest-react-sdk's ``scripts/bench-br-osm.mjs``
(``STRAIGHT_HALF_M``).
"""

STRAIGHT_MAX_TURN_DEG: Final[float] = 10.0
"""Largest bearing change, over :data:`STRAIGHT_HALF_M` each side, of a
straight stretch.

Ported from tempest-react-sdk's ``scripts/bench-br-osm.mjs``
(``STRAIGHT_MAX_TURN_DEG``).
"""

PARALLEL_MAX_DEG: Final[float] = 20.0
"""Largest angle between two edges for a switch between them to be parallel.

Ported from tempest-react-sdk's ``scripts/bench-br-osm.mjs``
(``PARALLEL_MAX_DEG``).
"""

PARALLEL_MIN_OFFSET_M: Final[float] = 8.0
"""Least distance from the new matched point to the old edge's line for a
switch to count as parallel (a collinear continuation does not).

Ported from tempest-react-sdk's ``scripts/bench-br-osm.mjs``
(``PARALLEL_MIN_OFFSET_M``).
"""

DEFAULT_MATCH_QUALITY_WINDOW_SECONDS: Final[int] = 3600
"""Length of a quality window: one hour, so a cell gathers k devices."""

MATCH_QUALITY_MAX_DIMENSIONS: Final[int] = 4
"""Most coarse dimensions a quality cell can be split by."""


def _bounds(*steps: tuple[float, float]) -> tuple[float, ...]:
    """Upper bounds of consecutive buckets: ``(limit, width)`` per range.

    Args:
        *steps (tuple[float, float]): Each range ends at ``limit`` and is
            cut into buckets ``width`` wide, starting where the previous one
            ended (or at 0).

    Returns:
        tuple[float, ...]: The increasing upper bounds.
    """
    out: list[float] = []
    low = 0.0
    for limit, width in steps:
        count = round((limit - low) / width)
        out.extend(round(low + width * (i + 1), 6) for i in range(count))
        low = limit
    return tuple(out)


SNAP_DISTANCE_BUCKETS_M: Final[tuple[float, ...]] = _bounds(
    (5.0, 0.1), (20.0, 0.25), (50.0, 1.0), (200.0, 5.0), (500.0, 50.0)
)
"""Upper bounds of the position → matched point histogram, metres.

0.1 m wide up to 5 m, 0.25 m up to 20 m, 1 m up to 50 m, 5 m up to 200 m
(the default candidate radius), 50 m up to 500 m; above, one overflow bucket.
"""

SAMPLING_INTERVAL_BUCKETS_S: Final[tuple[float, ...]] = _bounds(
    (2.0, 0.25), (10.0, 0.5), (30.0, 1.0), (120.0, 5.0), (300.0, 30.0)
)
"""Upper bounds of the sampling interval histogram, seconds.

Whole seconds are bounds up to 30 s, so a 1 Hz or 2 s source reads exactly.
"""


class QualityHistogram:
    """A histogram with fixed buckets: memory independent of the count.

    Buckets are right-closed: ``(bounds[i - 1], bounds[i]]``; values above
    the last bound go to one overflow bucket. :meth:`quantile` answers by
    nearest rank (the same rule as tempest-react-sdk's benchmarks) with the
    upper bound of the bucket holding that rank, clamped to the largest
    value seen — so it overestimates by less than one bucket width, and is
    exact for values that sit on a bound.

    Attributes:
        bounds (tuple[float, ...]): Upper bounds, increasing.
        counts (list[int]): One count per bucket plus the overflow.
        total (int): Values added.
        largest (float): Largest value added (``-inf`` when empty).
    """

    __slots__ = ("bounds", "counts", "largest", "total")

    def __init__(self, bounds: Sequence[float]) -> None:
        """Start empty.

        Args:
            bounds (Sequence[float]): Bucket upper bounds, increasing.

        Raises:
            ValueError: When ``bounds`` is empty or not increasing.
        """
        if not bounds or any(b >= a for a, b in itertools.pairwise(bounds[::-1])):
            raise ValueError("bounds must be a non-empty increasing sequence")
        self.bounds: tuple[float, ...] = tuple(bounds)
        self.counts: list[int] = [0] * (len(self.bounds) + 1)
        self.total: int = 0
        self.largest: float = -math.inf

    def add(self, value: float) -> None:
        """Count one value.

        Args:
            value (float): The value; NaN is ignored.
        """
        if math.isnan(value):
            return
        self.counts[bisect.bisect_left(self.bounds, value)] += 1
        self.total += 1
        self.largest = max(self.largest, value)

    def merge(self, other: QualityHistogram) -> None:
        """Add another histogram with the same bounds into this one.

        Args:
            other (QualityHistogram): The histogram to add.

        Raises:
            ValueError: When the bounds differ.
        """
        if other.bounds != self.bounds:
            raise ValueError("cannot merge histograms with different bounds")
        for i, count in enumerate(other.counts):
            self.counts[i] += count
        self.total += other.total
        self.largest = max(self.largest, other.largest)

    def quantile(self, q: float) -> float | None:
        """The ``q``-quantile by nearest rank, at bucket resolution.

        Args:
            q (float): Quantile in ``[0, 1]``.

        Returns:
            float | None: The bucket's upper bound clamped to the largest
            value (the largest value for the overflow bucket); ``None`` when
            empty.

        Raises:
            ValueError: When ``q`` is outside ``[0, 1]``.
        """
        if not 0 <= q <= 1:
            raise ValueError("q must be within [0, 1]")
        if self.total == 0:
            return None
        rank = min(self.total - 1, math.floor(q * self.total))
        seen = 0
        for i, count in enumerate(self.counts):
            seen += count
            if seen > rank:
                if i == len(self.bounds):
                    return self.largest
                return min(self.bounds[i], self.largest)
        return self.largest


@dataclass(frozen=True, slots=True)
class MatchQualityOptions:
    """How :class:`~tempest_fastapi_sdk.geo.TrafficAggregator` measures quality.

    The k of every cell is the aggregator's ``TrafficPrivacy.min_devices``.

    Attributes:
        window_seconds (int): Length of a window. Windows are aligned to
            the epoch (``floor(now / window_seconds)``); only closed ones
            are published.
        dimensions (tuple[str, ...]): Names of the coarse dimensions the app
            may give a trip (``("os", "app_version")``). A trip without a
            value for one is counted under ``"unknown"``.
        max_cells (int): Most cells a window holds. Trips of a new
            combination past it are counted only in the total.
        max_value_length (int): Longest dimension value accepted.
    """

    window_seconds: int = DEFAULT_MATCH_QUALITY_WINDOW_SECONDS
    dimensions: tuple[str, ...] = ()
    max_cells: int = 64
    max_value_length: int = 32

    def __post_init__(self) -> None:
        """Validate the options.

        Raises:
            ValueError: When ``window_seconds`` or ``max_cells`` is below 1,
                ``max_value_length`` below 1, or ``dimensions`` has more than
                :data:`MATCH_QUALITY_MAX_DIMENSIONS` names, a repeated or an
                empty one.
        """
        if self.window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        if self.max_cells < 1:
            raise ValueError("max_cells must be at least 1")
        if self.max_value_length < 1:
            raise ValueError("max_value_length must be at least 1")
        if len(self.dimensions) > MATCH_QUALITY_MAX_DIMENSIONS:
            raise ValueError(
                f"at most {MATCH_QUALITY_MAX_DIMENSIONS} dimensions: every one "
                "multiplies the cells, and a small cell is never published"
            )
        if len(set(self.dimensions)) != len(self.dimensions) or not all(
            name.strip() for name in self.dimensions
        ):
            raise ValueError("dimension names must be unique and non-empty")


class MatchQualityIndicatorsSchema(BaseSchema):
    """The map-matching quality indicators, as tempest-react-sdk#667 defines them.

    Every rate has the denominator the reference measurements used, so a
    window compares directly with :data:`MATCH_QUALITY_REFERENCE_KUBICKA` and
    :data:`MATCH_QUALITY_REFERENCE_BRAZIL_OSM`. ``None`` means not measured
    (or no denominator).

    Attributes:
        positions (int): Positions measured.
        straight_km (float): Kilometres of straight track (the denominator
            of ``parallel_switches_per_km``).
        breaks_per_1000_positions (float | None): Times the matched chain
            broke (no possible move between two positions) per 1000
            positions.
        matched_share (float | None): Share of positions with a road within
            the candidate radius (0 to 1).
        inferred_share (float | None): Share of positions the matcher
            skipped by spacing and projected on the previous one's edge.
        snap_distance_median_m (float | None): Median distance from a
            position to its matched point, metres.
        snap_distance_p90_m (float | None): 90th percentile of it.
        sampling_interval_median_s (float | None): Median time between two
            consecutive kept positions of a trip, seconds.
        sampling_interval_p90_s (float | None): 90th percentile of it.
        parallel_switches_per_km (float | None): Switches of matched road to
            a parallel one while the GPS track is straight, per km of
            straight track.
    """

    positions: int = Field(default=0, ge=0, title="Positions")
    straight_km: float = Field(default=0.0, ge=0, title="Straight km")
    breaks_per_1000_positions: float | None = Field(
        default=None, ge=0, title="Breaks per 1000 positions"
    )
    matched_share: float | None = Field(default=None, ge=0, le=1, title="Matched share")
    inferred_share: float | None = Field(
        default=None, ge=0, le=1, title="Inferred share"
    )
    snap_distance_median_m: float | None = Field(
        default=None, ge=0, title="Snap distance median (m)"
    )
    snap_distance_p90_m: float | None = Field(
        default=None, ge=0, title="Snap distance p90 (m)"
    )
    sampling_interval_median_s: float | None = Field(
        default=None, ge=0, title="Sampling interval median (s)"
    )
    sampling_interval_p90_s: float | None = Field(
        default=None, ge=0, title="Sampling interval p90 (s)"
    )
    parallel_switches_per_km: float | None = Field(
        default=None, ge=0, title="Parallel switches per km"
    )


class MatchQualityCellSchema(MatchQualityIndicatorsSchema):
    """The indicators of one published cell of a window.

    Attributes:
        dimensions (dict[str, str]): The cell's dimension values; empty for
            the total and for ``other``.
        devices (int): Distinct devices in the cell, at least the k.
    """

    dimensions: dict[str, str] = Field(
        default_factory=dict,
        title="Dimensions",
        description="Dimension values of the cell; empty for the total.",
        examples=[{"os": "android"}],
    )
    devices: int = Field(
        ge=2, title="Devices", description="Distinct devices (at least k)."
    )


class MatchQualityReferenceSchema(MatchQualityIndicatorsSchema):
    """A published measurement to compare a window with.

    Attributes:
        name (str): Short name.
        source (str): Where the numbers come from, with the command.
        note (str): What differs from the aggregator's own measure.
    """

    name: str = Field(title="Name")
    source: str = Field(title="Source")
    note: str = Field(default="", title="Note")


class MatchQualitySnapshotSchema(BaseSchema):
    """The quality of the last closed window, k-anonymous by construction.

    ``overall`` minus the published ``cells`` is exactly ``other``: cells
    below k are suppressed together (and, when their union is still below k,
    with the smallest published cells) so the difference never isolates a
    group smaller than k.

    Attributes:
        window_start (datetime | None): Start of the window; ``None`` before
            the first window closed.
        window_end (datetime | None): End of the window.
        min_devices (int | None): The k it was published under.
        dimensions (list[str]): The dimension names cells are split by.
        overall (MatchQualityCellSchema | None): Every measured position of
            the window; ``None`` with fewer than k devices.
        cells (list[MatchQualityCellSchema]): One per published combination
            of dimension values.
        other (MatchQualityCellSchema | None): The suppressed cells together,
            when there are any.
    """

    window_start: AwareDatetime | None = Field(default=None, title="Window start")
    window_end: AwareDatetime | None = Field(default=None, title="Window end")
    min_devices: int | None = Field(default=None, ge=2, title="k")
    dimensions: list[str] = Field(default_factory=list, title="Dimensions")
    overall: MatchQualityCellSchema | None = Field(default=None, title="Overall")
    cells: list[MatchQualityCellSchema] = Field(default_factory=list, title="Cells")
    other: MatchQualityCellSchema | None = Field(default=None, title="Other")


class MatchQualityReportSchema(BaseSchema):
    """What ``GET <prefix>`` of :func:`make_match_quality_router` answers.

    Attributes:
        snapshot (MatchQualitySnapshotSchema): The last closed window.
        references (list[MatchQualityReferenceSchema]): The measurements to
            compare it with.
    """

    snapshot: MatchQualitySnapshotSchema = Field(
        default_factory=MatchQualitySnapshotSchema, title="Snapshot"
    )
    references: list[MatchQualityReferenceSchema] = Field(
        default_factory=list, title="References"
    )


MATCH_QUALITY_REFERENCE_KUBICKA: Final[MatchQualityReferenceSchema] = (
    MatchQualityReferenceSchema(
        name="kubicka",
        source=(
            "tempest-react-sdk#667, phase 2 (2026-11-19): Kubicka et al., "
            "Zenodo 57731, 100 drives, 247 251 fixes; "
            "node scripts/bench-map-matching.mjs"
        ),
        note=(
            "breaks: 48 in 247 251 fixes. The snap distance there is position "
            "to the true route, not to the matched point."
        ),
        positions=247_251,
        breaks_per_1000_positions=0.19,
        snap_distance_median_m=3.7,
        sampling_interval_median_s=1.0,
    )
)
"""The Kubicka et al. dataset (worldwide, ~1 Hz), as matched in #667.

Ported from tempest-react-sdk#667 (comment "Fase 2" and the Brazil table's
Kubicka column, measured 2026-11-19).
"""

MATCH_QUALITY_REFERENCE_BRAZIL_OSM: Final[MatchQualityReferenceSchema] = (
    MatchQualityReferenceSchema(
        name="brazil-osm",
        source=(
            "tempest-react-sdk#667, Brazil validation (2026-11-19): public OSM "
            "GPS traces of Sao Paulo, Rio, Teresina, Sao Carlos and Parnaiba, "
            "161 vehicle runs, 19 806 positions; "
            "node scripts/bench-br-osm.mjs"
        ),
        note=(
            "Measured over whole runs, without the 200 m trim; the "
            "aggregator measures released stretches, so straight stretches "
            "at a stretch edge are shorter."
        ),
        positions=19_806,
        straight_km=119.7,
        breaks_per_1000_positions=1.06,
        matched_share=1.0,
        inferred_share=0.551,
        snap_distance_median_m=3.7,
        snap_distance_p90_m=11.6,
        sampling_interval_median_s=1.0,
        sampling_interval_p90_s=4.0,
        parallel_switches_per_km=0.109,
    )
)
"""Public OSM traces of five Brazilian cities, as matched in #667.

Ported from tempest-react-sdk#667 (comment "Validação no Brasil", column
"Brasil (todas)", measured 2026-11-19).
"""

MATCH_QUALITY_REFERENCES: Final[tuple[MatchQualityReferenceSchema, ...]] = (
    MATCH_QUALITY_REFERENCE_KUBICKA,
    MATCH_QUALITY_REFERENCE_BRAZIL_OSM,
)
"""Every reference :func:`make_match_quality_router` answers with."""


@dataclass(slots=True)
class QualityTrackPoint:
    """One position of a released stretch, on the graph's plane.

    Attributes:
        x (float): Plane x, metres.
        y (float): Plane y, metres.
        t (float): Device time, epoch seconds.
    """

    x: float
    y: float
    t: float


def _straight_flags(points: Sequence[QualityTrackPoint]) -> list[bool]:
    """Which positions sit on a straight stretch of the raw track.

    The bearing over the :data:`STRAIGHT_HALF_M` before a position and the
    one after differ by less than :data:`STRAIGHT_MAX_TURN_DEG`; positions
    without that much track on both sides are not straight. Measured on the
    GPS, not on the match, so the match cannot vouch for itself. Ported from
    tempest-react-sdk's ``straightFlags`` (``scripts/bench-br-osm.mjs``).

    Args:
        points (Sequence[QualityTrackPoint]): The stretch, in time order.

    Returns:
        list[bool]: One flag per position.
    """
    n = len(points)
    cum = [0.0]
    for a, b in itertools.pairwise(points):
        cum.append(cum[-1] + math.hypot(b.x - a.x, b.y - a.y))
    flags = [False] * n
    back = 0
    ahead = 0
    for i in range(n):
        while back + 1 < i and cum[i] - cum[back + 1] >= STRAIGHT_HALF_M:
            back += 1
        ahead = max(ahead, i)
        while ahead < n - 1 and cum[ahead] - cum[i] < STRAIGHT_HALF_M:
            ahead += 1
        if cum[i] - cum[back] < STRAIGHT_HALF_M or cum[ahead] - cum[i] < (
            STRAIGHT_HALF_M
        ):
            continue
        p, q, r = points[back], points[i], points[ahead]
        b1 = math.atan2(q.y - p.y, q.x - p.x)
        b2 = math.atan2(r.y - q.y, r.x - q.x)
        turn = math.degrees(abs(b1 - b2))
        if turn > 180:
            turn = 360 - turn
        flags[i] = turn < STRAIGHT_MAX_TURN_DEG
    return flags


def _road_of(match: MatchedFix) -> str:
    """The road an edge belongs to, to tell a road change from a new segment.

    Args:
        match (MatchedFix): A matched position.

    Returns:
        str: The edge ``id`` without its ``:<n>`` segment suffix
        (``"way/123"`` for ``"way/123:4"``); the ``id`` itself without one;
        ``#<edge index>`` without an ``id``.
    """
    edge_id = match.edge.id
    if edge_id is None:
        return f"#{match.edge_index}"
    return edge_id.rsplit(":", 1)[0] if ":" in edge_id else edge_id


def _is_parallel_switch(
    graph: RoadGraph, before: MatchedFix, after: MatchedFix
) -> bool:
    """Whether a road change lands on a parallel road, off the old one's line.

    Ported from tempest-react-sdk's ``zigzag`` (``scripts/bench-br-osm.mjs``):
    the two edges within :data:`PARALLEL_MAX_DEG` of each other and the new
    point more than :data:`PARALLEL_MIN_OFFSET_M` from the old edge's line.

    Args:
        graph (RoadGraph): The network.
        before (MatchedFix): The previous matched position.
        after (MatchedFix): The current one, on another road.

    Returns:
        bool: A parallel switch.
    """
    ax, ay = graph.node_xy(before.edge.start)
    bx, by = graph.node_xy(before.edge.end)
    cx, cy = graph.node_xy(after.edge.start)
    dx, dy = graph.node_xy(after.edge.end)
    len_a = math.hypot(bx - ax, by - ay)
    len_b = math.hypot(dx - cx, dy - cy)
    if len_a <= 1 or len_b <= 1:
        return False
    cos = abs(((bx - ax) * (dx - cx) + (by - ay) * (dy - cy)) / (len_a * len_b))
    angle = math.degrees(math.acos(min(1.0, cos)))
    px, py = graph.to_plane(after.point)
    offset = abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / len_a
    return angle < PARALLEL_MAX_DEG and offset > PARALLEL_MIN_OFFSET_M


@dataclass(slots=True)
class _Cell:
    """Counters of one cell of the open window. Nothing per device but hashes.

    Attributes:
        tokens (set[bytes]): HMAC of each device under the window salt.
        positions (int): Positions measured.
        matched (int): Positions with a match.
        inferred (int): Matches marked ``inferred``.
        breaks (int): Chain breaks.
        straight_m (float): Metres of straight track.
        parallel (int): Parallel switches on straight track.
        snap (QualityHistogram): Position to matched point, metres.
        interval (QualityHistogram): Sampling interval, seconds.
    """

    tokens: set[bytes] = field(default_factory=set)
    positions: int = 0
    matched: int = 0
    inferred: int = 0
    breaks: int = 0
    straight_m: float = 0.0
    parallel: int = 0
    snap: QualityHistogram = field(
        default_factory=lambda: QualityHistogram(SNAP_DISTANCE_BUCKETS_M)
    )
    interval: QualityHistogram = field(
        default_factory=lambda: QualityHistogram(SAMPLING_INTERVAL_BUCKETS_S)
    )

    def merge(self, other: _Cell) -> None:
        """Add another cell into this one.

        Args:
            other (_Cell): The cell to add.
        """
        self.tokens |= other.tokens
        self.positions += other.positions
        self.matched += other.matched
        self.inferred += other.inferred
        self.breaks += other.breaks
        self.straight_m += other.straight_m
        self.parallel += other.parallel
        self.snap.merge(other.snap)
        self.interval.merge(other.interval)

    def publish(self, dimensions: Mapping[str, str]) -> MatchQualityCellSchema:
        """The published indicators of this cell.

        Args:
            dimensions (Mapping[str, str]): Its dimension values.

        Returns:
            MatchQualityCellSchema: The indicators.
        """

        def rounded(value: float | None, digits: int) -> float | None:
            """Round a measure, keeping ``None``.

            Args:
                value (float | None): The measure.
                digits (int): Decimals.

            Returns:
                float | None: Rounded, or ``None``.
            """
            return None if value is None else round(value, digits)

        n = self.positions
        km = self.straight_m / 1000
        return MatchQualityCellSchema(
            dimensions=dict(dimensions),
            devices=len(self.tokens),
            positions=n,
            straight_km=round(km, 3),
            breaks_per_1000_positions=round(1000 * self.breaks / n, 3) if n else None,
            matched_share=round(self.matched / n, 4) if n else None,
            inferred_share=round(self.inferred / n, 4) if n else None,
            snap_distance_median_m=rounded(self.snap.quantile(0.5), 2),
            snap_distance_p90_m=rounded(self.snap.quantile(0.9), 2),
            sampling_interval_median_s=rounded(self.interval.quantile(0.5), 2),
            sampling_interval_p90_s=rounded(self.interval.quantile(0.9), 2),
            parallel_switches_per_km=round(self.parallel / km, 3) if km > 0 else None,
        )


_OVERFLOW: Final[tuple[str, ...]] = ("\x00overflow",)
"""Cell key of trips past ``max_cells``: never published alone."""

UNKNOWN_DIMENSION_VALUE: Final[str] = "unknown"
"""Value of a declared dimension the app did not give for a trip."""


class MatchQualityTracker:
    """The open window of quality cells and the last published one.

    Owned by :class:`~tempest_fastapi_sdk.geo.TrafficAggregator` (pass
    ``quality=`` there); usable alone to measure a matcher on stretches you
    already matched.

    Attributes:
        options (MatchQualityOptions): The window and the dimensions.
        min_devices (int): k of every published cell.
    """

    def __init__(self, options: MatchQualityOptions, *, min_devices: int) -> None:
        """Start with no window open and nothing published.

        Args:
            options (MatchQualityOptions): The window and the dimensions.
            min_devices (int): k of every published cell (``>= 2``).

        Raises:
            ValueError: When ``min_devices`` is below 2.
        """
        if min_devices < 2:
            raise ValueError("min_devices must be at least 2")
        self.options: MatchQualityOptions = options
        self.min_devices: int = min_devices
        self._epoch: int | None = None
        self._salt: bytes | None = None
        self._cells: dict[tuple[str, ...], _Cell] = {}
        self._published: MatchQualitySnapshotSchema = MatchQualitySnapshotSchema()

    def cell_of(self, dimensions: Mapping[str, str]) -> tuple[str, ...]:
        """Validate a trip's dimensions into its cell key.

        Args:
            dimensions (Mapping[str, str]): Values of declared dimensions.

        Returns:
            tuple[str, ...]: The values in declared order, ``"unknown"`` for
            the ones not given.

        Raises:
            ValueError: When a name is not declared, or a value is empty or
                longer than ``max_value_length``.
        """
        declared = self.options.dimensions
        unknown = set(dimensions) - set(declared)
        if unknown:
            raise ValueError(f"undeclared quality dimensions: {sorted(unknown)}")
        values: list[str] = []
        for name in declared:
            value = dimensions.get(name, UNKNOWN_DIMENSION_VALUE).strip()
            if not value or len(value) > self.options.max_value_length:
                raise ValueError(
                    f"quality dimension {name!r} needs 1 to "
                    f"{self.options.max_value_length} characters"
                )
            values.append(value)
        return tuple(values)

    def _window(self, now: float) -> int:
        """Window number of a server time.

        Args:
            now (float): Epoch seconds.

        Returns:
            int: ``floor(now / window_seconds)``.
        """
        return math.floor(now / self.options.window_seconds)

    def roll(self, now: float) -> None:
        """Close the open window when ``now`` is past it, and publish it.

        Closing computes the published aggregate and drops the salt, the
        device hashes and the counters.

        Args:
            now (float): Server time, epoch seconds.
        """
        if self._epoch is None or self._window(now) <= self._epoch:
            return
        self._published = self._build(self._epoch)
        self._epoch = None
        self._salt = None
        self._cells = {}

    def record(
        self,
        now: float,
        pseudonym: bytes,
        cell: tuple[str, ...],
        graph: RoadGraph,
        points: Sequence[QualityTrackPoint],
        result: RoadMatchResult,
        *,
        skip: int,
    ) -> None:
        """Fold one matched stretch into its cell of the open window.

        Args:
            now (float): Server time, epoch seconds.
            pseudonym (bytes): The device pseudonym (already an HMAC); only
                its HMAC under the window salt is kept.
            cell (tuple[str, ...]): From :meth:`cell_of` (``()`` without
                dimensions).
            graph (RoadGraph): The network the stretch was matched on.
            points (Sequence[QualityTrackPoint]): The stretch, in time order.
            result (RoadMatchResult): ``match_to_roads`` of ``points``.
            skip (int): Leading positions already measured with the previous
                stretch (the tail it was joined to): their match and
                position are not counted again, but the step from them is.
        """
        self.roll(now)
        if self._epoch is None or self._salt is None:
            self._epoch = self._window(now)
            self._salt = secrets.token_bytes(32)
        key = cell
        if key not in self._cells and len(self._cells) >= self.options.max_cells:
            key = _OVERFLOW
        target = self._cells.setdefault(key, _Cell())
        target.tokens.add(hmac.new(self._salt, pseudonym, hashlib.sha256).digest()[:16])
        matches = result.matches
        target.positions += max(0, len(points) - skip)
        for m in matches[skip:]:
            if m is None:
                continue
            target.matched += 1
            target.inferred += m.inferred
            target.snap.add(m.distance_m)
        target.breaks += result.breaks
        straight = _straight_flags(points)
        for i in range(1, len(points)):
            a, b = points[i - 1], points[i]
            target.interval.add(b.t - a.t)
            if straight[i] and straight[i - 1]:
                target.straight_m += math.hypot(b.x - a.x, b.y - a.y)
        before: tuple[int, MatchedFix] | None = None
        for i, m in enumerate(matches):
            if m is None:
                continue
            if (
                before is not None
                and i >= skip
                and straight[i]
                and straight[before[0]]
                and _road_of(before[1]) != _road_of(m)
                and _is_parallel_switch(graph, before[1], m)
            ):
                target.parallel += 1
            before = (i, m)

    def _build(self, epoch: int) -> MatchQualitySnapshotSchema:
        """The k-anonymous aggregate of a closing window.

        Args:
            epoch (int): The window number.

        Returns:
            MatchQualitySnapshotSchema: What is published for it.
        """
        k = self.min_devices
        names = list(self.options.dimensions)
        width = self.options.window_seconds
        snapshot = MatchQualitySnapshotSchema(
            window_start=datetime.fromtimestamp(epoch * width, UTC),
            window_end=datetime.fromtimestamp((epoch + 1) * width, UTC),
            min_devices=k,
            dimensions=names,
        )
        total = _Cell()
        for cell in self._cells.values():
            total.merge(cell)
        if len(total.tokens) < k:
            return snapshot
        snapshot.overall = total.publish({})
        if not names:
            return snapshot
        kept = sorted(
            (
                (key, cell)
                for key, cell in self._cells.items()
                if key != _OVERFLOW and len(cell.tokens) >= k
            ),
            key=lambda item: (len(item[1].tokens), item[0]),
        )
        kept_keys = {key for key, _ in kept}
        rest = _Cell()
        for key, cell in self._cells.items():
            if key not in kept_keys:
                rest.merge(cell)
        while kept and 0 < len(rest.tokens) < k:
            _, smallest = kept.pop(0)
            rest.merge(smallest)
        snapshot.cells = [
            cell.publish(dict(zip(names, key, strict=True)))
            for key, cell in sorted(kept, key=lambda item: item[0])
        ]
        if rest.positions or rest.tokens:
            snapshot.other = rest.publish({}) if kept else None
        return snapshot

    def snapshot(self, now: float) -> MatchQualitySnapshotSchema:
        """The last closed window's aggregate.

        Args:
            now (float): Server time, epoch seconds (closes a finished
                window first).

        Returns:
            MatchQualitySnapshotSchema: The aggregate; empty (``window_start``
            ``None``) before the first window closed.
        """
        self.roll(now)
        return self._published

    @property
    def open_cells(self) -> int:
        """Cells of the open window.

        Returns:
            int: How many dimension combinations the open window holds.
        """
        return len(self._cells)


__all__: list[str] = [
    "DEFAULT_MATCH_QUALITY_WINDOW_SECONDS",
    "MATCH_QUALITY_MAX_DIMENSIONS",
    "MATCH_QUALITY_REFERENCES",
    "MATCH_QUALITY_REFERENCE_BRAZIL_OSM",
    "MATCH_QUALITY_REFERENCE_KUBICKA",
    "PARALLEL_MAX_DEG",
    "PARALLEL_MIN_OFFSET_M",
    "SAMPLING_INTERVAL_BUCKETS_S",
    "SNAP_DISTANCE_BUCKETS_M",
    "STRAIGHT_HALF_M",
    "STRAIGHT_MAX_TURN_DEG",
    "UNKNOWN_DIMENSION_VALUE",
    "MatchQualityCellSchema",
    "MatchQualityIndicatorsSchema",
    "MatchQualityOptions",
    "MatchQualityReferenceSchema",
    "MatchQualityReportSchema",
    "MatchQualitySnapshotSchema",
    "MatchQualityTracker",
    "QualityHistogram",
    "QualityTrackPoint",
]
