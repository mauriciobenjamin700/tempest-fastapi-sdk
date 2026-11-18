"""Geolocation — distance, routing, geometry and geocoding, no paid API.

Layers, all sharing the same schemas:

* **Offline math** (zero deps, zero network): :func:`haversine_km` and the
  spatial helpers in :mod:`geometry` (:func:`bounding_box`,
  :func:`within_radius`, :func:`nearest`, :func:`destination_point`,
  :func:`initial_bearing`, :func:`point_in_polygon`,
  :func:`polygon_area_km2`, :func:`path_length_km`), plus
  :func:`estimate_travel` for a road distance / per-mode time.
* **Real routing** (free, open-source): :class:`OSRMBackend` for routes,
  routes through stops (:meth:`OSRMBackend.route_through`, with
  :func:`estimate_route_through` as the offline fallback), route geometry
  and distance/duration matrices; :class:`NominatimBackend`
  for forward/reverse geocoding. Both take an injected ``httpx.AsyncClient``
  (install the ``[geo]`` extra for ``httpx``); the rest imports without it.
* **Caching**: :class:`CachedGeocodingBackend` wraps any
  :class:`GeocodingBackend` in front of a pluggable
  :class:`GeocodeCacheStore` — :class:`InMemoryGeocodeCacheStore` for
  tests and dev, :class:`RedisGeocodeCacheStore` for a shared Redis.
* **Live position**: :class:`LivePositionStore` keeps the latest
  :class:`LivePositionSchema` per channel in Redis (throttle, least
  movement, TTL, :meth:`LivePositionStore.clear`) and delivers accepted
  positions over an SSE broker to the recipients an ``audience`` callback
  names, optionally with each one's ETA; :func:`make_live_position_router`
  receives them behind mandatory ``authorize`` and ``channel_active``
  dependencies.
* **Database**: :class:`GeoPointMixin` (model) + :class:`GeoRepositoryMixin`
  (portable radius search) / :class:`PostGISRepositoryMixin` (ST_DWithin).
* **Database**, paginated: :meth:`GeoRepositoryMixin.paginate_nearby`
  computes :func:`haversine_distance_sql` in SQL and pages there, each item
  a :class:`NearbyMatch` ``(row, distance_km)``.
* **Brazil**: :func:`uf_centroid` (offline state centres),
  :func:`cep_to_coordinate` (CEP via a geocoder), :func:`extract_cep` (CEP
  from free text) and :func:`resolve_br_coordinate` (CEP → address → state
  centroid). Every IBGE municipality offline, with code and coordinate:
  :func:`municipality_by_ibge_code`, :func:`search_municipalities` (typos
  included) and :func:`find_municipalities_in_text` (``"moro em sao jose
  dos campos - sp"``).

Motorcycle/bus/bicycle/pedestrian are derived from the car result by
scaling the duration (see :data:`DEFAULT_MODE_DURATION_FACTORS`).
"""

