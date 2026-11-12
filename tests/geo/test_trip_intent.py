"""Golden table and behaviour of ``parse_trip_intent``.

The table is the quality bar of the trip search: the phrases the BusCar
transport backend tested in production (typed and transcribed, with the
spoken ``l`` and typos) plus the cases its own parser got wrong and the
municipality data fixes (nicknames, Roman numerals, ordinary words). A change
that breaks any line is a regression of the search, not a test to update.

The reference date is a Wednesday (2026-10-14), so ``"sexta"``, ``"quarta"``
and ``"quarta que vem"`` have one answer.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from tempest_fastapi_sdk.geo import (
    CityMention,
    TripRoleEnum,
    br_municipalities,
    build_trip_city_index,
    municipality_by_name,
    parse_trip_intent,
)
from tempest_fastapi_sdk.geo.trip_intent import spoken_names, vocalized_key

TODAY: date = date(2026, 10, 14)
"""Wednesday of reference."""

Cities = tuple[tuple[str, str], ...] | None
"""Expected ``(UF, official name)`` candidates, or ``None`` when not said."""

PHRASES: list[tuple[str, Cities, Cities, date | None, str | None]] = [
    (
        "quero ir de Teresina pra Parnaíba sexta de manhã",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        date(2026, 10, 16),
        "manha",
    ),
    (
        "Teresinha para Sao Luis amanhã",
        (("PI", "Teresina"),),
        (("MA", "São Luís"),),
        date(2026, 10, 15),
        None,
    ),
    (
        "Parnaíba pra Teresina hoje",
        (("PI", "Parnaíba"),),
        (("PI", "Teresina"),),
        TODAY,
        None,
    ),
    (
        "saindo de picos indo para floriano depois de amanhã a noite",
        (("PI", "Picos"),),
        (("PI", "Floriano"),),
        date(2026, 10, 16),
        "noite",
    ),
    (
        "passagem de ônibus para Fortaleza dia 20",
        None,
        (("CE", "Fortaleza"),),
        date(2026, 10, 20),
        None,
    ),
    (
        "de Batalha no Piauí para Teresina",
        (("PI", "Batalha"),),
        (("PI", "Teresina"),),
        None,
        None,
    ),
    ("teresina parnaiba", (("PI", "Teresina"),), (("PI", "Parnaíba"),), None, None),
    (
        "quero uma carona pro rio de janero hoje",
        None,
        (("RJ", "Rio de Janeiro"),),
        TODAY,
        None,
    ),
    (
        "vou viajar de carro pra Picos 20/10",
        None,
        (("PI", "Picos"),),
        date(2026, 10, 20),
        None,
    ),
    (
        "indo para Belém do Pará sábado que vem",
        None,
        (("PA", "Belém"),),
        date(2026, 10, 17),
        None,
    ),
    (
        "de teresina até campo maior na próxima segunda feira à tarde",
        (("PI", "Teresina"),),
        (("PI", "Campo Maior"),),
        date(2026, 10, 19),
        "tarde",
    ),
    (
        "bom dia, tem vaga de Teresina para Altos?",
        (("PI", "Teresina"),),
        (("PI", "Altos"),),
        None,
        None,
    ),
    (
        "de Fortaleza para Juazeiro do Norte dia 5 de novembro",
        (("CE", "Fortaleza"),),
        (("CE", "Juazeiro do Norte"),),
        date(2026, 11, 5),
        None,
    ),
    (
        "Picos Floriano 25/12/2026",
        (("PI", "Picos"),),
        (("PI", "Floriano"),),
        date(2026, 12, 25),
        None,
    ),
    (
        "de Parnaiba pra Luis Correia domingo",
        (("PI", "Parnaíba"),),
        (("PI", "Luís Correia"),),
        date(2026, 10, 18),
        None,
    ),
    (
        "partindo de São Raimundo Nonato até Teresina quinta-feira",
        (("PI", "São Raimundo Nonato"),),
        (("PI", "Teresina"),),
        date(2026, 10, 15),
        None,
    ),
    (
        "teresina pra parnaíba quarta",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        TODAY,
        None,
    ),
    (
        "teresina pra parnaíba quarta que vem",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        date(2026, 10, 21),
        None,
    ),
    (
        "de Floriano para Oeiras de madrugada",
        (("PI", "Floriano"),),
        (("PI", "Oeiras"),),
        None,
        "madrugada",
    ),
    (
        "de São Paulo para Rio de Janeiro hoje",
        (("SP", "São Paulo"),),
        (("RJ", "Rio de Janeiro"),),
        TODAY,
        None,
    ),
    (
        "de teresina para piripiri dia 10",
        (("PI", "Teresina"),),
        (("PI", "Piripiri"),),
        date(2026, 11, 10),
        None,
    ),
    (
        "de teresina para parnaiba dia 31 de fevereiro",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        None,
        None,
    ),
    ("parnaiba amanhã", None, (("PI", "Parnaíba"),), date(2026, 10, 15), None),
    ("de Teresina", (("PI", "Teresina"),), None, None, None),
    (
        "pra Teresina amanhã de tarde",
        None,
        (("PI", "Teresina"),),
        date(2026, 10, 15),
        "tarde",
    ),
    (
        "vou de Teresina pra Picos",
        (("PI", "Teresina"),),
        (("PI", "Picos"),),
        None,
        None,
    ),
    (
        "chegando em Teresina hoje à noite saindo de Parnaíba",
        (("PI", "Parnaíba"),),
        (("PI", "Teresina"),),
        TODAY,
        "noite",
    ),
    (
        "de Teresina pra Parnaíba amanhã de manhã",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        date(2026, 10, 15),
        "manha",
    ),
    (
        "Teresina - PI para Picos - PI",
        (("PI", "Teresina"),),
        (("PI", "Picos"),),
        None,
        None,
    ),
    ("quero ir pra Florianópolis", None, (("SC", "Florianópolis"),), None, None),
    (
        "de teresina para parnaíba no dia 20",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        date(2026, 10, 20),
        None,
    ),
    ("uma passagem para Fortaleza", None, (("CE", "Fortaleza"),), None, None),
    (
        "de Teresinaa pra Parnaibaa",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        None,
        None,
    ),
    ("quero ir de carro pra Fortaleza", None, (("CE", "Fortaleza"),), None, None),
    (
        "de Belém da Paraíba para Teresina",
        (("PB", "Belém"),),
        (("PI", "Teresina"),),
        None,
        None,
    ),
    ("de Teresina pra Sobrau", (("PI", "Teresina"),), (("CE", "Sobral"),), None, None),
    ("de Teresina pra Natau", (("PI", "Teresina"),), (("RN", "Natal"),), None, None),
    ("de Autos pra Teresina", (("PI", "Altos"),), (("PI", "Teresina"),), None, None),
    ("pra Paumas no Tocantins", None, (("TO", "Palmas"),), None, None),
    (
        "de Pico pra Sobrau amanhã",
        (("PI", "Picos"),),
        (("CE", "Sobral"),),
        date(2026, 10, 15),
        None,
    ),
    (
        "de Teresina pra Auto Longa",
        (("PI", "Teresina"),),
        (("PI", "Alto Longá"),),
        None,
        None,
    ),
    (
        "de Teresina pra Rio de Janero",
        (("PI", "Teresina"),),
        (("RJ", "Rio de Janeiro"),),
        None,
        None,
    ),
    (
        "de Teresina pra Parnaíba amanhã",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        date(2026, 10, 15),
        None,
    ),
    (
        "quero ir de teresinha pra parnaiba",
        (("PI", "Teresina"),),
        (("PI", "Parnaíba"),),
        None,
        None,
    ),
    ("de Picos pra Sobrau", (("PI", "Picos"),), (("CE", "Sobral"),), None, None),
    ("quero ir pra natau sexta", None, (("RN", "Natal"),), date(2026, 10, 16), None),
    (
        "de floripa pra sampa",
        (("SC", "Florianópolis"),),
        (("SP", "São Paulo"),),
        None,
        None,
    ),
    (
        "de Batalha no Piauí pra Teresina",
        (("PI", "Batalha"),),
        (("PI", "Teresina"),),
        None,
        None,
    ),
    (
        "de campinas/sp pra são paulo",
        (("SP", "Campinas"),),
        (("SP", "São Paulo"),),
        None,
        None,
    ),
    ("picos sobral", (("PI", "Picos"),), (("CE", "Sobral"),), None, None),
    (
        "de sao jose dos campos pra rio de janeiro",
        (("SP", "São José dos Campos"),),
        (("RJ", "Rio de Janeiro"),),
        None,
        None,
    ),
    (
        "de bh pra vitoria da conquista",
        (("MG", "Belo Horizonte"),),
        (("BA", "Vitória da Conquista"),),
        None,
        None,
    ),
    (
        "de juazeiro do norte pra crato",
        (("CE", "Juazeiro do Norte"),),
        (("CE", "Crato"),),
        None,
        None,
    ),
    ("estou feliz de ir pra fortaleza", None, (("CE", "Fortaleza"),), None, None),
    (
        "de Parnaiba pra Luis Correia",
        (("PI", "Parnaíba"),),
        (("PI", "Luís Correia"),),
        None,
        None,
    ),
    (
        "ir de oeiras pra floriano depois de amanhã de manhã",
        (("PI", "Oeiras"),),
        (("PI", "Floriano"),),
        date(2026, 10, 16),
        "manha",
    ),
    (
        "de sao luis pra teresina hoje a noite",
        (("MA", "São Luís"),),
        (("PI", "Teresina"),),
        TODAY,
        "noite",
    ),
    (
        "picos para sobral hoje a noite",
        (("PI", "Picos"),),
        (("CE", "Sobral"),),
        TODAY,
        "noite",
    ),
    (
        "de pedro segundo pra piripiri",
        (("PI", "Pedro II"),),
        (("PI", "Piripiri"),),
        None,
        None,
    ),
    (
        "de teresina pra castelo do piaui",
        (("PI", "Teresina"),),
        (("PI", "Castelo do Piauí"),),
        None,
        None,
    ),
    (
        "vou de esperantina pra campo maior no sabado",
        (("TO", "Esperantina"), ("PI", "Esperantina")),
        (("PI", "Campo Maior"),),
        date(2026, 10, 17),
        None,
    ),
]
"""Phrase, origin, destination, date and period expected."""


def cities(mention: CityMention | None) -> Cities:
    """Reduce a mention to comparable ``(UF, name)`` pairs.

    Args:
        mention (CityMention | None): The parsed city.

    Returns:
        Cities: The candidates, or ``None``.
    """
    if mention is None:
        return None
    return tuple((city.uf.value, city.name) for city in mention.candidates)


@pytest.mark.parametrize(
    ("text", "origin", "destination", "departure", "period"), PHRASES
)
def test_phrase_is_understood(
    text: str,
    origin: Cities,
    destination: Cities,
    departure: date | None,
    period: str | None,
) -> None:
    """Every phrase of the table reads as recorded."""
    intent = parse_trip_intent(text, TODAY)

    assert cities(intent.origin) == origin
    assert cities(intent.destination) == destination
    assert intent.departure_date == departure
    assert intent.period == period


def test_table_keeps_its_size() -> None:
    """The bar only grows: dropping phrases would hide a regression."""
    assert len(PHRASES) >= 60


def test_period_comes_with_its_time_range() -> None:
    """``"à noite"`` sets the evening window."""
    intent = parse_trip_intent("de Teresina pra Picos à noite", TODAY)

    assert intent.period == "noite"
    assert (intent.period_start, intent.period_end) == (time(18, 0), time(23, 59))


@pytest.mark.parametrize(
    ("text", "unresolved"),
    [
        ("de Teresina pra Picos 30/02", "30/02"),
        ("pra Picos dia 31 de abril", "dia 31 de abril"),
    ],
)
def test_impossible_date_is_reported(text: str, unresolved: str) -> None:
    """A date that does not exist comes back to ask, with no date set."""
    intent = parse_trip_intent(text, TODAY)

    assert intent.departure_date is None
    assert intent.unresolved_date == unresolved


def test_homonym_without_state_keeps_every_candidate() -> None:
    """``"Batalha"`` alone is in PI and AL; both come back, same name."""
    intent = parse_trip_intent("de Batalha pra Teresina", TODAY)

    assert intent.origin is not None
    assert {city.uf.value for city in intent.origin.candidates} == {"PI", "AL"}
    assert intent.origin.resolved is None
    assert intent.origin.name == "Batalha"


def test_restricted_index_reports_a_city_outside_it() -> None:
    """With only the served cities, Natal is said but not recognized."""
    served = [
        municipality_by_name("Teresina", "PI"),
        municipality_by_name("Sobral", "CE"),
    ]
    index = build_trip_city_index([city for city in served if city is not None])

    intent = parse_trip_intent("de Teresina pra Natal", TODAY, municipalities=index)

    assert cities(intent.origin) == (("PI", "Teresina"),)
    assert intent.destination is None
    assert intent.unrecognized == {TripRoleEnum.DESTINATION: "natal"}


def test_restricted_index_accepts_loose_lowercase_cities() -> None:
    """``"picos sobral amanha"`` among served cities is Picos to Sobral."""
    served = [municipality_by_name("Picos", "PI"), municipality_by_name("Sobral", "CE")]
    index = build_trip_city_index([city for city in served if city is not None])

    intent = parse_trip_intent("quero picos sobral amanha", TODAY, municipalities=index)

    assert cities(intent.origin) == (("PI", "Picos"),)
    assert cities(intent.destination) == (("CE", "Sobral"),)


def test_ordinary_word_is_not_a_city_country_wide() -> None:
    """``"estou feliz"`` is not the town of Feliz (RS)."""
    intent = parse_trip_intent("estou feliz de ir pra fortaleza", TODAY)

    assert intent.origin is None
    assert cities(intent.destination) == (("CE", "Fortaleza"),)


@pytest.mark.parametrize("text", ["de novo pra Picos", "pra casa", "pra lá amanhã"])
def test_words_that_are_not_a_city_are_not_reported(text: str) -> None:
    """``"de novo"``, ``"pra casa"`` and ``"pra lá"`` are not cities said."""
    assert parse_trip_intent(text, TODAY).unrecognized == {}


def test_nickname_counts_only_when_its_city_is_served() -> None:
    """``"sampa"`` needs São Paulo in the restricted set."""
    teresina = municipality_by_name("Teresina", "PI")
    assert teresina is not None
    index = build_trip_city_index([teresina])

    intent = parse_trip_intent("de Teresina pra sampa", TODAY, municipalities=index)

    assert intent.destination is None
    assert intent.unrecognized == {TripRoleEnum.DESTINATION: "sampa"}


def test_spoken_names_include_the_roman_numeral_said() -> None:
    """Pedro II is also ``"pedro segundo"``."""
    pedro = municipality_by_name("Pedro II", "PI")

    assert pedro is not None
    assert spoken_names(pedro)[:2] == ["pedro ii", "pedro segundo"]


@pytest.mark.parametrize(
    ("word", "key"),
    [("sobral", "sobrau"), ("altos", "autos"), ("ilha", "ilha"), ("natal", "natau")],
)
def test_vocalized_key_only_touches_syllable_final_l(word: str, key: str) -> None:
    """The ``lh`` digraph keeps its ``l``."""
    assert vocalized_key(word) == key


def test_municipality_by_name_ignores_case_and_accents() -> None:
    """``"SAO LUIS"`` in MA is São Luís; in PI there is none."""
    found = municipality_by_name("SAO LUIS", "ma")

    assert found is not None
    assert found.name == "São Luís"
    assert municipality_by_name("SAO LUIS", "PI") is None
    assert municipality_by_name("Teresina", "XX") is None


def test_country_index_is_built_once() -> None:
    """``None`` reuses the cached country-wide index."""
    assert build_trip_city_index() is build_trip_city_index()
    assert len(br_municipalities()) == 5571


@pytest.mark.parametrize(
    ("name", "uf", "official"),
    [
        ("ALVORADA DO OESTE", "RO", "Alvorada D'Oeste"),
        ("CAMACÃ", "BA", "Camacan"),
        ("SANTA ISABEL DO PARÁ", "PA", "Santa Izabel do Pará"),
        ("ELDORADO DOS CARAJÁS", "PA", "Eldorado do Carajás"),
    ],
)
def test_municipality_by_name_links_old_spellings_with_a_cutoff(
    name: str, uf: str, official: str
) -> None:
    """Old dataset spellings link with ``typo_cutoff``, and only with it."""
    found = municipality_by_name(name, uf, typo_cutoff=0.85)

    assert found is not None
    assert found.name == official
    assert municipality_by_name(name, uf) is None


def test_day_missing_in_the_current_month_rolls_to_the_next() -> None:
    """``"dia 31"`` said on 14 April (30 days) is 31 May, not no date."""
    intent = parse_trip_intent("de Picos pra Floriano dia 31", date(2026, 4, 14))

    assert intent.departure_date == date(2026, 5, 31)
    assert intent.unresolved_date is None


def test_phrase_full_of_markers_spends_a_bounded_fuzzy_budget() -> None:
    """Forty ``"de xyzwq pra qwzxy"`` do not scan the index forty times."""
    text = " ".join(["de xyzwq pra qwzxy"] * 40)

    intent = parse_trip_intent(text, TODAY)

    assert intent.origin is None
    assert intent.destination is None
