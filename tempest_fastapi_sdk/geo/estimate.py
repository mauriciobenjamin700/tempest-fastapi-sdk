"""Offline travel estimate — Haversine + circuity factor + average speed.

Zero dependencies, zero network. Good for a fast, rough estimate when you
cannot (or do not want to) call a routing server. For real road geometry
use :class:`tempest_fastapi_sdk.geo.OSRMBackend` instead.
"""

from __future__ import annotations

from tempest_fastapi_sdk.geo.distance import haversine_km
from tempest_fastapi_sdk.geo.enums import TravelMode
from tempest_fastapi_sdk.geo.schemas import Coordinate, TravelEstimate

# How much longer a real road is than the straight line between two points.
# Empirical circuity factors sit around 1.2-1.4; 1.3 is a sane default.
DEFAULT_CIRCUITY_FACTOR: float = 1.3

# Blended average car speed (km/h) mixing urban and highway travel.
DEFAULT_CAR_SPEED_KMH: float = 50.0

# Duration multipliers relative to a car. This is the single source of truth
# for "how much slower/faster" each mode is — it scales both the offline
# heuristic (via speed) and a car-only OSRM result (via duration). A bus is
# slower because of stops; a motorcycle is slightly faster (lane filtering).
DEFAULT_MODE_DURATION_FACTORS: dict[TravelMode, float] = {
    TravelMode.CAR: 1.0,
    TravelMode.MOTORCYCLE: 0.95,
    TravelMode.BUS: 1.6,
}


def duration_factor(
    mode: TravelMode,
    factors: dict[TravelMode, float] | None = None,
) -> float:
    """Return the duration multiplier (relative to a car) for a mode.

    Args:
        mode: The travel mode to look up.
        factors: Optional override map; falls back to
            :data:`DEFAULT_MODE_DURATION_FACTORS` and then to ``1.0``.

    Returns:
        The multiplier applied to a car's travel time for this mode.
    """
    table = factors if factors is not None else DEFAULT_MODE_DURATION_FACTORS
    return table.get(mode, 1.0)


def estimate_travel(
    origin: Coordinate,
    destination: Coordinate,
    mode: TravelMode = TravelMode.CAR,
    *,
    circuity_factor: float = DEFAULT_CIRCUITY_FACTOR,
    car_speed_kmh: float = DEFAULT_CAR_SPEED_KMH,
    mode_duration_factors: dict[TravelMode, float] | None = None,
) -> TravelEstimate:
    """Estimate road distance and travel time offline for a single mode.

    Road distance is the Haversine distance scaled by ``circuity_factor``.
    Travel time is that distance at the car's average speed, then scaled by
    the mode's duration factor (see :data:`DEFAULT_MODE_DURATION_FACTORS`).

    Args:
        origin: The starting coordinate.
        destination: The ending coordinate.
        mode: The travel mode to estimate for. Defaults to
            :attr:`TravelMode.CAR`.
        circuity_factor: Ratio of road distance to straight-line distance.
        car_speed_kmh: Blended average car speed in km/h; must be positive.
        mode_duration_factors: Optional per-mode duration multiplier map.

    Returns:
        A :class:`TravelEstimate` with ``source="heuristic"``.

    Raises:
        ValueError: If ``car_speed_kmh`` or ``circuity_factor`` is not
            positive.
    """
    if car_speed_kmh <= 0:
        raise ValueError("car_speed_kmh must be positive")
    if circuity_factor <= 0:
        raise ValueError("circuity_factor must be positive")

    distance_km = haversine_km(origin, destination) * circuity_factor
    car_minutes = distance_km / car_speed_kmh * 60.0
    duration_minutes = car_minutes * duration_factor(mode, mode_duration_factors)

    return TravelEstimate(
        mode=mode,
        distance_km=distance_km,
        duration_minutes=duration_minutes,
        source="heuristic",
    )
