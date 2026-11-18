"""Regenerate ``tempest_fastapi_sdk/geo/_br_states_geometry_data.py``.

One source: the **IBGE meshes API** (``servicodados.ibge.gov.br/api/v3/malhas``)
— the outline of every federative unit, at its lowest quality (about 5 000
points for the whole country). Public data. Each feature carries the state's
two-digit IBGE code (``codarea``), turned into its acronym through the
municipalities the SDK already ships (a municipality code starts with its
state's code).

Usage::

    uv run python scripts/regen_br_states_geometry.py
    uv run python scripts/regen_br_states_geometry.py --mesh br_uf.json

The second form reads a file already downloaded (``curl`` answers in under a
second here while Python's ``urllib`` can take half a minute to connect).
"""

from __future__ import annotations

import argparse
import json
import textwrap
import urllib.request
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.geo.municipalities import br_municipalities

MESH_URL: str = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
    "?formato=application/vnd.geo+json&qualidade=minima&intrarregiao=UF"
)
"""The IBGE meshes API: Brazil split by state, GeoJSON, lowest quality."""

TARGET: Path = (
    Path(__file__).resolve().parents[1]
    / "tempest_fastapi_sdk"
    / "geo"
    / "_br_states_geometry_data.py"
)
"""The generated module."""

RING_WIDTH: int = 80
"""Column at which a ring's points wrap, under the 88-column lint limit."""

HEADER: str = '''"""Brazilian state outlines, generated. Do not edit by hand.

Regenerate with ``uv run python scripts/regen_br_states_geometry.py``.

A line ``>UF`` opens a ring of that state; the lines after it hold the ring's
``lon,lat`` points, space-separated, wrapped at 80 columns. A state with
islands has more than one ring; no ring has holes, so the Federal District
lies inside the outline of Goiás too. Source: the IBGE meshes API at its lowest quality
(public data), coordinates as the API rounds them (four decimals).
"""

'''
"""Module docstring of the generated file."""


def _read(source: str | None) -> str:
    """Return the mesh GeoJSON, from a local file or the network.

    Args:
        source (str | None): A local path, or ``None`` to download.

    Returns:
        str: The GeoJSON text.
    """
    if source is not None:
        return Path(source).read_text(encoding="utf-8-sig")
    with urllib.request.urlopen(MESH_URL, timeout=120) as response:
        return str(response.read().decode("utf-8-sig"))


def _state_acronyms() -> dict[str, str]:
    """Map each state's two-digit IBGE code to its acronym.

    Returns:
        dict[str, str]: ``{"35": "SP", ...}``, from the shipped municipalities.
    """
    return {
        str(municipality.ibge_code)[:2]: municipality.uf.value
        for municipality in br_municipalities()
    }


def build(mesh_text: str) -> str:
    """Turn the mesh GeoJSON into the generated module's source.

    Args:
        mesh_text (str): The GeoJSON of the IBGE meshes API.

    Returns:
        str: The module source.

    Raises:
        ValueError: When a feature's code is not a known state, a ring has
            a hole, or not every state is present.
    """
    acronyms = _state_acronyms()
    rings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for feature in json.loads(mesh_text)["features"]:
        code = str(feature["properties"]["codarea"])
        if code not in acronyms:
            raise ValueError(f"unknown state code {code!r}")
        uf = acronyms[code]
        seen.add(uf)
        geometry: dict[str, Any] = feature["geometry"]
        polygons = (
            [geometry["coordinates"]]
            if geometry["type"] == "Polygon"
            else geometry["coordinates"]
        )
        for polygon in polygons:
            if len(polygon) != 1:
                raise ValueError(f"{uf} has a polygon with holes")
            points = " ".join(f"{lon},{lat}" for lon, lat in polygon[0])
            rings.append((uf, points))
    missing = sorted(set(acronyms.values()) - seen)
    if missing:
        raise ValueError(f"states missing from the mesh: {missing}")
    rings.sort(key=lambda ring: ring[0])
    lines: list[str] = []
    for uf, points in rings:
        lines.append(f">{uf}")
        lines.extend(textwrap.wrap(points, width=RING_WIDTH))
    body = "\n".join(lines)
    return (
        HEADER
        + 'BR_STATE_OUTLINES: str = """\\\n'
        + body
        + '\n"""\n'
        + '"""Every state ring, one line each; parsed once, on first use."""\n'
    )


def main() -> None:
    """Fetch (or read) the mesh and write the generated module."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh", help="Local copy of the IBGE mesh GeoJSON.")
    args = parser.parse_args()
    source = build(_read(args.mesh))
    TARGET.write_text(source, encoding="utf-8")
    print(f"wrote {TARGET} ({source.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
