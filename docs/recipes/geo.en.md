# Geolocation (distance + time)

Need to know **how many km** separate two points and **how long** the trip
takes by car, motorcycle, or bus — without paying for a maps API? The
`tempest_fastapi_sdk.geo` module solves it in two layers that share the same
schemas:

- **Offline heuristic** — pure math, zero dependencies, zero network.
  Straight-line distance (Haversine) scaled by a circuity factor, and time
  from the mode's average speed. Instant and approximate.
- **Real routing** — `OSRMBackend` talks to an
  [OSRM](https://project-osrm.org/) server (open-source, free, self-hostable
  or the public demo server). Gives true road geometry.

Everything imports **without** the extra. Only `OSRMBackend` needs `httpx`:

```bash
uv add "tempest-fastapi-sdk[geo]"
```

!!! info "No paid API"
    The offline layer makes no network calls at all. OSRM is free software —
    use the public demo server or run your own
    (`docker run osrm/osrm-backend`). No paid key on either path.

## Straight-line distance

`haversine_km` takes two `Coordinate`s (latitude/longitude in decimal
degrees, validated by `LatitudeField`/`LongitudeField`) and returns the
great-circle distance in km — the "as the crow flies" distance, no roads:

```python
from tempest_fastapi_sdk.geo import Coordinate, haversine_km

sao_paulo = Coordinate(latitude=-23.5505, longitude=-46.6333)
rio = Coordinate(latitude=-22.9068, longitude=-43.1729)

km: float = haversine_km(sao_paulo, rio)
print(round(km, 1))  # 360.7
```

## Offline estimate (distance + time per mode)

`estimate_travel` turns the straight line into a road estimate: it multiplies
the distance by the **circuity factor** (how much longer the real road is
than the straight line, ~1.3 by default) and computes the time from the car's
average speed, scaled by the mode factor.

```python
from tempest_fastapi_sdk.geo import (
    Coordinate,
    TravelEstimate,
    TravelMode,
    estimate_travel,
)

origin = Coordinate(latitude=-23.5505, longitude=-46.6333)
destination = Coordinate(latitude=-23.5015, longitude=-46.6553)

by_car: TravelEstimate = estimate_travel(origin, destination, TravelMode.CAR)
by_bus: TravelEstimate = estimate_travel(origin, destination, TravelMode.BUS)

print(by_car.distance_km, by_car.duration_minutes)   # 7.659817427032203 9.191780912438643
print(by_bus.duration_minutes)                        # larger (bus stops)
print(by_car.source)                                  # "heuristic"
```

The defaults are tunable per call:

```python
from tempest_fastapi_sdk.geo import Coordinate, TravelMode, estimate_travel

destination = Coordinate(latitude=-7.9899, longitude=-34.8386)

origin = Coordinate(latitude=-8.0476, longitude=-34.8770)


estimate_travel(
    origin,
    destination,
    TravelMode.MOTORCYCLE,
    circuity_factor=1.4,       # windier road
    car_speed_kmh=70.0,        # highway leg
)
```

!!! note "Motorcycle and bus derive from the car"
    A single map, `DEFAULT_MODE_DURATION_FACTORS`, defines how much slower or
    faster each mode is versus the car (bus ~1.6x for stops, motorcycle
    ~0.95x). It scales **both** paths — the heuristic (via speed) and OSRM
    (via duration) — so everything works even against a car-only profile.

## Real routing with OSRM

`OSRMBackend` follows the SDK pattern: you **inject** the
`httpx.AsyncClient` (the SDK does not open or close connections for you) and
it returns the same `TravelEstimate`, now with `source="osrm"` and the real
road distance.

```python
import httpx

from tempest_fastapi_sdk.geo import Coordinate, OSRMBackend, TravelMode

origin = Coordinate(latitude=-23.5505, longitude=-46.6333)
destination = Coordinate(latitude=-22.9068, longitude=-43.1729)


async def route() -> None:
    """Query the real route via an OSRM server."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        backend = OSRMBackend(http_client=client)  # public demo by default
        estimate = await backend.route(origin, destination, mode=TravelMode.CAR)
        print(estimate.distance_km, estimate.duration_minutes)
```

`OSRMBackend` satisfies the `RoutingBackend` Protocol, so you can swap it for
a mock in tests or another implementation without touching the call site.

