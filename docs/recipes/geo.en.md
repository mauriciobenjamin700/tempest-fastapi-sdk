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

## Recap

- `haversine_km(a, b)` — great-circle distance, pure, always available.
- `estimate_travel(a, b, mode)` — offline distance + time (`source="heuristic"`).
- `OSRMBackend(http_client=...).route(a, b, mode=...)` — real road, free (`source="osrm"`), `[geo]` extra.
- Motorcycle/bus derive from the car via `DEFAULT_MODE_DURATION_FACTORS` — works on both layers.
- `Coordinate` validates lat/long; `TravelEstimate` serializes straight into a FastAPI response.
