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
print(round(km, 1))  # ~360.0
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

print(by_car.distance_km, by_car.duration_minutes)   # e.g. 8.2 9.8
print(by_bus.duration_minutes)                        # larger (bus stops)
print(by_car.source)                                  # "heuristic"
```

The defaults are tunable per call:

```python
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

## Recipe: radius filter (proximity)

With no routing server — just `haversine_km` — you can filter "what is
within N km of here", handy to list nearby couriers/stores:

```python
from tempest_fastapi_sdk.geo import Coordinate, haversine_km


def within_radius(
    center: Coordinate,
    points: list[Coordinate],
    radius_km: float,
) -> list[Coordinate]:
    """Filter the points within a radius from the center.

    Args:
        center: Reference point.
        points: Candidates.
        radius_km: Maximum radius in km.

    Returns:
        The points whose straight-line distance is <= `radius_km`,
        sorted nearest to farthest.
    """
    near = [(p, haversine_km(center, p)) for p in points]
    near = [(p, d) for p, d in near if d <= radius_km]
    return [p for p, _ in sorted(near, key=lambda pair: pair[1])]
```

!!! note "The radius is a cheap pre-filter"
    Straight-line distance underestimates road distance, so use a radius a
    bit larger than the target and refine with `estimate_travel`/OSRM only
    on the finalists — avoids calling the router for everyone.

## Recap

- `haversine_km(a, b)` — great-circle distance, pure, always available.
- `estimate_travel(a, b, mode)` — offline distance + time (`source="heuristic"`).
- `OSRMBackend(http_client=...).route(a, b, mode=...)` — real road, free (`source="osrm"`), `[geo]` extra.
- Motorcycle/bus derive from the car via `DEFAULT_MODE_DURATION_FACTORS` — works on both layers.
- `Coordinate` validates lat/long; `TravelEstimate` serializes straight into a FastAPI response.