!!! warning "Demo server = car only"
    The public demo (`router.project-osrm.org`) exposes only the car profile
    and is rate-limited. Motorcycle and bus reuse the car distance and scale
    the duration by the mode factor. For real motorcycle/bus profiles, run a
    self-hosted OSRM with your own data and point `base_url` at it.

## Choosing a layer

| You need... | Use |
| --- | --- |
| Fast, offline, "roughly" | `estimate_travel` (heuristic) |
| Real road distance/time | `OSRMBackend.route` |
| Just the straight line (radius, proximity) | `haversine_km` |

A common pattern: try OSRM and fall back to the heuristic if the network fails.

```python
from tempest_fastapi_sdk.geo import OSRMBackend, TravelEstimate, estimate_travel


async def estimate(origin, destination, mode, client) -> TravelEstimate:
    """Real route when possible; otherwise the offline estimate."""
    try:
        return await OSRMBackend(http_client=client).route(
            origin, destination, mode=mode
        )
    except RuntimeError:
        return estimate_travel(origin, destination, mode)
```

## Integrated example: delivery ETA (layered FastAPI)

A real service wants an endpoint that takes origin, destination, and mode
and returns distance + time, trying OSRM's real route and falling back to
the offline heuristic if the network fails. It follows the SDK's layered
architecture (schema → service → controller → router → dependency).

### Request/response schemas

```python
# src/schemas/geo.py
from tempest_fastapi_sdk.geo import Coordinate, TravelEstimate, TravelMode
from tempest_fastapi_sdk.schemas.base import BaseSchema


class RouteRequestSchema(BaseSchema):
    """A route-estimate request between two points.

    Attributes:
        origin: Start coordinate.
        destination: End coordinate.
        mode: Desired travel mode.
    """

    origin: Coordinate
    destination: Coordinate
    mode: TravelMode = TravelMode.CAR


# The response is the SDK's own TravelEstimate — nothing to redefine.
RouteResponseSchema = TravelEstimate
```

### Service — business logic + fallback

```python
# src/services/geo.py
from tempest_fastapi_sdk.geo import (
    Coordinate,
    RoutingBackend,
    TravelEstimate,
    TravelMode,
    estimate_travel,
)


class GeoService:
    """Estimates travel distance and time between two points.

    Uses a `RoutingBackend` (OSRM) for the real route and falls back to the
    offline heuristic when the backend fails, so the endpoint never 5xxs
    just because the routing server hiccuped.
    """

    def __init__(self, routing: RoutingBackend) -> None:
        """Initialize the service.

        Args:
            routing: A routing backend (e.g. `OSRMBackend`).
        """
        self.routing: RoutingBackend = routing

    async def estimate(
        self,
        origin: Coordinate,
        destination: Coordinate,
        mode: TravelMode = TravelMode.CAR,
    ) -> TravelEstimate:
        """Estimate the trip, real route with an offline fallback.

        Args:
            origin: Start coordinate.
            destination: End coordinate.
            mode: Travel mode.

        Returns:
            The `TravelEstimate` — `source="osrm"` when the real route
            answered, `source="heuristic"` on the fallback.
        """
        try:
            return await self.routing.route(origin, destination, mode=mode)
        except RuntimeError:
            return estimate_travel(origin, destination, mode)
```

### Controller — thin pass-through (room for orchestration)

```python
# src/controllers/geo.py
from src.schemas.geo import RouteRequestSchema
from src.services.geo import GeoService
from tempest_fastapi_sdk.geo import TravelEstimate


class GeoController:
    """Orchestrates `GeoService` for the routers."""

    def __init__(self, service: GeoService) -> None:
        """Initialize the controller.

        Args:
            service: The geolocation service.
        """
        self.service: GeoService = service

    async def estimate_route(self, payload: RouteRequestSchema) -> TravelEstimate:
        """Estimate a route from the validated payload.

        Args:
            payload: Origin, destination and mode.

        Returns:
            The travel estimate.
        """
        return await self.service.estimate(
            payload.origin, payload.destination, payload.mode
        )
```

### Dependency — injects the shared httpx client

