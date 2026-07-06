"""Tests for the OSRM routing backend using a mocked httpx transport."""

from __future__ import annotations

import httpx
import pytest

from tempest_fastapi_sdk.geo import (
    Coordinate,
    OSRMBackend,
    RoutingBackend,
    TravelMode,
)

SAO_PAULO = Coordinate(latitude=-23.5505, longitude=-46.6333)
RIO = Coordinate(latitude=-22.9068, longitude=-43.1729)

# A minimal successful OSRM /route response: 400 km in 300 minutes.
_OK_ROUTE: dict[str, object] = {
    "code": "Ok",
    "routes": [{"distance": 400_000.0, "duration": 18_000.0}],
}


def _client(handler: object) -> httpx.AsyncClient:
    """Build an AsyncClient whose requests are served by ``handler``."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


class TestOSRMBackend:
    """Behavior of :class:`OSRMBackend`."""

    def test_is_routing_backend(self) -> None:
        """The backend satisfies the RoutingBackend protocol."""
        backend = OSRMBackend(http_client=_client(lambda r: httpx.Response(200)))
        assert isinstance(backend, RoutingBackend)

    async def test_parses_distance_and_duration(self) -> None:
        """A successful car route maps meters/seconds to km/minutes."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_OK_ROUTE)

        async with _client(handler) as client:
            backend = OSRMBackend(http_client=client)
            estimate = await backend.route(SAO_PAULO, RIO, mode=TravelMode.CAR)

        assert estimate.distance_km == pytest.approx(400.0)
        assert estimate.duration_minutes == pytest.approx(300.0)
        assert estimate.source == "osrm"

    async def test_sends_lon_lat_order(self) -> None:
        """OSRM expects lon,lat order in the path."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            return httpx.Response(200, json=_OK_ROUTE)

        async with _client(handler) as client:
            await OSRMBackend(http_client=client).route(SAO_PAULO, RIO)

        assert "-46.6333,-23.5505;-43.1729,-22.9068" in seen["path"]

    async def test_bus_scales_car_duration(self) -> None:
        """Bus keeps the car distance but scales the duration up."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_OK_ROUTE)

        async with _client(handler) as client:
            backend = OSRMBackend(http_client=client)
            car = await backend.route(SAO_PAULO, RIO, mode=TravelMode.CAR)
            bus = await backend.route(SAO_PAULO, RIO, mode=TravelMode.BUS)

        assert bus.distance_km == pytest.approx(car.distance_km)
        assert bus.duration_minutes > car.duration_minutes

    async def test_no_route_raises(self) -> None:
        """A NoRoute response is surfaced as a RuntimeError."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "NoRoute", "routes": []})

        async with _client(handler) as client:
            backend = OSRMBackend(http_client=client)
            with pytest.raises(RuntimeError, match="no route"):
                await backend.route(SAO_PAULO, RIO)

    async def test_http_error_raises(self) -> None:
        """A transport/HTTP error is normalized to a RuntimeError."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        async with _client(handler) as client:
            backend = OSRMBackend(http_client=client)
            with pytest.raises(RuntimeError, match="OSRM request failed"):
                await backend.route(SAO_PAULO, RIO)
