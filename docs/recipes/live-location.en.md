# Live location

A driver on the road, passengers following on the map. A courier on the way,
a customer watching "where is my order". A fleet on the highway, a dashboard
showing every vehicle. The shape is always the same:

1. whoever moves **sends** their position every so often;
2. the service **limits** the rate and keeps **only the latest**;
3. the position reaches **only who may see it** — and stops when the trip
   ends.

Writing that glue by hand over `cache` + `sse` + `geo` in every service is
how the security rules (who publishes, who receives, when it stops) end up
different in each one. `tempest_fastapi_sdk.geo` ships the piece:
`LivePositionSchema`, `LivePositionStore` and `make_live_position_router`.

```bash
uv add "tempest-fastapi-sdk[cache,geo]"
```

`[cache]` brings `redis`; `[geo]` brings the `httpx` of `OSRMBackend` (only
needed for the ETA over the real road).

## The complete example: a shared ride

A ride with a driver, two passengers who paid (each with their own pickup
point) and a third person who did not. Copy it, run it against a local
Redis, and `main()` walks the whole trip in one process:

```python
import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Header
from redis.asyncio import Redis

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import (
    Coordinate,
    LivePositionStore,
    OSRMBackend,
    make_live_position_router,
)
from tempest_fastapi_sdk.sse import SSEBroker


@dataclass
class Ride:
    """A shared ride: who drives, whether it started, who paid and where they board."""

    driver_id: str
    started: bool = False
    paid_pickups: dict[str, Coordinate] = field(default_factory=dict)


RIDES: dict[str, Ride] = {
    "ride-42": Ride(
        driver_id="driver-1",
        paid_pickups={
            "ana": Coordinate(latitude=-5.0381, longitude=-42.4608),
            "bruno": Coordinate(latitude=-4.8278, longitude=-42.1686),
        },
    ),
}


async def paid_passengers(channel_id: str) -> list[str]:
    """Audience: only passengers who paid for this ride see the driver."""
    ride = RIDES.get(channel_id)
    return [] if ride is None else list(ride.paid_pickups)


async def pickup_points(channel_id: str) -> dict[str, Coordinate]:
    """Each paid passenger's boarding point, for their own ETA."""
    ride = RIDES.get(channel_id)
    return {} if ride is None else dict(ride.paid_pickups)


async def only_the_driver(channel_id: str, x_user_id: str = Header()) -> None:
    """Only the ride's driver may publish its position."""
    ride = RIDES.get(channel_id)
    if ride is None or ride.driver_id != x_user_id:
        raise ForbiddenException()


async def ride_started(channel_id: str) -> bool:
    """Positions are accepted only between start and finish."""
    ride = RIDES.get(channel_id)
    return ride is not None and ride.started


redis = Redis.from_url(
    os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
)
broker = SSEBroker(heartbeat_seconds=None)
osrm_client = httpx.AsyncClient(timeout=10.0)
store = LivePositionStore(
    redis,
    key_prefix="rides:live",
    broker=broker,
    audience=paid_passengers,
    eta_targets=pickup_points,
    routing=OSRMBackend(http_client=osrm_client),
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_live_position_router(
        store,
        authorize=only_the_driver,
        channel_active=ride_started,
        prefix="/api/rides",
    )
)


async def finish_ride(ride_id: str) -> None:
    """End the ride: stop accepting positions and forget the last one."""
    RIDES[ride_id].started = False
    await store.clear(ride_id)


async def main() -> None:
    """Drive the ride end to end in one process."""
    ana = broker.register("ana")
    carla = broker.register("carla")
    position = {
        "lat": -5.0892,
        "lng": -42.8019,
        "speed_kmh": 38.0,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    url = "/api/rides/ride-42/position"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as api:
        before = await api.post(url, json=position, headers={"X-User-Id": "driver-1"})
        print("before start:", before.status_code)

        RIDES["ride-42"].started = True
        intruder = await api.post(url, json=position, headers={"X-User-Id": "ana"})
        print("passenger publishing:", intruder.status_code)
        sent = await api.post(url, json=position, headers={"X-User-Id": "driver-1"})
        print("driver publishing:", sent.status_code, sent.json())
        again = await api.post(url, json=position, headers={"X-User-Id": "driver-1"})
        print("same spot again:", again.json())

    frame = await asyncio.wait_for(anext(ana.stream()), timeout=5.0)
    print(frame.decode().strip())
    try:
        await asyncio.wait_for(anext(carla.stream()), timeout=0.5)
    except TimeoutError:
        print("carla (did not pay): nothing")

    await finish_ride("ride-42")
    print("latest after finish:", await store.latest("ride-42"))
    print((await asyncio.wait_for(anext(ana.stream()), timeout=5.0)).decode().strip())

    await osrm_client.aclose()
    await redis.aclose()


asyncio.run(main())
```

Output measured on 2026-11-18, with a local Redis 6.2 and the public OSRM
demo answering the ETA:

