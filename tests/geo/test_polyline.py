"""Tests for the encoded-polyline codec."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.geo import Coordinate, decode_polyline, encode_polyline

# The canonical Google polyline example.
_ENCODED = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
_POINTS = [
    (38.5, -120.2),
    (40.7, -120.95),
    (43.252, -126.453),
]


class TestDecode:
    def test_known_value(self) -> None:
        decoded = decode_polyline(_ENCODED)
        assert len(decoded) == 3
        for coord, (lat, lon) in zip(decoded, _POINTS, strict=True):
            assert coord.latitude == pytest.approx(lat, abs=1e-5)
            assert coord.longitude == pytest.approx(lon, abs=1e-5)

    def test_empty_string(self) -> None:
        assert decode_polyline("") == []


class TestEncode:
    def test_known_value(self) -> None:
        points = [Coordinate(latitude=lat, longitude=lon) for lat, lon in _POINTS]
        assert encode_polyline(points) == _ENCODED

    def test_empty(self) -> None:
        assert encode_polyline([]) == ""


class TestRoundtrip:
    def test_precision_5(self) -> None:
        points = [
            Coordinate(latitude=-23.5505, longitude=-46.6333),
            Coordinate(latitude=-22.9068, longitude=-43.1729),
        ]
        decoded = decode_polyline(encode_polyline(points))
        for original, result in zip(points, decoded, strict=True):
            assert result.latitude == pytest.approx(original.latitude, abs=1e-5)
            assert result.longitude == pytest.approx(original.longitude, abs=1e-5)

    def test_precision_6(self) -> None:
        points = [Coordinate(latitude=-23.550520, longitude=-46.633308)]
        decoded = decode_polyline(
            encode_polyline(points, precision=6),
            precision=6,
        )
        assert decoded[0].latitude == pytest.approx(-23.550520, abs=1e-6)