from tempest_fastapi_sdk.geo.br import CEP_PATTERN as CEP_PATTERN
from tempest_fastapi_sdk.geo.br import UF_CENTROIDS as UF_CENTROIDS
from tempest_fastapi_sdk.geo.br import cep_to_coordinate as cep_to_coordinate
from tempest_fastapi_sdk.geo.br import extract_cep as extract_cep
from tempest_fastapi_sdk.geo.br import (
    resolve_br_coordinate as resolve_br_coordinate,
)
from tempest_fastapi_sdk.geo.br import uf_centroid as uf_centroid
from tempest_fastapi_sdk.geo.cache import (
    DEFAULT_GEOCODE_CACHE_KEY_PREFIX as DEFAULT_GEOCODE_CACHE_KEY_PREFIX,
)
from tempest_fastapi_sdk.geo.cache import (
    DEFAULT_GEOCODE_CACHE_PRECISION as DEFAULT_GEOCODE_CACHE_PRECISION,
)
from tempest_fastapi_sdk.geo.cache import (
    DEFAULT_GEOCODE_CACHE_TTL_SECONDS as DEFAULT_GEOCODE_CACHE_TTL_SECONDS,
)
from tempest_fastapi_sdk.geo.cache import (
    CachedGeocodingBackend as CachedGeocodingBackend,
)
from tempest_fastapi_sdk.geo.cache import GeocodeCacheStore as GeocodeCacheStore
from tempest_fastapi_sdk.geo.cache import (
    InMemoryGeocodeCacheStore as InMemoryGeocodeCacheStore,
)
from tempest_fastapi_sdk.geo.cache import (
    RedisGeocodeCacheStore as RedisGeocodeCacheStore,
)
from tempest_fastapi_sdk.geo.db import GeoPointMixin as GeoPointMixin
from tempest_fastapi_sdk.geo.db import GeoRepositoryMixin as GeoRepositoryMixin
from tempest_fastapi_sdk.geo.db import NearbyMatch as NearbyMatch
from tempest_fastapi_sdk.geo.db import (
    PostGISRepositoryMixin as PostGISRepositoryMixin,
)
from tempest_fastapi_sdk.geo.db import (
    haversine_distance_sql as haversine_distance_sql,
)
from tempest_fastapi_sdk.geo.db import make_geo_point_model as make_geo_point_model
from tempest_fastapi_sdk.geo.distance import EARTH_RADIUS_KM as EARTH_RADIUS_KM
from tempest_fastapi_sdk.geo.distance import haversine_km as haversine_km
from tempest_fastapi_sdk.geo.enums import TravelModeEnum as TravelModeEnum
from tempest_fastapi_sdk.geo.enums import TripRoleEnum as TripRoleEnum
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
from tempest_fastapi_sdk.geo.estimate import (
    estimate_route_through as estimate_route_through,
)
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
from tempest_fastapi_sdk.geo.live import (
    DEFAULT_LIVE_POSITION_CLEARED_EVENT as DEFAULT_LIVE_POSITION_CLEARED_EVENT,
)
from tempest_fastapi_sdk.geo.live import (
    DEFAULT_LIVE_POSITION_EVENT as DEFAULT_LIVE_POSITION_EVENT,
)
from tempest_fastapi_sdk.geo.live import (
    DEFAULT_LIVE_POSITION_INTERVAL_SECONDS as DEFAULT_LIVE_POSITION_INTERVAL_SECONDS,
)
from tempest_fastapi_sdk.geo.live import (
    DEFAULT_LIVE_POSITION_KEY_PREFIX as DEFAULT_LIVE_POSITION_KEY_PREFIX,
)
from tempest_fastapi_sdk.geo.live import (
    DEFAULT_LIVE_POSITION_MIN_DISTANCE_M as DEFAULT_LIVE_POSITION_MIN_DISTANCE_M,
)
from tempest_fastapi_sdk.geo.live import (
    DEFAULT_LIVE_POSITION_TTL_SECONDS as DEFAULT_LIVE_POSITION_TTL_SECONDS,
)
from tempest_fastapi_sdk.geo.live import (
    LIVE_POSITION_MAX_CLOCK_SKEW as LIVE_POSITION_MAX_CLOCK_SKEW,
)
from tempest_fastapi_sdk.geo.live import (
    AudienceResolver as AudienceResolver,
)
from tempest_fastapi_sdk.geo.live import (
    EtaTargetsResolver as EtaTargetsResolver,
)
from tempest_fastapi_sdk.geo.live import (
    LiveEtaSchema as LiveEtaSchema,
)
from tempest_fastapi_sdk.geo.live import (
    LivePositionEventSchema as LivePositionEventSchema,
)
from tempest_fastapi_sdk.geo.live import (
    LivePositionSchema as LivePositionSchema,
)
from tempest_fastapi_sdk.geo.live import (
    LivePositionStore as LivePositionStore,
)
from tempest_fastapi_sdk.geo.live_router import (
    LIVE_POSITION_INACTIVE_ERROR as LIVE_POSITION_INACTIVE_ERROR,
)
from tempest_fastapi_sdk.geo.live_router import (
    LivePositionPublishResponseSchema as LivePositionPublishResponseSchema,
)
from tempest_fastapi_sdk.geo.live_router import (
    make_live_position_router as make_live_position_router,
)
from tempest_fastapi_sdk.geo.municipalities import (
    BR_CITY_NICKNAMES as BR_CITY_NICKNAMES,
)
from tempest_fastapi_sdk.geo.municipalities import (
    BRASILIA_IBGE_CODE as BRASILIA_IBGE_CODE,
)
from tempest_fastapi_sdk.geo.municipalities import (
    Municipality as Municipality,
)
from tempest_fastapi_sdk.geo.municipalities import (
    MunicipalityMatch as MunicipalityMatch,
)
from tempest_fastapi_sdk.geo.municipalities import (
    br_municipalities as br_municipalities,
)
from tempest_fastapi_sdk.geo.municipalities import (
    find_municipalities_in_text as find_municipalities_in_text,
)
from tempest_fastapi_sdk.geo.municipalities import (
    municipality_by_ibge_code as municipality_by_ibge_code,
)
from tempest_fastapi_sdk.geo.municipalities import (
    municipality_by_name as municipality_by_name,
)
from tempest_fastapi_sdk.geo.municipalities import (
    search_municipalities as search_municipalities,
)
from tempest_fastapi_sdk.geo.polyline import decode_polyline as decode_polyline
from tempest_fastapi_sdk.geo.polyline import encode_polyline as encode_polyline
from tempest_fastapi_sdk.geo.routing import (
    DEFAULT_MODE_PROFILES as DEFAULT_MODE_PROFILES,
)
from tempest_fastapi_sdk.geo.routing import (
    DEFAULT_OSRM_BASE_URL as DEFAULT_OSRM_BASE_URL,
)
from tempest_fastapi_sdk.geo.routing import (
    DEFAULT_OSRM_MAX_POINTS as DEFAULT_OSRM_MAX_POINTS,
)
from tempest_fastapi_sdk.geo.routing import OSRMBackend as OSRMBackend
from tempest_fastapi_sdk.geo.routing import RoutingBackend as RoutingBackend
from tempest_fastapi_sdk.geo.schemas import BoundingBox as BoundingBox
from tempest_fastapi_sdk.geo.schemas import Coordinate as Coordinate
from tempest_fastapi_sdk.geo.schemas import DistanceMatrix as DistanceMatrix
from tempest_fastapi_sdk.geo.schemas import GeocodeAddress as GeocodeAddress
from tempest_fastapi_sdk.geo.schemas import GeocodeResult as GeocodeResult
from tempest_fastapi_sdk.geo.schemas import (
    RouteThroughResult as RouteThroughResult,
)
from tempest_fastapi_sdk.geo.schemas import TravelEstimate as TravelEstimate
from tempest_fastapi_sdk.geo.trip_intent import CityMention as CityMention
from tempest_fastapi_sdk.geo.trip_intent import TripCityIndex as TripCityIndex
from tempest_fastapi_sdk.geo.trip_intent import TripIntent as TripIntent
from tempest_fastapi_sdk.geo.trip_intent import (
    build_trip_city_index as build_trip_city_index,
)
from tempest_fastapi_sdk.geo.trip_intent import (
    parse_trip_intent as parse_trip_intent,
)