```text
before start: 409
passenger publishing: 403
driver publishing: 200 {'accepted': True}
same spot again: {'accepted': False}
event: live_position
data: {"channel_id": "ride-42", "position": {"lat": -5.0892, "lng": -42.8019, "accuracy_m": null, "speed_kmh": 38.0, "heading": null, "recorded_at": "2026-11-18T21:03:48.130416Z"}, "eta": {"target": {"latitude": -5.0381, "longitude": -42.4608}, "distance_km": 40.735099999999996, "duration_minutes": 37.755, "source": "osrm"}}
carla (did not pay): nothing
latest after finish: None
event: live_position_cleared
data: {"channel_id": "ride-42"}
```

Let's go piece by piece.

## The position: `LivePositionSchema`

It is the body the device sends: `lat`, `lng`, `accuracy_m`, `speed_kmh`,
`heading` and `recorded_at`. Validation refuses:

- a coordinate outside [-90, 90] / [-180, 180], a `heading` outside
  [0, 360), a negative speed or accuracy;
- a `recorded_at` without a timezone;
- a `recorded_at` more than 30 seconds in the future
  (`LIVE_POSITION_MAX_CLOCK_SKEW`) — a few seconds of drift between the phone
  clock and the server's pass.

`position.coordinate` returns a `Coordinate`, ready for `haversine_km` or
`OSRMBackend`.

## What is kept: `LivePositionStore`

The store keeps **only the latest position** of each channel in Redis, under
`"<key_prefix>:<channel_id>"`, with a TTL. `publish(channel_id, position)`
returns `True` when it kept it and `False` when it refused, in this order:

| Refusal | Why | Default |
| --- | --- | --- |
| `recorded_at` is not after the stored one | late or replayed fix | — |
| less than `min_interval_seconds` since the last accepted one | throttle (server time) | `5.0` |
| moved less than `min_distance_m` | GPS noise while standing still | `10.0` |

In the example, `same spot again` comes back `False`: same position, same
`recorded_at`, right after the first one.

!!! tip "Standing at the stop does not vanish from the map"
    A position refused for distance **renews the TTL**. A driver waiting at
    the stop keeps sending, nothing new is published, and the latest position
    stays alive.

The TTL (`ttl_seconds`, default 300) is what clears the marker on its own
when the device stops sending without anyone ending the trip — app killed,
battery dead.

`latest(channel_id)` reads the latest position (or `None`), and
`clear(channel_id)` ends it: deletes it at once and tells the audience with
the `live_position_cleared` event, so the client removes the marker.

## Who receives: `audience`

With an `SSEBroker`, every accepted position becomes a `live_position` event
for each recipient the `audience(channel_id)` callback returns — in the
example, only the passengers who paid. Carla, who did not, gets nothing.

The SDK **never** decides who may see. That is why `broker` and `audience`
go together: passing one without the other raises `ValueError` at
construction. There is no "send to everyone" by omission.

!!! info "The recipient is a broker channel"
    The store publishes on the channel named by what `audience` returns.
    `make_sse_router` subscribes each user to the channel `str(user.id)` by
    default, so return user ids and each one's authenticated stream already
    receives — see the [SSE](sse.md) recipe.

## ETA to the pickup point

Pass `eta_targets(channel_id) -> {recipient: Coordinate}` and every event
carries **that** recipient's ETA to their own point. With `routing=` (an
`OSRMBackend`), all points go in one `matrix` request; without it, or when
OSRM fails, each ETA comes from `estimate_travel`, with `source="heuristic"`
instead of `"osrm"`.

A recipient `eta_targets` names and `audience` does not is ignored: who sees
what is `audience`'s call, always.

## The endpoint: `make_live_position_router`

Mounts `POST <prefix>/{channel_id}/position`. The two rules are parameters
**with no default**:

- `authorize` — a dependency that raises (`ForbiddenException` → `403`) when
  the caller may not publish on that channel. Leaving the argument out is
  Python's own `TypeError`, and passing `None` raises `ValueError`: the
  router is not built without it.
- `channel_active` — a dependency answering whether the channel accepts
  positions now. `False` answers `409` and nothing is stored.

Both are ordinary FastAPI dependencies: they declare `channel_id: str` to get
the path parameter and can depend on your `current_user`.

The success response is only `{"accepted": true | false}`: the position
reaches viewers through `audience`, and no other way. The one response that
repeats part of the body is FastAPI's `422` validation error (a
`recorded_at` in the future, for instance), and it goes only to the sender —
after `authorize`, so only to the authorized publisher.

!!! warning "Several workers"
    With more than one process, build `SSEBroker(redis=..., channel_prefix=...)`
    and run `broker.run()` in the lifespan, as in the [SSE](sse.md) recipe.
    The store does not change: it only calls `broker.publish`. Delivery
    across workers is the `SSEBroker`'s, covered in the SSE recipe; it was
    not measured again through the store.

## Recap

- `LivePositionSchema` — `lat`/`lng`/`accuracy_m`/`speed_kmh`/`heading`/`recorded_at`, validated (future and naive timestamps refused).
- `LivePositionStore.publish` — keeps only the latest, with a TTL; throttle (`min_interval_seconds`), least movement (`min_distance_m`) and older fixes refused.
- `audience` is mandatory with `broker`: the service decides who sees, the SDK delivers.
- `eta_targets` + `routing` — per-recipient ETA through `matrix`, falling back to `estimate_travel`.
- `clear` — end of the trip: deletes and announces `live_position_cleared`.
- `make_live_position_router` — `authorize` and `channel_active` mandatory; `403`/`409`; the response never carries the position.