```python
# src/api/dependencies/services.py
from collections.abc import AsyncIterator

import httpx
from fastapi import Depends

from src.controllers.geo import GeoController
from src.services.geo import GeoService
from tempest_fastapi_sdk.geo import OSRMBackend


async def get_geo_controller() -> AsyncIterator[GeoController]:
    """Provide a `GeoController` with a short-lived httpx client.

    Yields:
        A ready-to-use controller; the client closes when done.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        backend = OSRMBackend(http_client=client)
        yield GeoController(GeoService(backend))
```

!!! tip "Reuse the client across requests"
    Opening an `httpx.AsyncClient` per request is simple but costs
    handshakes. In production, create one client in the app `lifespan`,
    stash it on `app.state`, and inject it into `OSRMBackend` — the SDK
    never closes the client you pass, so lifecycle is yours to own.

### Router — HTTP only

```python
# src/api/routers/geo.py
from fastapi import APIRouter, Depends

from src.api.dependencies.services import get_geo_controller
from src.controllers.geo import GeoController
from src.schemas.geo import RouteRequestSchema
from tempest_fastapi_sdk.geo import TravelEstimate

router = APIRouter(prefix="/api/geo", tags=["geo"])


@router.post("/estimate")
async def estimate_route(
    payload: RouteRequestSchema,
    controller: GeoController = Depends(get_geo_controller),
) -> TravelEstimate:
    """Estimate distance and time between two points per mode."""
    return await controller.estimate_route(payload)
```

A `POST /api/geo/estimate` with origin/destination/mode returns
`{"mode": "...", "distance_km": ..., "duration_minutes": ..., "source": ...}`.

## Radius filter and neighbours (in memory)

With no routing server, the geometry helpers filter and rank by
proximity. `within_radius` returns what's inside the radius; `nearest`
returns the `k` closest. Both take `key=` to extract a `Coordinate` from
your own objects:

```python
from dataclasses import dataclass

from tempest_fastapi_sdk.geo import Coordinate, nearest, within_radius


@dataclass
class Store:
    """A store of yours, holding its coordinate in a field of its own."""

    name: str
    location: Coordinate


store_a = Store("Boa Viagem", Coordinate(latitude=-8.0476, longitude=-34.8770))
store_b = Store("Olinda", Coordinate(latitude=-7.9899, longitude=-34.8386))
store_c = Store("Jaboatão", Coordinate(latitude=-8.1130, longitude=-34.9060))

center = Coordinate(latitude=-23.55, longitude=-46.63)
stores = [store_a, store_b, store_c]

near = within_radius(center, stores, 5.0, key=lambda s: s.location)
top3 = nearest(center, stores, k=3, key=lambda s: s.location)
```

!!! note "The radius is a cheap pre-filter"
    Straight-line distance underestimates road distance: use a radius a bit
    larger than the target and refine with `estimate_travel`/OSRM only on
    the finalists.

## Radius search in the database (`GeoRepositoryMixin`)

To search a radius straight from the database, mix `GeoPointMixin` into
the model and `GeoRepositoryMixin` into the repository. `nearby` runs a
**bounding-box pre-filter in SQL** (indexed) and refines with Haversine in
Python:

```python
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.geo import Coordinate, GeoPointMixin, GeoRepositoryMixin


class StoreModel(GeoPointMixin, BaseModel):
    __tablename__ = "stores"
    name: Mapped[str] = mapped_column(String(120))


class StoreRepository(GeoRepositoryMixin, BaseRepository[StoreModel]):
    ...


async def nearby_stores(repo: StoreRepository, center: Coordinate) -> list[StoreModel]:
    # Active stores within 5 km, nearest first, at most 20.
    return await repo.nearby(
        center,
        radius_km=5.0,
        extra_filters={"is_active": True},
        limit=20,
    )
```

!!! tip "PostGIS when the volume grows"
    On Postgres + the PostGIS extension, swap in `PostGISRepositoryMixin`:
    `nearby` pushes the filter and distance sort into the database via
    `ST_DWithin` / `ST_Distance` — no extra Python dependency, same
    signature.

## Paginating a radius search in the database (`paginate_nearby`)

`nearby` loads the whole bounding box and sorts in Python — great for "the 20
closest stores", poor for a **paginated listing**: page 3 of a radius search
would mean fetching everything and slicing it in memory. `paginate_nearby`
keeps it all in the database: the Haversine distance becomes a **SQL
expression** (`haversine_distance_sql`), the radius becomes a `WHERE` on it
(behind the bounding-box pre-filter the index covers), and sorting, `COUNT`
and `OFFSET`/`LIMIT` run there. No PostGIS — it works on plain PostgreSQL and
on SQLite.

