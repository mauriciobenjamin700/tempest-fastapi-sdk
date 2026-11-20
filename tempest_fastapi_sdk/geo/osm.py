"""OpenStreetMap excerpt → :class:`RoadGraph`, with no GIS dependency.

Two input shapes, both plain JSON:

* **Overpass JSON** — what ``[out:json]`` answers to a query such as
  ``way["highway"](bbox); (._;>;); out;``: ``node`` elements with
  ``lat``/``lon`` and ``way`` elements with ``nodes`` and ``tags``.
* **GeoJSON** — a ``FeatureCollection`` of ``LineString`` /
  ``MultiLineString`` features (``osmtogeojson``, QGIS, ``ogr2ogr``), tags
  in ``properties``. Vertices are joined by coordinate (rounded to
  :data:`OSM_GEOJSON_COORDINATE_DIGITS` decimals), so lines that share an
  endpoint connect.

Each way becomes one edge per consecutive pair of its vertices, with ``id``
``"way/<id>:<n>"`` and ``data`` holding ``highway``, ``name`` and
``maxspeed`` when present. ``oneway`` follows OSM: ``yes``/``true``/``1`` and
``junction=roundabout`` are one-way forward, ``-1`` is one-way against the
drawing order, anything else two-way.

:func:`load_road_graph` reads a file and keeps the built graph in a small
in-process cache keyed by path, size and modification time — building is the
expensive part, and a service loads the same extract on every worker start.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from tempest_fastapi_sdk.geo.road_graph import RoadEdge, RoadGraph
from tempest_fastapi_sdk.geo.schemas import Coordinate

OSM_DRIVABLE_HIGHWAYS: Final[frozenset[str]] = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
    }
)
"""``highway`` values kept by default: roads a motor vehicle drives.

Footways, cycleways, paths, steps and tracks are left out; pass
``highways=None`` to keep every way that has a ``highway`` tag.
"""

OSM_GEOJSON_COORDINATE_DIGITS: Final[int] = 7
"""Decimals a GeoJSON vertex is rounded to before vertices are joined.

