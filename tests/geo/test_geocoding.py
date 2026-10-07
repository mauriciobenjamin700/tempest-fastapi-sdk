"""Tests for the Nominatim geocoding backend (mocked httpx transport).

Every payload used here is a response recorded from the public
``nominatim.openstreetmap.org`` instance — ``format=jsonv2&addressdetails=1``,
``Accept-Language: pt-BR``, 2026-10-07 — and stored under
``tests/geo/fixtures``: a museum in Teresina/PI, the Sé station in
São Paulo/SP, the town of Coivaras/PI and the village of São José da
Tenda/PI. Derived cases (a key removed) say so in their own docstring.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

from tempest_fastapi_sdk.geo import (
    Coordinate,
    GeocodeAddress,
    GeocodingBackend,
    NominatimBackend,
)

_FIXTURES: Path = Path(__file__).resolve().parent / "fixtures"


def _payload(name: str) -> dict[str, Any]:
    """Load one recorded Nominatim payload from disk.

    Args:
        name: File name inside ``tests/geo/fixtures``.

    Returns:
        The payload exactly as the server returned it.
    """
    payload: dict[str, Any] = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return payload


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
        """The search entry is the recorded Teresina payload, wrapped in a list."""
        results = [_payload("nominatim_reverse_teresina.json")]
        async with _client(lambda r: httpx.Response(200, json=results)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.geocode("Teresina")
        assert result is not None
        assert result.coordinate.latitude == -5.0892285
        assert result.place_type == "museum"

    async def test_geocode_empty_returns_none(self) -> None:
        async with _client(lambda r: httpx.Response(200, json=[])) as client:
            backend = NominatimBackend(http_client=client)
            assert await backend.geocode("nowhere") is None

    async def test_reverse_parses_result(self) -> None:
        payload = _payload("nominatim_reverse_sao_paulo.json")
        async with _client(lambda r: httpx.Response(200, json=payload)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-23.5505, longitude=-46.6333),
            )
        assert result is not None
        assert "Sé" in result.display_name
        assert result.place_type == "station"

    async def test_sends_user_agent(self) -> None:
        """The configured ``User-Agent`` reaches the wire."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, json=[])

        async with _client(handler) as client:
            backend = NominatimBackend(http_client=client, user_agent="myapp/1.0")
            await backend.geocode("São Paulo")
        assert seen["ua"] == "myapp/1.0"


class TestAddressDetails:
    async def test_reverse_reports_every_address_field(self) -> None:
        """The Teresina payload fills all six fields of ``GeocodeAddress``."""
        payload = _payload("nominatim_reverse_teresina.json")
        async with _client(lambda r: httpx.Response(200, json=payload)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-5.0892, longitude=-42.8019),
            )
        assert result is not None
        address = result.address
        assert isinstance(address, GeocodeAddress)
        assert address.city == "Teresina"
        assert address.state == "Piauí"
        assert address.state_code == "PI"
        assert address.country == "Brasil"
        assert address.country_code == "BR"
        assert address.postcode == "64001-490"

    async def test_city_falls_back_to_town(self) -> None:
        """Coivaras/PI carries ``town`` and no ``city`` key at all."""
        payload = _payload("nominatim_reverse_coivaras.json")
        async with _client(lambda r: httpx.Response(200, json=payload)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-4.9842, longitude=-42.2769),
            )
        assert result is not None
        address = result.address
        assert isinstance(address, GeocodeAddress)
        assert address.city == "Coivaras"
        assert address.state == "Piauí"
        assert address.state_code == "PI"
        assert address.postcode == "64335-000"

    async def test_city_prefers_town_over_village(self) -> None:
        """São José da Tenda answers with three settlement keys at once.

        The outer settlement (its town) is the city a service wants to
        store, not the village inside it.
        """
        async with _client(
            lambda r: httpx.Response(
                200,
                json=_payload("nominatim_reverse_sao_jose_da_tenda.json"),
            )
        ) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-7.8168485, longitude=-42.581098),
            )
        assert result is not None
        address = result.address
        assert isinstance(address, GeocodeAddress)
        assert address.city == "Socorro do Piauí"
        assert address.state_code == "PI"
        assert address.postcode is None

    async def test_city_falls_back_to_village(self) -> None:
        """The recorded São José da Tenda payload with its ``town`` key removed.

        Only the removal is derived: the rest of the payload, including the
        ``village`` and ``hamlet`` keys, is what the server answered.
        """
        payload = deepcopy(_payload("nominatim_reverse_sao_jose_da_tenda.json"))
        del payload["address"]["town"]
        async with _client(lambda r: httpx.Response(200, json=payload)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-7.8168485, longitude=-42.581098),
            )
        assert result is not None
        address = result.address
        assert isinstance(address, GeocodeAddress)
        assert address.city == "São José da Tenda"
        assert address.state_code == "PI"

    async def test_state_code_is_none_without_the_iso_key(self) -> None:
        """The recorded Coivaras payload with ``ISO3166-2-lvl4`` removed."""
        payload = deepcopy(_payload("nominatim_reverse_coivaras.json"))
        del payload["address"]["ISO3166-2-lvl4"]
        async with _client(lambda r: httpx.Response(200, json=payload)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-4.9842, longitude=-42.2769),
            )
        assert result is not None
        address = result.address
        assert isinstance(address, GeocodeAddress)
        assert address.state == "Piauí"
        assert address.state_code is None

    async def test_address_is_none_without_an_address_object(self) -> None:
        """The recorded Teresina payload with its ``address`` object removed."""
        payload = deepcopy(_payload("nominatim_reverse_teresina.json"))
        del payload["address"]
        async with _client(lambda r: httpx.Response(200, json=payload)) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.reverse(
                Coordinate(latitude=-5.0892, longitude=-42.8019),
            )
        assert result is not None
        assert result.address is None

    async def test_reverse_sends_addressdetails(self) -> None:
        """``reverse`` asks the server for the address object."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=_payload("nominatim_reverse_teresina.json"))

        async with _client(handler) as client:
            backend = NominatimBackend(http_client=client)
            await backend.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))
        assert seen["addressdetails"] == "1"
        assert seen["format"] == "jsonv2"

    async def test_geocode_sends_addressdetails(self) -> None:
        """``geocode`` asks for the address object and parses what comes back."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(
                200,
                json=[_payload("nominatim_reverse_teresina.json")],
            )

        async with _client(handler) as client:
            backend = NominatimBackend(http_client=client)
            result = await backend.geocode("Teresina")
        assert seen["addressdetails"] == "1"
        assert seen["format"] == "jsonv2"
        assert result is not None
        assert result.address is not None
        assert result.address.city == "Teresina"
