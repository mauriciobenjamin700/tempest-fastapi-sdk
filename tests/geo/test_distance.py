"""Tests for the great-circle distance helper."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.geo import Coordinate, haversine_km

# Reference coordinates.
SAO_PAULO = Coordinate(latitude=-23.5505, longitude=-46.6333)
RIO = Coordinate(latitude=-22.9068, longitude=-43.1729)


class TestHaversineKm:
    """Behavior of :func:`haversine_km`."""

    def test_same_point_is_zero(self) -> None:
        """Distance from a point to itself is zero."""
        assert haversine_km(SAO_PAULO, SAO_PAULO) == pytest.approx(0.0, abs=1e-9)

    def test_symmetric(self) -> None:
        """Distance does not depend on direction."""
        there = haversine_km(SAO_PAULO, RIO)
        back = haversine_km(RIO, SAO_PAULO)
        assert there == pytest.approx(back)

    def test_known_distance(self) -> None:
        """SP-Rio great-circle distance is ~360 km."""
        distance = haversine_km(SAO_PAULO, RIO)
        assert distance == pytest.approx(360.0, abs=15.0)

    def test_non_negative(self) -> None:
        """Distance is always non-negative."""
        assert haversine_km(SAO_PAULO, RIO) > 0
