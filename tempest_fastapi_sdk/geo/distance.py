"""Great-circle distance — pure math, no dependencies, no network."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from tempest_fastapi_sdk.geo.schemas import Coordinate

# Mean Earth radius in kilometers (IUGG mean radius R1).
EARTH_RADIUS_KM: float = 6371.0088


def haversine_km(origin: Coordinate, destination: Coordinate) -> float:
    """Compute the great-circle distance between two coordinates.

    Uses the Haversine formula, which treats the Earth as a sphere. This
    is the straight-line ("as the crow flies") distance, not the road
    distance — feed it through a circuity factor (see
    :func:`tempest_fastapi_sdk.geo.estimate_travel`) for a road estimate.

    Args:
        origin: The starting coordinate.
        destination: The ending coordinate.

    Returns:
        The great-circle distance in kilometers (always non-negative).
    """
    lat1 = radians(origin.latitude)
    lat2 = radians(destination.latitude)
    d_lat = radians(destination.latitude - origin.latitude)
    d_lon = radians(destination.longitude - origin.longitude)

    a = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))
