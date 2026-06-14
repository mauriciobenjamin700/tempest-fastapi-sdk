"""Tests for tempest_fastapi_sdk.utils.locations."""

import pytest
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.utils import (
    UF,
    CityBR,
    CityNameField,
    Region,
    StateBR,
    UFField,
    cities_by_uf,
    get_state,
    is_valid_city,
    is_valid_uf,
    list_states,
    normalize_city,
    normalize_uf,
    states_by_region,
)


def test_list_states_returns_all_27() -> None:
    """``list_states`` exposes every federative unit exactly once."""
    states = list_states()
    assert len(states) == 27
    assert {state.uf for state in states} == set(UF)
    assert all(isinstance(state, StateBR) for state in states)


def test_states_are_sorted_by_uf() -> None:
    """States come back ordered by their acronym."""
    states = list_states()
    acronyms = [state.uf.value for state in states]
    assert acronyms == sorted(acronyms)


def test_get_state_known_uf() -> None:
    """``get_state`` returns the matching state with cities and region."""
    sp = get_state("SP")
    assert sp.uf is UF.SP
    assert sp.name == "São Paulo"
    assert sp.region is Region.SOUTHEAST
    assert "São Paulo" in sp.cities
    assert sp.cities == sorted(sp.cities)


def test_get_state_is_case_insensitive() -> None:
    """A lower-case acronym resolves to the same state."""
    assert get_state("rj").uf is get_state("RJ").uf is UF.RJ


def test_get_state_accepts_enum() -> None:
    """Passing a :class:`UF` member works as well as a string."""
    assert get_state(UF.MG).name == "Minas Gerais"


def test_get_state_unknown_raises() -> None:
    """An unknown acronym raises ``ValueError``."""
    with pytest.raises(ValueError, match="invalid UF"):
        get_state("XX")


def test_cities_by_uf() -> None:
    """``cities_by_uf`` returns the state's municipality list."""
    cities = cities_by_uf("DF")
    assert "Brasília" in cities
    assert cities == get_state("DF").cities


def test_every_region_has_states() -> None:
    """Each macro-region maps to at least one state and covers all 27."""
    total = 0
    for region in Region:
        members = states_by_region(region)
        assert members, f"region {region} has no states"
        assert all(state.region is region for state in members)
        total += len(members)
    assert total == 27


def test_region_mapping_examples() -> None:
    """A few well-known states land in the expected region."""
    assert get_state("AM").region is Region.NORTH
    assert get_state("BA").region is Region.NORTHEAST
    assert get_state("MT").region is Region.CENTRAL_WEST
    assert get_state("RJ").region is Region.SOUTHEAST
    assert get_state("RS").region is Region.SOUTH


@pytest.mark.parametrize(
    ("value", "expected"),
    [(" sp ", True), ("RJ", True), ("mg", True), ("XX", False), ("", False)],
)
def test_is_valid_uf(value: str, expected: bool) -> None:
    """``is_valid_uf`` accepts any-case acronyms and rejects unknowns."""
    assert is_valid_uf(value) is expected


def test_normalize_uf() -> None:
    """``normalize_uf`` upper-cases, trims and returns a :class:`UF`."""
    assert normalize_uf(" mg ") is UF.MG


def test_normalize_uf_invalid_raises() -> None:
    """An unknown acronym raises ``ValueError``."""
    with pytest.raises(ValueError, match="invalid UF"):
        normalize_uf("zz")


@pytest.mark.parametrize(
    ("uf", "city", "expected"),
    [
        ("SP", "São Paulo", True),
        ("SP", "sao paulo", True),
        ("rj", "RIO DE JANEIRO", True),
        ("DF", "  brasilia  ", True),
        ("SP", "Rio de Janeiro", False),
        ("XX", "anything", False),
    ],
)
def test_is_valid_city(uf: str, city: str, expected: bool) -> None:
    """City matching ignores accents, case and whitespace."""
    assert is_valid_city(uf, city) is expected


def test_normalize_city_returns_canonical() -> None:
    """``normalize_city`` resolves to the canonical proper-case name."""
    assert normalize_city("rj", "rio de janeiro") == "Rio de Janeiro"
    assert normalize_city("SP", "SAO PAULO") == "São Paulo"


def test_normalize_city_unknown_raises() -> None:
    """An unknown city for a valid UF raises ``ValueError``."""
    with pytest.raises(ValueError, match="unknown city"):
        normalize_city("SP", "Rio de Janeiro")


def test_uf_field_coerces_and_validates() -> None:
    """``UFField`` accepts any-case acronyms and yields a :class:`UF`."""

    class Address(BaseModel):
        uf: UFField

    assert Address(uf=" sp ").uf is UF.SP
    with pytest.raises(ValidationError):
        Address(uf="XX")


def test_city_name_field_trims() -> None:
    """``CityNameField`` strips surrounding whitespace."""

    class Place(BaseModel):
        city: CityNameField

    assert Place(city="  Campinas  ").city == "Campinas"


def test_city_br_schema() -> None:
    """``CityBR`` ties a municipality name to its federative unit."""
    city = CityBR(name="Salvador", uf=UF.BA)
    assert city.name == "Salvador"
    assert city.uf is UF.BA


def test_no_duplicate_cities_within_state() -> None:
    """Every state's municipality list is free of duplicates."""
    for state in list_states():
        assert len(state.cities) == len(set(state.cities))
