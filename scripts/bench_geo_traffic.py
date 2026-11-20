"""Measure the fleet traffic pipeline: aggregation cost, memory and inference.

Synthetic city: a 9 x 9 grid of 150 m blocks (1.2 km square, two-way) in
Teresina. One north-south street (x = 600 m, from y = 300 to 750 m — three
blocks, 450 m) is removed from the graph the aggregator sees; a fleet drives
through it anyway. Positions at 1 Hz, 10 m/s, Gaussian GPS noise.

Prints:

* aggregation time per 1000 positions (``add_position`` + ``process``,
  map-matching included), over ``--devices`` devices;
* memory: peak while aggregating and what stays held afterwards
  (``tracemalloc``);
* inference quality: how much of the removed street the candidates recover,
  their Hausdorff distance to it, and candidates that are not it.

Usage::

    uv run python scripts/bench_geo_traffic.py [--devices 40] [--seed 667]
"""

from __future__ import annotations

import argparse
import itertools
import math
import random
import time
import tracemalloc
from datetime import UTC, datetime

from tempest_fastapi_sdk.geo import (
    Coordinate,
    LivePositionSchema,
    OffRoadDensity,
    RoadCandidateSchema,
    RoadEdge,
    RoadGraph,
    TrafficAggregator,
    TrafficPrivacy,
    infer_road_candidates,
)

BASE = Coordinate(latitude=-5.09, longitude=-42.80)
M_PER_DEG = 6371008.8 * math.pi / 180
BLOCK = 150.0
SIZE = 8
MISSING_X = 600.0
MISSING_Y = (300.0, 750.0)
DAY0 = datetime(2026, 11, 1, tzinfo=UTC).timestamp()


def at(x: float, y: float) -> Coordinate:
    """A coordinate ``x`` m east and ``y`` m north of the base.

    Args:
        x (float): Metres east.
        y (float): Metres north.

    Returns:
        Coordinate: The point.
    """
    return Coordinate(
        latitude=BASE.latitude + y / M_PER_DEG,
        longitude=BASE.longitude
        + x / (M_PER_DEG * math.cos(math.radians(BASE.latitude))),
    )


def local(point: Coordinate) -> tuple[float, float]:
    """Inverse of :func:`at`: metres east and north of the base.

    Args:
        point (Coordinate): The point.

    Returns:
        tuple[float, float]: ``(x, y)`` in metres.
    """
    return (
        (point.longitude - BASE.longitude)
        * M_PER_DEG
        * math.cos(math.radians(BASE.latitude)),
        (point.latitude - BASE.latitude) * M_PER_DEG,
    )


def city(*, without_missing: bool) -> RoadGraph:
    """The grid, optionally without the street the fleet will reveal.

    Args:
        without_missing (bool): Leave the x = 600 m stretch out.

    Returns:
        RoadGraph: The graph.
    """
    nodes = [at(i * BLOCK, j * BLOCK) for i in range(SIZE + 1) for j in range(SIZE + 1)]
    edges: list[RoadEdge] = []

    def nid(i: int, j: int) -> int:
        """Node index of grid point (i, j).

        Args:
            i (int): Column.
            j (int): Row.

        Returns:
            int: Index.
        """
        return i * (SIZE + 1) + j

    for i in range(SIZE + 1):
        for j in range(SIZE):
            missing = (
                i * BLOCK == MISSING_X and MISSING_Y[0] <= j * BLOCK < MISSING_Y[1]
            )
            if not (without_missing and missing):
                edges.append(RoadEdge(nid(i, j), nid(i, j + 1), True, f"ns-{i}-{j}"))
    for j in range(SIZE + 1):
        for i in range(SIZE):
            edges.append(RoadEdge(nid(i, j), nid(i + 1, j), True, f"ew-{i}-{j}"))
    return RoadGraph(nodes, edges)


def drive(
    waypoints: list[tuple[float, float]],
    rand: random.Random,
    *,
    speed: float = 10.0,
    sigma: float = 5.0,
) -> list[tuple[float, float]]:
    """Positions along waypoints at 1 Hz, with Gaussian noise.

    Args:
        waypoints (list[tuple[float, float]]): Plane points, metres.
        rand (random.Random): Seeded generator.
        speed (float): Metres per second.
        sigma (float): Noise, metres.

    Returns:
        list[tuple[float, float]]: Noisy plane points.
    """
    out: list[tuple[float, float]] = []
    for (ax, ay), (bx, by) in itertools.pairwise(waypoints):
        steps = max(1, round(math.hypot(bx - ax, by - ay) / speed))
        for s in range(steps):
            t = s / steps
            out.append(
                (
                    ax + (bx - ax) * t + rand.gauss(0, sigma),
                    ay + (by - ay) * t + rand.gauss(0, sigma),
                )
            )
    return out


def trip_through_missing(rand: random.Random) -> list[tuple[float, float]]:
    """A trip that crosses the city north-south on the missing street.

    Args:
        rand (random.Random): Seeded generator.

    Returns:
        list[tuple[float, float]]: Noisy plane points.
    """
    xs = rand.choice([0.0, 150.0, 1050.0, 1200.0])
    xe = rand.choice([0.0, 150.0, 1050.0, 1200.0])
    return drive([(xs, 0.0), (MISSING_X, 0.0), (MISSING_X, 1200.0), (xe, 1200.0)], rand)


