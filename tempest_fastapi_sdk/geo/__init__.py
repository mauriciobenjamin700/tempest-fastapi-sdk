"""Geolocation — distance, routing, geometry and geocoding, no paid API.

Layers, all sharing the same schemas:

* **Offline math** (zero deps, zero network): :func:`haversine_km` and the
  spatial helpers in :mod:`geometry` (:func:`bounding_box`,
  :func:`within_radius`, :func:`nearest`, :func:`destination_point`,
  :func:`initial_bearing`, :func:`point_in_polygon`,
  :func:`polygon_area_km2`, :func:`path_length_km`), plus
  :func:`estimate_travel` for a road distance / per-mode time.
* **Real routing** (free, open-source): :class:`OSRMBackend` for routes,
  route geometry and distance/duration matrices; :class:`NominatimBackend`
  for forward/reverse geocoding. Both take an injected ``httpx.AsyncClient``
  (install the ``[geo]`` extra for ``httpx``); the rest imports without it.
* **Database**: :class:`GeoPointMixin` (model) + :class:`GeoRepositoryMixin`
  (portable radius search) / :class:`PostGISRepositoryMixin` (ST_DWithin).
* **Brazil**: :func:`uf_centroid` (offline state centres) and
  :func:`cep_to_coordinate` (CEP via a geocoder).

Motorcycle/bus/bicycle/pedestrian are derived from the car result by
scaling the duration (see :data:`DEFAULT_MODE_DURATION_FACTORS`).
"""

from tempest_fastapi_sdk.geo.br import UF_CENTROIDS as UF_CENTROIDS
from tempest_fastapi_sdk.geo.br import cep_to_coordinate as cep_to_coordinate
from tempest_fastapi_sdk.geo.br import uf_centroid as uf_centroid
from tempest_fastapi_sdk.geo.db import GeoPointMixin as GeoPointMixin
from tempest_fastapi_sdk.geo.db import GeoRepositoryMixin as GeoRepositoryMixin
from tempest_fastapi_sdk.geo.db import (
    PostGISRepositoryMixin as PostGISRepositoryMixin,
)
from tempest_fastapi_sdk.geo.db import make_geo_point_model as make_geo_point_model
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
from tempest_fastapi_sdk.geo.geocoding import (
    DEFAULT_NOMINATIM_BASE_URL as DEFAULT_NOMINATIM_BASE_URL,
)
from tempest_fastapi_sdk.geo.geocoding import GeocodingBackend as GeocodingBackend
from tempest_fastapi_sdk.geo.geocoding import NominatimBackend as NominatimBackend
from tempest_fastapi_sdk.geo.geometry import bounding_box as bounding_box
from tempest_fastapi_sdk.geo.geometry import destination_point as destination_point
from tempest_fastapi_sdk.geo.geometry import initial_bearing as initial_bearing
from tempest_fastapi_sdk.geo.geometry import nearest as nearest
from tempest_fastapi_sdk.geo.geometry import path_length_km as path_length_km
from tempest_fastapi_sdk.geo.geometry import point_in_polygon as point_in_polygon
from tempest_fastapi_sdk.geo.geometry import polygon_area_km2 as polygon_area_km2
from tempest_fastapi_sdk.geo.geometry import within_radius as within_radius
from tempest_fastapi_sdk.geo.polyline import decode_polyline as decode_polyline
from tempest_fastapi_sdk.geo.polyline import encode_polyline as encode_polyline
from tempest_fastapi_sdk.geo.routing import (
    DEFAULT_MODE_PROFILES as DEFAULT_MODE_PROFILES,
)
from tempest_fastapi_sdk.geo.routing import (
    DEFAULT_OSRM_BASE_URL as DEFAULT_OSRM_BASE_URL,
)
from tempest_fastapi_sdk.geo.routing import OSRMBackend as OSRMBackend
from tempest_fastapi_sdk.geo.routing import RoutingBackend as RoutingBackend
from tempest_fastapi_sdk.geo.schemas import BoundingBox as BoundingBox
from tempest_fastapi_sdk.geo.schemas import Coordinate as Coordinate
from tempest_fastapi_sdk.geo.schemas import DistanceMatrix as DistanceMatrix
from tempest_fastapi_sdk.geo.schemas import GeocodeResult as GeocodeResult
from tempest_fastapi_sdk.geo.schemas import TravelEstimate as TravelEstimate

__all__: list[str] = [
    "DEFAULT_CAR_SPEED_KMH",
    "DEFAULT_CIRCUITY_FACTOR",
    "DEFAULT_MODE_DURATION_FACTORS",
    "DEFAULT_MODE_PROFILES",
    "DEFAULT_NOMINATIM_BASE_URL",
    "DEFAULT_OSRM_BASE_URL",
    "EARTH_RADIUS_KM",
    "UF_CENTROIDS",
    "BoundingBox",
    "Coordinate",
    "DistanceMatrix",
    "GeoPointMixin",
    "GeoRepositoryMixin",
    "GeocodeResult",
    "GeocodingBackend",
    "NominatimBackend",
    "OSRMBackend",
    "PostGISRepositoryMixin",
    "RoutingBackend",
    "TravelEstimate",
    "TravelMode",
    "bounding_box",
    "cep_to_coordinate",
    "decode_polyline",
    "destination_point",
    "duration_factor",
    "encode_polyline",
    "estimate_travel",
    "haversine_km",
    "initial_bearing",
    "make_geo_point_model",
    "nearest",
    "path_length_km",
    "point_in_polygon",
    "polygon_area_km2",
    "uf_centroid",
    "within_radius",
]
