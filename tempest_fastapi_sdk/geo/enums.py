"""Travel modes for geolocation distance and duration estimates."""

from __future__ import annotations

from tempest_fastapi_sdk.core import BaseStrEnum


class TravelModeEnum(BaseStrEnum):
    """A way of travelling by road between two points.

    Used to pick an average speed (offline heuristic) or to scale a
    car-only routing result (OSRM public demo). Values are lowercase so
    they can go straight into an OSRM profile or a query string.
    """

    CAR = "car"
    MOTORCYCLE = "motorcycle"
    BUS = "bus"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"


class TripRoleEnum(BaseStrEnum):
    """The part a city plays in a trip request (:func:`parse_trip_intent`).

    Values are the lowercase English words so they can key a JSON object
    the client reads (``{"destination": "natal"}``).
    """

    ORIGIN = "origin"
    DESTINATION = "destination"


__all__: list[str] = [
    "TravelModeEnum",
    "TripRoleEnum",
]