```python
import asyncio

from sqlalchemy import Float, String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.geo import Coordinate, GeoRepositoryMixin


class EventModel(BaseModel):
    __tablename__ = "events_nearby_demo"

    name: Mapped[str] = mapped_column(String(80))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)


class EventRepository(GeoRepositoryMixin, BaseRepository[EventModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=EventModel)


async def main() -> None:
    """Run this example."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(EventModel.metadata.create_all)
    async with AsyncSession(engine) as session:
        repo = EventRepository(session)
        await repo.add_all(
            [
                EventModel(name="Sé", latitude=-23.5505, longitude=-46.6333),
                EventModel(name="Paulista", latitude=-23.5614, longitude=-46.6559),
                EventModel(name="Santos", latitude=-23.9608, longitude=-46.3336),
                EventModel(name="Rio", latitude=-22.9068, longitude=-43.1729),
                EventModel(name="No pin", latitude=None, longitude=None),
            ],
        )
        center = Coordinate(latitude=-23.5505, longitude=-46.6333)
        page = await repo.paginate_nearby(center, 100.0, page=1, page_size=2)
        for event, distance_km in page["items"]:
            print(f"{event.name}: {distance_km:.1f} km")
        print(page["total"], page["pages"])
    await engine.dispose()


asyncio.run(main())
```

Output:

```text
Sé: 0.0 km
Paulista: 2.6 km
3 2
```

Piece by piece:

- The return value is the **SDK pagination envelope** (`items`, `total`,
  `page`, `page_size`, `pages`) — the same shape as `paginate`. Each item is a
  `NearbyMatch`, a named tuple `(row, distance_km)`: unpack it in the `for` or
  read it by name (`match.row`, `match.distance_km`). The distance is the one
  the database computed and sorted by, so it never disagrees with the order.
- A row whose latitude or longitude is `NULL` never matches ("No pin" was left
  out). Ties on distance break on `id`, so pages are stable.
- `extra_filters=` (the `paginate` vocabulary), `where=` (a `Q`) and `query=`
  (your own `select` whose first entity is the model) are ANDed with the
  radius. `latitude_field=`/`longitude_field=` point at columns with other
  names; a name that is not a mapped column raises `ValueError`.
