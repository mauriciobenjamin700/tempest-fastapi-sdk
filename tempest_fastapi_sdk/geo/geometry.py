"""Offline spatial geometry — pure math, no dependencies, no network.

Building blocks for proximity search and geofencing that never leave the
process: a bounding box for a radius (the coarse SQL pre-filter), in-memory
radius filtering and nearest-neighbour ranking, point projection along a
bearing, point-in-polygon tests, polygon area, and path length. All angles
are decimal degrees (WGS84); all distances are kilometres.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from math import (
    asin,
    atan2,
    cos,
    degrees,
    radians,
    sin,
)
from typing import TypeVar

from tempest_fastapi_sdk.geo.distance import EARTH_RADIUS_KM, haversine_km
from tempest_fastapi_sdk.geo.schemas import BoundingBox, Coordinate

T = TypeVar("T")


def _as_coordinate(item: T, key: Callable[[T], Coordinate] | None) -> Coordinate:
    """Return the coordinate of ``item`` via ``key`` (or ``item`` itself)."""
    if key is not None:
        return key(item)
    if isinstance(item, Coordinate):
        return item
    raise TypeError(
        "point is not a Coordinate; pass key= to extract one from your item",
    )


def bounding_box(center: Coordinate, radius_km: float) -> BoundingBox:
    """Return the smallest lat/long box enclosing a radius around a point.

    The box over-covers the circle (it is the circle's bounding square), so
    use it as a **coarse pre-filter** — narrow rows to the box in SQL, then
    refine with :func:`~tempest_fastapi_sdk.geo.haversine_km`. Longitude
    span widens with latitude; near the poles it is clamped to the full
    ``[-180, 180]`` range.

    Args:
        center: The circle centre.
        radius_km: The radius in kilometres; must be non-negative.

    Returns:
        A :class:`~tempest_fastapi_sdk.geo.BoundingBox` enclosing the circle.

    Raises:
        ValueError: If ``radius_km`` is negative.
    """
    if radius_km < 0:
        raise ValueError("radius_km must be non-negative")

    lat_delta = degrees(radius_km / EARTH_RADIUS_KM)
    cos_lat = cos(radians(center.latitude))
    if cos_lat <= 1e-12:
        lon_delta = 180.0
    else:
        lon_delta = degrees(radius_km / (EARTH_RADIUS_KM * cos_lat))

    return BoundingBox(
        min_latitude=max(-90.0, center.latitude - lat_delta),
        max_latitude=min(90.0, center.latitude + lat_delta),
        min_longitude=max(-180.0, center.longitude - lon_delta),
        max_longitude=min(180.0, center.longitude + lon_delta),
    )


def within_radius(
    center: Coordinate,
    points: Iterable[T],
    radius_km: float,
    *,
    key: Callable[[T], Coordinate] | None = None,
) -> list[T]:
    """Return the items whose coordinate is within ``radius_km`` of ``center``.

    Works on any objects: pass ``key`` to extract a
    :class:`~tempest_fastapi_sdk.geo.Coordinate` from each item (omit it
    when the items already are coordinates). Returns ``[]`` when nothing
    matches, in line with the SDK collection convention.

    Args:
        center: The circle centre.
        points: The items to filter.
        radius_km: The inclusive radius in kilometres.
        key: Extracts a coordinate from an item; identity when ``None``.

    Returns:
        The matching items, in input order.
    """
    return [
        item
        for item in points
        if haversine_km(center, _as_coordinate(item, key)) <= radius_km
    ]


def nearest(
    center: Coordinate,
    points: Iterable[T],
    *,
    k: int = 1,
    key: Callable[[T], Coordinate] | None = None,
) -> list[T]:
    """Return the ``k`` items closest to ``center``, nearest first.

    Args:
        center: The reference point.
        points: The items to rank.
        k: How many closest items to return; must be positive.
        key: Extracts a coordinate from an item; identity when ``None``.

    Returns:
        Up to ``k`` items sorted by ascending distance from ``center``.

    Raises:
        ValueError: If ``k`` is not positive.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    ranked = sorted(
        points,
        key=lambda item: haversine_km(center, _as_coordinate(item, key)),
    )
    return ranked[:k]


def initial_bearing(origin: Coordinate, destination: Coordinate) -> float:
    """Return the initial compass bearing from ``origin`` to ``destination``.

    Args:
        origin: The starting coordinate.
        destination: The target coordinate.

    Returns:
        The forward azimuth in degrees, normalised to ``[0, 360)`` (0 = north,
        90 = east).
    """
    lat1 = radians(origin.latitude)
    lat2 = radians(destination.latitude)
    d_lon = radians(destination.longitude - origin.longitude)
    x = sin(d_lon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(d_lon)
    return (degrees(atan2(x, y)) + 360.0) % 360.0


def destination_point(
    origin: Coordinate,
    bearing_degrees: float,
    distance_km: float,
) -> Coordinate:
    """Project a point ``distance_km`` from ``origin`` along a bearing.

    The inverse of :func:`initial_bearing` — handy for circular geofences
    and "X km due north" style computations. Uses the spherical direct
    (forward) geodesic formula.

    Args:
        origin: The starting coordinate.
        bearing_degrees: The compass bearing to travel (0 = north).
        distance_km: The distance to travel in kilometres.

    Returns:
        The destination :class:`~tempest_fastapi_sdk.geo.Coordinate`.
    """
    angular = distance_km / EARTH_RADIUS_KM
    bearing = radians(bearing_degrees)
    lat1 = radians(origin.latitude)
    lon1 = radians(origin.longitude)

    lat2 = asin(
        sin(lat1) * cos(angular) + cos(lat1) * sin(angular) * cos(bearing),
    )
    lon2 = lon1 + atan2(
        sin(bearing) * sin(angular) * cos(lat1),
        cos(angular) - sin(lat1) * sin(lat2),
    )
    # Normalise longitude to [-180, 180].
    lon2_deg = (degrees(lon2) + 540.0) % 360.0 - 180.0
    return Coordinate(latitude=degrees(lat2), longitude=lon2_deg)


def point_in_polygon(point: Coordinate, polygon: list[Coordinate]) -> bool:
    """Return whether ``point`` lies inside ``polygon`` (ray casting).

    The polygon is an ordered ring of vertices; the closing edge (last →
    first) is implicit, so do not repeat the first vertex. Treats
    coordinates as planar, which is accurate for city/neighbourhood-scale
    geofences. Points exactly on an edge are not guaranteed either way.

    Args:
        point: The coordinate to test.
        polygon: The polygon vertices (at least 3), not self-closed.

    Returns:
        ``True`` when the point is inside the polygon.

    Raises:
        ValueError: If the polygon has fewer than 3 vertices.
    """
    if len(polygon) < 3:
        raise ValueError("polygon needs at least 3 vertices")

    inside = False
    x = point.longitude
    y = point.latitude
    count = len(polygon)
    for i in range(count):
        j = (i - 1) % count
        xi, yi = polygon[i].longitude, polygon[i].latitude
        xj, yj = polygon[j].longitude, polygon[j].latitude
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi
        if intersects:
            inside = not inside
    return inside


def polygon_area_km2(polygon: list[Coordinate]) -> float:
    """Return the area of a polygon on the sphere, in square kilometres.

    Uses the spherical-excess shoelace formula. The polygon is an ordered
    ring (not self-closed); winding direction does not matter (the absolute
    value is returned).

    Args:
        polygon: The polygon vertices (at least 3), not self-closed.

    Returns:
        The enclosed area in square kilometres.

    Raises:
        ValueError: If the polygon has fewer than 3 vertices.
    """
    if len(polygon) < 3:
        raise ValueError("polygon needs at least 3 vertices")

    total = 0.0
    count = len(polygon)
    for i in range(count):
        j = (i + 1) % count
        lon1 = radians(polygon[i].longitude)
        lon2 = radians(polygon[j].longitude)
        lat1 = radians(polygon[i].latitude)
        lat2 = radians(polygon[j].latitude)
        total += (lon2 - lon1) * (2 + sin(lat1) + sin(lat2))
    return abs(total * EARTH_RADIUS_KM * EARTH_RADIUS_KM / 2.0)


def path_length_km(points: list[Coordinate]) -> float:
    """Return the total great-circle length of a path through ``points``.

    Sums the :func:`~tempest_fastapi_sdk.geo.haversine_km` distance between
    each consecutive pair. A path of 0 or 1 points has length ``0.0``.

    Args:
        points: The ordered path vertices.

    Returns:
        The path length in kilometres.
    """
    return sum(haversine_km(points[i], points[i + 1]) for i in range(len(points) - 1))


__all__: list[str] = [
    "bounding_box",
    "destination_point",
    "initial_bearing",
    "nearest",
    "path_length_km",
    "point_in_polygon",
    "polygon_area_km2",
    "within_radius",
]
