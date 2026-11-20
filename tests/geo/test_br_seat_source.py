"""Fonte fixada das sedes municipais e o fallback das 5 sedes erradas.

O ``_br_municipalities_data.py`` sai de ``kelvins/municipios-brasileiros`` num
commit fixado, conferido por SHA-256, e cinco sedes daquele arquivo caem fora
do próprio município (medido contra a malha IBGE 2022): elas trazem o centróide
do território, os mesmos valores do ``br-seats.json`` do tempest-react-sdk
(PR #679, issue #677).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from tempest_fastapi_sdk.geo import br_municipalities, municipality_by_ibge_code
from tempest_fastapi_sdk.geo._br_municipalities_data import (
    CENTROID_FALLBACK_IBGE_CODES,
)
from tempest_fastapi_sdk.geo._br_municipalities_data import (
    KELVINS_COMMIT as GENERATED_COMMIT,
)
from tempest_fastapi_sdk.geo._br_municipalities_data import (
    KELVINS_CSV_SHA256 as GENERATED_CSV_SHA256,
)
from tempest_fastapi_sdk.geo.br_map import uf_at
from tempest_fastapi_sdk.geo.schemas import Coordinate
from tempest_fastapi_sdk.utils.locations import UFEnum


def _repo_root() -> Path:
    """Acha a raiz do repositório subindo até o ``pyproject.toml``.

    Returns:
        Path: A raiz.

    Raises:
        RuntimeError: Quando nenhum ancestral tem ``pyproject.toml``.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("repository root not found")


sys.path.insert(0, str(_repo_root() / "scripts"))

from regen_br_municipalities import (  # noqa: E402
    KELVINS_COMMIT,
    KELVINS_CSV_SHA256,
    KELVINS_HEADER,
    KELVINS_LICENSE_SHA256,
    KELVINS_LICENSE_URL,
    KELVINS_URL,
    MAX_SOURCE_BYTES,
    SEAT_CENTROID_FALLBACK,
    _read,
    build,
    parse_kelvins,
)

PINNED_COMMIT: str = "503e2f70bbf1b4b7ec0b1f68b09086ccc38fe861"
"""O commit que o ``scripts/gen-br-seats.mjs`` do tempest-react-sdk fixa."""

PINNED_CSV_SHA256: str = (
    "22bd03d004361329f69ddfa3f6c99cd53012aa53972a172e10b141288ad0b1fe"
)
"""SHA-256 do ``csv/municipios.csv`` naquele commit (o mesmo do react-sdk)."""

PINNED_LICENSE_SHA256: str = (
    "c8254a78fe3edcb841e0df79bcd217e2626c2a13f9374dd8a51729d88dc0bc88"
)
"""SHA-256 do ``LICENSE`` naquele commit (o mesmo do react-sdk)."""

REACT_SEATS_SHA256: str = (
    "076d28dccb999467961befa6b3b97f9b99632e603cf5e84881528d0d9e5fdb2c"
)
"""Hash das 5571 coordenadas do ``br-seats.json`` do tempest-react-sdk (#679).

Uma linha ``<código>,<latitude>,<longitude>\\n`` por município, em ordem de
código, cada número como ``repr(float(...))``. Produzido por::

    gh api "repos/mauriciobenjamin700/tempest-react-sdk/contents/src/br/data/\\
    br-seats.json?ref=main" \\
        -H "Accept: application/vnd.github.raw" > br-seats.json
    python3 -I -c "import json, hashlib; d = json.load(open('br-seats.json'));
    print(hashlib.sha256(''.join(f'{int(r[0])},{float(r[4])!r},{float(r[3])!r}\\n'
    for r in sorted(d['municipalities'])).encode()).hexdigest())"
"""

REJECTED_SEATS: dict[int, tuple[UFEnum, Coordinate]] = {
    1505551: (UFEnum.PA, Coordinate(latitude=-1.59772, longitude=-46.9268)),
    2613107: (UFEnum.PE, Coordinate(latitude=-8.33763, longitude=-36.2869)),
    3164431: (UFEnum.MG, Coordinate(latitude=-19.7477, longitude=-43.3679)),
    4105201: (UFEnum.PR, Coordinate(latitude=-26.0891, longitude=-52.8691)),
    4128625: (UFEnum.PR, Coordinate(latitude=-26.1146, longitude=-52.7469)),
}
"""A sede que a fonte dá para cada um dos cinco, que o SDK shippou até aqui."""