- `page_size` honours the repository's `max_page_size` when declared
  (`PageSizeTooLargeException`). See
  [Pagination](database.md#which-columns-sort-and-how-many-rows-fit-in-a-page).

To return this from a route, map each pair to the response schema and reuse
the envelope's metadata:

```python
from typing import Any

from pydantic import Field
from tempest_fastapi_sdk import BasePaginationSchema, BaseSchema


class EventNearbyResponse(BaseSchema):
    """An event with its distance to the searched point."""

    name: str = Field(description="Event name.")
    distance_km: float = Field(description="Distance in km.")


def to_response(page: dict[str, Any]) -> BasePaginationSchema[EventNearbyResponse]:
    """Map a paginate_nearby result to the API envelope."""
    return BasePaginationSchema[EventNearbyResponse](
        items=[
            EventNearbyResponse(name=row.name, distance_km=distance_km)
            for row, distance_km in page["items"]
        ],
        total=page["total"],
        page=page["page"],
        page_size=page["page_size"],
        pages=page["pages"],
    )
```

!!! info "Technical details: the expression and the clamp"
    `haversine_distance_sql(lat, lng, center)` uses only `sin`, `cos`, `asin`,
    `sqrt` and arithmetic — degrees become radians by multiplying with a
    constant, so not even `radians()` is required. SQLite needs its math
    functions compiled in (`SQLITE_ENABLE_MATH_FUNCTIONS`, 3.35+). Check
    yours with `SELECT sin(1), asin(1), sqrt(4)` — the SQLite 3.47.1 inside the
    CPython 3.13.3 that `uv` installs (python-build-standalone) answers it.

    The Haversine term goes past 1 through floating-point error: for random
    antipodal pairs it came out as `1.0000000000000002` in about 4% of
    2,000,000 draws, on PostgreSQL 16 and on SQLite. `sqrt` rounds that value
    back to `1.0`, but `asin` above 1 is `NULL` on SQLite and
    `ERROR: input is out of range` on PostgreSQL — so the term is clamped to
    `[0, 1]` with a `CASE` first, and the result does not depend on that
    rounding.

!!! warning "Antimeridian"
    As with `nearby`, the bounding box is clamped at ±180 longitude: a circle
    crossing the antimeridian misses the far side.

## Geocoding (address <-> coordinate)

`NominatimBackend` resolves address → coordinate (and reverse) via
OpenStreetMap Nominatim, for free. Injected `httpx` client, like OSRM:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import Coordinate, NominatimBackend


async def main() -> None:
    """Run this example."""
    async with httpx.AsyncClient() as client:
        geocoder = NominatimBackend(http_client=client, user_agent="my-app/1.0")
        hit = await geocoder.geocode("Av. Paulista, 1578, São Paulo")
        if hit:
            print(hit.coordinate, hit.display_name)
        place = await geocoder.reverse(Coordinate(latitude=-23.561, longitude=-46.656))


asyncio.run(main())
```

!!! warning "Public Nominatim usage policy"
    `nominatim.openstreetmap.org` requires a descriptive `User-Agent` and
    caps you at ~1 req/s. Self-host for scale.

## Distance matrix and route geometry

OSRM does more than point-to-point: `matrix` computes N×M in one call
(dispatching, "nearest courier") and `route(..., with_geometry=True)`
returns the route line decoded into `TravelEstimate.geometry`:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import Coordinate, OSRMBackend

store_a = Coordinate(latitude=-8.0476, longitude=-34.8770)
a = store_a

store_b = Coordinate(latitude=-7.9899, longitude=-34.8386)
b = store_b

client = httpx.AsyncClient()

destination = Coordinate(latitude=-7.9899, longitude=-34.8386)
destinations = [destination]


def draw_on_map(line: list[Coordinate]) -> None:
    """Render the route line on your map widget."""


origin = Coordinate(latitude=-8.0476, longitude=-34.8770)
origins = [origin]


backend = OSRMBackend(http_client=client)


async def main() -> None:
    """Run this example."""
    matrix = await backend.matrix(origins, destinations)  # DistanceMatrix
    print(matrix.durations_minutes[0][2])  # time origin 0 -> destination 2

    route = await backend.route(a, b, with_geometry=True)
    draw_on_map(route.geometry)  # list[Coordinate]


asyncio.run(main())
```

`encode_polyline` / `decode_polyline` convert the line to/from the compact
Google/OSRM format (precision 5 or 6), no dependency.

## Geometry: projection, geofence, length

```python
from tempest_fastapi_sdk.geo import (
    Coordinate,
    bounding_box,
    destination_point,
    initial_bearing,
    path_length_km,
    point_in_polygon,
    polygon_area_km2,
)

point = Coordinate(latitude=-8.0476, longitude=-34.8770)
center = point

delivery_zone = bounding_box(point, radius_km=5)

zone_polygon = [
    Coordinate(
        latitude=delivery_zone.min_latitude,
        longitude=delivery_zone.min_longitude,
    ),
    Coordinate(
        latitude=delivery_zone.min_latitude,
        longitude=delivery_zone.max_longitude,
    ),
    Coordinate(
        latitude=delivery_zone.max_latitude,
        longitude=delivery_zone.max_longitude,
    ),
    Coordinate(
        latitude=delivery_zone.max_latitude,
        longitude=delivery_zone.min_longitude,
    ),
]

gps_points = [point, Coordinate(latitude=-7.9899, longitude=-34.8386)]


target = destination_point(center, bearing_degrees=90.0, distance_km=2.0)  # 2 km east
heading = initial_bearing(center, target)  # ~90.0
inside = delivery_zone.contains(point)  # geofence: the cheap box test
in_polygon = point_in_polygon(point, zone_polygon)  # geofence: the ring
area = polygon_area_km2(zone_polygon)
travelled = path_length_km(gps_points)
```

## Brazil: UF centroid, CEP and address → coordinate

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import NominatimBackend, cep_to_coordinate, uf_centroid

geocoder = NominatimBackend(http_client=httpx.AsyncClient())


pin = uf_centroid("SP")  # approximate state centre, offline


async def main() -> None:
    """Run this example."""
    coord = await cep_to_coordinate("01310-100", geocoder=geocoder)  # via Nominatim


asyncio.run(main())
```

### Free-text address: `resolve_br_coordinate`

Brazilian sign-up data often keeps the address in a single field, with the
CEP somewhere in the middle (`"Av. Paulista, 1578 - 01310-200"`).
`resolve_br_coordinate` tries three sources, most precise first, and returns
the first that answers:

1. the **CEP** found in `address` or `complement` (`extract_cep`), through
   `cep_to_coordinate`;
2. the **full address** — `"address, city, UF, Brasil"`, empty parts left
   out — geocoded;
3. the **state centroid** (`uf_centroid`), offline.

```python
import asyncio

from tempest_fastapi_sdk.geo import extract_cep, resolve_br_coordinate

print(extract_cep("Av. Frei Serafim, 2280", "CEP 64001020, sala 4"))


async def main() -> None:
    """Run this example."""
    point = await resolve_br_coordinate(
        geocoder=None,
        uf="pi",
        address="Av. Frei Serafim, 2280",
        city="Teresina",
    )
    print(point)
    print(await resolve_br_coordinate(geocoder=None, uf="ZZ"))


asyncio.run(main())
```

Output:

```text
64001-020
latitude=-7.4 longitude=-42.5
None
```

- `geocoder=None` skips straight to the centroid — the offline path, for tests
  and for deployments without geocoding. An unknown UF returns `None`.
- **A geocoder failure never propagates.** Any exception from `geocode` is
  logged at `WARNING` (with the traceback) and the chain moves to the next
  step; the worst outcome is the state centroid.
- **Retrying belongs to the geocoder you inject.** The function only sees the
  failure after your retries are spent, so wrap `geocode`:

```python
import httpx
from tempest_fastapi_sdk import RetryPolicy, async_retry
from tempest_fastapi_sdk.geo import (
    Coordinate,
    GeocodeResult,
    GeocodingBackend,
    NominatimBackend,
)


class RetryingGeocoder:
    """Geocoder that retries transport errors before giving up."""

    def __init__(self, inner: GeocodingBackend) -> None:
        self._inner = inner

    @async_retry(RetryPolicy(max_attempts=3), (httpx.HTTPError,))
    async def geocode(self, query: str) -> GeocodeResult | None:
        """Forward to the wrapped backend, retrying transport errors."""
        return await self._inner.geocode(query)

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        """Forward reverse geocoding unchanged."""
        return await self._inner.reverse(coordinate)


geocoder = RetryingGeocoder(
    NominatimBackend(
        http_client=httpx.AsyncClient(timeout=10.0),
        user_agent="my-service/1.0 (ops@example.com)",
    ),
)
```

!!! warning "Keep public Nominatim off the request path"
    The public instance limits you to ~1 req/s and requires your own
    `User-Agent`. Resolve the coordinate in the background (after saving the
    record), not in the user's request.

## Recap

- `haversine_km(a, b)` — great-circle distance, pure, always available.
- `bounding_box` / `within_radius` / `nearest` — offline proximity; `key=` for your own objects.
- `GeoPointMixin` + `GeoRepositoryMixin.nearby` — radius search in the DB (PostGIS via `PostGISRepositoryMixin`).
- `GeoRepositoryMixin.paginate_nearby` — radius, sort, `COUNT` and page in SQL, no PostGIS; each item is `NearbyMatch(row, distance_km)`.
- `NominatimBackend` — address<->coordinate geocoding, free, injected `httpx`.
- `OSRMBackend.matrix` / `route(with_geometry=True)` — N×M matrix and route line; `encode_polyline`/`decode_polyline`.
- `destination_point` / `initial_bearing` / `point_in_polygon` / `polygon_area_km2` / `path_length_km` — offline geometry.
- `uf_centroid` / `cep_to_coordinate` — Brazil shortcuts.
- `extract_cep` / `resolve_br_coordinate` — CEP from free text and the CEP → address → state centroid chain, never raising on a geocoder failure.
- `estimate_travel` / `OSRMBackend.route` — distance + time (`heuristic`/`osrm`); car/motorcycle/bus/bicycle/pedestrian modes.
