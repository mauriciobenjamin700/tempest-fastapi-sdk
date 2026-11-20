# Fleet traffic (anonymous GPS)

Your fleet already sends live positions (see the [Live location](live-location.md)
recipe). Those same positions answer two questions a commercial map charges a
lot for:

1. **What is the traffic like right now?** Median speed per road stretch over
   the last few minutes.
2. **Which road does the fleet use that the map does not have?** Stretches with
   many traces far from every OpenStreetMap road.

The map is **hybrid**: OpenStreetMap is the base (the road graph, the names), and
the fleet's GPS is the live layer. The decision and the measurements are in
[tempest-react-sdk#667](https://github.com/mauriciobenjamin700/tempest-react-sdk/issues/667).

A GPS trace is personal data: it reveals home, work and routine. That is why the
privacy rules (LGPD) here **are code**, not advice — the start and end of every
trip are cut, every road stretch is k-anonymous, no trace is kept and no device
id is stored in the clear. Each rule has a test.

```bash
uv add "tempest-fastapi-sdk[geo]"
```

Nothing else: the graph, the map-matching, the aggregation and the inference are
pure Python. `[cache]` only comes in if you keep the traffic snapshot in Redis
(`RedisTrafficSnapshotStore`).

## The complete example

Two parallel avenues 400 m apart, in an OSM excerpt. The buses go along Av. Sul,
up a street **OSM does not have**, and on along Av. Norte. Copy and run it — no
network, no Redis:

```python
import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI, Header

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import (
    Coordinate,
    InMemoryTrafficSnapshotStore,
    LivePositionSchema,
    OffRoadDensity,
    TrafficAggregator,
    TrafficPrivacy,
    infer_road_candidates,
    make_traffic_router,
    road_graph_from_overpass,
)

CENTER = Coordinate(latitude=-5.0892, longitude=-42.8019)
METERS_PER_DEGREE = 6371008.8 * math.pi / 180


def point(x: float, y: float) -> Coordinate:
    """A point x metres east and y metres north of the centre."""
    cos_lat = math.cos(math.radians(CENTER.latitude))
    return Coordinate(
        latitude=CENTER.latitude + y / METERS_PER_DEGREE,
        longitude=CENTER.longitude + x / (METERS_PER_DEGREE * cos_lat),
    )


def overpass_excerpt() -> dict[str, Any]:
    """Two parallel avenues 400 m apart, joined at both ends (no middle street)."""
    xs = [-600, 0, 300, 600, 900]
    corners = [(x, 0) for x in xs] + [(x, 400) for x in xs]
    nodes = [
        {
            "type": "node",
            "id": n,
            "lat": point(x, y).latitude,
            "lon": point(x, y).longitude,
        }
        for n, (x, y) in enumerate(corners, start=1)
    ]
    ways = [
        {
            "type": "way",
            "id": 100,
            "nodes": [1, 2, 3, 4, 5],
            "tags": {"highway": "primary", "name": "Av. Sul", "maxspeed": "50"},
        },
        {
            "type": "way",
            "id": 200,
            "nodes": [6, 7, 8, 9, 10],
            "tags": {"highway": "primary", "name": "Av. Norte"},
        },
        {
            "type": "way",
            "id": 300,
            "nodes": [1, 6],
            "tags": {"highway": "residential"},
        },
        {
            "type": "way",
            "id": 400,
            "nodes": [5, 10],
            "tags": {"highway": "residential"},
        },
    ]
    return {"elements": [*nodes, *ways]}


def route(second: int) -> tuple[float, float]:
    """10 m/s: east on Av. Sul, north through an unmapped street, east on Av. Norte."""
    d = 10.0 * second - 600
    if d <= 300:
        return d, 0.0
    if d <= 700:
        return 300.0, d - 300
    return 300.0 + d - 700, 400.0


async def ops_only(x_user_id: str = Header()) -> None:
    """Only the operations team reads traffic."""
    if x_user_id != "ops":
        raise ForbiddenException()


graph = road_graph_from_overpass(overpass_excerpt())
store = InMemoryTrafficSnapshotStore()
density = OffRoadDensity(CENTER)
start = datetime(2026, 11, 18, 7, tzinfo=UTC)
clock = [start.timestamp()]
traffic = TrafficAggregator(
    graph,
    privacy=TrafficPrivacy(min_devices=5),
    store=store,
    density=density,
    clock=lambda: clock[0],
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_traffic_router(store, authorize=ops_only, prefix="/api/traffic")
)


def drive(bus: str, departure: datetime) -> None:
    """One bus trip, one position per second, then the trip ends."""
    for second in range(191):
        when = departure + timedelta(seconds=second)
        clock[0] = when.timestamp()
        x, y = route(second)
        here = point(x, y)
        traffic.add_position(
            bus,
            LivePositionSchema(
                lat=here.latitude, lng=here.longitude, recorded_at=when
            ),
        )
        if second % 20 == 19:
            traffic.process()
    traffic.end_trip(bus)


async def main() -> None:
    """Five buses drive, the app reads the traffic, the backend infers a street."""
    for n in range(4):
        drive(f"bus-{n}", start + timedelta(seconds=20 * n))
    snapshot = await traffic.flush()
    print("4 buses -> published edges:", len(snapshot.edges))

    drive("bus-4", start + timedelta(seconds=80))
    snapshot = await traffic.flush()
    print("5 buses -> published edges:", len(snapshot.edges))
    for edge in snapshot.edges:
        print(" ", edge.edge_id, edge.speed_kmh, "km/h,", edge.devices, "devices")

    box = {
        "min_latitude": point(0, -50).latitude,
        "max_latitude": point(0, 50).latitude,
        "min_longitude": point(-650, 0).longitude,
        "max_longitude": point(250, 0).longitude,
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as api:
        stranger = await api.get(
            "/api/traffic", params=box, headers={"X-User-Id": "x"}
        )
        print("stranger:", stranger.status_code)
        ops = await api.get(
            "/api/traffic", params=box, headers={"X-User-Id": "ops"}
        )
        print("ops:", ops.status_code, [e["edge_id"] for e in ops.json()["edges"]])

    for n in range(5, 20):
        drive(f"bus-{n}", start + timedelta(minutes=n))
    density.roll((start + timedelta(days=1)).timestamp())
    for candidate in infer_road_candidates(density, graph):
        first, last = candidate.coordinates[0], candidate.coordinates[-1]
        print(
            "candidate:",
            candidate.status,
            round(candidate.length_m),
            "m,",
            candidate.devices,
            "devices, from",
            (round(first.latitude, 5), round(first.longitude, 5)),
            "to",
            (round(last.latitude, 5), round(last.longitude, 5)),
        )


asyncio.run(main())
```

Output measured on 2026-11-19 (Python 3.13.3):

```text
4 buses -> published edges: 0
5 buses -> published edges: 4
  way/100:0 36.0 km/h, 5 devices
  way/100:1 36.0 km/h, 5 devices
  way/200:2 36.0 km/h, 5 devices
  way/200:3 36.0 km/h, 5 devices
stranger: 403
ops: 200 ['way/100:0', 'way/100:1']
candidate: candidate 400 m, 20 devices, from (-5.0892, -42.79921) to (-5.0856, -42.79921)
```

With four buses nothing comes out: the minimum is five devices per stretch. With
the fifth, the four avenue stretches come out at 36 km/h (the example's 10 m/s).
The middle street comes back as a 400 m **candidate** joining one avenue to the
other. Let's go piece by piece.

## The road graph

`RoadGraph` is the network: WGS84 nodes and edges that point to them by index.
It is the same contract as
[tempest-react-sdk](https://github.com/mauriciobenjamin700/tempest-react-sdk)'s
`createRoadGraph`, on the wire:

```json
{"nodes": [{"latitude": -5.09, "longitude": -42.8}, {"latitude": -5.09, "longitude": -42.799}],
 "edges": [{"from": 0, "to": 1, "twoWay": true, "id": "way/100:0", "name": "Av. Sul"}],
 "twoWay": false}
```

In Python an edge is `RoadEdge(start, end, two_way, id, data)`: `from`/`to`
become `start`/`end`, and any extra field (name, class, speed limit) lives in
`data` and comes back untouched. `RoadGraph.from_dict` and `to_dict` go both
ways.

The graph comes from an OpenStreetMap excerpt, with no osmnx and no shapely:

| Function | Reads |
| --- | --- |
| `road_graph_from_overpass(data)` | an Overpass `[out:json]` answer (`way["highway"](bbox); (._;>;); out;`) |
| `road_graph_from_geojson(data)` | a `FeatureCollection` of `LineString`/`MultiLineString` (osmtogeojson, QGIS); equal vertices become one node |
| `road_graph_from_osm_json(data)` | either of the two, by shape |
| `load_road_graph(path)` | a file, cached in memory by path, size and mtime |

Each way becomes one edge per vertex pair, with `id` `"way/<id>:<n>"`. Direction
follows OSM: `oneway=yes`, roundabouts and `motorway` are one-way; `oneway=-1`
is one-way against the drawing; everything else is two-way. By default only car
roads stay (`OSM_DRIVABLE_HIGHWAYS`); `highways=None` keeps them all.

!!! tip "Tiles for the app"
    `graph.extract(bbox)` cuts the graph to a box, keeping the `id`s.
    `make_traffic_router(..., graph=graph)` serves that at `GET <prefix>/graph`,
    in the shape the app's `createRoadGraph` reads — the app matches the vehicle
    against the same edges the traffic is published on.

## Snapping to the road: `match_to_roads`

City GPS is off by 5 to 20 m, sometimes by hundreds. Snapping each position to
the nearest road picks the wrong road for 1 in 7 positions (measured in phase 0
of #667). `match_to_roads(fixes, graph)` uses the hidden Markov model of
Newson & Krumm (2009): candidates by distance (Gaussian), transitions by the
difference between the straight line and the network path (exponential), and
Viterbi picks the most likely sequence.

It is a port of tempest-react-sdk's `matchToRoads`, with the same defaults
(`ROAD_MATCH_DEFAULTS`: σ 7 m, β 3 m, radius 200 m, 16 roads, 100 m maximum
detour), chosen there by sweeping 100 real drives. The test
`tests/geo/test_map_matching_parity.py` compares the output against the
TypeScript one on the same graph and the same positions (six scenarios, a chain
break included), generated by
`node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist`.

You rarely call `match_to_roads` yourself: the aggregator does.

## The aggregation: `TrafficAggregator`

```python
from tempest_fastapi_sdk.geo import TrafficAggregator, TrafficPrivacy, load_road_graph

graph = load_road_graph("teresina.osm.json")
traffic = TrafficAggregator(graph, privacy=TrafficPrivacy(min_devices=5))
```

Three calls:

- `traffic.add_position(device_id, position)` — every position of every device
  (`LivePositionSchema`). Synchronous and cheap: no map-matching here.
- `traffic.end_trip(device_id)` — when you know the trip is over.
- `await traffic.flush()` — every so often (30 to 60 s): releases what is far
  enough behind each device, matches it to the roads, ends silent trips,
  computes the snapshot and saves it to the store, telling the SSE audience.

What comes out is a `TrafficSnapshotSchema`: `generated_at`, `window_seconds`,
`min_devices` and `edges`, each a `TrafficEdgeSchema` with `edge_id`, both ends,
`speed_kmh` (median), `devices` and `speed_limit_kmh` (from OSM's `maxspeed`,
when numeric). Direction matters: driving a road against its drawing shows up as
`"<id>~"`.

## The privacy rules

They all live in `TrafficPrivacy`, with protective defaults:

| Rule | Parameter (default) | Test |
| --- | --- | --- |
| A position within the radius of the trip **start** is dropped | `trim_radius_m` (200) | `TestTrimsStartAndEnd` |
| A position is only released once the device is farther than the radius from it; what is waiting at the trip **end** is dropped | `trim_radius_m` (200), `trip_gap_seconds` (300) | `TestTrimsStartAndEnd` |
| A stretch is published only with **k distinct devices** in the window | `min_devices` (5, at least 2), `window_seconds` (300) | `TestKAnonymity` |
| One device on two trips counts once; never double-counted across a salt rotation | — | `TestKAnonymity` |
| The device id is never kept: HMAC under a per-process secret; each sample carries a per-stretch HMAC under a salt that rotates every window and is forgotten after it | — | `TestNoIdentifierKept` |
| The store gets the k-anonymous snapshot only | — | `TestNoIdentifierKept` |
| Bad accuracy, impossible jumps and out-of-order positions are dropped | `max_accuracy_m` (50), `max_speed_kmh` (200) | `TestCleaning` |

??? info "Why the count is a lower bound"
    The salt rotates every window, so the same device in two windows has two
    different hashes. Adding the counts would count that device twice — and
    publish a stretch with fewer than k people. The aggregator takes the
    **largest** count among the epochs the window covers: it may under-count,
    never over-count. That is also why the hash is per stretch: two samples of
    the same device on different roads do not link to each other.

!!! warning "End-cut trade-off"
    A position is released once the device is `trim_radius_m` away from it. A
    trip that passes near a spot, goes far and **comes back** to it to finish
    had that stretch released on the way out. That is the price of real-time
    traffic instead of traffic only after the trip; the stretch still needs k
    devices to show up.

## Reading from the app: `make_traffic_router`

`make_traffic_router(store, authorize=..., graph=graph, prefix="/api/traffic")`
mounts two routes (the example mounts only the first, since it passes no
`graph=`):

- `GET /api/traffic?min_latitude=&max_latitude=&min_longitude=&max_longitude=`
  — the snapshot cut to the box. With no snapshot yet, `edges` is empty and
  `generated_at` is `null`. An inverted box answers `422`.
- `GET /api/traffic/graph?...` — with `graph=`, the graph tile.

`authorize` is mandatory, like in every geo router: who may see the traffic is
the service's decision.

With `broker=` and `audience=` on the aggregator, every `flush` sends the
`traffic_updated` event (`{"generated_at", "edges"}`) to whoever `audience()`
returns. The event is only the notice; the client fetches the box on its screen
again. That way nobody gets the whole city every minute.

### Wiring the live position router

Pass `traffic=traffic` to `make_live_position_router` and every authorized
position of an active channel also goes to the aggregator, with the `channel_id`
as the device — even one the `LivePositionStore` refused for throttling, because
map-matching wants them all.

!!! warning "Several workers"
    Trip state lives in process memory. With several workers, each one sees part
    of every device's positions and counts fewer devices per stretch: the result
    publishes **less**, never below k. For the full traffic, run the aggregator
    in a single process (a queue consumer, for instance) and read the snapshot
    from any worker through `RedisTrafficSnapshotStore`.

## Roads the map does not have

`OffRoadDensity(centre)` counts, on a 5 m grid, the **already trimmed**
positions that fell more than 20 m from every road of the graph. Give it to the
aggregator with `density=`. The grid has the same protection:

- a cell is only committed when its **25 m block** had k distinct devices in
  that epoch (one day by default);
- until then it lives as per-block hashes under the day's salt, and at the end
  of the day the hashes and the salt are deleted;
- what remains — and what `to_dict()` lets you persist — is `points` and
  `devices` per cell, nothing per device.

`density.roll(now)` closes the days that are over (`flush` calls it for you).

`infer_road_candidates(density, graph)` turns the density into lines:
morphological closing, Zhang–Suen thinning, the skeleton split into lines
between junctions, Douglas–Peucker, and the **diff against OSM** — only a line
that stays away from every road over at least 80% of its length comes out. Its
ends are joined to the nearest road (up to 40 m), showing where the new street
meets the network.

Every `RoadCandidateSchema` has `status="candidate"`: nothing enters the graph
on its own. `road_candidates_geojson(candidates)` writes a GeoJSON to review in
JOSM or QGIS.

## Measurements

Command: `uv run python scripts/bench_geo_traffic.py [--devices N] [--spacing S]`
(i9-13900F, Python 3.13.3, 2026-11-19). A synthetic city of 9 × 9 crossings
every 150 m; one 450 m street is taken out of the graph, and the fleet drives it
with 5 m Gaussian noise at 1 Hz.

| Measure | 40 trips | 200 trips |
| --- | --- | --- |
| Aggregation, map-matching included (4 runs each) | 48.6–63.9 ms per 1000 positions | 48.1–59.2 ms per 1000 positions |
| Trips open at once | up to 16 | up to 77 |
| Peak memory (`tracemalloc`) | 0.77 MB | 3.00 MB |
| Removed street recovered (sampled every 1 m, 15 m tolerance) | 100% | 100% |
| Largest offset of a candidate vertex | 2.5 m | 2.5 m |
| Candidates elsewhere | 0 | 0 |

With fewer trips a day, inference depends on k: 12 trips recover 77.2% of the
street; 8 or fewer give no candidate (k = 5 per 25 m block).

!!! note "What these numbers do not say"
    The case is synthetic: a straight street, Gaussian noise, no parking lot. In
    a real city, yards and parking lots are also far from the roads and become
    candidates — that is what human review is for. Performance is one desktop
    core; tempest-react-sdk's map-matching, in TypeScript, does the same work at
    8.1 µs per position.

## Recap

- `RoadGraph` / `RoadEdge` — the graph, in the same contract as the app's `createRoadGraph`; `road_graph_from_overpass`, `road_graph_from_geojson`, `load_road_graph` (cached).
- `match_to_roads` — Newson & Krumm's HMM, a port of tempest-react-sdk with a parity test.
- `TrafficAggregator` — `add_position`, `end_trip`, `flush`; publishes only stretches with k devices.
- `TrafficPrivacy` — start and end cut, k, window, rotating salt; each rule tested.
- `make_traffic_router` — `GET` by box and graph tile, mandatory `authorize`; `traffic_updated` over SSE.
- `OffRoadDensity` + `infer_road_candidates` — roads OSM lacks, as candidates for review.