def fleet(
    devices: int, rand: random.Random, *, spacing_s: float
) -> list[tuple[str, LivePositionSchema, bool]]:
    """Every position of the fleet, interleaved in time order.

    Device ``n`` starts ``n * spacing_s`` seconds after the first, so many
    trips are open at once, the way a real fleet arrives.

    Args:
        devices (int): Fleet size.
        rand (random.Random): Seeded generator.
        spacing_s (float): Seconds between two trips' starts.

    Returns:
        list[tuple[str, LivePositionSchema, bool]]: ``(device, position,
        last_of_trip)`` sorted by time.
    """
    out: list[tuple[float, str, LivePositionSchema, bool]] = []
    for n in range(devices):
        points = trip_through_missing(rand)
        for i, (x, y) in enumerate(points):
            t = DAY0 + n * spacing_s + i
            position = LivePositionSchema(
                lat=at(x, y).latitude,
                lng=at(x, y).longitude,
                accuracy_m=5.0,
                recorded_at=datetime.fromtimestamp(t, UTC),
            )
            out.append((t, f"bus-{n}", position, i == len(points) - 1))
    out.sort(key=lambda item: item[0])
    return [(device, position, last) for _, device, position, last in out]


def run(
    graph: RoadGraph,
    positions: list[tuple[str, LivePositionSchema, bool]],
) -> tuple[TrafficAggregator, OffRoadDensity, int]:
    """Feed the fleet, processing every 30 s of clock, ending each trip.

    Args:
        graph (RoadGraph): The base graph.
        positions (list[tuple[str, LivePositionSchema, bool]]): From
            :func:`fleet`.

    Returns:
        tuple[TrafficAggregator, OffRoadDensity, int]: The aggregator, its
        density and the most trips open at once.
    """
    clock = [DAY0]
    density = OffRoadDensity(BASE)
    aggregator = TrafficAggregator(
        graph,
        privacy=TrafficPrivacy(min_devices=5, window_seconds=3600),
        density=density,
        clock=lambda: clock[0],
    )
    next_process = DAY0 + 30
    most_open = 0
    for device, position, last in positions:
        clock[0] = position.recorded_at.timestamp()
        if clock[0] >= next_process:
            aggregator.process()
            next_process = clock[0] + 30
        aggregator.add_position(device, position)
        most_open = max(most_open, aggregator.active_trips)
        if last:
            aggregator.end_trip(device)
    return aggregator, density, most_open


def coverage(candidates: list[RoadCandidateSchema]) -> tuple[float, float, int]:
    """How well the candidates recover the removed street.

    The street is sampled every metre; a sample is covered when a candidate
    segment passes within 15 m of it.

    Args:
        candidates (list[RoadCandidateSchema]): Inference output.

    Returns:
        tuple[float, float, int]: Covered share of the street, the largest
        distance from a vertex of a matching candidate to the street, and
        how many candidates are elsewhere.
    """
    lines: list[list[tuple[float, float]]] = []
    worst = 0.0
    elsewhere = 0
    for c in candidates:
        xy = [local(p) for p in c.coordinates]
        if all(
            abs(x - MISSING_X) <= 15 and MISSING_Y[0] - 15 <= y <= MISSING_Y[1] + 15
            for x, y in xy
        ):
            lines.append(xy)
            worst = max(worst, *(abs(x - MISSING_X) for x, _ in xy))
        else:
            elsewhere += 1

    def near(px: float, py: float) -> bool:
        """Whether a point lies within 15 m of a matching candidate.

        Args:
            px (float): Metres east.
            py (float): Metres north.

        Returns:
            bool: Covered.
        """
        for line in lines:
            for (ax, ay), (bx, by) in itertools.pairwise(line):
                dx, dy = bx - ax, by - ay
                len2 = dx * dx + dy * dy
                t = 0.0 if len2 == 0 else ((px - ax) * dx + (py - ay) * dy) / len2
                t = max(0.0, min(1.0, t))
                if math.hypot(px - ax - t * dx, py - ay - t * dy) <= 15:
                    return True
        return False

    samples = [MISSING_Y[0] + k for k in range(int(MISSING_Y[1] - MISSING_Y[0]) + 1)]
    covered = sum(1 for y in samples if near(MISSING_X, y)) / len(samples)
    return covered, worst, elsewhere


def main() -> None:
    """Run the three measurements and print them."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=40)
    parser.add_argument("--seed", type=int, default=667)
    parser.add_argument(
        "--spacing", type=float, default=15.0, help="seconds between trip starts"
    )
    args = parser.parse_args()
    graph = city(without_missing=True)
    positions = fleet(args.devices, random.Random(args.seed), spacing_s=args.spacing)
    print(
        f"graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges; "
        f"fleet: {args.devices} trips, {len(positions)} positions"
    )

    started = time.perf_counter()
    aggregator, density, most_open = run(graph, positions)
    elapsed = time.perf_counter() - started
    print(
        f"aggregation: {elapsed:.2f} s -> "
        f"{elapsed / len(positions) * 1000 * 1000:.1f} ms per 1000 positions "
        f"(map-matching included; up to {most_open} trips open at once)"
    )
    snapshot = aggregator.snapshot()
    print(f"traffic: {len(snapshot.edges)} directed edges published with k=5")

    tracemalloc.start()
    aggregator, density, _ = run(graph, positions)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"memory: peak {peak / 1e6:.2f} MB traced while aggregating")

    density.roll(DAY0 + 86_400 * 2)
    started = time.perf_counter()
    candidates = infer_road_candidates(density, graph)
    infer_s = time.perf_counter() - started
    covered, worst, elsewhere = coverage(candidates)
    print(
        f"inference: {len(density.cells)} committed cells, "
        f"{infer_s * 1000:.0f} ms, {len(candidates)} candidates; "
        f"removed street covered {covered:.1%}, worst vertex offset "
        f"{worst:.1f} m, {elsewhere} candidates elsewhere"
    )


if __name__ == "__main__":
    main()
