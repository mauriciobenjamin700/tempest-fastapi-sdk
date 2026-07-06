"""Schemas for the geolocation module — coordinates and travel estimates."""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.geo.enums import TravelMode
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils import (
    LatitudeField,
    LongitudeField,
    NonNegativeFloatField,
)


class Coordinate(BaseSchema):
    """A geographic point in decimal degrees (WGS84).

    Attributes:
        latitude: Latitude in decimal degrees, constrained to [-90, 90].
        longitude: Longitude in decimal degrees, constrained to [-180, 180].
    """

    latitude: LatitudeField = Field(
        title="Latitude",
        description="Latitude in decimal degrees (WGS84).",
        examples=[-23.5505],
    )
    longitude: LongitudeField = Field(
        title="Longitude",
        description="Longitude in decimal degrees (WGS84).",
        examples=[-46.6333],
    )


class TravelEstimate(BaseSchema):
    """An estimated road trip between two coordinates for a single mode.

    Attributes:
        mode: The travel mode the estimate was computed for.
        distance_km: Road distance in kilometers (never negative).
        duration_minutes: Estimated travel time in minutes (never negative).
        source: How the estimate was produced — ``"heuristic"`` (offline
            Haversine + circuity factor + average speed) or ``"osrm"``
            (real road routing via an OSRM server).
    """

    mode: TravelMode = Field(
        title="Travel mode",
        description="The travel mode the estimate was computed for.",
        examples=[TravelMode.CAR],
    )
    distance_km: NonNegativeFloatField = Field(
        title="Distance (km)",
        description="Road distance in kilometers.",
        examples=[12.4],
    )
    duration_minutes: NonNegativeFloatField = Field(
        title="Duration (min)",
        description="Estimated travel time in minutes.",
        examples=[18.6],
    )
    source: str = Field(
        default="heuristic",
        title="Estimate source",
        description='How the estimate was produced ("heuristic" or "osrm").',
        examples=["heuristic"],
    )
    geometry: list[Coordinate] = Field(
        default_factory=list,
        title="Route geometry",
        description=(
            "Decoded route polyline as ordered coordinates. Empty unless a "
            "routing backend was asked to include the geometry."
        ),
    )


class BoundingBox(BaseSchema):
    """An axis-aligned latitude/longitude box (a coarse spatial pre-filter).

    A box is cheap to test in SQL (``lat BETWEEN ? AND ? AND lon BETWEEN
    ? AND ?``), so it is the fast first pass of a radius search: narrow to
    the box in the database, then refine with the exact
    :func:`~tempest_fastapi_sdk.geo.haversine_km` distance.

    Attributes:
        min_latitude: Southern edge, decimal degrees.
        max_latitude: Northern edge, decimal degrees.
        min_longitude: Western edge, decimal degrees.
        max_longitude: Eastern edge, decimal degrees.
    """

    min_latitude: LatitudeField = Field(
        title="Min latitude",
        description="Southern edge of the box (decimal degrees).",
        examples=[-23.6],
    )
    max_latitude: LatitudeField = Field(
        title="Max latitude",
        description="Northern edge of the box (decimal degrees).",
        examples=[-23.5],
    )
    min_longitude: LongitudeField = Field(
        title="Min longitude",
        description="Western edge of the box (decimal degrees).",
        examples=[-46.7],
    )
    max_longitude: LongitudeField = Field(
        title="Max longitude",
        description="Eastern edge of the box (decimal degrees).",
        examples=[-46.6],
    )

    def contains(self, point: Coordinate) -> bool:
        """Return whether ``point`` falls inside the box (edges included).

        Args:
            point: The coordinate to test.

        Returns:
            ``True`` when the point is within the box, edges inclusive.
        """
        return (
            self.min_latitude <= point.latitude <= self.max_latitude
            and self.min_longitude <= point.longitude <= self.max_longitude
        )


class GeocodeResult(BaseSchema):
    """A resolved place from a geocoding backend.

    Attributes:
        coordinate: The resolved point.
        display_name: Human-readable label the backend returned.
        place_type: Optional backend-specific place category
            (e.g. ``"city"``, ``"house"``), when available.
    """

    coordinate: Coordinate = Field(
        title="Coordinate",
        description="The resolved geographic point.",
    )
    display_name: str = Field(
        title="Display name",
        description="Human-readable label for the resolved place.",
        examples=["São Paulo, Região Sudeste, Brasil"],
    )
    place_type: str | None = Field(
        default=None,
        title="Place type",
        description="Backend-specific place category, when available.",
        examples=["city"],
    )


class DistanceMatrix(BaseSchema):
    """A many-to-many distance/duration matrix between coordinate sets.

    ``distances_km[i][j]`` and ``durations_minutes[i][j]`` describe the
    trip from ``sources[i]`` to ``destinations[j]``.

    Attributes:
        sources: The origin coordinates, in row order.
        destinations: The destination coordinates, in column order.
        distances_km: Road distance per (source, destination) pair.
        durations_minutes: Travel time per (source, destination) pair.
        mode: The travel mode the matrix was computed for.
        source_label: How the matrix was produced (e.g. ``"osrm"``).
    """

    sources: list[Coordinate] = Field(
        default_factory=list,
        title="Sources",
        description="Origin coordinates, in row order.",
    )
    destinations: list[Coordinate] = Field(
        default_factory=list,
        title="Destinations",
        description="Destination coordinates, in column order.",
    )
    distances_km: list[list[float]] = Field(
        default_factory=list,
        title="Distances (km)",
        description="Road distance for each (source, destination) pair.",
    )
    durations_minutes: list[list[float]] = Field(
        default_factory=list,
        title="Durations (min)",
        description="Travel time for each (source, destination) pair.",
    )
    mode: TravelMode = Field(
        default=TravelMode.CAR,
        title="Travel mode",
        description="The travel mode the matrix was computed for.",
    )
    source_label: str = Field(
        default="osrm",
        title="Matrix source",
        description="How the matrix was produced.",
        examples=["osrm"],
    )


__all__: list[str] = [
    "BoundingBox",
    "Coordinate",
    "DistanceMatrix",
    "GeocodeResult",
    "TravelEstimate",
]
