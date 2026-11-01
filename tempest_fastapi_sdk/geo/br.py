"""Brazil-specific geolocation helpers built on the SDK's BR datasets.

Two conveniences for Brazilian services:

* :func:`uf_centroid` — an **offline** approximate geographic centre for
  each of the 27 federative units (no network, no dataset download). Good
  for a coarse "somewhere in this state" pin or a map default.
* :func:`cep_to_coordinate` — resolve a Brazilian postal code (CEP) to a
  coordinate through any injected
  :class:`~tempest_fastapi_sdk.geo.GeocodingBackend` (e.g. Nominatim).
* :func:`extract_cep` — find the first CEP written inside free-text
  address fields.
* :func:`resolve_br_coordinate` — the best point for a Brazilian address,
  falling back CEP → full address → state centroid, never raising on a
  geocoder failure.

City-level centroids are intentionally not shipped — they need a
municipality coordinate dataset the SDK does not bundle; geocode the city
name via a :class:`GeocodingBackend` when you need that precision.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.exceptions.value_errors import ValidationValueError
from tempest_fastapi_sdk.geo.schemas import Coordinate
from tempest_fastapi_sdk.utils import UFEnum, normalize_uf

if TYPE_CHECKING:
    from tempest_fastapi_sdk.geo.geocoding import GeocodingBackend

_logger: logging.Logger = logging.getLogger(__name__)

CEP_PATTERN: re.Pattern[str] = re.compile(r"\b(\d{5})-?(\d{3})\b")
"""A CEP written anywhere in free text, with or without the dash.