def _coordinates_digest() -> str:
    """Calcula o hash das 5571 coordenadas no formato de ``REACT_SEATS_SHA256``.

    Returns:
        str: O SHA-256 em hexadecimal.
    """
    lines = "".join(
        f"{m.ibge_code},{m.coordinate.latitude!r},{m.coordinate.longitude!r}\n"
        for m in sorted(br_municipalities(), key=lambda m: m.ibge_code)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _kelvins_row(code: str, latitude: str = "-7.0", longitude: str = "-41.0") -> str:
    """Monta uma linha do CSV da fonte no formato fixado.

    Args:
        code (str): O código IBGE.
        latitude (str): A latitude, como texto.
        longitude (str): A longitude, como texto.

    Returns:
        str: A linha, sem quebra.
    """
    return f"{code},Lugar,{latitude},{longitude},0,22,1234,89,America/Fortaleza"


class TestPinnedSource:
    def test_commit_and_hashes_are_pinned(self) -> None:
        """Cenário: o gerador e o módulo gerado.

        Esperado: o mesmo commit e os mesmos SHA-256 que o react-sdk fixa,
        nos dois arquivos, e as URLs apontando para o commit, nunca a ``main``.
        """
        assert KELVINS_COMMIT == GENERATED_COMMIT == PINNED_COMMIT
        assert KELVINS_CSV_SHA256 == GENERATED_CSV_SHA256 == PINNED_CSV_SHA256
        assert KELVINS_LICENSE_SHA256 == PINNED_LICENSE_SHA256
        for url in (KELVINS_URL, KELVINS_LICENSE_URL):
            assert f"/{PINNED_COMMIT}/" in url
            assert "/main/" not in url

    def test_a_local_copy_with_another_hash_is_refused(self, tmp_path: Path) -> None:
        """Cenário: uma cópia local do CSV que não é a do commit fixado.

        Esperado: ``ValueError`` nomeando os dois hashes, antes de qualquer
        parse.
        """
        copy = tmp_path / "municipios.csv"
        copy.write_text(KELVINS_HEADER + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="pinned"):
            _read(str(copy), KELVINS_URL, KELVINS_CSV_SHA256)

    def test_an_oversized_source_is_refused(self, tmp_path: Path) -> None:
        """Cenário: um arquivo maior que o teto.

        Esperado: ``ValueError``, lendo só o teto mais um byte.
        """
        copy = tmp_path / "big.csv"
        copy.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
        with pytest.raises(ValueError, match="larger than"):
            _read(str(copy), KELVINS_URL)


class TestStrictParse:
    def test_the_pinned_format_parses(self) -> None:
        """Cenário: cabeçalho e uma linha no formato do arquivo fixado.

        Esperado: a linha, chaveada pelo código inteiro.
        """
        rows = parse_kelvins(f"{KELVINS_HEADER}\n{_kelvins_row('2210607')}\n")
        assert list(rows) == [2210607]
        assert rows[2210607]["latitude"] == "-7.0"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("codigo_ibge,nome\n", "unexpected header"),
            (f"{KELVINS_HEADER}\n" + _kelvins_row('"2210607"'), "plain"),
            (f"{KELVINS_HEADER}\n2210607,Lugar,-7.0,-41.0\n", "plain"),
            (f"{KELVINS_HEADER}\n" + _kelvins_row("221060"), "code"),
            (f"{KELVINS_HEADER}\n" + _kelvins_row("2210607", "1e1"), "coordinate"),
            (f"{KELVINS_HEADER}\n" + _kelvins_row("2210607", "40.0"), "outside"),
            (
                f"{KELVINS_HEADER}\n"
                + _kelvins_row("2210607").replace(",0,22,", ",2,22,"),
                "capital",
            ),
            (
                f"{KELVINS_HEADER}\n"
                + _kelvins_row("2210607")
                + "\n"
                + _kelvins_row("2210607"),
                "duplicate",
            ),
        ],
    )
    def test_anything_else_is_refused(self, text: str, message: str) -> None:
        """Cenário: aspas, colunas, código, número, faixa, capital ou duplicata.

        Esperado: ``ValueError`` na primeira linha fora do formato.
        """
        with pytest.raises(ValueError, match=message):
            parse_kelvins(text)

    def test_build_refuses_a_fallback_code_missing_from_the_sources(self) -> None:
        """Cenário: fontes que não listam um código do fallback.

        Esperado: ``ValueError``, em vez de um fallback que não se aplica.
        """
        ibge = json.dumps(
            [{"municipio-id": 2210607, "municipio-nome": "Lugar", "UF-sigla": "PI"}]
        )
        kelvins = f"{KELVINS_HEADER}\n{_kelvins_row('2210607')}\n"
        with pytest.raises(ValueError, match="fallback codes"):
            build(ibge, kelvins)


class TestCentroidFallback:
    def test_exactly_the_five_measured_municipalities(self) -> None:
        """Cenário: a lista de fallback do gerador, do dado e do modelo.

        Esperado: os cinco códigos medidos fora do próprio município, os
        mesmos do ``centroidFallback`` do react-sdk; todo o resto é sede.
        """
        expected = {1505551, 2613107, 3164431, 4105201, 4128625}
        assert set(SEAT_CENTROID_FALLBACK) == expected
        assert expected == CENTROID_FALLBACK_IBGE_CODES
        centroid = {m.ibge_code for m in br_municipalities() if m.point == "centroid"}
        assert centroid == expected
        assert sum(m.point == "seat" for m in br_municipalities()) == 5571 - 5

    @pytest.mark.parametrize("code", sorted(REJECTED_SEATS))
    def test_the_rejected_seat_is_replaced_by_the_centroid(self, code: int) -> None:
        """Cenário: cada um dos cinco municípios.

        Esperado: a coordenada é o centróide copiado do react-sdk, cai na UF
        do município, e não é mais a sede errada da fonte.
        """
        municipality = municipality_by_ibge_code(code)
        assert municipality is not None
        uf, rejected = REJECTED_SEATS[code]
        latitude, longitude = SEAT_CENTROID_FALLBACK[code]
        assert municipality.coordinate == Coordinate(
            latitude=latitude, longitude=longitude
        )
        assert municipality.coordinate != rejected
        assert municipality.uf is uf
        assert uf_at(municipality.coordinate) is uf


class TestReactSdkParity:
    def test_every_coordinate_matches_br_seats_json(self) -> None:
        """Cenário: as 5571 coordenadas do SDK.

        Esperado: o mesmo hash das coordenadas do ``br-seats.json`` do
        tempest-react-sdk, com o fallback incluído (comando no docstring de
        ``REACT_SEATS_SHA256``).
        """
        assert len(br_municipalities()) == 5571
        assert _coordinates_digest() == REACT_SEATS_SHA256
