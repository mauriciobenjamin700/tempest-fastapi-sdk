"""Brazil-specific geolocation helpers built on the SDK's BR datasets.

Two conveniences for Brazilian services:

* :func:`uf_centroid` — an **offline** approximate geographic centre for
  each of the 27 federative units (no network, no dataset download). Good
  for a coarse "somewhere in this state" pin or a map default.
* :func:`cep_to_coordinate` — resolve a Brazilian postal code (CEP) to a
  coordinate through any injected
  :class:`~tempest_fastapi_sdk.geo.GeocodingBackend` (e.g. Nominatim).

City-level centroids are intentionally not shipped — they need a
municipality coordinate dataset the SDK does not bundle; geocode the city
name via a :class:`GeocodingBackend` when you need that precision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tempest_fastapi_sdk.geo.schemas import Coordinate
from tempest_fastapi_sdk.utils import UF, normalize_uf

if TYPE_CHECKING:
    from tempest_fastapi_sdk.geo.geocoding import GeocodingBackend

# Approximate geographic centre of each Brazilian federative unit
# (decimal degrees, WGS84). Coarse by design — a state-level pin, not a
# survey point.
UF_CENTROIDS: dict[UF, Coordinate] = {
    UF.AC: Coordinate(latitude=-9.0, longitude=-70.5),
    UF.AL: Coordinate(latitude=-9.55, longitude=-36.6),
    UF.AP: Coordinate(latitude=1.4, longitude=-51.8),
    UF.AM: Coordinate(latitude=-3.9, longitude=-63.0),
    UF.BA: Coordinate(latitude=-12.5, longitude=-41.7),
    UF.CE: Coordinate(latitude=-5.1, longitude=-39.5),
    UF.DF: Coordinate(latitude=-15.78, longitude=-47.9),
    UF.ES: Coordinate(latitude=-19.6, longitude=-40.7),
    UF.GO: Coordinate(latitude=-15.9, longitude=-49.7),
    UF.MA: Coordinate(latitude=-5.1, longitude=-45.2),
    UF.MT: Coordinate(latitude=-13.0, longitude=-55.9),
    UF.MS: Coordinate(latitude=-20.5, longitude=-54.7),
    UF.MG: Coordinate(latitude=-18.5, longitude=-44.5),
    UF.PA: Coordinate(latitude=-4.0, longitude=-52.9),
    UF.PB: Coordinate(latitude=-7.2, longitude=-36.7),
    UF.PR: Coordinate(latitude=-24.6, longitude=-51.6),
    UF.PE: Coordinate(latitude=-8.4, longitude=-37.9),
    UF.PI: Coordinate(latitude=-7.4, longitude=-42.5),
    UF.RJ: Coordinate(latitude=-22.25, longitude=-42.7),
    UF.RN: Coordinate(latitude=-5.8, longitude=-36.6),
    UF.RS: Coordinate(latitude=-30.0, longitude=-53.5),
    UF.RO: Coordinate(latitude=-10.9, longitude=-63.4),
    UF.RR: Coordinate(latitude=2.1, longitude=-61.4),
    UF.SC: Coordinate(latitude=-27.2, longitude=-50.5),
    UF.SP: Coordinate(latitude=-22.2, longitude=-48.7),
    UF.SE: Coordinate(latitude=-10.6, longitude=-37.4),
    UF.TO: Coordinate(latitude=-10.2, longitude=-48.3),
}


def uf_centroid(uf: UF | str) -> Coordinate:
    """Return the approximate geographic centre of a Brazilian state.

    Args:
        uf: A :class:`~tempest_fastapi_sdk.utils.UF` member or a state sigla
            (case-insensitive, e.g. ``"sp"``).

    Returns:
        The state's approximate centre as a :class:`Coordinate`.

    Raises:
        ValueError: If ``uf`` is not a valid federative unit.
    """
    normalized = normalize_uf(uf) if not isinstance(uf, UF) else uf.value
    if normalized is None:
        raise ValueError(f"invalid UF: {uf!r}")
    return UF_CENTROIDS[UF(normalized)]


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


__all__: list[str] = [
    "UF_CENTROIDS",
    "cep_to_coordinate",
    "uf_centroid",
]
