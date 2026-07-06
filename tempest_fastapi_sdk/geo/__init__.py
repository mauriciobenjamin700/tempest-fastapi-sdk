"""Geolocation — distance and travel-time estimates without a paid API.

Two layers, same schemas:

* **Offline heuristic** (zero deps, zero network): :func:`haversine_km` for
  the great-circle distance and :func:`estimate_travel` for a road distance
  and per-mode travel time (Haversine x circuity factor / average speed).
* **Real routing** (free, open-source): :class:`OSRMBackend` talks to any
  OSRM server (public demo or self-hosted) for true road geometry. It needs
  an injected ``httpx.AsyncClient``; install the ``[geo]`` extra to pull in
  ``httpx``. The rest of this package imports without it.

Motorcycle and bus are derived from the car result by scaling the duration
(see :data:`DEFAULT_MODE_DURATION_FACTORS`), so both layers work against a
car-only routing profile.
"""

from tempest_fastapi_sdk.geo.distance import EARTH_RADIUS_KM as EARTH_RADIUS_KM
from tempest_fastapi_sdk.geo.distance import haversine_km as haversine_km
from tempest_fastapi_sdk.geo.enums import TravelMode as TravelMode
from tempest_fastapi_sdk.geo.estimate import (
    DEFAULT_CAR_SPEED_KMH as DEFAULT_CAR_SPEED_KMH,
)
from tempest_fastapi_sdk.geo.estimate import (
    DEFAULT_CIRCUITY_FACTOR as DEFAULT_CIRCUITY_FACTOR,
)
from tempest_fastapi_sdk.geo.estimate import (
    DEFAULT_MODE_DURATION_FACTORS as DEFAULT_MODE_DURATION_FACTORS,
)
from tempest_fastapi_sdk.geo.estimate import duration_factor as duration_factor
from tempest_fastapi_sdk.geo.estimate import estimate_travel as estimate_travel
from tempest_fastapi_sdk.geo.routing import (
    DEFAULT_OSRM_BASE_URL as DEFAULT_OSRM_BASE_URL,
)
from tempest_fastapi_sdk.geo.routing import OSRMBackend as OSRMBackend
from tempest_fastapi_sdk.geo.routing import RoutingBackend as RoutingBackend
from tempest_fastapi_sdk.geo.schemas import Coordinate as Coordinate
from tempest_fastapi_sdk.geo.schemas import TravelEstimate as TravelEstimate

__all__: list[str] = [
    "DEFAULT_CAR_SPEED_KMH",
    "DEFAULT_CIRCUITY_FACTOR",
    "DEFAULT_MODE_DURATION_FACTORS",
    "DEFAULT_OSRM_BASE_URL",
    "EARTH_RADIUS_KM",
    "Coordinate",
    "OSRMBackend",
    "RoutingBackend",
    "TravelEstimate",
    "TravelMode",
    "duration_factor",
    "estimate_travel",
    "haversine_km",
]
