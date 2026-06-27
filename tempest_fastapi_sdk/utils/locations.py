"""Brazilian states (UF), regions and municipalities.

This module ships an offline, dependency-free dataset of every
Brazilian federative unit and its municipalities, plus ready-to-use
Pydantic building blocks:

* :class:`UF` -- a :class:`~enum.StrEnum` with the 27 federative unit
  acronyms (``"SP"``, ``"RJ"``, ...).
* :class:`Region` -- the five official IBGE macro-regions.
* :class:`StateBR` and :class:`CityBR` -- Pydantic schemas describing a
  state (acronym + name + region + cities) and a single city.
* :func:`list_states`, :func:`get_state`, :func:`cities_by_uf`,
  :func:`states_by_region` -- query helpers over the bundled dataset.
* :func:`is_valid_uf`, :func:`normalize_uf`, :func:`is_valid_city` and
  :func:`normalize_city` -- validators/normalizers (accent and
  case-insensitive for city names).
* :data:`UFField` and :data:`CityNameField` -- annotated types ready to
  drop into Pydantic schema fields.

The dataset is loaded lazily from the bundled ``data/br_locations.json``
on first access and cached for the lifetime of the process.
"""

import json
import unicodedata
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Annotated, Final, TypedDict

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field


class UF(StrEnum):
    """The 27 Brazilian federative units, keyed by their acronym."""

    AC = "AC"
    AL = "AL"
    AP = "AP"
    AM = "AM"
    BA = "BA"
    CE = "CE"
    DF = "DF"
    ES = "ES"
    GO = "GO"
    MA = "MA"
    MT = "MT"
    MS = "MS"
    MG = "MG"
    PA = "PA"
    PB = "PB"
    PR = "PR"
    PE = "PE"
    PI = "PI"
    RJ = "RJ"
    RN = "RN"
    RS = "RS"
    RO = "RO"
    RR = "RR"
    SC = "SC"
    SP = "SP"
    SE = "SE"
    TO = "TO"


class Region(StrEnum):
    """The five official IBGE macro-regions of Brazil."""

    NORTH = "Norte"
    NORTHEAST = "Nordeste"
    CENTRAL_WEST = "Centro-Oeste"
    SOUTHEAST = "Sudeste"
    SOUTH = "Sul"


_UF_TO_REGION: Final[dict[UF, Region]] = {
    UF.AC: Region.NORTH,
    UF.AP: Region.NORTH,
    UF.AM: Region.NORTH,
    UF.PA: Region.NORTH,
    UF.RO: Region.NORTH,
    UF.RR: Region.NORTH,
    UF.TO: Region.NORTH,
    UF.AL: Region.NORTHEAST,
    UF.BA: Region.NORTHEAST,
    UF.CE: Region.NORTHEAST,
    UF.MA: Region.NORTHEAST,
    UF.PB: Region.NORTHEAST,
    UF.PE: Region.NORTHEAST,
    UF.PI: Region.NORTHEAST,
    UF.RN: Region.NORTHEAST,
    UF.SE: Region.NORTHEAST,
    UF.DF: Region.CENTRAL_WEST,
    UF.GO: Region.CENTRAL_WEST,
    UF.MS: Region.CENTRAL_WEST,
    UF.MT: Region.CENTRAL_WEST,
    UF.ES: Region.SOUTHEAST,
    UF.MG: Region.SOUTHEAST,
    UF.RJ: Region.SOUTHEAST,
    UF.SP: Region.SOUTHEAST,
    UF.PR: Region.SOUTH,
    UF.RS: Region.SOUTH,
    UF.SC: Region.SOUTH,
}
"""Static mapping of every UF to its IBGE macro-region."""


class CityBR(BaseModel):
    """A single Brazilian municipality.

    Attributes:
        name (str): The municipality name in canonical proper case
            (e.g. ``"Rio Branco"``).
        uf (UF): The federative unit the municipality belongs to.
    """

    name: str = Field(description="Municipality name in canonical proper case.")
    uf: UF = Field(description="Federative unit acronym the city belongs to.")


class StateBR(BaseModel):
    """A Brazilian federative unit and its municipalities.

    Attributes:
        uf (UF): The federative unit acronym (e.g. ``UF.SP``).
        name (str): The full state name (e.g. ``"São Paulo"``).
        region (Region): The IBGE macro-region the state belongs to.
        cities (list[str]): The municipality names, alphabetically
            sorted, in canonical proper case.
    """

    uf: UF = Field(description="Federative unit acronym.")
    name: str = Field(description="Full state name.")
    region: Region = Field(description="IBGE macro-region.")
    cities: list[str] = Field(
        default_factory=list,
        description="Alphabetically sorted municipality names.",
    )