Seven decimals is ~1 cm, the precision OSM stores coordinates at.
"""

OSM_GRAPH_CACHE_SIZE: Final[int] = 8
"""Graphs :func:`load_road_graph` keeps in memory, least recently used out."""

_ONEWAY_FORWARD: Final[frozenset[str]] = frozenset({"yes", "true", "1"})
_ONEWAY_REVERSE: Final[frozenset[str]] = frozenset({"-1", "reverse"})
_KEPT_TAGS: Final[tuple[str, ...]] = ("highway", "name", "maxspeed")

_cache: dict[tuple[str, int, int], RoadGraph] = {}


def _direction(tags: Mapping[str, Any]) -> int:
    """Read OSM's one-way rules from a way's tags.

    Args:
        tags (Mapping[str, Any]): The way's tags.

    Returns:
        int: ``1`` one-way along the drawing order, ``-1`` one-way against
        it, ``0`` two-way.
    """
    oneway = str(tags.get("oneway", "")).strip().lower()
    if oneway in _ONEWAY_REVERSE:
        return -1
    if oneway in _ONEWAY_FORWARD:
        return 1
    if oneway == "no":
        return 0
    if str(tags.get("junction", "")).lower() in {"roundabout", "circular"}:
        return 1
    if str(tags.get("highway", "")).lower() == "motorway":
        return 1
    return 0


def _keep(tags: Mapping[str, Any], highways: frozenset[str] | None) -> bool:
    """Whether a way is a road this graph wants.

    Args:
        tags (Mapping[str, Any]): The way's tags.
        highways (frozenset[str] | None): Allowed ``highway`` values, or
            ``None`` for any.

    Returns:
        bool: ``True`` to keep it.
    """
    highway = tags.get("highway")
    if highway is None:
        return False
    if str(tags.get("area", "")).lower() == "yes":
        return False
    return highways is None or str(highway) in highways


def _way_edges(
    way_id: str,
    vertices: Sequence[int],
    tags: Mapping[str, Any],
) -> Iterable[RoadEdge]:
    """One edge per consecutive vertex pair of a way.

    Args:
        way_id (str): The way's id, used in each edge ``id``.
        vertices (Sequence[int]): Node indices in drawing order.
        tags (Mapping[str, Any]): The way's tags.

    Yields:
        RoadEdge: The edges, ``id`` ``"way/<way_id>:<n>"``.
    """
    direction = _direction(tags)
    data = {key: tags[key] for key in _KEPT_TAGS if key in tags}
    for n in range(len(vertices) - 1):
        a, b = vertices[n], vertices[n + 1]
        if a == b:
            continue
        if direction == -1:
            a, b = b, a
        yield RoadEdge(
            start=a,
            end=b,
            two_way=direction == 0,
            id=f"way/{way_id}:{n}",
            data=data,
        )


def road_graph_from_overpass(
    data: Mapping[str, Any],
    *,
    highways: frozenset[str] | None = OSM_DRIVABLE_HIGHWAYS,
) -> RoadGraph:
    """Build a graph from an Overpass API ``[out:json]`` answer.

    Only nodes some kept way uses become graph nodes. A way that names a
    node the answer does not carry (query without ``>;``) loses the edges
    touching it instead of failing.

    Args:
        data (Mapping[str, Any]): The parsed answer (``{"elements": [...]}``).
        highways (frozenset[str] | None): ``highway`` values to keep.
            Defaults to :data:`OSM_DRIVABLE_HIGHWAYS`; ``None`` keeps all.

    Returns:
        RoadGraph: The graph.
    """
    coords: dict[int, Coordinate] = {}
    ways: list[Mapping[str, Any]] = []
    for element in data.get("elements", []):
        kind = element.get("type")
        if kind == "node" and "lat" in element and "lon" in element:
            coords[int(element["id"])] = Coordinate(
                latitude=float(element["lat"]), longitude=float(element["lon"])
            )
        elif kind == "way":
            ways.append(element)
    index: dict[int, int] = {}
    nodes: list[Coordinate] = []
    edges: list[RoadEdge] = []
    for way in ways:
        tags = way.get("tags", {})
        if not _keep(tags, highways):
            continue
        run: list[int] = []
        runs: list[list[int]] = []
        for ref in way.get("nodes", []):
            point = coords.get(int(ref))
            if point is None:
                if len(run) > 1:
                    runs.append(run)
                run = []
                continue
            if int(ref) not in index:
                index[int(ref)] = len(nodes)
                nodes.append(point)
            run.append(index[int(ref)])
        if len(run) > 1:
            runs.append(run)
        for vertices in runs:
            edges.extend(_way_edges(str(way.get("id")), vertices, tags))
    return RoadGraph(nodes, edges)


def road_graph_from_geojson(
    data: Mapping[str, Any],
    *,
    highways: frozenset[str] | None = OSM_DRIVABLE_HIGHWAYS,
) -> RoadGraph:
    """Build a graph from a GeoJSON ``FeatureCollection`` of road lines.

    A feature without a ``highway`` property is kept only with
    ``highways=None`` (a hand-drawn network has no OSM tags). The way id is
    the feature ``id``, ``properties["@id"]`` or ``properties["id"]``, else
    the feature's position in the collection.

    Args:
        data (Mapping[str, Any]): The parsed ``FeatureCollection``.
        highways (frozenset[str] | None): ``highway`` values to keep.

    Returns:
        RoadGraph: The graph.
    """
    index: dict[tuple[float, float], int] = {}
    nodes: list[Coordinate] = []
    edges: list[RoadEdge] = []

    def node_of(position: Sequence[float]) -> int:
        """Index of a GeoJSON position, joined by rounded coordinate.

        Args:
            position (Sequence[float]): ``[longitude, latitude, ...]``.

        Returns:
            int: Node index.
        """
        lon = round(float(position[0]), OSM_GEOJSON_COORDINATE_DIGITS)
        lat = round(float(position[1]), OSM_GEOJSON_COORDINATE_DIGITS)
        key = (lat, lon)
        if key not in index:
            index[key] = len(nodes)
            nodes.append(Coordinate(latitude=lat, longitude=lon))
        return index[key]

    for position, feature in enumerate(data.get("features", [])):
        props: Mapping[str, Any] = feature.get("properties") or {}
        if highways is not None and not _keep(props, highways):
            continue
        geometry: Mapping[str, Any] = feature.get("geometry") or {}
        kind = geometry.get("type")
        if kind == "LineString":
            lines = [geometry.get("coordinates", [])]
        elif kind == "MultiLineString":
            lines = list(geometry.get("coordinates", []))
        else:
            continue
        raw_id = feature.get("id") or props.get("@id") or props.get("id") or position
        way_id = str(raw_id).removeprefix("way/")
        for line in lines:
            vertices = [node_of(p) for p in line]
            edges.extend(_way_edges(way_id, vertices, props))
    return RoadGraph(nodes, edges)


def road_graph_from_osm_json(
    data: Mapping[str, Any],
    *,
    highways: frozenset[str] | None = OSM_DRIVABLE_HIGHWAYS,
) -> RoadGraph:
    """Build a graph from either Overpass JSON or GeoJSON, by its shape.

    Args:
        data (Mapping[str, Any]): ``{"elements": ...}`` (Overpass) or
            ``{"type": "FeatureCollection", ...}`` (GeoJSON).
        highways (frozenset[str] | None): ``highway`` values to keep.

    Returns:
        RoadGraph: The graph.

    Raises:
        ValueError: When the document is neither shape.
    """
    if "elements" in data:
        return road_graph_from_overpass(data, highways=highways)
    if data.get("type") == "FeatureCollection":
        return road_graph_from_geojson(data, highways=highways)
    raise ValueError(
        "expected Overpass JSON ('elements') or a GeoJSON FeatureCollection"
    )


def load_road_graph(
    path: str | os.PathLike[str],
    *,
    highways: frozenset[str] | None = OSM_DRIVABLE_HIGHWAYS,
) -> RoadGraph:
    """Read an OSM extract from disk, cached by path, size and mtime.

    The same file loaded twice returns the same :class:`RoadGraph` object;
    rewriting the file (new size or mtime) rebuilds it. At most
    :data:`OSM_GRAPH_CACHE_SIZE` graphs stay cached. Synchronous: call it at
    startup or through ``asyncio.to_thread``.

    Args:
        path (str | os.PathLike[str]): A ``.json`` / ``.geojson`` file in
            either shape :func:`road_graph_from_osm_json` reads.
        highways (frozenset[str] | None): ``highway`` values to keep. Only the
            default is cached; another filter always rebuilds.

    Returns:
        RoadGraph: The graph.

    Raises:
        ValueError: When the file is neither shape.
    """
    resolved = Path(path).resolve()
    stat = resolved.stat()
    key = (str(resolved), stat.st_size, stat.st_mtime_ns)
    cacheable = highways is OSM_DRIVABLE_HIGHWAYS
    if cacheable and key in _cache:
        graph = _cache.pop(key)
        _cache[key] = graph
        return graph
    graph = road_graph_from_osm_json(
        json.loads(resolved.read_text(encoding="utf-8")), highways=highways
    )
    if cacheable:
        for stale in [k for k in _cache if k[0] == key[0]]:
            del _cache[stale]
        _cache[key] = graph
        while len(_cache) > OSM_GRAPH_CACHE_SIZE:
            del _cache[next(iter(_cache))]
    return graph


__all__: list[str] = [
    "OSM_DRIVABLE_HIGHWAYS",
    "OSM_GEOJSON_COORDINATE_DIGITS",
    "OSM_GRAPH_CACHE_SIZE",
    "load_road_graph",
    "road_graph_from_geojson",
    "road_graph_from_osm_json",
    "road_graph_from_overpass",
]
