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
* **Fleet traffic**: :class:`RoadGraph` (from an OSM excerpt with
  :func:`road_graph_from_overpass` / :func:`load_road_graph`),
  :func:`match_to_roads` (HMM map-matching, a port of tempest-react-sdk's),
  :class:`TrafficAggregator` (median speed per road stretch, published only
  k-anonymously, start and end of every trip cut, rotating hashes) and
  :func:`make_traffic_router`; map-matching quality per window, k-anonymous
  (``quality=`` + :func:`make_match_quality_router`); :class:`OffRoadDensity` +
  :func:`infer_road_candidates` turn off-road density into road candidates
  for human review.
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
from tempest_fastapi_sdk.geo.br_map import BR_MAP_LEVELS as BR_MAP_LEVELS
from tempest_fastapi_sdk.geo.br_map import BR_MAP_MAX_RADIUS as BR_MAP_MAX_RADIUS
from tempest_fastapi_sdk.geo.br_map import BR_MAP_MIN_RADIUS as BR_MAP_MIN_RADIUS
from tempest_fastapi_sdk.geo.br_map import BR_MAP_PADDING as BR_MAP_PADDING
from tempest_fastapi_sdk.geo.br_map import BR_MAP_WIDTH as BR_MAP_WIDTH
from tempest_fastapi_sdk.geo.br_map import BRMapLegendEntry as BRMapLegendEntry
from tempest_fastapi_sdk.geo.br_map import BRMapPoint as BRMapPoint
from tempest_fastapi_sdk.geo.br_map import br_map_height as br_map_height
from tempest_fastapi_sdk.geo.br_map import br_map_legend as br_map_legend
from tempest_fastapi_sdk.geo.br_map import br_state_outlines as br_state_outlines
from tempest_fastapi_sdk.geo.br_map import project_br as project_br
from tempest_fastapi_sdk.geo.br_map import render_br_map_svg as render_br_map_svg
from tempest_fastapi_sdk.geo.br_map import uf_at as uf_at
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
from tempest_fastapi_sdk.geo.location import BR_CITY_MAX_LENGTH as BR_CITY_MAX_LENGTH
from tempest_fastapi_sdk.geo.location import BRCityCountSchema as BRCityCountSchema
from tempest_fastapi_sdk.geo.location import (
    BRLocationDistributionSchema as BRLocationDistributionSchema,
)
from tempest_fastapi_sdk.geo.location import BRLocationMixin as BRLocationMixin
from tempest_fastapi_sdk.geo.location import BRLocationSchema as BRLocationSchema
from tempest_fastapi_sdk.geo.location import BRStateCountSchema as BRStateCountSchema
from tempest_fastapi_sdk.geo.location import (
    UnknownMunicipalityError as UnknownMunicipalityError,
)
from tempest_fastapi_sdk.geo.location import (
    br_location_distribution as br_location_distribution,
)
from tempest_fastapi_sdk.geo.location import (
    resolve_br_municipality as resolve_br_municipality,
)
from tempest_fastapi_sdk.geo.map_matching import (
    ROAD_MATCH_DEFAULTS as ROAD_MATCH_DEFAULTS,
)
from tempest_fastapi_sdk.geo.map_matching import MatchedFix as MatchedFix
from tempest_fastapi_sdk.geo.map_matching import MatchedRoute as MatchedRoute
from tempest_fastapi_sdk.geo.map_matching import RoadMatchOptions as RoadMatchOptions
from tempest_fastapi_sdk.geo.map_matching import RoadMatchResult as RoadMatchResult
from tempest_fastapi_sdk.geo.map_matching import RoadTraversal as RoadTraversal
from tempest_fastapi_sdk.geo.map_matching import match_to_roads as match_to_roads
from tempest_fastapi_sdk.geo.match_quality import (
    MATCH_QUALITY_REFERENCE_BRAZIL_OSM as MATCH_QUALITY_REFERENCE_BRAZIL_OSM,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MATCH_QUALITY_REFERENCE_KUBICKA as MATCH_QUALITY_REFERENCE_KUBICKA,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MATCH_QUALITY_REFERENCES as MATCH_QUALITY_REFERENCES,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualityCellSchema as MatchQualityCellSchema,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualityIndicatorsSchema as MatchQualityIndicatorsSchema,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualityOptions as MatchQualityOptions,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualityReferenceSchema as MatchQualityReferenceSchema,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualityReportSchema as MatchQualityReportSchema,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualitySnapshotSchema as MatchQualitySnapshotSchema,
)
from tempest_fastapi_sdk.geo.match_quality import (
    MatchQualityTracker as MatchQualityTracker,
)
from tempest_fastapi_sdk.geo.match_quality import (
    QualityHistogram as QualityHistogram,
)
from tempest_fastapi_sdk.geo.match_quality import (
    QualityTrackPoint as QualityTrackPoint,
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
from tempest_fastapi_sdk.geo.osm import OSM_DRIVABLE_HIGHWAYS as OSM_DRIVABLE_HIGHWAYS
from tempest_fastapi_sdk.geo.osm import (
    OSM_GEOJSON_COORDINATE_DIGITS as OSM_GEOJSON_COORDINATE_DIGITS,
)
from tempest_fastapi_sdk.geo.osm import OSM_GRAPH_CACHE_SIZE as OSM_GRAPH_CACHE_SIZE
from tempest_fastapi_sdk.geo.osm import load_road_graph as load_road_graph
from tempest_fastapi_sdk.geo.osm import (
    road_graph_from_geojson as road_graph_from_geojson,
)
from tempest_fastapi_sdk.geo.osm import (
    road_graph_from_osm_json as road_graph_from_osm_json,
)
from tempest_fastapi_sdk.geo.osm import (
    road_graph_from_overpass as road_graph_from_overpass,
)
from tempest_fastapi_sdk.geo.polyline import decode_polyline as decode_polyline
from tempest_fastapi_sdk.geo.polyline import encode_polyline as encode_polyline
from tempest_fastapi_sdk.geo.road_graph import ROAD_GRID_CELL_M as ROAD_GRID_CELL_M
from tempest_fastapi_sdk.geo.road_graph import RoadCandidate as RoadCandidate
from tempest_fastapi_sdk.geo.road_graph import RoadEdge as RoadEdge
from tempest_fastapi_sdk.geo.road_graph import RoadGraph as RoadGraph
from tempest_fastapi_sdk.geo.road_inference import (
    DEFAULT_DENSITY_BLOCK_CELLS as DEFAULT_DENSITY_BLOCK_CELLS,
)
from tempest_fastapi_sdk.geo.road_inference import (
    DEFAULT_DENSITY_CELL_M as DEFAULT_DENSITY_CELL_M,
)
from tempest_fastapi_sdk.geo.road_inference import (
    DEFAULT_DENSITY_EPOCH_SECONDS as DEFAULT_DENSITY_EPOCH_SECONDS,
)
from tempest_fastapi_sdk.geo.road_inference import (
    DEFAULT_OFF_ROAD_M as DEFAULT_OFF_ROAD_M,
)
from tempest_fastapi_sdk.geo.road_inference import OffRoadDensity as OffRoadDensity
from tempest_fastapi_sdk.geo.road_inference import (
    RoadCandidateSchema as RoadCandidateSchema,
)
from tempest_fastapi_sdk.geo.road_inference import (
    infer_road_candidates as infer_road_candidates,
)
from tempest_fastapi_sdk.geo.road_inference import (
    road_candidates_geojson as road_candidates_geojson,
)
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
from tempest_fastapi_sdk.geo.traffic import (
    DEFAULT_TRAFFIC_EVENT as DEFAULT_TRAFFIC_EVENT,
)
from tempest_fastapi_sdk.geo.traffic import (
    DEFAULT_TRAFFIC_PRIVACY as DEFAULT_TRAFFIC_PRIVACY,
)
from tempest_fastapi_sdk.geo.traffic import (
    DEFAULT_TRAFFIC_SNAPSHOT_KEY as DEFAULT_TRAFFIC_SNAPSHOT_KEY,
)
from tempest_fastapi_sdk.geo.traffic import (
    TRAFFIC_MIN_DEVICES_FLOOR as TRAFFIC_MIN_DEVICES_FLOOR,
)
from tempest_fastapi_sdk.geo.traffic import (
    InMemoryTrafficSnapshotStore as InMemoryTrafficSnapshotStore,
)
from tempest_fastapi_sdk.geo.traffic import (
    RedisTrafficSnapshotStore as RedisTrafficSnapshotStore,
)
from tempest_fastapi_sdk.geo.traffic import TrafficAggregator as TrafficAggregator
from tempest_fastapi_sdk.geo.traffic import (
    TrafficAudienceResolver as TrafficAudienceResolver,
)
from tempest_fastapi_sdk.geo.traffic import (
    TrafficDimensionsResolver as TrafficDimensionsResolver,
)
from tempest_fastapi_sdk.geo.traffic import TrafficEdgeSchema as TrafficEdgeSchema
from tempest_fastapi_sdk.geo.traffic import TrafficPrivacy as TrafficPrivacy
from tempest_fastapi_sdk.geo.traffic import (
    TrafficSnapshotSchema as TrafficSnapshotSchema,
)
from tempest_fastapi_sdk.geo.traffic import TrafficSnapshotStore as TrafficSnapshotStore
from tempest_fastapi_sdk.geo.traffic_router import (
    TRAFFIC_INVALID_BBOX_ERROR as TRAFFIC_INVALID_BBOX_ERROR,
)
from tempest_fastapi_sdk.geo.traffic_router import (
    RoadGraphTileSchema as RoadGraphTileSchema,
)
from tempest_fastapi_sdk.geo.traffic_router import (
    make_match_quality_router as make_match_quality_router,
)
from tempest_fastapi_sdk.geo.traffic_router import (
    make_traffic_router as make_traffic_router,
)
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
    "BR_CITY_MAX_LENGTH",
    "BR_CITY_NICKNAMES",
    "BR_MAP_LEVELS",
    "BR_MAP_MAX_RADIUS",
    "BR_MAP_MIN_RADIUS",
    "BR_MAP_PADDING",
    "BR_MAP_WIDTH",
    "CEP_PATTERN",
    "DEFAULT_CAR_SPEED_KMH",
    "DEFAULT_CIRCUITY_FACTOR",
    "DEFAULT_DENSITY_BLOCK_CELLS",
    "DEFAULT_DENSITY_CELL_M",
    "DEFAULT_DENSITY_EPOCH_SECONDS",
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
    "DEFAULT_OFF_ROAD_M",
    "DEFAULT_OSRM_BASE_URL",
    "DEFAULT_OSRM_MAX_POINTS",
    "DEFAULT_TRAFFIC_EVENT",
    "DEFAULT_TRAFFIC_PRIVACY",
    "DEFAULT_TRAFFIC_SNAPSHOT_KEY",
    "EARTH_RADIUS_KM",
    "LIVE_POSITION_INACTIVE_ERROR",
    "LIVE_POSITION_MAX_CLOCK_SKEW",
    "MATCH_QUALITY_REFERENCES",
    "MATCH_QUALITY_REFERENCE_BRAZIL_OSM",
    "MATCH_QUALITY_REFERENCE_KUBICKA",
    "OSM_DRIVABLE_HIGHWAYS",
    "OSM_GEOJSON_COORDINATE_DIGITS",
    "OSM_GRAPH_CACHE_SIZE",
    "ROAD_GRID_CELL_M",
    "ROAD_MATCH_DEFAULTS",
    "TRAFFIC_INVALID_BBOX_ERROR",
    "TRAFFIC_MIN_DEVICES_FLOOR",
    "UF_CENTROIDS",
    "AudienceResolver",
    "BRCityCountSchema",
    "BRLocationDistributionSchema",
    "BRLocationMixin",
    "BRLocationSchema",
    "BRMapLegendEntry",
    "BRMapPoint",
    "BRStateCountSchema",
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
    "InMemoryTrafficSnapshotStore",
    "LiveEtaSchema",
    "LivePositionEventSchema",
    "LivePositionPublishResponseSchema",
    "LivePositionSchema",
    "LivePositionStore",
    "MatchQualityCellSchema",
    "MatchQualityIndicatorsSchema",
    "MatchQualityOptions",
    "MatchQualityReferenceSchema",
    "MatchQualityReportSchema",
    "MatchQualitySnapshotSchema",
    "MatchQualityTracker",
    "MatchedFix",
    "MatchedRoute",
    "Municipality",
    "MunicipalityMatch",
    "NearbyMatch",
    "NominatimBackend",
    "OSRMBackend",
    "OffRoadDensity",
    "PostGISRepositoryMixin",
    "QualityHistogram",
    "QualityTrackPoint",
    "RedisGeocodeCacheStore",
    "RedisTrafficSnapshotStore",
    "RoadCandidate",
    "RoadCandidateSchema",
    "RoadEdge",
    "RoadGraph",
    "RoadGraphTileSchema",
    "RoadMatchOptions",
    "RoadMatchResult",
    "RoadTraversal",
    "RouteThroughResult",
    "RoutingBackend",
    "TrafficAggregator",
    "TrafficAudienceResolver",
    "TrafficDimensionsResolver",
    "TrafficEdgeSchema",
    "TrafficPrivacy",
    "TrafficSnapshotSchema",
    "TrafficSnapshotStore",
    "TravelEstimate",
    "TravelModeEnum",
    "TripCityIndex",
    "TripIntent",
    "TripRoleEnum",
    "UnknownMunicipalityError",
    "bounding_box",
    "br_location_distribution",
    "br_map_height",
    "br_map_legend",
    "br_municipalities",
    "br_state_outlines",
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
    "infer_road_candidates",
    "initial_bearing",
    "load_road_graph",
    "make_geo_point_model",
    "make_live_position_router",
    "make_match_quality_router",
    "make_traffic_router",
    "match_to_roads",
    "municipality_by_ibge_code",
    "municipality_by_name",
    "nearest",
    "parse_trip_intent",
    "path_length_km",
    "point_in_polygon",
    "polygon_area_km2",
    "project_br",
    "render_br_map_svg",
    "resolve_br_coordinate",
    "resolve_br_municipality",
    "road_candidates_geojson",
    "road_graph_from_geojson",
    "road_graph_from_osm_json",
    "road_graph_from_overpass",
    "search_municipalities",
    "uf_at",
    "uf_centroid",
    "within_radius",
]
