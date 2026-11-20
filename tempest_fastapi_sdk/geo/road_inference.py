"""Roads the base map lacks, inferred from anonymous fleet density.

Two pieces:

* :class:`OffRoadDensity` — a fine grid (``cell_m``, default 5 m) counting
  the positions a :class:`~tempest_fastapi_sdk.geo.TrafficAggregator`
  released (already trimmed at both ends of each trip) that are farther than
  ``off_road_m`` from every road of the graph. A cell is only committed when
  ``min_devices`` distinct devices hit its 25 m block in one epoch (default
  one day); until then it lives in memory as rotating per-block hashes, and at the end
  of the epoch the hashes and the salt are dropped. What survives — and what
  :meth:`OffRoadDensity.to_dict` lets you persist — is ``points`` and
  ``devices`` per cell, nothing per device.
* :func:`infer_road_candidates` — dense cells → morphological closing →
  Zhang-Suen thinning → skeleton graph → polylines (Douglas-Peucker) →
  **diff against the graph**: only lines mostly away from every existing road
  come out, as :class:`RoadCandidateSchema` with ``status="candidate"``.
  Nothing is added to the graph: a candidate goes to human review
  (:func:`road_candidates_geojson` opens in JOSM or QGIS).

Pure Python, no ``numpy``/``scikit-image``: the grid is sparse (a set of
cells), and thinning a few thousand cells is milliseconds.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Literal

from pydantic import Field

from tempest_fastapi_sdk.geo.distance import EARTH_RADIUS_KM
from tempest_fastapi_sdk.geo.geometry import path_length_km
from tempest_fastapi_sdk.geo.road_graph import RoadGraph
from tempest_fastapi_sdk.geo.schemas import Coordinate
from tempest_fastapi_sdk.geo.traffic import TRAFFIC_MIN_DEVICES_FLOOR
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils import NonNegativeFloatField

Cell = tuple[int, int]
"""A grid cell: column and row."""

DEFAULT_DENSITY_CELL_M: Final[float] = 5.0
"""Side of a density cell, metres: finer than a lane, coarser than GPS noise."""

DEFAULT_OFF_ROAD_M: Final[float] = 20.0
"""Distance from every road beyond which a position counts as off-road.

The phase-0 measurement of tempest-react-sdk#667 (Kubicka et al., 247 251
fixes) found 9.4% of fixes farther than 20 m from any road of their OSM
excerpt, and 90.6% within it: the raw signal of a road the base lacks.
"""

DEFAULT_DENSITY_BLOCK_CELLS: Final[int] = 5
"""Side of a k-anonymity block, in cells: 5 x 5 m = 25 m.

