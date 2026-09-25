"""Tests for the Brazil geolocation helpers."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.geo import (
    UF_CENTROIDS,
    Coordinate,
    GeocodeResult,
    cep_to_coordinate,
    extract_cep,
    resolve_br_coordinate,
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


class TestExtractCep:
    @pytest.mark.parametrize(
        ("texts", "expected"),
        [
            (("Av. Paulista, 1578 - 01310-200",), "01310-200"),
            (("CEP 01310200, sala 4",), "01310-200"),
            ((None, "", "Bloco B, 64000-000"), "64000-000"),
            (("Rua A 01310-100", "Rua B 20040-002"), "01310-100"),
            (("Rua sem CEP", None), None),
            (("tel 86999998888",), None),
            (("doc 123456789",), None),
            ((), None),
        ],
    )
    def test_finds_the_first_cep(
        self, texts: tuple[str | None, ...], expected: str | None
    ) -> None:
        assert extract_cep(*texts) == expected


class _ScriptedGeocoder:
    """Geocoder answering each query from a script, in order."""

    def __init__(self, *answers: GeocodeResult | Exception | None) -> None:
        self._answers = list(answers)
        self.queries: list[str] = []

    async def geocode(self, query: str) -> GeocodeResult | None:
        self.queries.append(query)
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        return None


def _hit(latitude: float, longitude: float) -> GeocodeResult:
    return GeocodeResult(
        coordinate=Coordinate(latitude=latitude, longitude=longitude),
        display_name="hit",
    )


class TestResolveBrCoordinate:
    async def test_cep_wins(self) -> None:
        geocoder = _ScriptedGeocoder(_hit(-5.09, -42.80))
        point = await resolve_br_coordinate(
            geocoder=geocoder,
            uf="PI",
            address="Av. Frei Serafim, 2280",
            city="Teresina",
            complement="CEP 64001-020",
        )
        assert point == Coordinate(latitude=-5.09, longitude=-42.80)
        assert geocoder.queries == ["64001-020, Brasil"]

    async def test_falls_back_to_the_address(self) -> None:
        geocoder = _ScriptedGeocoder(None, _hit(-5.1, -42.8))
        point = await resolve_br_coordinate(
            geocoder=geocoder,
            uf="pi",
            address="Av. Frei Serafim, 2280 - 64001-020",
            city="Teresina",
        )
        assert point == Coordinate(latitude=-5.1, longitude=-42.8)
        assert geocoder.queries == [
            "64001-020, Brasil",
            "Av. Frei Serafim, 2280 - 64001-020, Teresina, PI, Brasil",
        ]

    async def test_no_cep_goes_straight_to_the_address(self) -> None:
        geocoder = _ScriptedGeocoder(_hit(-5.1, -42.8))
        await resolve_br_coordinate(
            geocoder=geocoder, uf=UF.PI, address="Rua Sem Número", city=None
        )
        assert geocoder.queries == ["Rua Sem Número, PI, Brasil"]

    async def test_falls_back_to_the_centroid(self) -> None:
        geocoder = _ScriptedGeocoder(None, None)
        point = await resolve_br_coordinate(
            geocoder=geocoder, uf="SP", address="Rua X 01310-100", city="São Paulo"
        )
        assert point == UF_CENTROIDS[UF.SP]

    async def test_geocoder_failures_never_raise(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        geocoder = _ScriptedGeocoder(
            RuntimeError("429 from Nominatim"),
            TimeoutError("read timeout"),
        )
        point = await resolve_br_coordinate(
            geocoder=geocoder, uf="RJ", address="Rua Y 20040-002", city="Rio"
        )
        assert point == UF_CENTROIDS[UF.RJ]
        assert len(geocoder.queries) == 2
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 2
        assert all(r.exc_info is not None for r in warnings)

    async def test_cep_failure_still_tries_the_address(self) -> None:
        geocoder = _ScriptedGeocoder(RuntimeError("boom"), _hit(-22.9, -43.2))
        point = await resolve_br_coordinate(
            geocoder=geocoder, uf="RJ", address="Rua Y 20040-002", city="Rio"
        )
        assert point == Coordinate(latitude=-22.9, longitude=-43.2)

    async def test_no_geocoder_is_the_offline_path(self) -> None:
        point = await resolve_br_coordinate(
            geocoder=None, uf="ce", address="Rua Z 60000-000", city="Fortaleza"
        )
        assert point == UF_CENTROIDS[UF.CE]

    async def test_nothing_to_geocode_skips_the_address_step(self) -> None:
        geocoder = _ScriptedGeocoder()
        point = await resolve_br_coordinate(geocoder=geocoder, uf="BA")
        assert point == UF_CENTROIDS[UF.BA]
        assert geocoder.queries == []

    async def test_unknown_uf_returns_none(self) -> None:
        assert await resolve_br_coordinate(geocoder=None, uf="ZZ") is None

    async def test_unknown_uf_is_left_out_of_the_query(self) -> None:
        geocoder = _ScriptedGeocoder(None)
        point = await resolve_br_coordinate(
            geocoder=geocoder, uf="ZZ", address="Rua A", city="Cidade"
        )
        assert point is None
        assert geocoder.queries == ["Rua A, Cidade, Brasil"]

    async def test_country_is_configurable(self) -> None:
        geocoder = _ScriptedGeocoder(_hit(0.0, 0.0))
        await resolve_br_coordinate(
            geocoder=geocoder, uf="SP", complement="01310-100", country="Brazil"
        )
        assert geocoder.queries == ["01310-100, Brazil"]
