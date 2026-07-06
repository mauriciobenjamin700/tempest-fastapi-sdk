"""Tests for the Nominatim geocoding backend (mocked httpx transport)."""

from __future__ import annotations

import httpx

from tempest_fastapi_sdk.geo import (
    Coordinate,
    GeocodingBackend,
    NominatimBackend,
)

_SEARCH_OK = [
    {
        "lat": "-23.5506",
        "lon": "-46.6334",
        "display_name": "São Paulo, Brasil",
        "type": "city",
    },
]
_REVERSE_OK = {
    "lat": "-23.5506",
    "lon": "-46.6334",
    "display_name": "Praça da Sé, São Paulo",
    "type": "square",
}


def _client(handler: object) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


class TestNominatimBackend:
    def test_is_geocoding_backend(self) -> None:
        backend = NominatimBackend(
            http_client=_client(lambda r: httpx.Response(200, json=[])),
        )
        assert isinstance(backend, GeocodingBackend)

    async def test_geocode_parses_first_result(self) -> None:
        async with _client(lambda r: httpx.Response(200, json=_SEARCH_OK)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.geocode("São Paulo")
        assert result is not None
        assert result.coordinate.latitude == -23.5506
        assert result.place_type == "city"

    async def test_geocode_empty_returns_none(self) -> None:
        async with _client(lambda r: httpx.Response(200, json=[])) as client:
            backend = NominatimBackend(http_client=client)
            assert await backend.geocode("nowhere") is None

    async def test_reverse_parses_result(self) -> None:
        async with _client(lambda r: httpx.Response(200, json=_REVERSE_OK)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-23.5506, longitude=-46.6334),
            )
        assert result is not None
        assert "Sé" in result.display_name

    async def test_sends_user_agent(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, json=_SEARCH_OK)

        async with _client(handler) as client:
            backend = NominatimBackend(http_client=client, user_agent="myapp/1.0")
            await backend.geocode("São Paulo")
        assert seen["ua"] == "myapp/1.0"
