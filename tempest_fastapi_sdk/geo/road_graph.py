"""Road network for map-matching, traffic and road inference — pure Python.

A port of ``tempest-react-sdk``'s ``src/geo/road-graph.ts`` (0.90.0), with
the same contract on the wire, so a graph the backend serves is the graph the
app matches against:

* **Nodes** are WGS84 points; **edges** refer to them by index
  (``{"from": 0, "to": 1}``), directed ``from → to`` unless ``twoWay``.
* Every other field of an edge (``id``, road class, name, speed limit) is the
  app's own and comes back untouched.

Building projects the nodes onto a local equirectangular plane in metres,
splits two-way edges into both directions and indexes the segments in a
50 m grid. Queries (nearest roads, bounded shortest path) cost what they
explore, not the size of the graph. No ``numpy``: the ``[geo]`` extra only
brings ``httpx``, and everything here is loops over lists.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, NamedTuple

from tempest_fastapi_sdk.geo.distance import EARTH_RADIUS_KM
from tempest_fastapi_sdk.geo.schemas import BoundingBox, Coordinate

ROAD_GRID_CELL_M: Final[float] = 50.0
"""Edge length of a cell of the spatial index, in metres.

Ported from ``tempest-react-sdk``'s ``ROAD_GRID_CELL_M``. Each segment is
registered in the cells it passes through (sampled at this step), so a long
motorway segment costs cells along its length, not across its bounding box.
"""

_RESERVED_EDGE_KEYS: Final[frozenset[str]] = frozenset({"from", "to", "twoWay", "id"})


@dataclass(frozen=True, slots=True)
class RoadEdge:
    """One road segment between two nodes of a :class:`RoadGraph`.

    A road drawn as a polyline is several edges, one per consecutive pair of
    its vertices: the matcher works on straight segments.

    Attributes:
        start (int): Index of the start node (``"from"`` on the wire).
        end (int): Index of the end node (``"to"`` on the wire).
        two_way (bool | None): Whether the road can also be driven
            ``end → start``. ``None`` takes the graph's default.
            OpenStreetMap's ``oneway=no`` is ``True`` here.
        id (str | None): Stable identifier, the key traffic is reported
            under (``"way/123:4"`` from :func:`road_graph_from_overpass`).
        data (Mapping[str, Any]): Any other field of the app (road class,
            name, speed limit), returned untouched.
    """

    start: int
    end: int
    two_way: bool | None = None
    id: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize in the ``tempest-react-sdk`` ``RoadEdge`` shape.

        Returns:
            dict[str, Any]: ``{"from", "to", "twoWay"?, "id"?, **data}``.
        """
        out: dict[str, Any] = {
            key: value
            for key, value in self.data.items()
            if key not in _RESERVED_EDGE_KEYS
        }
        out["from"] = self.start
        out["to"] = self.end
        if self.two_way is not None:
            out["twoWay"] = self.two_way
        if self.id is not None:
            out["id"] = self.id
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RoadEdge:
        """Parse an edge in the ``tempest-react-sdk`` ``RoadEdge`` shape.

        Args:
            raw (Mapping[str, Any]): ``{"from", "to", "twoWay"?, "id"?, ...}``.

        Returns:
            RoadEdge: The edge; unknown keys land in ``data``.

        Raises:
            ValueError: When ``from``/``to`` are not integers.
        """
        start = raw.get("from")
        end = raw.get("to")
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("a road edge needs integer 'from' and 'to'")
        two_way = raw.get("twoWay")
        edge_id = raw.get("id")
        return cls(
            start=start,
            end=end,
            two_way=bool(two_way) if two_way is not None else None,
            id=str(edge_id) if edge_id is not None else None,
            data={k: v for k, v in raw.items() if k not in _RESERVED_EDGE_KEYS},
        )


