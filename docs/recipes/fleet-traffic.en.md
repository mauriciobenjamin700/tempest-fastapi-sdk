# Fleet traffic (anonymous GPS)

Your fleet already sends live positions (see the [Live location](live-location.md)
recipe). Those same positions answer two questions a commercial map charges a
lot for:

1. **What is the traffic like right now?** Median speed per road stretch over
   the last few minutes.
2. **Which road does the fleet use that the map does not have?** Stretches with
   many traces far from every OpenStreetMap road.

And a third one, about the tool itself: **does the road matching work on this
fleet?** Measured on the real traces, published only in aggregate (see
[Matching quality on the real fleet](#matching-quality-on-the-real-fleet)).

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
TypeScript one on the same graph and the same positions (31 scenarios: a chain
break, timed positions, a clock that repeats or goes back), generated by
`node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist`.

You rarely call `match_to_roads` yourself: the aggregator does.

### Sparse positions: pass the time

The 100 m maximum detour was tuned for one position per second. A sparse source
(one position every 5–30 s) turns corners and goes round the block between two
positions, and with the fixed bound the chain breaks where the vehicle only
drove. With each position's time in `timestamps=` (epoch seconds, the
`datetime.timestamp()`), the bound of a transition becomes

`straight line + max_detour_m + detour_per_second_m · min(Δt, max_interval_s)`

with `detour_per_second_m` 2 m/s and `max_interval_s` 60 s by default — the
same as tempest-react-sdk (PR #678), chosen there on the Kubicka drives thinned
to one position every 5–300 s and on public OSM traces of five Brazilian city
centres.

```python
import math
from datetime import UTC, datetime

from tempest_fastapi_sdk.geo import (
    Coordinate,
    RoadEdge,
    RoadGraph,
    RoadMatchOptions,
    match_to_roads,
)

BASE = Coordinate(latitude=-5.09, longitude=-42.8)
M_PER_DEG: float = 6371008.8 * math.pi / 180


def at(x: float, y: float) -> Coordinate:
    """A point ``x`` m east and ``y`` m north of BASE."""
    return Coordinate(
        latitude=BASE.latitude + y / M_PER_DEG,
        longitude=BASE.longitude
        + x / (M_PER_DEG * math.cos(math.radians(BASE.latitude))),
    )


graph = RoadGraph(
    [at(0, 0), at(0, 80), at(150, 80), at(150, 0)],
    [
        RoadEdge(start=0, end=1, id="west"),
        RoadEdge(start=1, end=2, id="top"),
        RoadEdge(start=2, end=3, id="east"),
    ],
)
fixes = [at(0, 10), at(150, 10)]
sent = [
    datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC),
    datetime(2026, 11, 20, 12, 0, 30, tzinfo=UTC),
]
options = RoadMatchOptions(radius_m=50)

untimed = match_to_roads(fixes, graph, options)
timed = match_to_roads(
    fixes, graph, options, timestamps=[t.timestamp() for t in sent]
)

print("untimed breaks:", untimed.breaks)
print("timed breaks:", timed.breaks)
print("route:", [t.edge.id for t in timed.routes[0].edges])
```

Output:

```text
untimed breaks: 1
timed breaks: 0
route: ['west', 'top', 'east']
```

Without time, 150 m in a straight line against 290 m along the network exceed
the 100 m detour and the chain breaks. With 30 s between the two positions the
bound gains 60 m and the route comes out whole: up `west`, across `top`, down
`east`.

!!! note "No time, no change"
    A missing (`None`), `nan`, repeated (Δt 0) or backwards time counts as
    Δt 0: the bound is the fixed one and the result is identical to not
    passing `timestamps`. Only a transition where **both** positions have a
    time gets the slack. `detour_per_second_m=0` turns it off.

`TrafficAggregator` already passes each position's device time (`recorded_at`):
a fleet that reports every 10 s stays chained with no configuration.

`create_road_matcher` (incremental matching, one position at a time) is not
ported: the backend matches stretches already released all at once, and the
live marker is the app's job.

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

## Matching quality on the real fleet

`match_to_roads`' defaults were chosen on OSM mappers' traces and on the
Kubicka dataset — good devices, logs often smoothed. The open question is
**your** fleet: entry-level phones, urban canyons, batched uploads. Answering
it needs measuring on real traces, and real traces are exactly what may not be
kept.

`TrafficAggregator(..., quality=MatchQualityOptions(...))` measures on the
match the aggregator already makes and keeps **only the window's aggregate**.
No trace, no trip, no id.

Copy and run it — no network, no Redis:

```python
import asyncio
import math
import random
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI, Header

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import (
    Coordinate,
    InMemoryTrafficSnapshotStore,
    LivePositionSchema,
    MatchQualityOptions,
    RoadEdge,
    RoadGraph,
    TrafficAggregator,
    TrafficPrivacy,
    make_match_quality_router,
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


def avenue() -> RoadGraph:
    """A 4 km two-way avenue, one edge per 100 m block of way 100."""
    nodes = [point(100.0 * i, 0.0) for i in range(41)]
    edges = [RoadEdge(i, i + 1, True, f"way/100:{i}") for i in range(40)]
    return RoadGraph(nodes, edges)


async def ops_only(x_user_id: str = Header()) -> None:
    """Only the operations team reads the quality."""
    if x_user_id != "ops":
        raise ForbiddenException()


store = InMemoryTrafficSnapshotStore()
window_start = datetime(2026, 11, 18, 7, tzinfo=UTC)
clock = [window_start.timestamp()]
traffic = TrafficAggregator(
    avenue(),
    privacy=TrafficPrivacy(min_devices=5),
    quality=MatchQualityOptions(window_seconds=3600, dimensions=("os",)),
    store=store,
    clock=lambda: clock[0],
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_match_quality_router(store, authorize=ops_only, prefix="/api/traffic/quality")
)


def drive(bus: str, os_name: str, departure: datetime, noise: random.Random) -> None:
    """One trip east at 10 m/s, 1 Hz, GPS 5 m off the avenue on average."""
    for second in range(301):
        when = departure + timedelta(seconds=second)
        clock[0] = when.timestamp()
        here = point(10.0 * second, noise.gauss(0.0, 5.0))
        traffic.add_position(
            bus,
            LivePositionSchema(lat=here.latitude, lng=here.longitude, recorded_at=when),
            dimensions={"os": os_name},
        )
        if second % 20 == 19:
            traffic.process()
    traffic.end_trip(bus)


async def main() -> None:
    """Twelve trips in one hour, then the operations team reads the quality."""
    noise = random.Random(667)
    fleet = ["android"] * 6 + ["ios"] * 5 + ["harmony"]
    for n, os_name in enumerate(fleet):
        drive(f"bus-{n}", os_name, window_start + timedelta(minutes=4 * n), noise)
    clock[0] = (window_start + timedelta(hours=1, seconds=30)).timestamp()
    await traffic.flush()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as api:
        stranger = await api.get("/api/traffic/quality", headers={"X-User-Id": "x"})
        print("stranger:", stranger.status_code)
        report = (
            await api.get("/api/traffic/quality", headers={"X-User-Id": "ops"})
        ).json()

    snapshot = report["snapshot"]
    print("window:", snapshot["window_start"], "->", snapshot["window_end"])
    for label, cell in [
        ("overall", snapshot["overall"]),
        *((str(c["dimensions"]), c) for c in snapshot["cells"]),
        ("other", snapshot["other"]),
    ]:
        if cell is None:
            continue
        print(
            f"{label}: {cell['devices']} devices, {cell['positions']} positions,",
            f"breaks/1000 {cell['breaks_per_1000_positions']},",
            f"snap {cell['snap_distance_median_m']}/{cell['snap_distance_p90_m']} m,",
            f"inferred {cell['inferred_share']}",
        )
    for ref in report["references"]:
        print(
            f"reference {ref['name']}: breaks/1000 {ref['breaks_per_1000_positions']},",
            f"snap median {ref['snap_distance_median_m']} m",
        )


asyncio.run(main())
```

Output measured on 2026-11-20 (Python 3.13.3):

```text
stranger: 403
window: 2026-11-18T07:00:00Z -> 2026-11-18T08:00:00Z
overall: 12 devices, 3132 positions, breaks/1000 0.0, snap 3.3/8.0 m, inferred 0.4639
{'os': 'android'}: 6 devices, 1566 positions, breaks/1000 0.0, snap 3.3/8.25 m, inferred 0.461
other: 6 devices, 1566 positions, breaks/1000 0.0, snap 3.3/8.0 m, inferred 0.4668
reference kubicka: breaks/1000 0.19, snap median 3.7 m
reference brazil-osm: breaks/1000 1.06, snap median 3.7 m
```

The example's noise is 5 m Gaussian across the avenue. The distance to the
matched point is the absolute value of that noise, whose theoretical median is
0.674 × 5 = 3.37 m and p90 1.645 × 5 = 8.22 m. It came out 3.3 / 8.0 m. Let's
go piece by piece.

### The indicators

They are the ones that validated the matcher in
[tempest-react-sdk#667](https://github.com/mauriciobenjamin700/tempest-react-sdk/issues/667),
with the same denominator. That is why a window compares directly with the
references:

| Indicator (field) | What it says | Kubicka | Brazil (OSM) |
| --- | --- | --- | --- |
| Breaks per 1000 positions (`breaks_per_1000_positions`) | Times the HMM chain broke: no possible path between two positions. Rises with wrong-way driving (or a wrong OSM `oneway`), roads missing from the graph and sparse sampling | 0.19 | 1.06 |
| Position → matched point, median / p90 (`snap_distance_median_m` / `snap_distance_p90_m`) | The GPS error as the match sees it. A long tail (high p90) is urban canyon, overpass or a missing road | 3.7 m (to the true route) | 3.7 / 11.6 m |
| Matched share (`matched_share`) | Positions with some road within 200 m | — | 100% |
| `inferred` share (`inferred_share`) | Positions the 2σ (14 m) pre-filter skipped and projected on the previous one's edge. High in slow traffic at 1 Hz, and expected | — | 55.1% |
| Sampling interval, median / p90 (`sampling_interval_median_s` / `sampling_interval_p90_s`) | Time between two kept positions of a trip. At ≥ 5 s the fixed 100 m detour breaks 5.7 to 24.2 times per 1000 positions (#667's sparse measurement) | ~1 s | 1 / 4 s |
| Parallel-road switches per km (`parallel_switches_per_km`) | On straight GPS track (bearing over the 75 m before and after differs < 10°), the matched road changes to another within 20° and more than 8 m off the previous one's line. In Brazil these were dual carriageways and overpass × tunnel | — | 0.109 |

`straight_km` is the last row's denominator, and `positions` the first three's.
Both references ship in `MATCH_QUALITY_REFERENCES`, and the router returns them
next to the window.

!!! note "Where the measure differs from the reference"
    The Brazil reference measured whole runs. The aggregator measures the
    stretches it releases, already without the first and last 200 m, and
    matches each stretch with only the previous one's last position as
    context. So a straight stretch across the border of two stretches counts a
    little less. Kubicka's median is to the true route, not to the matched
    point.

??? info "Precision of the medians"
    Distance and interval go into fixed-bucket histograms
    (`SNAP_DISTANCE_BUCKETS_M`, `SAMPLING_INTERVAL_BUCKETS_S`): 0.1 m up to
    5 m, 0.25 m up to 20 m, and whole seconds up to 30 s. The quantile is by
    nearest rank, the same rule as #667's scripts. It returns the bucket's
    upper bound, capped at the largest value seen. It overestimates by less
    than one bucket and is exact for a value on a bound. Memory does not grow
    with the number of positions.

### Coarse dimensions, k-anonymous too

`MatchQualityOptions(dimensions=("os", "app_version"))` declares up to four
dimensions. The app gives them per trip:
`add_position(..., dimensions={"os": "android"})`. The latest one given applies
to the whole trip, and a missing one becomes `"unknown"`. Use coarse values (OS
family, app version range), never a device model or a full version.

With `make_live_position_router(..., traffic=traffic)`, also pass
`traffic_dimensions=`: a FastAPI dependency that reads the dimension from the
request (a header the app sends, for instance) and returns `{"os": "android"}`
or `None`. A value the aggregator refuses (an undeclared name, a text too long)
is dropped with a warning in the log, and the position still goes in.

The protection does not depend on you getting that right:

- a cell only comes out with **k distinct devices** (the same `min_devices` as
  `TrafficPrivacy`);
- the cells below k are added into `other`. When that sum is still below k,
  the smallest published cell goes into `other` too, until `other` reaches k or
  no cell is left. So `overall` minus the cells never isolates a group smaller
  than k;
- in the example, `ios` had exactly 5 devices. But `harmony`, with 1, would
  only be protected if `ios` went down with it, which is why only `android`
  shows;
- past `max_cells` (64), a new combination counts only in the total.

### What stays and what leaves

| Rule | Test |
| --- | --- |
| Each stretch goes at once into the cell's fixed-size counters and histograms; nothing per trip is kept | `TestNoTripDataKept` |
| A device only appears as an HMAC under a salt drawn for the window | `TestNoTripDataKept` |
| When the window closes only the published aggregate remains: salt, hashes and counters are deleted | `TestNoTripDataKept` |
| The store gets the snapshot with the aggregate `quality` and nothing else; the traffic `GET` by box carries no `quality` | `TestNoTripDataKept` |
| A cell with fewer than k devices never leaves, not even by difference | `TestDimensionKAnonymity` |
| Histogram with fixed memory | `TestHistogram` |
| Known noise gives the expected median; a forced gap counts one break | `TestKnownNoise` |

All in `tests/geo/test_match_quality.py`.

### Reading: `make_match_quality_router`

`make_match_quality_router(store, authorize=..., prefix="/api/traffic/quality")`
mounts `GET <prefix>`, which reads the same store as traffic and answers a
`MatchQualityReportSchema`: `snapshot` (the last closed window) and
`references`. Before the first window closes, `snapshot.window_start` is
`null`. The router is separate from `make_traffic_router` so you can give it a
narrower `authorize`: the whole app reads traffic, while quality is the
operations team's business.

!!! tip "Window"
    The default is one hour (`window_seconds=3600`), aligned to the clock. With
    a small fleet, widen the window so cells reach k. A window that does not
    gather k devices publishes a `null` `overall`, and nothing leaks.

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

### Cost of the quality

Command: `uv run python scripts/bench_geo_traffic.py --devices N [--spacing 3] [--quality] --repeat 6`
(Python 3.13.3, 2026-11-20). The 200 trips use `--spacing 3`, to give the 77
open ones of the table above. The machine was shared (load average 15 to 24 on
12 cores), so the total time varied more between runs than between "with" and
"without". The stable number is the time **inside**
`MatchQualityTracker.record`, because the indicators reuse the `match_to_roads`
the aggregator already runs:

| Measure | 40 trips | 200 trips |
| --- | --- | --- |
| Aggregation without `--quality` (fastest / median) | 66.6 / 77.7 ms per 1000 positions | 49.4–56.1 / 53.3–63.8 ms |
| Aggregation with `--quality` (fastest / median) | 75.0 / 84.0 ms | 51.7–59.3 / 63.2–63.8 ms |
| The indicators alone (`record`), fastest / median | 2.07 / 4.50 ms per 1000 positions | 1.72–1.93 / 2.01–2.11 ms |
| Peak memory (`tracemalloc`), without / with | 0.75 / 0.77 MB | 2.98 / 3.01 MB |
| Open window state (2 cells, histograms + hashes) | 10.6 kB | 30.8 kB |

The state grows with the window's devices (one hash per device and cell,
measured with `sys.getsizeof`), not with positions: the histograms have a fixed size.

## Recap

- `RoadGraph` / `RoadEdge` — the graph, in the same contract as the app's `createRoadGraph`; `road_graph_from_overpass`, `road_graph_from_geojson`, `load_road_graph` (cached).
- `match_to_roads` — Newson & Krumm's HMM, a port of tempest-react-sdk with a parity test.
- `TrafficAggregator` — `add_position`, `end_trip`, `flush`; publishes only stretches with k devices.
- `TrafficPrivacy` — start and end cut, k, window, rotating salt; each rule tested.
- `make_traffic_router` — `GET` by box and graph tile, mandatory `authorize`; `traffic_updated` over SSE.
- `MatchQualityOptions` + `make_match_quality_router` — matching quality on the real fleet, per window, aggregate only and k-anonymous in the dimensions too; next to the Kubicka and Brazil references.
- `OffRoadDensity` + `infer_road_candidates` — roads OSM lacks, as candidates for review.
