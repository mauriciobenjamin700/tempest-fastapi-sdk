"""Tests for OSRM route geometry and distance matrix (mocked transport)."""

from __future__ import annotations

import httpx
import pytest

from tempest_fastapi_sdk.geo import (
    Coordinate,
    OSRMBackend,
    TravelMode,
    encode_polyline,
)

SAO_PAULO = Coordinate(latitude=-23.5505, longitude=-46.6333)
RIO = Coordinate(latitude=-22.9068, longitude=-43.1729)

# A real 2-point geometry so decoding yields exactly two coordinates.
_ROUTE_WITH_GEOMETRY = {
    "code": "Ok",
    "routes": [
        {
            "distance": 400_000.0,
            "duration": 18_000.0,
            "geometry": encode_polyline([SAO_PAULO, RIO]),
        },
    ],
}
_TABLE_OK = {
    "code": "Ok",
    "distances": [[0.0, 400_000.0], [400_000.0, 0.0]],
    "durations": [[0.0, 18_000.0], [18_000.0, 0.0]],
}


def _client(handler: object) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


class TestRouteGeometry:
    async def test_decodes_geometry_when_requested(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["overview"] = request.url.params.get("overview", "")
            return httpx.Response(200, json=_ROUTE_WITH_GEOMETRY)

        async with _client(handler) as client:
            backend = OSRMBackend(http_client=client)
            estimate = await backend.route(SAO_PAULO, RIO, with_geometry=True)

        assert seen["overview"] == "full"
        assert len(estimate.geometry) == 2

    async def test_no_geometry_by_default(self) -> None:
        async with _client(
            lambda r: httpx.Response(200, json=_ROUTE_WITH_GEOMETRY),
        ) as client:
            backend = OSRMBackend(http_client=client)
            estimate = await backend.route(SAO_PAULO, RIO)
        assert estimate.geometry == []


class TestMatrix:
    async def test_parses_grids(self) -> None:
        async with _client(lambda r: httpx.Response(200, json=_TABLE_OK)) as client:
            backend = OSRMBackend(http_client=client)
            matrix = await backend.matrix([SAO_PAULO, RIO])

        assert matrix.distances_km[0][1] == pytest.approx(400.0)
        assert matrix.durations_minutes[0][1] == pytest.approx(300.0)
        assert len(matrix.sources) == 2
        assert len(matrix.destinations) == 2

    async def test_empty_sources_raises(self) -> None:
        async with _client(lambda r: httpx.Response(200, json=_TABLE_OK)) as client:
            backend = OSRMBackend(http_client=client)
            with pytest.raises(ValueError, match="must not be empty"):
                await backend.matrix([])

    async def test_uses_table_endpoint(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            return httpx.Response(200, json=_TABLE_OK)

        async with _client(handler) as client:
            backend = OSRMBackend(http_client=client)
            await backend.matrix([SAO_PAULO], [RIO], mode=TravelMode.CAR)
        assert "/table/v1/" in seen["path"]