class ChoiceBR(BaseModel):
    """A ``value``/``label`` pair ready for a frontend ``<select>``.

    The shape every dropdown wants: a stable ``value`` to store/submit
    and a human ``label`` to show. Returned by :func:`uf_choices`,
    :func:`region_choices` and :func:`city_choices`.

    Attributes:
        value (str): The value to store/submit (e.g. the UF acronym
            ``"SP"`` or a city name).
        label (str): The human-facing text to display (e.g. the full
            state name ``"São Paulo"``).
    """

    value: str = Field(description="Value to store/submit.")
    label: str = Field(description="Human-facing text to display.")


class _RawStateEntry(TypedDict):
    """Shape of a single entry in the bundled ``br_locations.json``."""

    sigla: str
    estado: str
    cidades: list[str]


class _RawLocations(TypedDict):
    """Top-level shape of the bundled ``br_locations.json``."""

    dataLocals: list[_RawStateEntry]


def _strip_accents(value: str) -> str:
    """Return ``value`` casefolded and stripped of diacritics.

    Used to compare city names regardless of accents or letter case.

    Args:
        value (str): The raw text to normalize.

    Returns:
        str: The text with combining marks removed and casefolded.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.casefold().strip()


@lru_cache(maxsize=1)
def _load_states() -> tuple[StateBR, ...]:
    """Load and cache every :class:`StateBR` from the bundled dataset.

    Returns:
        tuple[StateBR, ...]: All 27 states, ordered by acronym.
    """
    data_file = (
        resources.files("tempest_fastapi_sdk.utils") / "data" / "br_locations.json"
    )
    raw: _RawLocations = json.loads(data_file.read_text(encoding="utf-8"))
    states: list[StateBR] = []
    for entry in raw["dataLocals"]:
        uf = UF(entry["sigla"])
        states.append(
            StateBR(
                uf=uf,
                name=entry["estado"],
                region=_UF_TO_REGION[uf],
                cities=sorted(entry["cidades"]),
            ),
        )
    states.sort(key=lambda state: state.uf.value)
    return tuple(states)


@lru_cache(maxsize=1)
def _states_by_uf() -> dict[UF, StateBR]:
    """Return a cached ``UF -> StateBR`` index over the dataset.

    Returns:
        dict[UF, StateBR]: One entry per federative unit.
    """
    return {state.uf: state for state in _load_states()}


def is_valid_uf(value: str) -> bool:
    """Check whether ``value`` is a valid federative unit acronym.

    The match is case-insensitive and ignores surrounding whitespace,
    so ``"sp"`` and ``" SP "`` are both accepted.

    Args:
        value (str): The acronym to inspect.

    Returns:
        bool: ``True`` when ``value`` maps to a known :class:`UF`.
    """
    return value.strip().upper() in UF.__members__


def normalize_uf(value: str) -> UF:
    """Validate ``value`` and return the matching :class:`UF`.

    Args:
        value (str): The acronym (any case, optional whitespace).

    Returns:
        UF: The federative unit enum member.

    Raises:
        ValueError: If ``value`` is not a known federative unit.
    """
    candidate = value.strip().upper()
    if candidate not in UF.__members__:
        raise ValueError(f"invalid UF: {value!r}")
    return UF(candidate)


def list_states() -> list[StateBR]:
    """Return every Brazilian state ordered by acronym.

    Returns:
        list[StateBR]: All 27 federative units with their cities.
    """
    return list(_load_states())


def get_state(uf: str | UF) -> StateBR:
    """Return the :class:`StateBR` for a given federative unit.

    Args:
        uf (str | UF): The acronym (case-insensitive) or :class:`UF`.

    Returns:
        StateBR: The matching state, including its municipalities.

    Raises:
        ValueError: If ``uf`` is not a known federative unit.
    """
    key = uf if isinstance(uf, UF) else normalize_uf(uf)
    return _states_by_uf()[key]


def cities_by_uf(uf: str | UF) -> list[str]:
    """Return the alphabetically sorted municipalities of a state.

    Args:
        uf (str | UF): The acronym (case-insensitive) or :class:`UF`.

    Returns:
        list[str]: The municipality names in canonical proper case.

    Raises:
        ValueError: If ``uf`` is not a known federative unit.
    """
    return list(get_state(uf).cities)


def states_by_region(region: Region) -> list[StateBR]:
    """Return every state belonging to a given macro-region.

    Args:
        region (Region): The IBGE macro-region to filter by.

    Returns:
        list[StateBR]: The states in that region, ordered by acronym.
    """
    return [state for state in _load_states() if state.region == region]


def uf_choices() -> list[ChoiceBR]:
    """Return every federative unit as a frontend ``<select>`` choice.

    Each choice carries the acronym as ``value`` (what you store/submit,
    and what :data:`UFField` validates) and the full state name as
    ``label``. Ordered by acronym.

    Returns:
        list[ChoiceBR]: One choice per state, e.g.
        ``ChoiceBR(value="SP", label="São Paulo")``.
    """
    return [
        ChoiceBR(value=state.uf.value, label=state.name) for state in _load_states()
    ]


def region_choices() -> list[ChoiceBR]:
    """Return every IBGE macro-region as a frontend ``<select>`` choice.

    Both ``value`` and ``label`` carry the region name (e.g. ``"Sudeste"``)
    since that is the stored :class:`Region` value.

    Returns:
        list[ChoiceBR]: One choice per macro-region, ordered as declared.
    """
    return [ChoiceBR(value=region.value, label=region.value) for region in Region]


def city_choices(uf: str | UF) -> list[ChoiceBR]:
    """Return the cities of a federative unit as ``<select>`` choices.

    Each choice uses the canonical city name for both ``value`` and
    ``label``. Ordered alphabetically.

    Args:
        uf (str | UF): The acronym (case-insensitive) or :class:`UF`.

    Returns:
        list[ChoiceBR]: One choice per municipality.

    Raises:
        ValueError: If ``uf`` is not a known federative unit (matching
            :func:`cities_by_uf`).
    """
    return [ChoiceBR(value=city, label=city) for city in cities_by_uf(uf)]


@lru_cache(maxsize=27)
def _city_index(uf: UF) -> dict[str, str]:
    """Return a cached ``accentless-name -> canonical-name`` index.

    Args:
        uf (UF): The federative unit whose cities are indexed.

    Returns:
        dict[str, str]: Lookup from normalized to canonical city name.
    """
    return {_strip_accents(city): city for city in _states_by_uf()[uf].cities}


def is_valid_city(uf: str | UF, city: str) -> bool:
    """Check whether ``city`` exists in the given federative unit.

    Matching ignores accents, letter case and surrounding whitespace,
    so ``"sao paulo"`` matches ``"São Paulo"``.

    Args:
        uf (str | UF): The acronym (case-insensitive) or :class:`UF`.
        city (str): The municipality name to look up.

    Returns:
        bool: ``True`` when the city belongs to the state. Returns
        ``False`` for an unknown ``uf`` instead of raising.
    """
    try:
        key = uf if isinstance(uf, UF) else normalize_uf(uf)
    except ValueError:
        return False
    return _strip_accents(city) in _city_index(key)


def normalize_city(uf: str | UF, city: str) -> str:
    """Validate ``city`` against ``uf`` and return its canonical name.

    Args:
        uf (str | UF): The acronym (case-insensitive) or :class:`UF`.
        city (str): The municipality name (any case/accents).

    Returns:
        str: The canonical proper-case municipality name.

    Raises:
        ValueError: If ``uf`` is unknown or ``city`` is not part of it.
    """
    key = uf if isinstance(uf, UF) else normalize_uf(uf)
    canonical = _city_index(key).get(_strip_accents(city))
    if canonical is None:
        raise ValueError(f"unknown city {city!r} for UF {key.value!r}")
    return canonical


UFField = Annotated[UF, BeforeValidator(normalize_uf)]
"""Pydantic type that accepts any-case acronym and yields a :class:`UF`.

The :class:`~pydantic.BeforeValidator` runs ahead of the enum coercion,
so lower-case and whitespace-padded acronyms (``"sp"``, ``" RJ "``) are
accepted and an invalid value raises a clear ``invalid UF`` error.
"""

CityNameField = Annotated[str, AfterValidator(lambda v: v.strip())]
"""Pydantic type for a trimmed city name (no cross-field UF validation)."""


__all__: list[str] = [
    "UF",
    "ChoiceBR",
    "CityBR",
    "CityNameField",
    "Region",
    "StateBR",
    "UFField",
    "cities_by_uf",
    "city_choices",
    "get_state",
    "is_valid_city",
    "is_valid_uf",
    "list_states",
    "normalize_city",
    "normalize_uf",
    "region_choices",
    "states_by_region",
    "uf_choices",
]