class RoadCandidate(NamedTuple):
    """A matching candidate: a directed edge and a point projected onto it.

    Attributes:
        dir (int): Directed edge index.
        px (float): Projection, plane x in metres.
        py (float): Projection, plane y in metres.
        offset (float): Metres from the start of the directed edge to the
            projection.
        distance (float): Metres from the point to the projection.
    """

    dir: int
    px: float
    py: float
    offset: float
    distance: float


class RoadGraph:
    """A road network ready for matching: nodes, edges, index and adjacency.

    Build it once per region and reuse it for every trace — building is the
    expensive part; querying is cheap. The plane is equirectangular around
    the centre of the nodes' bounding box: within a city the scale error is a
    fraction of a percent. Graphs that cross the antimeridian are not
    supported — split them.

    Attributes:
        nodes (tuple[Coordinate, ...]): The nodes as given.
        edges (tuple[RoadEdge, ...]): The edges as given; matches point back
            into this tuple.
        two_way (bool): Default for edges that leave ``two_way`` unset.
        directed_edge_count (int): Directed edges after two-way roads are
            split into both directions.
    """

    def __init__(
        self,
        nodes: Sequence[Coordinate],
        edges: Sequence[RoadEdge],
        *,
        two_way: bool = False,
    ) -> None:
        """Build the graph.

        Args:
            nodes (Sequence[Coordinate]): Road network vertices, WGS84.
            edges (Sequence[RoadEdge]): Road segments, directed
                ``start → end`` unless two-way.
            two_way (bool): Default for edges that leave ``two_way`` unset.
                ``False`` — every edge one-way — is how a source that already
                lists both directions as separate edges arrives.

        Raises:
            ValueError: When an edge refers to a node index that does not
                exist.
        """
        self.nodes: tuple[Coordinate, ...] = tuple(nodes)
        self.edges: tuple[RoadEdge, ...] = tuple(edges)
        self.two_way: bool = two_way
        n = len(self.nodes)
        if n:
            lats = [p.latitude for p in self.nodes]
            lons = [p.longitude for p in self.nodes]
            self._lat0: float = (min(lats) + max(lats)) / 2
            self._lon0: float = (min(lons) + max(lons)) / 2
        else:
            self._lat0 = 0.0
            self._lon0 = 0.0
        self._ky: float = EARTH_RADIUS_KM * 1000 * math.radians(1)
        self._kx: float = self._ky * math.cos(math.radians(self._lat0))
        self._nx: list[float] = [(p.longitude - self._lon0) * self._kx for p in nodes]
        self._ny: list[float] = [(p.latitude - self._lat0) * self._ky for p in nodes]

        m = len(self.edges)
        self._edge_dirs: list[int] = [-1] * (2 * m)
        self.dir_from: list[int] = []
        self.dir_to: list[int] = []
        self.dir_edge: list[int] = []
        self.dir_reversed: list[bool] = []
        self.dir_length: list[float] = []
        for index, edge in enumerate(self.edges):
            for node in (edge.start, edge.end):
                if node < 0 or node >= n:
                    raise ValueError(f"road edge {index} has no node {node}")
            a, b = edge.start, edge.end
            length = math.hypot(self._nx[b] - self._nx[a], self._ny[b] - self._ny[a])
            self._edge_dirs[2 * index] = len(self.dir_from)
            self._add_dir(a, b, index, False, length)
            if self.is_two_way(index):
                self._edge_dirs[2 * index + 1] = len(self.dir_from)
                self._add_dir(b, a, index, True, length)
        self.directed_edge_count: int = len(self.dir_from)
        self._out: list[list[int]] = [[] for _ in range(n)]
        for d, start in enumerate(self.dir_from):
            self._out[start].append(d)

        self._cells: dict[tuple[int, int], list[int]] = {}
        for index, edge in enumerate(self.edges):
            ax, ay = self._nx[edge.start], self._ny[edge.start]
            bx, by = self._nx[edge.end], self._ny[edge.end]
            steps = max(1, math.ceil(math.hypot(bx - ax, by - ay) / ROAD_GRID_CELL_M))
            last: tuple[int, int] | None = None
            for s in range(steps + 1):
                key = _cell_of(ax + (bx - ax) * s / steps, ay + (by - ay) * s / steps)
                if key == last:
                    continue
                last = key
                bucket = self._cells.setdefault(key, [])
                if not bucket or bucket[-1] != index:
                    bucket.append(index)

    def _add_dir(
        self, start: int, end: int, edge: int, reversed_: bool, length: float
    ) -> None:
        """Append one directed edge.

        Args:
            start (int): Start node.
            end (int): End node.
            edge (int): Input edge index.
            reversed_ (bool): Whether it runs ``end → start`` of the input edge.
            length (float): Length in metres.
        """
        self.dir_from.append(start)
        self.dir_to.append(end)
        self.dir_edge.append(edge)
        self.dir_reversed.append(reversed_)
        self.dir_length.append(length)

    def is_two_way(self, edge_index: int) -> bool:
        """Return whether an input edge is driven in both directions.

        Args:
            edge_index (int): Index into :attr:`edges`.

        Returns:
            bool: The edge's ``two_way``, or the graph default.
        """
        flag = self.edges[edge_index].two_way
        return self.two_way if flag is None else flag

    def reverse_of(self, directed: int) -> int:
        """Return the opposite direction of a directed edge.

        Args:
            directed (int): Directed edge index.

        Returns:
            int: The other direction, or ``-1`` on a one-way road.
        """
        edge = self.dir_edge[directed]
        if self.dir_reversed[directed]:
            return self._edge_dirs[2 * edge]
        return self._edge_dirs[2 * edge + 1]

    def to_plane(self, coordinate: Coordinate) -> tuple[float, float]:
        """Project a coordinate onto the graph's plane.

        Args:
            coordinate (Coordinate): WGS84 point.

        Returns:
            tuple[float, float]: ``(x, y)`` in metres.
        """
        return (
            (coordinate.longitude - self._lon0) * self._kx,
            (coordinate.latitude - self._lat0) * self._ky,
        )

    def from_plane(self, x: float, y: float) -> Coordinate:
        """Inverse of :meth:`to_plane`.

        Args:
            x (float): Plane x, metres.
            y (float): Plane y, metres.

        Returns:
            Coordinate: The WGS84 point.
        """
        return Coordinate(
            latitude=self._lat0 + y / self._ky, longitude=self._lon0 + x / self._kx
        )

    def node_xy(self, node: int) -> tuple[float, float]:
        """Return a node on the plane.

        Args:
            node (int): Node index.

        Returns:
            tuple[float, float]: ``(x, y)`` in metres.
        """
        return self._nx[node], self._ny[node]

    def project_onto(self, directed: int, x: float, y: float) -> RoadCandidate:
        """Project a plane point onto a directed edge, clamped to the segment.

        Args:
            directed (int): Directed edge index.
            x (float): Point x, metres.
            y (float): Point y, metres.

        Returns:
            RoadCandidate: The candidate for that edge.
        """
        ax = self._nx[self.dir_from[directed]]
        ay = self._ny[self.dir_from[directed]]
        dx = self._nx[self.dir_to[directed]] - ax
        dy = self._ny[self.dir_to[directed]] - ay
        len2 = dx * dx + dy * dy
        t = (
            0.0
            if len2 == 0
            else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / len2))
        )
        px = ax + t * dx
        py = ay + t * dy
        return RoadCandidate(
            directed,
            px,
            py,
            t * self.dir_length[directed],
            math.hypot(x - px, y - py),
        )

    def candidates_near(
        self, x: float, y: float, radius: float, limit: int
    ) -> list[RoadCandidate]:
        """The roads within ``radius`` of a point, nearest first, per direction.

        ``limit`` counts **roads** (input edges), not directions: a two-way
        road yields two candidates. Ties keep the scan order of the grid, the
        same as the TypeScript original, so both pick the same candidates.

        Args:
            x (float): Point x, metres.
            y (float): Point y, metres.
            radius (float): Search radius, metres.
            limit (int): Maximum number of roads kept.

        Returns:
            list[RoadCandidate]: The candidates, nearest road first.
        """
        reach = math.ceil(radius / ROAD_GRID_CELL_M + 0.5)
        cx = math.floor(x / ROAD_GRID_CELL_M)
        cy = math.floor(y / ROAD_GRID_CELL_M)
        seen: set[int] = set()
        found: list[RoadCandidate] = []
        for i in range(cx - reach, cx + reach + 1):
            for j in range(cy - reach, cy + reach + 1):
                bucket = self._cells.get((i, j))
                if bucket is None:
                    continue
                for edge in bucket:
                    if edge in seen:
                        continue
                    seen.add(edge)
                    candidate = self.project_onto(self._edge_dirs[2 * edge], x, y)
                    if candidate.distance <= radius:
                        found.append(candidate)
        found.sort(key=lambda c: c.distance)
        out: list[RoadCandidate] = []
        for forward in found[:limit]:
            out.append(forward)
            back = self._edge_dirs[2 * self.dir_edge[forward.dir] + 1]
            if back >= 0:
                out.append(
                    RoadCandidate(
                        back,
                        forward.px,
                        forward.py,
                        self.dir_length[back] - forward.offset,
                        forward.distance,
                    )
                )
        return out

    def shortest_paths(
        self, source: int, targets: Iterable[int], limit: float
    ) -> tuple[dict[int, float], dict[int, int]]:
        """Bounded Dijkstra from one node until every target is settled.

        Stops once every target is settled or the nearest unsettled node is
        farther than ``limit``.

        Args:
            source (int): Start node.
            targets (Iterable[int]): Nodes whose distance is wanted.
            limit (float): Search bound, metres (``math.inf`` for none).

        Returns:
            tuple[dict[int, float], dict[int, int]]: Distance of every node
            reached, and the directed edge each was reached by (absent for
            the source).
        """
        pending = set(targets)
        dist: dict[int, float] = {source: 0.0}
        prev: dict[int, int] = {}
        heap: list[tuple[float, int]] = [(0.0, source)]
        while heap:
            key, node = heapq.heappop(heap)
            if key > dist[node]:
                continue
            if key > limit:
                break
            if node in pending:
                pending.discard(node)
                if not pending:
                    break
            for directed in self._out[node]:
                nxt = self.dir_to[directed]
                candidate = key + self.dir_length[directed]
                if candidate > limit:
                    continue
                if nxt not in dist or candidate < dist[nxt]:
                    dist[nxt] = candidate
                    prev[nxt] = directed
                    heapq.heappush(heap, (candidate, nxt))
        return dist, prev

    def path_to(self, prev: Mapping[int, int], node: int) -> list[int]:
        """Directed edges of the shortest path to ``node``, in travel order.

        Args:
            prev (Mapping[int, int]): The predecessor map of
                :meth:`shortest_paths`.
            node (int): A node it reached.

        Returns:
            list[int]: Directed edge indices; empty when ``node`` is the
            source.
        """
        path: list[int] = []
        at = node
        while at in prev:
            directed = prev[at]
            path.append(directed)
            at = self.dir_from[directed]
        path.reverse()
        return path

    def distance_to_road_m(
        self, coordinate: Coordinate, radius_m: float
    ) -> float | None:
        """Distance from a point to the nearest road, within ``radius_m``.

        Args:
            coordinate (Coordinate): The point.
            radius_m (float): Search radius, metres.

        Returns:
            float | None: Metres to the nearest edge, or ``None`` when no
            edge is within ``radius_m``.
        """
        x, y = self.to_plane(coordinate)
        found = self.candidates_near(x, y, radius_m, 1)
        return found[0].distance if found else None

    def edge_coordinates(self, directed: int) -> tuple[Coordinate, Coordinate]:
        """Start and end of a directed edge, in travel order.

        Args:
            directed (int): Directed edge index.

        Returns:
            tuple[Coordinate, Coordinate]: ``(start, end)``.
        """
        return self.nodes[self.dir_from[directed]], self.nodes[self.dir_to[directed]]

    def edge_key(self, directed: int) -> str:
        """Stable key of a directed edge: its ``id`` (or index) and direction.

        Args:
            directed (int): Directed edge index.

        Returns:
            str: ``"<id>"`` forward, ``"<id>~"`` reversed; ``<id>`` falls back
            to ``"#<index>"`` when the edge has no ``id``.
        """
        edge_index = self.dir_edge[directed]
        edge_id = self.edges[edge_index].id
        base = edge_id if edge_id is not None else f"#{edge_index}"
        return f"{base}~" if self.dir_reversed[directed] else base

    def extract(self, bbox: BoundingBox) -> RoadGraph:
        """The sub-graph of edges with an end inside ``bbox`` — one tile.

        Nodes are re-indexed; edge ``id`` and ``data`` are kept, so traffic
        keyed by :meth:`edge_key` still lines up with the tile.

        Args:
            bbox (BoundingBox): The tile.

        Returns:
            RoadGraph: The tile's graph, same ``two_way`` default.
        """
        remap: dict[int, int] = {}
        nodes: list[Coordinate] = []
        edges: list[RoadEdge] = []

        def index_of(node: int) -> int:
            """Return the tile index of ``node``, adding it on first use.

            Args:
                node (int): Index in this graph.

            Returns:
                int: Index in the tile.
            """
            if node not in remap:
                remap[node] = len(nodes)
                nodes.append(self.nodes[node])
            return remap[node]

        for edge in self.edges:
            if bbox.contains(self.nodes[edge.start]) or bbox.contains(
                self.nodes[edge.end]
            ):
                edges.append(
                    RoadEdge(
                        start=index_of(edge.start),
                        end=index_of(edge.end),
                        two_way=edge.two_way,
                        id=edge.id,
                        data=edge.data,
                    )
                )
        return RoadGraph(nodes, edges, two_way=self.two_way)

    def to_dict(self) -> dict[str, Any]:
        """Serialize in the ``tempest-react-sdk`` ``createRoadGraph`` input shape.

        Returns:
            dict[str, Any]: ``{"nodes": [{"latitude", "longitude"}],
            "edges": [{"from", "to", ...}], "twoWay": bool}``.
        """
        return {
            "nodes": [
                {"latitude": p.latitude, "longitude": p.longitude} for p in self.nodes
            ],
            "edges": [edge.to_dict() for edge in self.edges],
            "twoWay": self.two_way,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RoadGraph:
        """Build a graph from the ``tempest-react-sdk`` ``createRoadGraph`` shape.

        Args:
            raw (Mapping[str, Any]): ``{"nodes", "edges", "twoWay"?}``.

        Returns:
            RoadGraph: The graph.
        """
        nodes = [Coordinate.model_validate(node) for node in raw.get("nodes", [])]
        edges = [RoadEdge.from_dict(edge) for edge in raw.get("edges", [])]
        return cls(nodes, edges, two_way=bool(raw.get("twoWay", False)))


def _cell_of(x: float, y: float) -> tuple[int, int]:
    """Grid cell of a plane point.

    Args:
        x (float): Plane x, metres.
        y (float): Plane y, metres.

    Returns:
        tuple[int, int]: Column and row.
    """
    return math.floor(x / ROAD_GRID_CELL_M), math.floor(y / ROAD_GRID_CELL_M)


__all__: list[str] = [
    "ROAD_GRID_CELL_M",
    "RoadCandidate",
    "RoadEdge",
    "RoadGraph",
]
