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
