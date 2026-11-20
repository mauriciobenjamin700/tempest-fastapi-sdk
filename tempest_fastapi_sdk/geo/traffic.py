"""Anonymous traffic from fleet GPS: speed per road, k-anonymous by construction.

:class:`TrafficAggregator` takes live positions per device, puts them on a
:class:`~tempest_fastapi_sdk.geo.RoadGraph` with
:func:`~tempest_fastapi_sdk.geo.match_to_roads`, and keeps, per directed
edge, the speed samples of a sliding window. What leaves it is a
:class:`TrafficSnapshotSchema`: median speed and distinct-device count per
edge, only for edges at least ``min_devices`` devices drove in the window.

The privacy rules (LGPD) are code here, each one with a test:

* **Start and end of every trip are cut.** A position within
  ``trim_radius_m`` of where the trip started is dropped on arrival. A
  position is only aggregated once the device is ``trim_radius_m`` away from
  it, and whatever is still waiting when the trip ends (explicitly or by
  silence) is dropped, so the last stretch never counts.
* **k-anonymity.** An edge is published only with ``min_devices`` distinct
  devices in the window (``>= 2``; default 5). The count is a lower bound by
  construction (see :class:`TrafficPrivacy`), never an over-count.
* **No individual trace persisted or exposed.** Raw positions live in
  process memory only until they are released or dropped; the store and the
  SSE event get the snapshot and nothing else.
* **Map-matching quality only in aggregate** (``quality=``): each released
  stretch is folded at once into fixed-size counters of a window cell;
  nothing per trip is kept, and a cell is published with k devices (see
  :mod:`~tempest_fastapi_sdk.geo.match_quality`).
* **Device ids only as rotating hashes.** The raw id is never kept: trips
  are keyed by an HMAC under a secret that exists only in this process, and
  each speed sample carries an HMAC of that and the edge under a salt that
  rotates every window and is forgotten after it, so samples of two edges
  cannot be linked to each other, nor across windows.

One aggregator per process, and it must see every position of a device: the
trip state is in memory. With several workers each one sees part of the
fleet; each counts fewer devices, so the split under-reports and never
publishes an edge below ``k``. Readers on any worker read the snapshot from a
shared :class:`TrafficSnapshotStore` (:class:`RedisTrafficSnapshotStore`).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import secrets
import statistics
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from pydantic import AwareDatetime, Field

from tempest_fastapi_sdk.geo.cache import _GeocodeRedisClient
from tempest_fastapi_sdk.geo.live import LivePositionSchema
from tempest_fastapi_sdk.geo.map_matching import (
    ROAD_MATCH_DEFAULTS,
    MatchedFix,
    RoadMatchOptions,
    RoadMatchResult,
    match_to_roads,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualityOptions,
    MatchQualitySnapshotSchema,
    MatchQualityTracker,
    QualityTrackPoint,
)
from tempest_fastapi_sdk.geo.road_graph import RoadGraph
from tempest_fastapi_sdk.geo.schemas import BoundingBox, Coordinate
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils import NonNegativeFloatField

if TYPE_CHECKING:
    from tempest_fastapi_sdk.geo.road_inference import OffRoadDensity
    from tempest_fastapi_sdk.sse.broker import SSEBroker

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_TRAFFIC_EVENT: Final[str] = "traffic_updated"
"""SSE event name :meth:`TrafficAggregator.flush` sends after a new snapshot."""

DEFAULT_TRAFFIC_SNAPSHOT_KEY: Final[str] = "geo:traffic:snapshot"
"""Redis key :class:`RedisTrafficSnapshotStore` writes the snapshot under."""

TRAFFIC_MIN_DEVICES_FLOOR: Final[int] = 2
"""Least ``min_devices`` accepted: one device per edge is not anonymity."""

TrafficAudienceResolver = Callable[[], Awaitable[Sequence[str]]]
"""Answers the SSE channels (usually user ids) told that traffic changed."""

TrafficDimensionsResolver = Callable[..., Awaitable[Mapping[str, str] | None]]
"""FastAPI dependency answering a trip's coarse quality dimensions, or ``None``.

