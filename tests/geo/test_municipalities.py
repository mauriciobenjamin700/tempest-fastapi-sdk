"""Tests for the bundled municipalities and the text finder."""

from __future__ import annotations

import hashlib

import pytest

from tempest_fastapi_sdk.geo import (
    BR_CITY_NICKNAMES,
    br_municipalities,
    find_municipalities_in_text,
    municipality_by_ibge_code,
    search_municipalities,
)
from tempest_fastapi_sdk.geo._br_municipalities_data import BR_MUNICIPALITIES_CSV
from tempest_fastapi_sdk.utils.locations import UFEnum

DATA_SHA256: str = hashlib.sha256(BR_MUNICIPALITIES_CSV.encode("utf-8")).hexdigest()
"""Recomputed here; the pinned value is in ``test_the_data_is_pinned``."""


def _found(text: str) -> list[tuple[str, str, bool]]:
    """Return ``(name, uf, uf_confirmed)`` for each match in ``text``.

    Args:
        text (str): The input.

    Returns:
        list[tuple[str, str, bool]]: The matches, best first.
    """
    return [
        (match.municipality.name, match.municipality.uf.value, match.uf_confirmed)
        for match in find_municipalities_in_text(text, limit=None)
    ]


class TestDataset:
    def test_the_data_is_pinned(self) -> None:
        """Cenário: o CSV gerado embutido.

        Esperado: o hash fixado aqui; regenerar com
        ``scripts/regen_br_municipalities.py`` muda os dois juntos.
        """
        assert DATA_SHA256 == (
            "79851c19c0fddfabe8ef782a5b957eb3ba0a0842da2de57d9b309f0ac9865d58"
        )

    def test_every_municipality_once_with_27_capitals(self) -> None:
        """Cenário: a lista inteira.

        Esperado: 5571 entradas (5570 municípios e o Distrito Federal),
        códigos únicos de sete dígitos, uma capital por UF.
        """
        municipalities = br_municipalities()
        codes = [municipality.ibge_code for municipality in municipalities]
        assert len(codes) == 5571 == len(set(codes))
        assert all(1_000_000 <= code <= 9_999_999 for code in codes)
        capitals = {m.uf for m in municipalities if m.capital}
        assert capitals == set(UFEnum)

    def test_known_rows(self) -> None:
        """Cenário: São Paulo, Brasília e uma grafia antiga.

        Esperado: código, UF, capital, coordenada no lugar e o apelido.
        """
        sao_paulo = municipality_by_ibge_code(3550308)
        assert sao_paulo is not None
        assert (sao_paulo.name, sao_paulo.uf, sao_paulo.capital) == (
            "São Paulo",
            UFEnum.SP,
            True,
        )
        assert -24 < sao_paulo.coordinate.latitude < -23
        assert municipality_by_ibge_code(2400208) is not None
        assert municipality_by_ibge_code(2400208).aliases == ("Açu",)  # type: ignore[union-attr]
        assert municipality_by_ibge_code(1) is None

    def test_nicknames_point_at_real_codes(self) -> None:
        """Cenário: cada apelido da tabela.

        Esperado: todo código existe.
        """
        assert all(
            municipality_by_ibge_code(code) for code in BR_CITY_NICKNAMES.values()
        )


class TestSearch:
    def test_typo_and_state_filter(self) -> None:
        """Cenário: nome com erro, e homônimo filtrado pela UF.

        Esperado: São Paulo para ``sao paolo``; só a Bom Jesus do Piauí com
        ``uf="PI"``, confirmada.
        """
        assert search_municipalities("sao paolo")[0].municipality.ibge_code == 3550308
        (only,) = search_municipalities("bom jesus", uf="pi")
        assert (only.municipality.uf, only.uf_confirmed) == (UFEnum.PI, True)

    def test_homonyms_tie_without_a_state(self) -> None:
        """Cenário: ``Bom Jesus`` sem UF.

        Esperado: várias UFs, todas com a mesma nota.
        """
        matches = search_municipalities("Bom Jesus", limit=None)
        assert len({match.municipality.uf for match in matches}) > 1
        assert len({match.score for match in matches}) == 1


