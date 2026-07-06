"""Tests for the offline travel estimate and the mode duration factor."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.geo import (
    DEFAULT_MODE_DURATION_FACTORS,
    Coordinate,
    TravelMode,
    duration_factor,
    estimate_travel,
    haversine_km,
)

SAO_PAULO = Coordinate(latitude=-23.5505, longitude=-46.6333)
RIO = Coordinate(latitude=-22.9068, longitude=-43.1729)


class TestDurationFactor:
    """Behavior of :func:`duration_factor`."""

    def test_car_is_one(self) -> None:
        """A car is the baseline (factor 1.0)."""
        assert duration_factor(TravelMode.CAR) == 1.0

    def test_bus_is_slower(self) -> None:
        """A bus takes longer than a car."""
        assert duration_factor(TravelMode.BUS) > 1.0

    def test_custom_table_overrides(self) -> None:
        """A caller-supplied table wins over the default."""
        assert duration_factor(TravelMode.CAR, {TravelMode.CAR: 2.0}) == 2.0

    def test_unknown_mode_falls_back_to_one(self) -> None:
        """A mode missing from the table defaults to 1.0."""
        assert duration_factor(TravelMode.BUS, {}) == 1.0


class TestEstimateTravel:
    """Behavior of :func:`estimate_travel`."""

    def test_distance_applies_circuity_factor(self) -> None:
        """Road distance is the straight line scaled by the circuity factor."""
        straight = haversine_km(SAO_PAULO, RIO)
        estimate = estimate_travel(SAO_PAULO, RIO, circuity_factor=1.3)
        assert estimate.distance_km == pytest.approx(straight * 1.3)

    def test_source_is_heuristic(self) -> None:
        """Offline estimates are tagged as heuristic."""
        estimate = estimate_travel(SAO_PAULO, RIO)
        assert estimate.source == "heuristic"

    def test_bus_slower_than_car_same_distance(self) -> None:
        """Bus and car share distance but the bus takes longer."""
        car = estimate_travel(SAO_PAULO, RIO, TravelMode.CAR)
        bus = estimate_travel(SAO_PAULO, RIO, TravelMode.BUS)
        assert bus.distance_km == pytest.approx(car.distance_km)
        assert bus.duration_minutes > car.duration_minutes

    def test_duration_matches_speed_and_factor(self) -> None:
        """Duration equals distance/speed scaled by the mode factor."""
        estimate = estimate_travel(SAO_PAULO, RIO, TravelMode.BUS, car_speed_kmh=60.0)
        expected = (
            estimate.distance_km
            / 60.0
            * 60.0
            * DEFAULT_MODE_DURATION_FACTORS[TravelMode.BUS]
        )
        assert estimate.duration_minutes == pytest.approx(expected)

    @pytest.mark.parametrize("bad", [0.0, -5.0])
    def test_invalid_speed_raises(self, bad: float) -> None:
        """A non-positive speed is rejected."""
        with pytest.raises(ValueError):
            estimate_travel(SAO_PAULO, RIO, car_speed_kmh=bad)

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_invalid_circuity_raises(self, bad: float) -> None:
        """A non-positive circuity factor is rejected."""
        with pytest.raises(ValueError):
            estimate_travel(SAO_PAULO, RIO, circuity_factor=bad)
