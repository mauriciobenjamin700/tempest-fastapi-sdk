"""Tests for the Brazil geolocation helpers."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.geo import (
    Coordinate,
    GeocodeResult,
    cep_to_coordinate,
    uf_centroid,
)
from tempest_fastapi_sdk.utils import UF


class _FakeGeocoder:
    """Geocoder stub returning a fixed result (or None)."""

    def __init__(self, result: GeocodeResult | None) -> None:
        self._result = result
        self.queries: list[str] = []

    async def geocode(self, query: str) -> GeocodeResult | None:
        self.queries.append(query)
        return self._result

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        return self._result


class TestUFCentroid:
    def test_by_sigla_string(self) -> None:
        coord = uf_centroid("sp")
        assert coord.latitude == pytest.approx(-22.2)

    def test_by_enum(self) -> None:
        assert uf_centroid(UF.RJ).longitude == pytest.approx(-42.7)

    def test_all_states_present(self) -> None:
        for uf in UF:
            assert uf_centroid(uf) is not None

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid UF"):
            uf_centroid("ZZ")


class TestCepToCoordinate:
    async def test_returns_coordinate(self) -> None:
        result = GeocodeResult(
            coordinate=Coordinate(latitude=-23.55, longitude=-46.63),
            display_name="01310-100, São Paulo",
        )
        geocoder = _FakeGeocoder(result)
        coord = await cep_to_coordinate("01310-100", geocoder=geocoder)
        assert coord is not None
        assert coord.latitude == -23.55
        assert "01310-100" in geocoder.queries[0]

    async def test_none_when_not_found(self) -> None:
        coord = await cep_to_coordinate("00000-000", geocoder=_FakeGeocoder(None))
        assert coord is None