class TestFindInText:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "moro em sao jose dos campos - sp",
                [("São José dos Campos", "SP", True)],
            ),
            ("Sou de Campinas, São Paulo", [("Campinas", "SP", True)]),
            ("Campinas SP", [("Campinas", "SP", True)]),
            ("moro em Feliz/RS", [("Feliz", "RS", True)]),
            ("nasci em Bom Jesus - PI", [("Bom Jesus", "PI", True)]),
            ("to em bh", [("Belo Horizonte", "MG", False)]),
            ("vou pra floripa semana que vem", [("Florianópolis", "SC", False)]),
            ("moro em ceilandia", [("Brasília", "DF", False)]),
            ("Assú RN", [("Assú", "RN", True)]),
            ("Açu, RN", [("Assú", "RN", True)]),
        ],
    )
    def test_finds_the_city_and_its_state(
        self, text: str, expected: list[tuple[str, str, bool]]
    ) -> None:
        """Cenário: frases como o usuário escreve.

        Esperado: a cidade certa, e ``uf_confirmed`` quando a UF veio junto.
        """
        assert _found(text) == expected

    @pytest.mark.parametrize(
        "text", ["estou feliz hoje", "o rio estava cheio", "foi um bom dia"]
    )
    def test_everyday_words_are_not_cities(self, text: str) -> None:
        """Cenário: palavra comum que também é município, sem cara de lugar.

        Esperado: nada acima do ``min_score`` padrão.
        """
        assert _found(text) == []

    def test_typos_inside_a_sentence(self) -> None:
        """Cenário: duas cidades, uma com erro.

        Esperado: as duas; a com erro com nota menor.
        """
        matches = find_municipalities_in_text("trabalho em sao paolo e moro em santos")
        names = {match.municipality.name: match.score for match in matches}
        assert set(names) == {"São Paulo", "Santos"}
        assert names["São Paulo"] < names["Santos"]

    def test_a_state_that_does_not_have_the_city_drops_it(self) -> None:
        """Cenário: ``Campinas/RJ`` (Campinas não existe no RJ).

        Esperado: Campinas não aparece.
        """
        assert all(name != "Campinas" for name, _uf, _ok in _found("Campinas/RJ"))

    def test_homonyms_without_state_tie(self) -> None:
        """Cenário: ``nasci em bom jesus``.

        Esperado: todas as Bom Jesus, mesma nota, nenhuma confirmada.
        """
        matches = find_municipalities_in_text("nasci em bom jesus", limit=None)
        assert len(matches) > 1
        assert len({match.score for match in matches}) == 1
        assert not any(match.uf_confirmed for match in matches)

    def test_offsets_point_at_the_original_text(self) -> None:
        """Cenário: nome com acento no meio da frase.

        Esperado: ``text[start:end]`` é o trecho como escrito.
        """
        text = "Eu moro em São José dos Campos."
        (match,) = find_municipalities_in_text(text)
        assert (
            text[match.start : match.end] == match.matched_text == "São José dos Campos"
        )


def test_function_words_do_not_become_a_misspelled_city() -> None:
    """Cenário: ``"está ao"`` (duas edições de "Estação") num texto longo.

    Esperado: nenhuma cidade; o achado do benchmark de 2000 palavras.
    """
    text = " ".join(["ontem eu fui ao mercado e o tempo está ao contrário"] * 20)
    assert find_municipalities_in_text(text, limit=None) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("campinas", [("Campinas", "SP", False)]),
        ("bh", [("Belo Horizonte", "MG", False)]),
        ("feliz/rs", [("Feliz", "RS", True)]),
    ],
)
def test_a_text_that_is_only_the_name_is_a_place(
    text: str, expected: list[tuple[str, str, bool]]
) -> None:
    """Cenário: o campo "cidade" de um formulário, só com o nome.

    Esperado: achado, mesmo minúsculo e de uma palavra; o benchmark mostrou
    que esse era o erro do caso "sem acento".
    """
    assert _found(text) == expected
