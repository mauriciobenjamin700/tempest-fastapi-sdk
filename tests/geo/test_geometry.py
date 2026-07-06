"""Tests for the offline spatial geometry helpers."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.geo import (
    Coordinate,
    bounding_box,
    destination_point,
    haversine_km,
    initial_bearing,
    nearest,
    path_length_km,
    point_in_polygon,
    polygon_area_km2,
    within_radius,
)

SAO_PAULO = Coordinate(latitude=-23.5505, longitude=-46.6333)
RIO = Coordinate(latitude=-22.9068, longitude=-43.1729)
CAMPINAS = Coordinate(latitude=-22.9099, longitude=-47.0626)


class TestBoundingBox:
    def test_contains_center(self) -> None:
        box = bounding_box(SAO_PAULO, 10.0)
        assert box.contains(SAO_PAULO)

    def test_zero_radius_collapses(self) -> None:
        box = bounding_box(SAO_PAULO, 0.0)
        assert box.min_latitude == pytest.approx(box.max_latitude)

    def test_negative_radius_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            bounding_box(SAO_PAULO, -1.0)

    def test_excludes_far_point(self) -> None:
        box = bounding_box(SAO_PAULO, 10.0)
        assert not box.contains(RIO)


class TestWithinRadius:
    def test_filters_by_distance(self) -> None:
        points = [SAO_PAULO, CAMPINAS, RIO]
        near = within_radius(SAO_PAULO, points, 120.0)
        assert SAO_PAULO in near
        assert CAMPINAS in near
        assert RIO not in near

    def test_empty_when_none_match(self) -> None:
        assert within_radius(SAO_PAULO, [RIO], 1.0) == []

    def test_key_extractor(self) -> None:
        items = [("a", SAO_PAULO), ("b", RIO)]
        near = within_radius(SAO_PAULO, items, 50.0, key=lambda t: t[1])
        assert [name for name, _ in near] == ["a"]


class TestNearest:
    def test_ranks_closest_first(self) -> None:
        result = nearest(SAO_PAULO, [RIO, CAMPINAS, SAO_PAULO], k=2)
        assert result[0] == SAO_PAULO
        assert result[1] == CAMPINAS

    def test_k_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            nearest(SAO_PAULO, [RIO], k=0)


class TestBearingAndProjection:
    def test_destination_matches_distance(self) -> None:
        target = destination_point(SAO_PAULO, 90.0, 100.0)
        assert haversine_km(SAO_PAULO, target) == pytest.approx(100.0, rel=1e-3)

    def test_bearing_roundtrip(self) -> None:
        target = destination_point(SAO_PAULO, 45.0, 50.0)
        assert initial_bearing(SAO_PAULO, target) == pytest.approx(45.0, abs=0.5)


class TestPolygon:
    def _square(self) -> list[Coordinate]:
        return [
            Coordinate(latitude=0.0, longitude=0.0),
            Coordinate(latitude=0.0, longitude=1.0),
            Coordinate(latitude=1.0, longitude=1.0),
            Coordinate(latitude=1.0, longitude=0.0),
        ]

    def test_point_inside(self) -> None:
        assert point_in_polygon(
            Coordinate(latitude=0.5, longitude=0.5),
            self._square(),
        )

    def test_point_outside(self) -> None:
        assert not point_in_polygon(
            Coordinate(latitude=2.0, longitude=2.0),
            self._square(),
        )

    def test_too_few_vertices(self) -> None:
        with pytest.raises(ValueError, match="3 vertices"):
            point_in_polygon(SAO_PAULO, [SAO_PAULO, RIO])

    def test_area_of_one_degree_square(self) -> None:
        # A 1deg x 1deg square at the equator is ~12,300 km^2.
        area = polygon_area_km2(self._square())
        assert area == pytest.approx(12_300.0, rel=0.05)


class TestPathLength:
    def test_two_points_equals_haversine(self) -> None:
        assert path_length_km([SAO_PAULO, RIO]) == pytest.approx(
            haversine_km(SAO_PAULO, RIO),
        )

    def test_single_point_is_zero(self) -> None:
        assert path_length_km([SAO_PAULO]) == 0.0