__all__: list[str] = [
    "BRASILIA_IBGE_CODE",
    "BR_CITY_NICKNAMES",
    "CEP_PATTERN",
    "DEFAULT_CAR_SPEED_KMH",
    "DEFAULT_CIRCUITY_FACTOR",
    "DEFAULT_GEOCODE_CACHE_KEY_PREFIX",
    "DEFAULT_GEOCODE_CACHE_PRECISION",
    "DEFAULT_GEOCODE_CACHE_TTL_SECONDS",
    "DEFAULT_LIVE_POSITION_CLEARED_EVENT",
    "DEFAULT_LIVE_POSITION_EVENT",
    "DEFAULT_LIVE_POSITION_INTERVAL_SECONDS",
    "DEFAULT_LIVE_POSITION_KEY_PREFIX",
    "DEFAULT_LIVE_POSITION_MIN_DISTANCE_M",
    "DEFAULT_LIVE_POSITION_TTL_SECONDS",
    "DEFAULT_MODE_DURATION_FACTORS",
    "DEFAULT_MODE_PROFILES",
    "DEFAULT_NOMINATIM_BASE_URL",
    "DEFAULT_OSRM_BASE_URL",
    "DEFAULT_OSRM_MAX_POINTS",
    "EARTH_RADIUS_KM",
    "LIVE_POSITION_INACTIVE_ERROR",
    "LIVE_POSITION_MAX_CLOCK_SKEW",
    "UF_CENTROIDS",
    "AudienceResolver",
    "BoundingBox",
    "CachedGeocodingBackend",
    "CityMention",
    "Coordinate",
    "DistanceMatrix",
    "EtaTargetsResolver",
    "GeoPointMixin",
    "GeoRepositoryMixin",
    "GeocodeAddress",
    "GeocodeCacheStore",
    "GeocodeResult",
    "GeocodingBackend",
    "InMemoryGeocodeCacheStore",
    "LiveEtaSchema",
    "LivePositionEventSchema",
    "LivePositionPublishResponseSchema",
    "LivePositionSchema",
    "LivePositionStore",
    "Municipality",
    "MunicipalityMatch",
    "NearbyMatch",
    "NominatimBackend",
    "OSRMBackend",
    "PostGISRepositoryMixin",
    "RedisGeocodeCacheStore",
    "RouteThroughResult",
    "RoutingBackend",
    "TravelEstimate",
    "TravelModeEnum",
    "TripCityIndex",
    "TripIntent",
    "TripRoleEnum",
    "bounding_box",
    "br_municipalities",
    "build_trip_city_index",
    "cep_to_coordinate",
    "decode_polyline",
    "destination_point",
    "duration_factor",
    "encode_polyline",
    "estimate_route_through",
    "estimate_travel",
    "extract_cep",
    "find_municipalities_in_text",
    "haversine_distance_sql",
    "haversine_km",
    "initial_bearing",
    "make_geo_point_model",
    "make_live_position_router",
    "municipality_by_ibge_code",
    "municipality_by_name",
    "nearest",
    "parse_trip_intent",
    "path_length_km",
    "point_in_polygon",
    "polygon_area_km2",
    "resolve_br_coordinate",
    "search_municipalities",
    "uf_centroid",
    "within_radius",
]