Given to ``make_live_position_router(traffic_dimensions=...)``.
"""


@dataclass(frozen=True, slots=True)
class TrafficPrivacy:
    """The privacy and cleaning rules of a :class:`TrafficAggregator`.

    The distinct-device count of an edge is taken per salt epoch (each epoch
    is ``window_seconds`` long and has its own salt) and the window's count
    is the **largest** of the epochs it overlaps: hashes of two epochs never
    match, so adding them would count a device twice; the maximum can only
    under-count.

    Attributes:
        min_devices (int): k of k-anonymity: an edge is published only when
            this many distinct devices drove it in the window.
        window_seconds (int): Sliding window of the snapshot, and the life
            of each hash salt.
        trim_radius_m (float): Positions this close to the start of a trip,
            or to where the device is when the trip ends, are never
            aggregated.
        trip_gap_seconds (float): Silence after which a device's trip is
            over (its pending stretch is dropped) and the next position
            starts a new one.
        max_accuracy_m (float): Positions reporting a worse ``accuracy_m``
            are dropped.
        max_speed_kmh (float): A position implying a faster move from the
            previous kept one is an outlier and is dropped; speed samples
            above it are discarded.
        min_edge_coverage_m (float): Least distance driven on an edge for a
            pass to become a speed sample.
    """

    min_devices: int = 5
    window_seconds: int = 300
    trim_radius_m: float = 200.0
    trip_gap_seconds: float = 300.0
    max_accuracy_m: float = 50.0
    max_speed_kmh: float = 200.0
    min_edge_coverage_m: float = 10.0

    def __post_init__(self) -> None:
        """Validate the rules.

        Raises:
            ValueError: When ``min_devices`` is below
                :data:`TRAFFIC_MIN_DEVICES_FLOOR`, ``window_seconds`` is
                below 1, or a distance, speed or time is not positive.
        """
        if self.min_devices < TRAFFIC_MIN_DEVICES_FLOOR:
            raise ValueError(
                f"min_devices must be at least {TRAFFIC_MIN_DEVICES_FLOOR}: "
                "one device per edge is a trace, not traffic"
            )
        if self.window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        for name in (
            "trim_radius_m",
            "trip_gap_seconds",
            "max_accuracy_m",
            "max_speed_kmh",
        ):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")
        if self.min_edge_coverage_m < 0:
            raise ValueError("min_edge_coverage_m must be >= 0")


DEFAULT_TRAFFIC_PRIVACY: Final[TrafficPrivacy] = TrafficPrivacy()
"""k 5, 5-minute window, 200 m trim, 5-minute trip gap, 50 m accuracy."""


class TrafficEdgeSchema(BaseSchema):
    """Traffic on one directed road edge in the window.

    Attributes:
        edge_id (str): :meth:`RoadGraph.edge_key` — the edge ``id``, with a
            trailing ``~`` when driven against its drawing order.
        start (Coordinate): Where the directed edge starts.
        end (Coordinate): Where it ends.
        speed_kmh (float): Median of the speed samples.
        devices (int): Distinct devices in the window (a lower bound), at
            least the aggregator's ``min_devices``.
        speed_limit_kmh (float | None): The edge's numeric ``maxspeed`` tag,
            when it has one.
    """

    edge_id: str = Field(
        title="Edge id",
        description="Edge id; a trailing '~' means driven end to start.",
        examples=["way/123:4"],
    )
    start: Coordinate = Field(title="Start", description="Start of the edge.")
    end: Coordinate = Field(title="End", description="End of the edge.")
    speed_kmh: NonNegativeFloatField = Field(
        title="Speed (km/h)",
        description="Median speed in the window.",
        examples=[23.5],
    )
    devices: int = Field(
        ge=TRAFFIC_MIN_DEVICES_FLOOR,
        title="Devices",
        description="Distinct devices in the window (lower bound).",
        examples=[7],
    )
    speed_limit_kmh: NonNegativeFloatField | None = Field(
        default=None,
        title="Speed limit (km/h)",
        description="The edge's numeric maxspeed, when tagged.",
        examples=[50.0],
    )


class TrafficSnapshotSchema(BaseSchema):
    """What the aggregator publishes: the k-anonymous edges of a window.

    Attributes:
        generated_at (datetime | None): When the snapshot was computed;
            ``None`` in the empty answer the router gives before any
            snapshot was published (or after the last one expired).
        window_seconds (int | None): Length of the window it covers.
        min_devices (int | None): The k it was published under.
        edges (list[TrafficEdgeSchema]): Edges with at least ``min_devices``
            devices.
        quality (MatchQualitySnapshotSchema | None): The map-matching
            quality of the last closed quality window, when the aggregator
            measures it (``quality=``). Served by
            :func:`~tempest_fastapi_sdk.geo.make_match_quality_router`;
            :meth:`within` leaves it out.
    """

    generated_at: AwareDatetime | None = Field(
        default=None,
        title="Generated at",
        description="When the snapshot was computed; null before the first.",
    )
    window_seconds: int | None = Field(
        default=None, ge=1, title="Window (s)", description="Length of the window."
    )
    min_devices: int | None = Field(
        default=None,
        ge=TRAFFIC_MIN_DEVICES_FLOOR,
        title="k",
        description="Least distinct devices for an edge to be published.",
    )
    edges: list[TrafficEdgeSchema] = Field(
        default_factory=list,
        title="Edges",
        description="Published edges.",
    )
    quality: MatchQualitySnapshotSchema | None = Field(
        default=None,
        title="Map-matching quality",
        description="Aggregate quality of the last closed window, when measured.",
    )

    def within(self, bbox: BoundingBox) -> TrafficSnapshotSchema:
        """The same snapshot restricted to edges with an end inside ``bbox``.

        Args:
            bbox (BoundingBox): The area.

        Returns:
            TrafficSnapshotSchema: A copy with the matching edges and no
            ``quality`` (city-wide, read from its own route).
        """
        return self.model_copy(
            update={
                "edges": [
                    e
                    for e in self.edges
                    if bbox.contains(e.start) or bbox.contains(e.end)
                ],
                "quality": None,
            }
        )


@runtime_checkable
class TrafficSnapshotStore(Protocol):
    """Where the published snapshot lives for readers on any worker."""

    async def save(self, snapshot: TrafficSnapshotSchema, ttl_seconds: int, /) -> None:
        """Replace the snapshot.

        Args:
            snapshot: The new snapshot.
            ttl_seconds: How long it stays readable without a newer one.
        """
        ...

    async def load(self) -> TrafficSnapshotSchema | None:
        """Return the current snapshot.

        Returns:
            The snapshot, or ``None`` when there is none (or it expired).
        """
        ...


class InMemoryTrafficSnapshotStore:
    """A :class:`TrafficSnapshotStore` in this process (tests, one worker)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Start empty.

        Args:
            clock (Callable[[], float]): Monotonic seconds, for the TTL.
        """
        self._clock: Callable[[], float] = clock
        self._entry: tuple[float, TrafficSnapshotSchema] | None = None

    async def save(self, snapshot: TrafficSnapshotSchema, ttl_seconds: int, /) -> None:
        """Replace the snapshot.

        Args:
            snapshot (TrafficSnapshotSchema): The new snapshot.
            ttl_seconds (int): Seconds it stays readable.
        """
        self._entry = (self._clock() + ttl_seconds, snapshot)

    async def load(self) -> TrafficSnapshotSchema | None:
        """Return the snapshot while it is fresh.

        Returns:
            TrafficSnapshotSchema | None: The snapshot, or ``None``.
        """
        if self._entry is None or self._clock() >= self._entry[0]:
            return None
        return self._entry[1]


class RedisTrafficSnapshotStore:
    """A :class:`TrafficSnapshotStore` over an async Redis client, one key.

    Takes the client, like every Redis store the SDK ships; nothing here
    imports ``redis``. Only the snapshot is written — no position, no trip,
    no hash.

    Attributes:
        key (str): The Redis key.
    """

    def __init__(
        self,
        client: _GeocodeRedisClient,
        *,
        key: str = DEFAULT_TRAFFIC_SNAPSHOT_KEY,
    ) -> None:
        """Wrap a connected (or proxy) async Redis client.

        Args:
            client (_GeocodeRedisClient): ``redis.asyncio.Redis`` or a
                compatible client (``AsyncRedisManager.client_proxy``).
            key (str): The Redis key. Give each service its own.
        """
        self._client: _GeocodeRedisClient = client
        self.key: str = key

    async def save(self, snapshot: TrafficSnapshotSchema, ttl_seconds: int, /) -> None:
        """Write the snapshot as JSON with ``ex=ttl_seconds``.

        Args:
            snapshot (TrafficSnapshotSchema): The new snapshot.
            ttl_seconds (int): Seconds it stays readable.
        """
        await self._client.set(self.key, snapshot.model_dump_json(), ex=ttl_seconds)

    async def load(self) -> TrafficSnapshotSchema | None:
        """Read and validate the snapshot.

        A value that does not validate is logged (key only) and treated as
        absent; the next flush overwrites it.

        Returns:
            TrafficSnapshotSchema | None: The snapshot, or ``None``.
        """
        raw = await self._client.get(self.key)
        if raw is None:
            return None
        try:
            return TrafficSnapshotSchema.model_validate_json(raw)
        except ValueError:
            logger.warning("RedisTrafficSnapshotStore ignored an undecodable value")
            return None


@dataclass(slots=True)
class _Fix:
    """One kept position, on the graph's plane.

    Attributes:
        x (float): Plane x, metres.
        y (float): Plane y, metres.
        t (float): Device time, epoch seconds.
        coordinate (Coordinate): The position.
    """

    x: float
    y: float
    t: float
    coordinate: Coordinate


@dataclass(slots=True)
class _Trip:
    """In-memory state of one device's trip. Never stored, never exposed.

    Attributes:
        origin (tuple[float, float]): Where the trip started, plane metres.
        last (_Fix): Latest kept position (for outliers and the end trim).
        seen_at (float): Server time of the latest position, for the gap.
        pending (list[_Fix]): Positions waiting to be far enough behind.
        tail (_Fix | None): Last aggregated position, to join the next
            stretch to the previous one.
        carry (dict[int, list[float]]): Distance and time on the edge the
            device is on, not yet a sample.
        cell (tuple[str, ...]): Quality cell of the trip (its coarse
            dimension values), when quality is measured.
    """

    origin: tuple[float, float]
    last: _Fix
    seen_at: float
    pending: list[_Fix] = field(default_factory=list)
    tail: _Fix | None = None
    carry: dict[int, list[float]] = field(default_factory=dict)
    cell: tuple[str, ...] = ()


@dataclass(slots=True)
class _Sample:
    """One device's pass over one directed edge.

    Attributes:
        t (float): Device time of the pass.
        epoch (int): Salt epoch of ``t``.
        token (bytes): HMAC of the device pseudonym and the edge, under the
            epoch salt.
        speed_mps (float): Speed over the pass.
    """

    t: float
    epoch: int
    token: bytes
    speed_mps: float


def _speed_limit_kmh(raw: object) -> float | None:
    """Parse an OSM ``maxspeed`` into km/h.

    Args:
        raw (object): The tag value (``"50"``, ``"30 mph"``, ``60``).

    Returns:
        float | None: km/h, or ``None`` when not numeric.
    """
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    factor = 1.609344 if text.endswith("mph") else 1.0
    try:
        return float(text.removesuffix("mph").strip()) * factor
    except ValueError:
        return None


class TrafficAggregator:
    """Speed per road from fleet positions, published only k-anonymously.

    Feed it with :meth:`add_position` (every position of every device), end
    trips with :meth:`end_trip` when known, and call :meth:`flush`
    periodically (every 30-60 s): it releases what is far enough behind each
    device, matches it to the roads, expires silent trips, and saves the new
    snapshot to the store, telling the SSE audience.

    :meth:`add_position` and :meth:`process` are synchronous CPU work with no
    await inside, so under asyncio they never interleave.

    Attributes:
        graph (RoadGraph): The road network.
        privacy (TrafficPrivacy): The rules.
        match_options (RoadMatchOptions): Map-matching tuning.
        event_name (str): SSE event name of an update.
    """

    def __init__(
        self,
        graph: RoadGraph,
        *,
        privacy: TrafficPrivacy = DEFAULT_TRAFFIC_PRIVACY,
        match_options: RoadMatchOptions = ROAD_MATCH_DEFAULTS,
        store: TrafficSnapshotStore | None = None,
        broker: SSEBroker | None = None,
        audience: TrafficAudienceResolver | None = None,
        event_name: str = DEFAULT_TRAFFIC_EVENT,
        density: OffRoadDensity | None = None,
        quality: MatchQualityOptions | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialize the aggregator.

        Args:
            graph (RoadGraph): The road network positions are matched to.
            privacy (TrafficPrivacy): Trim radius, k, window and cleaning.
            match_options (RoadMatchOptions): Map-matching tuning.
            store (TrafficSnapshotStore | None): Where :meth:`flush` saves
                the snapshot. ``None`` keeps it only in :meth:`snapshot`.
            broker (SSEBroker | None): Where :meth:`flush` announces a new
                snapshot.
            audience (TrafficAudienceResolver | None): Who is told. Required
                with ``broker``: the SDK never decides who sees what.
            event_name (str): SSE event name of an update.
            density (OffRoadDensity | None): Collects the released positions
                that are off every road, for road inference. Its grid uses
                ``graph``'s plane.
            quality (MatchQualityOptions | None): Also measure the
                map-matching quality of every released stretch, published
                only per window and k-anonymously (k is
                ``privacy.min_devices``) in :meth:`snapshot`.
            clock (Callable[[], float]): Server time, epoch seconds.

        Raises:
            ValueError: When ``broker`` and ``audience`` are not given
                together.
        """
        if (broker is None) != (audience is None):
            raise ValueError(
                "broker and audience go together: the SDK never decides who "
                "is told about traffic, so a broker needs an audience callback",
            )
        self.graph: RoadGraph = graph
        self.privacy: TrafficPrivacy = privacy
        self.match_options: RoadMatchOptions = match_options
        self._store: TrafficSnapshotStore | None = store
        self._broker: SSEBroker | None = broker
        self._audience: TrafficAudienceResolver | None = audience
        self.event_name: str = event_name
        self._density: OffRoadDensity | None = density
        self._clock: Callable[[], float] = clock
        self._secret: bytes = secrets.token_bytes(32)
        self._trips: dict[bytes, _Trip] = {}
        self._salts: dict[int, bytes] = {}
        self._samples: dict[int, deque[_Sample]] = {}
        self._quality: MatchQualityTracker | None = (
            None
            if quality is None
            else MatchQualityTracker(quality, min_devices=privacy.min_devices)
        )

    def _pseudonym(self, device_id: str) -> bytes:
        """HMAC of a device id under this process's secret.

        Args:
            device_id (str): The raw id.

        Returns:
            bytes: The pseudonym; the raw id is not kept anywhere.
        """
        return hmac.new(self._secret, device_id.encode(), hashlib.sha256).digest()

    def _epoch(self, t: float) -> int:
        """Salt epoch of a time.

        Args:
            t (float): Epoch seconds.

        Returns:
            int: ``floor(t / window_seconds)``.
        """
        return math.floor(t / self.privacy.window_seconds)

    def _salt(self, epoch: int) -> bytes:
        """The salt of an epoch, drawn on first use.

        Args:
            epoch (int): Salt epoch.

        Returns:
            bytes: 32 random bytes.
        """
        salt = self._salts.get(epoch)
        if salt is None:
            salt = secrets.token_bytes(32)
            self._salts[epoch] = salt
        return salt

    def add_position(
        self,
        device_id: str,
        position: LivePositionSchema,
        *,
        dimensions: Mapping[str, str] | None = None,
    ) -> bool:
        """Take one position of a device.

        Dropped (answer ``False``) when: ``accuracy_m`` is worse than
        ``max_accuracy_m``; it is not newer than the previous one; it
        implies a move faster than ``max_speed_kmh``; or it is within
        ``trim_radius_m`` of where the trip started. A position after
        ``trip_gap_seconds`` of silence ends the previous trip (its pending
        stretch is dropped) and starts a new one.

        Args:
            device_id (str): The device (vehicle, phone). Hashed at once.
            position (LivePositionSchema): The position.
            dimensions (Mapping[str, str] | None): Coarse values of the
                quality dimensions declared in ``MatchQualityOptions``
                (``{"os": "android"}``) for this device's trip; the latest
                given applies to the whole trip. ``None`` keeps them.

        Returns:
            bool: Whether it was kept for aggregation.

        Raises:
            ValueError: When ``dimensions`` is given without ``quality=``, or
                names an undeclared dimension or a value out of bounds.
        """
        cell: tuple[str, ...] | None = None
        if dimensions is not None:
            if self._quality is None:
                raise ValueError("dimensions need TrafficAggregator(quality=...)")
            cell = self._quality.cell_of(dimensions)
        p = self.privacy
        if position.accuracy_m is not None and position.accuracy_m > p.max_accuracy_m:
            return False
        now = self._clock()
        key = self._pseudonym(device_id)
        x, y = self.graph.to_plane(position.coordinate)
        fix = _Fix(x, y, position.recorded_at.timestamp(), position.coordinate)
        trip = self._trips.get(key)
        if trip is not None and now - trip.seen_at > p.trip_gap_seconds:
            self._finish(key, trip)
            trip = None
        if trip is None:
            self._trips[key] = _Trip(
                origin=(x, y), last=fix, seen_at=now, cell=cell or ()
            )
            return False
        if cell is not None:
            trip.cell = cell
        if fix.t <= trip.last.t:
            return False
        moved = math.hypot(x - trip.last.x, y - trip.last.y)
        if moved / (fix.t - trip.last.t) * 3.6 > p.max_speed_kmh:
            return False
        trip.last = fix
        trip.seen_at = now
        if math.hypot(x - trip.origin[0], y - trip.origin[1]) < p.trim_radius_m:
            return False
        trip.pending.append(fix)
        return True

    def end_trip(self, device_id: str) -> None:
        """End a device's trip now (the trip, delivery or shift is over).

        What is still pending and within ``trim_radius_m`` of the last
        position is dropped; the rest is aggregated. The trip state is
        forgotten.

        Args:
            device_id (str): The device.
        """
        key = self._pseudonym(device_id)
        trip = self._trips.get(key)
        if trip is not None:
            self._finish(key, trip)

    def _finish(self, key: bytes, trip: _Trip) -> None:
        """Aggregate a trip's releasable rest and forget it.

        Args:
            key (bytes): The device pseudonym.
            trip (_Trip): Its state.
        """
        r = self.privacy.trim_radius_m
        lx, ly = trip.last.x, trip.last.y
        keep = [f for f in trip.pending if math.hypot(f.x - lx, f.y - ly) >= r]
        self._aggregate(key, trip, keep, final=True)
        del self._trips[key]

    def process(self) -> int:
        """Release, match and aggregate what is far enough behind each device.

        Also ends trips silent for ``trip_gap_seconds``, prunes samples
        older than the window and forgets salts no sample can use.

        Returns:
            int: Positions aggregated in this call.
        """
        now = self._clock()
        r = self.privacy.trim_radius_m
        released = 0
        for key, trip in list(self._trips.items()):
            if now - trip.seen_at > self.privacy.trip_gap_seconds:
                lx, ly = trip.last.x, trip.last.y
                released += sum(
                    1 for f in trip.pending if math.hypot(f.x - lx, f.y - ly) >= r
                )
                self._finish(key, trip)
                continue
            lx, ly = trip.last.x, trip.last.y
            cut = 0
            while (
                cut < len(trip.pending)
                and math.hypot(trip.pending[cut].x - lx, trip.pending[cut].y - ly) >= r
            ):
                cut += 1
            if cut:
                chunk = trip.pending[:cut]
                del trip.pending[:cut]
                self._aggregate(key, trip, chunk, final=False)
                released += cut
        self._prune(now)
        return released

    def _aggregate(
        self, key: bytes, trip: _Trip, chunk: list[_Fix], *, final: bool
    ) -> None:
        """Match a released stretch and turn it into speed samples.

        Args:
            key (bytes): The device pseudonym.
            trip (_Trip): Its state (``tail`` and ``carry`` are updated).
            chunk (list[_Fix]): Released positions, in time order.
            final (bool): Whether the trip ends here (the carry is emitted).
        """
        if self._density is not None:
            radius = self._density.off_road_m
            for f in chunk:
                if not self.graph.candidates_near(f.x, f.y, radius, 1):
                    self._density.add(key, f.coordinate, f.t)
        fixes = ([trip.tail] if trip.tail is not None else []) + chunk
        if chunk and (len(fixes) >= 2 or self._quality is not None):
            result = match_to_roads(
                [f.coordinate for f in fixes],
                self.graph,
                self.match_options,
                timestamps=[f.t for f in fixes],
            )
            if len(fixes) >= 2:
                self._accumulate(key, trip, fixes, result)
            if self._quality is not None:
                self._quality.record(
                    self._clock(),
                    key,
                    trip.cell,
                    self.graph,
                    [QualityTrackPoint(f.x, f.y, f.t) for f in fixes],
                    result,
                    skip=len(fixes) - len(chunk),
                )
        if chunk:
            trip.tail = chunk[-1]
        if final:
            for directed, (dist, dt, t) in trip.carry.items():
                self._emit(key, directed, dist, dt, t)
            trip.carry.clear()

    def _accumulate(
        self,
        key: bytes,
        trip: _Trip,
        fixes: list[_Fix],
        result: RoadMatchResult,
    ) -> None:
        """Spread distance and time of each step over the edges it drove.

        Args:
            key (bytes): The device pseudonym.
            trip (_Trip): The trip; its ``carry`` holds the open edge.
            fixes (list[_Fix]): Positions, the first one possibly the
                previous stretch's tail.
            result (RoadMatchResult): ``match_to_roads`` of ``fixes``.
        """
        chain_of: dict[int, int] = {}
        for number, route in enumerate(result.routes):
            for i in range(route.first_fix, route.last_fix + 1):
                chain_of[i] = number
        g = self.graph
        prev_index = -1
        a: MatchedFix | None = None
        for i, match in enumerate(result.matches):
            if match is None or match.inferred:
                continue
            if a is None or chain_of.get(prev_index) != chain_of.get(i):
                prev_index, a = i, match
                continue
            dt = fixes[i].t - fixes[prev_index].t
            pieces: list[tuple[int, float]]
            if a.directed == match.directed and match.offset_m >= a.offset_m:
                pieces = [(a.directed, match.offset_m - a.offset_m)]
            else:
                target = g.dir_from[match.directed]
                _, prev = g.shortest_paths(g.dir_to[a.directed], [target], math.inf)
                pieces = [(a.directed, g.dir_length[a.directed] - a.offset_m)]
                pieces += [(d, g.dir_length[d]) for d in g.path_to(prev, target)]
                pieces.append((match.directed, match.offset_m))
            total = sum(length for _, length in pieces)
            if total > 0 and dt > 0:
                for directed, length in pieces:
                    self._carry(
                        key, trip, directed, length, dt * length / total, fixes[i].t
                    )
            prev_index, a = i, match

    def _carry(
        self,
        key: bytes,
        trip: _Trip,
        directed: int,
        dist: float,
        dt: float,
        t: float,
    ) -> None:
        """Add a piece to the open edge, closing every other open edge.

        Args:
            key (bytes): The device pseudonym.
            trip (_Trip): The trip.
            directed (int): Directed edge of the piece.
            dist (float): Metres driven on it.
            dt (float): Seconds spent on it.
            t (float): Device time at the end of the step.
        """
        if directed not in trip.carry:
            for other, (d, s, ts) in list(trip.carry.items()):
                self._emit(key, other, d, s, ts)
                del trip.carry[other]
            trip.carry[directed] = [0.0, 0.0, t]
        entry = trip.carry[directed]
        entry[0] += dist
        entry[1] += dt
        entry[2] = t

    def _emit(
        self, key: bytes, directed: int, dist: float, dt: float, t: float
    ) -> None:
        """Record one pass as a speed sample, if it is long and plausible.

        Args:
            key (bytes): The device pseudonym.
            directed (int): Directed edge.
            dist (float): Metres driven on it.
            dt (float): Seconds spent on it.
            t (float): Device time of the pass.
        """
        p = self.privacy
        if dist < p.min_edge_coverage_m or dt <= 0:
            return
        speed = dist / dt
        if speed * 3.6 > p.max_speed_kmh:
            return
        if t <= self._clock() - p.window_seconds:
            return
        epoch = self._epoch(t)
        token = hmac.new(
            self._salt(epoch),
            key + b"|" + self.graph.edge_key(directed).encode(),
            hashlib.sha256,
        ).digest()[:16]
        self._samples.setdefault(directed, deque()).append(
            _Sample(t=t, epoch=epoch, token=token, speed_mps=speed)
        )

    def _prune(self, now: float) -> None:
        """Drop samples older than the window and salts no sample can use.

        Args:
            now (float): Server time.
        """
        horizon = now - self.privacy.window_seconds
        for directed in list(self._samples):
            queue = self._samples[directed]
            kept = deque(s for s in queue if s.t > horizon)
            if kept:
                self._samples[directed] = kept
            else:
                del self._samples[directed]
        oldest = self._epoch(horizon)
        for epoch in [e for e in self._salts if e < oldest]:
            del self._salts[epoch]
        if self._density is not None:
            self._density.roll(now)
        if self._quality is not None:
            self._quality.roll(now)

    def snapshot(self) -> TrafficSnapshotSchema:
        """The k-anonymous edges of the current window.

        Returns:
            TrafficSnapshotSchema: Edges with at least ``min_devices``
            distinct devices, sorted by ``edge_id``.
        """
        now = self._clock()
        self._prune(now)
        k = self.privacy.min_devices
        edges: list[TrafficEdgeSchema] = []
        for directed, queue in self._samples.items():
            per_epoch: dict[int, set[bytes]] = {}
            for s in queue:
                per_epoch.setdefault(s.epoch, set()).add(s.token)
            devices = max((len(tokens) for tokens in per_epoch.values()), default=0)
            if devices < k:
                continue
            speeds = [s.speed_mps for s in queue]
            start, end = self.graph.edge_coordinates(directed)
            edge = self.graph.edges[self.graph.dir_edge[directed]]
            edges.append(
                TrafficEdgeSchema(
                    edge_id=self.graph.edge_key(directed),
                    start=start,
                    end=end,
                    speed_kmh=round(statistics.median(speeds) * 3.6, 1),
                    devices=devices,
                    speed_limit_kmh=_speed_limit_kmh(edge.data.get("maxspeed")),
                )
            )
        edges.sort(key=lambda e: e.edge_id)
        return TrafficSnapshotSchema(
            generated_at=datetime.fromtimestamp(now, UTC),
            window_seconds=self.privacy.window_seconds,
            min_devices=k,
            edges=edges,
            quality=self.quality_snapshot(),
        )

    def quality_snapshot(self) -> MatchQualitySnapshotSchema | None:
        """The map-matching quality of the last closed quality window.

        Returns:
            MatchQualitySnapshotSchema | None: The k-anonymous aggregate
            (empty before the first window closed); ``None`` without
            ``quality=``.
        """
        if self._quality is None:
            return None
        return self._quality.snapshot(self._clock())

    async def flush(self) -> TrafficSnapshotSchema:
        """Process, publish the snapshot to the store and tell the audience.

        Returns:
            TrafficSnapshotSchema: The snapshot just published.
        """
        self.process()
        snapshot = self.snapshot()
        now = datetime.fromtimestamp(self._clock(), UTC)
        if self._store is not None:
            await self._store.save(snapshot, self.privacy.window_seconds * 2)
        if self._broker is not None and self._audience is not None:
            payload = {
                "generated_at": now.isoformat(),
                "edges": len(snapshot.edges),
            }
            for recipient in dict.fromkeys(await self._audience()):
                await self._broker.publish(recipient, payload, event=self.event_name)
        return snapshot

    @property
    def active_trips(self) -> int:
        """Trips currently held in memory.

        Returns:
            int: How many devices have an open trip.
        """
        return len(self._trips)


__all__: list[str] = [
    "DEFAULT_TRAFFIC_EVENT",
    "DEFAULT_TRAFFIC_PRIVACY",
    "DEFAULT_TRAFFIC_SNAPSHOT_KEY",
    "TRAFFIC_MIN_DEVICES_FLOOR",
    "InMemoryTrafficSnapshotStore",
    "RedisTrafficSnapshotStore",
    "TrafficAggregator",
    "TrafficAudienceResolver",
    "TrafficDimensionsResolver",
    "TrafficEdgeSchema",
    "TrafficPrivacy",
    "TrafficSnapshotSchema",
    "TrafficSnapshotStore",
]