k is decided per block, not per 5 m cell: with GPS noise a device spreads
its points over several cells across the road, so k distinct devices in one
5 m cell is rare where k in a 25 m block is not. Measured on the synthetic
street of ``scripts/bench_geo_traffic.py`` (k 5, sigma 5 m): deciding per
cell, 20 trips a day gave no candidate and 40 recovered the street; per
block, 12 trips recover 77% of it and 20 all of it. Counts stay per cell.
"""

DEFAULT_DENSITY_EPOCH_SECONDS: Final[int] = 86_400
"""Epoch of the density hashes: one day, then only counts remain."""

_NEIGHBOURS_8: Final[tuple[Cell, ...]] = (
    (0, 1),
    (1, 1),
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, -1),
    (-1, 0),
    (-1, 1),
)
_ORTHOGONAL: Final[tuple[Cell, ...]] = ((0, 1), (1, 0), (0, -1), (-1, 0))
_DIAGONAL: Final[tuple[Cell, ...]] = ((1, 1), (1, -1), (-1, -1), (-1, 1))


class OffRoadDensity:
    """k-anonymous density of off-road positions on a fixed metric grid.

    The grid is anchored at ``reference`` (equirectangular, metres), not at
    any graph, so a persisted density stays valid when the road extract is
    reloaded or redrawn.

    Attributes:
        reference (Coordinate): Origin of the grid.
        cell_m (float): Side of a cell, metres.
        off_road_m (float): Distance from every road beyond which a position
            is counted.
        min_devices (int): Distinct devices a privacy block needs in one
            epoch for its cells to be committed.
        block_cells (int): Side of a privacy block, in cells: k-anonymity is
            decided per block (``block_cells * cell_m`` metres, 25 m by
            default), counts are kept per cell.
        epoch_seconds (int): Length of an epoch (life of a salt).
        cells (dict[Cell, tuple[int, int]]): Committed ``(points, devices)``
            per cell; ``devices`` sums the per-epoch distinct counts of the
            cell's block.
    """

    def __init__(
        self,
        reference: Coordinate,
        *,
        cell_m: float = DEFAULT_DENSITY_CELL_M,
        off_road_m: float = DEFAULT_OFF_ROAD_M,
        min_devices: int = 5,
        block_cells: int = DEFAULT_DENSITY_BLOCK_CELLS,
        epoch_seconds: int = DEFAULT_DENSITY_EPOCH_SECONDS,
    ) -> None:
        """Start an empty density.

        Args:
            reference (Coordinate): Origin of the grid (the city centre).
            cell_m (float): Side of a cell, metres.
            off_road_m (float): Off-road threshold, metres.
            min_devices (int): k for a block to be committed (``>= 2``).
            block_cells (int): Side of a privacy block, in cells (``>= 1``).
            epoch_seconds (int): Length of an epoch, seconds.

        Raises:
            ValueError: When ``min_devices`` is below 2 or a size is not
                positive.
        """
        if min_devices < TRAFFIC_MIN_DEVICES_FLOOR:
            raise ValueError(
                f"min_devices must be at least {TRAFFIC_MIN_DEVICES_FLOOR}"
            )
        if cell_m <= 0 or off_road_m <= 0 or epoch_seconds < 1 or block_cells < 1:
            raise ValueError(
                "cell_m, off_road_m, block_cells and epoch_seconds must be positive"
            )
        self.reference: Coordinate = reference
        self.cell_m: float = cell_m
        self.off_road_m: float = off_road_m
        self.min_devices: int = min_devices
        self.block_cells: int = block_cells
        self.epoch_seconds: int = epoch_seconds
        self._ky: float = EARTH_RADIUS_KM * 1000 * math.radians(1)
        self._kx: float = self._ky * math.cos(math.radians(reference.latitude))
        self.cells: dict[Cell, tuple[int, int]] = {}
        self._open: dict[int, dict[Cell, tuple[set[bytes], dict[Cell, int]]]] = {}
        self._salts: dict[int, bytes] = {}
        self._closed_before: int | None = None

    def cell_of(self, coordinate: Coordinate) -> Cell:
        """The cell a coordinate falls in.

        Args:
            coordinate (Coordinate): The point.

        Returns:
            Cell: Column and row.
        """
        x = (coordinate.longitude - self.reference.longitude) * self._kx
        y = (coordinate.latitude - self.reference.latitude) * self._ky
        return math.floor(x / self.cell_m), math.floor(y / self.cell_m)

    def centre_of(self, cell: Cell) -> Coordinate:
        """The coordinate of a cell's centre.

        Args:
            cell (Cell): Column and row.

        Returns:
            Coordinate: Its centre.
        """
        x = (cell[0] + 0.5) * self.cell_m
        y = (cell[1] + 0.5) * self.cell_m
        return Coordinate(
            latitude=self.reference.latitude + y / self._ky,
            longitude=self.reference.longitude + x / self._kx,
        )

    def add(self, device_key: bytes, coordinate: Coordinate, t: float) -> None:
        """Count one off-road position (the aggregator calls this).

        Args:
            device_key (bytes): The device pseudonym (already an HMAC).
            coordinate (Coordinate): The position.
            t (float): Device time, epoch seconds.
        """
        epoch = math.floor(t / self.epoch_seconds)
        if self._closed_before is not None and epoch < self._closed_before:
            return
        cell = self.cell_of(coordinate)
        block = (cell[0] // self.block_cells, cell[1] // self.block_cells)
        salt = self._salts.get(epoch)
        if salt is None:
            salt = secrets.token_bytes(32)
            self._salts[epoch] = salt
        token = hmac.new(
            salt, device_key + f"|{block[0]},{block[1]}".encode(), hashlib.sha256
        ).digest()[:16]
        tokens, counts = self._open.setdefault(epoch, {}).setdefault(block, (set(), {}))
        tokens.add(token)
        counts[cell] = counts.get(cell, 0) + 1

    def roll(self, now: float) -> None:
        """Commit every epoch that ended before ``now``; drop its hashes.

        Blocks with fewer than ``min_devices`` distinct devices in the epoch
        are discarded with their cells, not carried over.

        Args:
            now (float): Server time, epoch seconds.
        """
        current = math.floor(now / self.epoch_seconds)
        self._closed_before = current
        for epoch in [e for e in self._open if e < current]:
            for tokens, counts in self._open.pop(epoch).values():
                if len(tokens) < self.min_devices:
                    continue
                for cell, points in counts.items():
                    old_points, old_devices = self.cells.get(cell, (0, 0))
                    self.cells[cell] = (
                        old_points + points,
                        old_devices + len(tokens),
                    )
        for epoch in [e for e in self._salts if e < current]:
            del self._salts[epoch]

    def to_dict(self) -> dict[str, Any]:
        """The committed counts, safe to persist (no device data).

        Returns:
            dict[str, Any]: Grid parameters and ``[[col, row, points,
            devices], ...]``.
        """
        return {
            "reference": {
                "latitude": self.reference.latitude,
                "longitude": self.reference.longitude,
            },
            "cell_m": self.cell_m,
            "off_road_m": self.off_road_m,
            "min_devices": self.min_devices,
            "block_cells": self.block_cells,
            "epoch_seconds": self.epoch_seconds,
            "cells": [[c[0], c[1], p, d] for c, (p, d) in sorted(self.cells.items())],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> OffRoadDensity:
        """Restore what :meth:`to_dict` wrote.

        Args:
            raw (Mapping[str, Any]): The persisted dict.

        Returns:
            OffRoadDensity: The density, with its committed cells.
        """
        density = cls(
            Coordinate.model_validate(raw["reference"]),
            cell_m=float(raw["cell_m"]),
            off_road_m=float(raw["off_road_m"]),
            min_devices=int(raw["min_devices"]),
            block_cells=int(raw.get("block_cells", DEFAULT_DENSITY_BLOCK_CELLS)),
            epoch_seconds=int(raw["epoch_seconds"]),
        )
        for col, row, points, devices in raw.get("cells", []):
            density.cells[(int(col), int(row))] = (int(points), int(devices))
        return density


class RoadCandidateSchema(BaseSchema):
    """A road the fleet drives that the graph does not have — for review.

    Attributes:
        status (Literal["candidate"]): Always ``"candidate"``: nothing enters
            the map without a person accepting it.
        coordinates (list[Coordinate]): The inferred centreline.
        length_m (float): Its length.
        points (int): Off-road positions behind it.
        devices (int): Largest committed device count of its cells (per-epoch
            distinct devices, summed over epochs).
        novel_fraction (float): Share of the line farther than ``off_road_m``
            from every road of the graph.
    """

    status: Literal["candidate"] = Field(
        default="candidate",
        title="Status",
        description="Always 'candidate': human review decides.",
    )
    coordinates: list[Coordinate] = Field(
        default_factory=list,
        title="Coordinates",
        description="The inferred centreline.",
    )
    length_m: NonNegativeFloatField = Field(
        title="Length (m)", description="Length of the centreline."
    )
    points: int = Field(ge=0, title="Points", description="Positions behind it.")
    devices: int = Field(
        ge=0, title="Devices", description="Largest committed device count."
    )
    novel_fraction: float = Field(
        ge=0,
        le=1,
        title="Novel fraction",
        description="Share of the line away from every known road.",
    )


def _closing(cells: set[Cell]) -> set[Cell]:
    """Morphological closing (3x3 dilation, then erosion) of a cell set.

    Args:
        cells (set[Cell]): The occupied cells.

    Returns:
        set[Cell]: The closed set: one-cell holes and gaps filled.
    """
    dilated = set(cells)
    for cx, cy in cells:
        for dx, dy in _NEIGHBOURS_8:
            dilated.add((cx + dx, cy + dy))
    return {
        (cx, cy)
        for cx, cy in dilated
        if all((cx + dx, cy + dy) in dilated for dx, dy in _NEIGHBOURS_8)
    }


def _thin(cells: set[Cell]) -> set[Cell]:
    """Zhang-Suen thinning of a cell set down to a one-cell skeleton.

    Neighbours ``P2..P9`` run clockwise from north (``+y``).

    Args:
        cells (set[Cell]): The cells to thin.

    Returns:
        set[Cell]: The skeleton.
    """
    skeleton = set(cells)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            remove: list[Cell] = []
            for cx, cy in skeleton:
                p = [int((cx + dx, cy + dy) in skeleton) for dx, dy in _NEIGHBOURS_8]
                b = sum(p)
                if b < 2 or b > 6:
                    continue
                a = sum(1 for i in range(8) if p[i] == 0 and p[(i + 1) % 8] == 1)
                if a != 1:
                    continue
                n, e, s, w = p[0], p[2], p[4], p[6]
                if (step == 0 and (n * e * s == 0 and e * s * w == 0)) or (
                    step == 1 and (n * e * w == 0 and n * s * w == 0)
                ):
                    remove.append((cx, cy))
            if remove:
                changed = True
                skeleton.difference_update(remove)
    return skeleton


def _adjacent(skeleton: set[Cell], cell: Cell) -> list[Cell]:
    """Skeleton neighbours of a cell, without diagonal shortcuts.

    A diagonal neighbour that an orthogonal neighbour also touches is left
    out, so a staircase reads as a line and not as a chain of junctions.
    The relation stays symmetric.

    Args:
        skeleton (set[Cell]): The skeleton.
        cell (Cell): The cell.

    Returns:
        list[Cell]: Its neighbours.
    """
    cx, cy = cell
    orth = [
        (cx + dx, cy + dy) for dx, dy in _ORTHOGONAL if (cx + dx, cy + dy) in skeleton
    ]
    out = list(orth)
    for dx, dy in _DIAGONAL:
        d = (cx + dx, cy + dy)
        if d not in skeleton:
            continue
        if any(abs(o[0] - d[0]) + abs(o[1] - d[1]) == 1 for o in orth):
            continue
        out.append(d)
    return out


def _trace(skeleton: set[Cell]) -> list[list[Cell]]:
    """Split a skeleton into paths between junctions and ends.

    Args:
        skeleton (set[Cell]): One-cell-wide skeleton.

    Returns:
        list[list[Cell]]: Cell paths; a closed loop comes back as one path
        that starts and ends on the same cell.
    """
    adjacency = {c: _adjacent(skeleton, c) for c in skeleton}
    nodes = {c for c, n in adjacency.items() if len(n) != 2}
    used: set[frozenset[Cell]] = set()
    paths: list[list[Cell]] = []

    def walk(start: Cell, nxt: Cell) -> list[Cell]:
        """Follow the skeleton from ``start`` through ``nxt`` to a node.

        Args:
            start (Cell): A node (or a loop's first cell).
            nxt (Cell): The neighbour to leave by.

        Returns:
            list[Cell]: The path, ``start`` included.
        """
        path = [start]
        prev, at = start, nxt
        used.add(frozenset((start, nxt)))
        while True:
            path.append(at)
            if at in nodes or at == start:
                return path
            onward = [c for c in adjacency[at] if c != prev]
            if not onward or frozenset((at, onward[0])) in used:
                return path
            used.add(frozenset((at, onward[0])))
            prev, at = at, onward[0]

    for node in sorted(nodes):
        for nxt in adjacency[node]:
            if frozenset((node, nxt)) not in used:
                paths.append(walk(node, nxt))
    for cell in sorted(skeleton):
        for nxt in adjacency[cell]:
            if frozenset((cell, nxt)) not in used:
                paths.append(walk(cell, nxt))
    return paths


def _simplify(points: Sequence[tuple[float, float]], tolerance: float) -> list[int]:
    """Douglas-Peucker on plane points; indices of the kept vertices.

    Args:
        points (Sequence[tuple[float, float]]): Plane points, metres.
        tolerance (float): Largest allowed deviation, metres.

    Returns:
        list[int]: Kept indices, first and last included.
    """
    if len(points) < 3:
        return list(range(len(points)))
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        ax, ay = points[first]
        bx, by = points[last]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, at = -1.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if norm == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if d > worst:
                worst, at = d, i
        if worst > tolerance and at > 0:
            keep.add(at)
            stack.append((first, at))
            stack.append((at, last))
    return sorted(keep)


def infer_road_candidates(
    density: OffRoadDensity,
    graph: RoadGraph,
    *,
    min_points: int = 3,
    min_length_m: float = 40.0,
    min_novel_fraction: float = 0.8,
    connect_m: float | None = None,
) -> list[RoadCandidateSchema]:
    """Turn committed off-road density into road candidates for review.

    Cells with at least ``min_points`` positions are closed (3x3), thinned
    (Zhang-Suen), traced into lines between junctions, simplified
    (Douglas-Peucker at one cell), joined at each end to the road within
    ``connect_m`` and kept when at least ``min_length_m``
    long and at least ``min_novel_fraction`` of the line is farther than
    ``density.off_road_m`` from every road of ``graph``.

    Args:
        density (OffRoadDensity): Committed density (only k-anonymous cells).
        graph (RoadGraph): The base network the candidates are diffed against.
        min_points (int): Least positions for a cell to count.
        min_length_m (float): Shorter lines are dropped (spurs, noise).
        min_novel_fraction (float): Least share of the line off every road.
        connect_m (float | None): An end of a line within this distance of a
            road is extended to its projection on that road, so the candidate
            shows where it joins the network (the stretch near a crossing
            street is never off-road, so the density alone stops short of
            it). ``None`` is ``2 * density.off_road_m``; ``0`` disables it.

    Returns:
        list[RoadCandidateSchema]: Candidates, longest first.
    """
    reach = 2 * density.off_road_m if connect_m is None else connect_m
    dense = {c for c, (points, _) in density.cells.items() if points >= min_points}
    skeleton = _thin(_closing(dense))
    candidates: list[RoadCandidateSchema] = []
    for path in _trace(skeleton):
        plane = [
            ((c[0] + 0.5) * density.cell_m, (c[1] + 0.5) * density.cell_m) for c in path
        ]
        length = sum(math.dist(plane[i], plane[i + 1]) for i in range(len(plane) - 1))
        if length < min_length_m:
            continue
        coords = [density.centre_of(path[i]) for i in _simplify(plane, density.cell_m)]
        novel = sum(
            1
            for c in path
            if graph.distance_to_road_m(density.centre_of(c), density.off_road_m)
            is None
        ) / len(path)
        if novel < min_novel_fraction:
            continue
        coords = _connect(coords, graph, reach)
        near = _around(path, 2)
        candidates.append(
            RoadCandidateSchema(
                coordinates=coords,
                length_m=round(path_length_km(coords) * 1000, 1),
                points=sum(density.cells.get(c, (0, 0))[0] for c in near),
                devices=max((density.cells.get(c, (0, 0))[1] for c in near), default=0),
                novel_fraction=round(novel, 3),
            )
        )
    candidates.sort(key=lambda c: c.length_m, reverse=True)
    return candidates


def _connect(
    coords: list[Coordinate], graph: RoadGraph, reach: float
) -> list[Coordinate]:
    """Extend both ends of a line to the nearest road within ``reach``.

    Args:
        coords (list[Coordinate]): The line, at least two points.
        graph (RoadGraph): The base network.
        reach (float): Search radius, metres; ``0`` returns ``coords``.

    Returns:
        list[Coordinate]: The line, with a road point added at each end that
        has one in reach.
    """
    if reach <= 0 or len(coords) < 2:
        return coords
    out = list(coords)
    for end in (0, -1):
        x, y = graph.to_plane(out[end])
        found = graph.candidates_near(x, y, reach, 1)
        if found and found[0].distance > 0:
            joint = graph.from_plane(found[0].px, found[0].py)
            if end == 0:
                out.insert(0, joint)
            else:
                out.append(joint)
    return out


def _around(path: Iterable[Cell], reach: int) -> set[Cell]:
    """Every cell within ``reach`` cells (Chebyshev) of a path.

    Args:
        path (Iterable[Cell]): The cells.
        reach (int): Radius in cells.

    Returns:
        set[Cell]: The neighbourhood, path included.
    """
    out: set[Cell] = set()
    for cx, cy in path:
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                out.add((cx + dx, cy + dy))
    return out


def road_candidates_geojson(
    candidates: Sequence[RoadCandidateSchema],
) -> dict[str, Any]:
    """Candidates as a GeoJSON ``FeatureCollection`` for JOSM/QGIS review.

    Args:
        candidates (Sequence[RoadCandidateSchema]): The candidates.

    Returns:
        dict[str, Any]: One ``LineString`` feature per candidate, its
        numbers in ``properties``.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[p.longitude, p.latitude] for p in c.coordinates],
                },
                "properties": {
                    "status": c.status,
                    "length_m": c.length_m,
                    "points": c.points,
                    "devices": c.devices,
                    "novel_fraction": c.novel_fraction,
                },
            }
            for c in candidates
        ],
    }


__all__: list[str] = [
    "DEFAULT_DENSITY_BLOCK_CELLS",
    "DEFAULT_DENSITY_CELL_M",
    "DEFAULT_DENSITY_EPOCH_SECONDS",
    "DEFAULT_OFF_ROAD_M",
    "Cell",
    "OffRoadDensity",
    "RoadCandidateSchema",
    "infer_road_candidates",
    "road_candidates_geojson",
]
