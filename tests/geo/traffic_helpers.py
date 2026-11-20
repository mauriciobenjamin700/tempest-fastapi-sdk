"""Synthetic streets and drives shared by the traffic and inference tests."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime

from tempest_fastapi_sdk.geo import (
    Coordinate,
    LivePositionSchema,
    RoadEdge,
    RoadGraph,
    TrafficAggregator,
)

BASE: Coordinate = Coordinate(latitude=-5.09, longitude=-42.8)
M_PER_DEG: float = 6371008.8 * math.pi / 180
T0: float = datetime(2026, 11, 1, 12, tzinfo=UTC).timestamp()
"""A fixed past instant: positions must not be in the future."""


def at(x: float, y: float) -> Coordinate:
    """A coordinate ``x`` m east and ``y`` m north of :data:`BASE`.

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


def straight_street(length_m: float = 3000.0, block_m: float = 100.0) -> RoadGraph:
    """A two-way street along y = 0, one edge ``s-<n>`` per block.

    Args:
        length_m (float): Street length.
        block_m (float): Edge length.

    Returns:
        RoadGraph: The graph.
    """
    count = round(length_m / block_m)
    nodes = [at(i * block_m, 0.0) for i in range(count + 1)]
    edges = [
        RoadEdge(start=i, end=i + 1, two_way=True, id=f"s-{i}") for i in range(count)
    ]
    return RoadGraph(nodes, edges)


class Clock:
    """A settable server clock.

    Attributes:
        now (float): Epoch seconds.
    """

    def __init__(self, now: float = T0) -> None:
        """Start at ``now``.

        Args:
            now (float): Epoch seconds.
        """
        self.now: float = now

    def __call__(self) -> float:
        """Return :attr:`now`.

        Returns:
            float: The fake time.
        """
        return self.now


def position(
    x: float, y: float, t: float, accuracy_m: float = 5.0
) -> LivePositionSchema:
    """A position ``(x, y)`` metres from the base at epoch ``t``.

    Args:
        x (float): Metres east.
        y (float): Metres north.
        t (float): Epoch seconds.
        accuracy_m (float): Reported accuracy.

    Returns:
        LivePositionSchema: The position.
    """
    point = at(x, y)
    return LivePositionSchema(
        lat=point.latitude,
        lng=point.longitude,
        accuracy_m=accuracy_m,
        recorded_at=datetime.fromtimestamp(t, UTC),
    )


def drive(
    aggregator: TrafficAggregator,
    clock: Clock,
    device: str,
    path: Callable[[float], tuple[float, float]],
    seconds: int,
    *,
    start: float,
    end_trip: bool = True,
) -> None:
    """Feed one trip at 1 Hz, processing every 20 s, optionally ending it.

    Args:
        aggregator (TrafficAggregator): The aggregator.
        clock (Clock): Server clock, kept equal to the position time.
        device (str): Device id.
        path (Callable[[float], tuple[float, float]]): Seconds since the
            start to ``(x, y)`` metres.
        seconds (int): Duration.
        start (float): Epoch seconds of the first position.
        end_trip (bool): Whether to call ``end_trip`` at the end.
    """
    for s in range(seconds + 1):
        clock.now = start + s
        x, y = path(float(s))
        aggregator.add_position(device, position(x, y, clock.now))
        if s % 20 == 19:
            aggregator.process()
    aggregator.process()
    if end_trip:
        aggregator.end_trip(device)


def east(
    x0: float, speed: float = 10.0, y: float = 2.0
) -> Callable[[float], tuple[float, float]]:
    """A path heading east along the street from ``x0``.

    Args:
        x0 (float): Start, metres east.
        speed (float): Metres per second.
        y (float): Offset from the street centre, metres.

    Returns:
        Callable[[float], tuple[float, float]]: The path.
    """
    return lambda s: (x0 + speed * s, y)