Word boundaries on both ends keep it from matching eight digits inside a
longer run (a phone number, a document number): ``"123456789"`` does not
match, ``"CEP 01310-100, sala 4"`` does.
"""

# Approximate geographic centre of each Brazilian federative unit
# (decimal degrees, WGS84). Coarse by design — a state-level pin, not a
# survey point.
UF_CENTROIDS: dict[UFEnum, Coordinate] = {
    UFEnum.AC: Coordinate(latitude=-9.0, longitude=-70.5),
    UFEnum.AL: Coordinate(latitude=-9.55, longitude=-36.6),
    UFEnum.AP: Coordinate(latitude=1.4, longitude=-51.8),
    UFEnum.AM: Coordinate(latitude=-3.9, longitude=-63.0),
    UFEnum.BA: Coordinate(latitude=-12.5, longitude=-41.7),
    UFEnum.CE: Coordinate(latitude=-5.1, longitude=-39.5),
    UFEnum.DF: Coordinate(latitude=-15.78, longitude=-47.9),
    UFEnum.ES: Coordinate(latitude=-19.6, longitude=-40.7),
    UFEnum.GO: Coordinate(latitude=-15.9, longitude=-49.7),
    UFEnum.MA: Coordinate(latitude=-5.1, longitude=-45.2),
    UFEnum.MT: Coordinate(latitude=-13.0, longitude=-55.9),
    UFEnum.MS: Coordinate(latitude=-20.5, longitude=-54.7),
    UFEnum.MG: Coordinate(latitude=-18.5, longitude=-44.5),
    UFEnum.PA: Coordinate(latitude=-4.0, longitude=-52.9),
    UFEnum.PB: Coordinate(latitude=-7.2, longitude=-36.7),
    UFEnum.PR: Coordinate(latitude=-24.6, longitude=-51.6),
    UFEnum.PE: Coordinate(latitude=-8.4, longitude=-37.9),
    UFEnum.PI: Coordinate(latitude=-7.4, longitude=-42.5),
    UFEnum.RJ: Coordinate(latitude=-22.25, longitude=-42.7),
    UFEnum.RN: Coordinate(latitude=-5.8, longitude=-36.6),
    UFEnum.RS: Coordinate(latitude=-30.0, longitude=-53.5),
    UFEnum.RO: Coordinate(latitude=-10.9, longitude=-63.4),
    UFEnum.RR: Coordinate(latitude=2.1, longitude=-61.4),
    UFEnum.SC: Coordinate(latitude=-27.2, longitude=-50.5),
    UFEnum.SP: Coordinate(latitude=-22.2, longitude=-48.7),
    UFEnum.SE: Coordinate(latitude=-10.6, longitude=-37.4),
    UFEnum.TO: Coordinate(latitude=-10.2, longitude=-48.3),
}


def uf_centroid(uf: UFEnum | str) -> Coordinate:
    """Return the approximate geographic centre of a Brazilian state.

    Args:
        uf: A :class:`~tempest_fastapi_sdk.utils.UFEnum` member or a state sigla
            (case-insensitive, e.g. ``"sp"``).

    Returns:
        The state's approximate centre as a :class:`Coordinate`.

    Raises:
        ValueError: If ``uf`` is not a valid federative unit.
    """
    normalized = normalize_uf(uf) if not isinstance(uf, UFEnum) else uf.value
    if normalized is None:
        raise ValidationValueError(
            "INVALID_UF",
            f"invalid UFEnum: {uf!r}",
            params={"value": uf},
        )
    return UF_CENTROIDS[UFEnum(normalized)]


async def cep_to_coordinate(
    cep: str,
    *,
    geocoder: GeocodingBackend,
    country: str = "Brasil",
) -> Coordinate | None:
    """Resolve a Brazilian CEP (postal code) to a coordinate via geocoding.

    Composes an injected :class:`~tempest_fastapi_sdk.geo.GeocodingBackend`:
    the CEP (plus country) is geocoded and the resolved point returned.
    Nominatim resolves Brazilian CEPs directly, so no separate CEP-lookup
    service is required.

    Args:
        cep: The postal code (with or without a dash, e.g. ``"01310-100"``).
        geocoder: The geocoding backend to resolve the CEP.
        country: Country appended to the query to disambiguate.

    Returns:
        The resolved :class:`Coordinate`, or ``None`` when nothing matches.
    """
    result = await geocoder.geocode(f"{cep}, {country}")
    return result.coordinate if result is not None else None


def extract_cep(*texts: str | None) -> str | None:
    """Return the first CEP written in the given texts, normalized.

    Built for address data typed into a single free-text field — the
    postal code sits somewhere inside ``"Av. Paulista, 1578 - 01310-200"``
    and no column holds it. Texts are scanned in the order given, so pass
    the most trustworthy field first.

    Args:
        *texts (str | None): The texts to scan; ``None`` and empty strings
            are skipped.

    Returns:
        str | None: The CEP as ``"00000-000"``, or ``None`` when no text
        carries one. Only the shape is checked — whether the CEP exists is
        a question for a geocoder.
    """
    for text in texts:
        if not text:
            continue
        match = CEP_PATTERN.search(text)
        if match is not None:
            return f"{match.group(1)}-{match.group(2)}"
    return None


async def resolve_br_coordinate(
    *,
    geocoder: GeocodingBackend | None,
    uf: UFEnum | str,
    address: str | None = None,
    city: str | None = None,
    complement: str | None = None,
    country: str = "Brasil",
) -> Coordinate | None:
    """Resolve the best known point for a Brazilian address.

    Tries three sources, most precise first, and returns the first that
    answers:

    1. the **CEP** found in ``address`` or ``complement`` (see
       :func:`extract_cep`), through :func:`cep_to_coordinate`;
    2. the **full address** — ``"address, city, UFEnum, country"`` with the
       empty parts left out — geocoded as one query (skipped when both
       ``address`` and ``city`` are empty);
    3. the **state centroid** (:func:`uf_centroid`), offline, which always
       answers for a valid UFEnum.

    A geocoder failure is not an error here: any exception from
    ``geocoder.geocode`` is logged at ``WARNING`` (with the traceback) and
    the chain moves to the next step, so the worst outcome is the state
    centroid. Retrying belongs **on the geocoder you inject** — wrap its
    ``geocode`` with :func:`~tempest_fastapi_sdk.async_retry` — because this
    function only sees the failure after your retries are spent.

    Args:
        geocoder (GeocodingBackend | None): The backend for steps 1 and 2.
            ``None`` skips straight to the state centroid — the offline
            path, for tests and for deployments without geocoding.
        uf (UFEnum | str): The state, as a member or a sigla
            (case-insensitive).
        address (str | None): The street address, free text.
        city (str | None): The city name.
        complement (str | None): Extra address text, scanned for a CEP
            after ``address`` but not sent in the address query.
        country (str): Country appended to both geocoding queries.

    Returns:
        Coordinate | None: The resolved point, or ``None`` when nothing
        matched and ``uf`` is not a valid federative unit.
    """
    state = _uf_or_none(uf)
    sigla = state.value if state is not None else None
    if geocoder is not None:
        cep = extract_cep(address, complement)
        if cep is not None:
            try:
                point = await cep_to_coordinate(
                    cep,
                    geocoder=geocoder,
                    country=country,
                )
            except Exception:
                _logger.warning(
                    "CEP geocoding failed, trying the full address",
                    exc_info=True,
                )
            else:
                if point is not None:
                    return point
        if address or city:
            parts = [address, city, sigla, country]
            try:
                result = await geocoder.geocode(
                    ", ".join(part for part in parts if part),
                )
            except Exception:
                _logger.warning(
                    "Address geocoding failed, falling back to the UF centroid",
                    exc_info=True,
                )
            else:
                if result is not None:
                    return result.coordinate
    if state is None:
        return None
    return UF_CENTROIDS[state]


def _uf_or_none(uf: UFEnum | str) -> UFEnum | None:
    """Return the federative unit ``uf`` names, or ``None`` when it names none.

    Args:
        uf (UFEnum | str): A member or a sigla (case-insensitive).

    Returns:
        UFEnum | None: The member, or ``None`` for an unknown sigla.
    """
    if isinstance(uf, UFEnum):
        return uf
    try:
        return normalize_uf(uf)
    except ValueError:
        return None


__all__: list[str] = [
    "CEP_PATTERN",
    "UF_CENTROIDS",
    "cep_to_coordinate",
    "extract_cep",
    "resolve_br_coordinate",
    "uf_centroid",
]
