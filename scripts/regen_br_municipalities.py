"""Regenerate ``tempest_fastapi_sdk/geo/_br_municipalities_data.py``.

Two sources, joined by the IBGE municipality code:

* **IBGE** (``servicodados.ibge.gov.br/api/v1/localidades/municipios``) —
  the official name and the state of every municipality. Public data.
* **kelvins/municipios-brasileiros** (MIT) — latitude, longitude and the
  capital flag. When its name differs from the IBGE one (an older spelling,
  ``Açu`` for ``Assú``), it is kept as an alias.

Usage::

    uv run python scripts/regen_br_municipalities.py
    uv run python scripts/regen_br_municipalities.py \\
        --ibge ibge.json --kelvins municipios.csv

The second form reads files already downloaded (``curl`` answers in under a
second here while Python's ``urllib`` can take half a minute to connect).

The kelvins file is **pinned**: read from commit :data:`KELVINS_COMMIT`, never
from ``main``, and refused unless its SHA-256 is :data:`KELVINS_CSV_SHA256`
(its ``LICENSE`` likewise, :data:`KELVINS_LICENSE_SHA256`). The download is
untrusted input either way, local copy included: size-capped
(:data:`MAX_SOURCE_BYTES`), parsed as strict CSV (:func:`parse_kelvins`) and
never evaluated.

Five seats of that file lie outside their own municipality (measured against
the IBGE 2022 mesh, see :data:`SEAT_CENTROID_FALLBACK`). Those rows ship the
territory centroid instead and are listed in the generated
``CENTROID_FALLBACK_IBGE_CODES``, the same five, with the same coordinates, as
``centroidFallback`` in tempest-react-sdk's ``src/br/data/br-seats.json``
(tempest-react-sdk#677).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.request
from pathlib import Path

IBGE_URL: str = (
    "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
)
"""The IBGE localities API, one flat record per municipality."""

KELVINS_COMMIT: str = "503e2f70bbf1b4b7ec0b1f68b09086ccc38fe861"
"""The kelvins/municipios-brasileiros commit every coordinate is read from.

The same commit tempest-react-sdk's ``scripts/gen-br-seats.mjs`` pins, so both
SDKs answer the same point for a municipality.
"""

KELVINS_CSV_SHA256: str = (
    "22bd03d004361329f69ddfa3f6c99cd53012aa53972a172e10b141288ad0b1fe"
)
"""SHA-256 of ``csv/municipios.csv`` at :data:`KELVINS_COMMIT`."""

KELVINS_LICENSE_SHA256: str = (
    "c8254a78fe3edcb841e0df79bcd217e2626c2a13f9374dd8a51729d88dc0bc88"
)
"""SHA-256 of ``LICENSE`` at :data:`KELVINS_COMMIT` (MIT, quoted in the header)."""

KELVINS_RAW: str = (
    f"https://raw.githubusercontent.com/kelvins/municipios-brasileiros/{KELVINS_COMMIT}"
)
"""Raw-file base URL of the pinned commit."""

KELVINS_URL: str = f"{KELVINS_RAW}/csv/municipios.csv"
"""Coordinates and capital flag, MIT-licensed, at the pinned commit."""

KELVINS_LICENSE_URL: str = f"{KELVINS_RAW}/LICENSE"
"""The license of :data:`KELVINS_URL`, at the pinned commit."""

MAX_SOURCE_BYTES: int = 20_000_000
"""Upper bound on any source read; the pinned CSV is 390 KB, the IBGE JSON ~2 MB."""

KELVINS_HEADER: str = (
    "codigo_ibge,nome,latitude,longitude,capital,codigo_uf,siafi_id,ddd,fuso_horario"
)
"""The only header :func:`parse_kelvins` accepts, column for column."""

IBGE_CODE: re.Pattern[str] = re.compile(r"\d{7}")
"""A municipality code: exactly seven digits."""

COORDINATE: re.Pattern[str] = re.compile(r"-?\d{1,2}(\.\d{1,10})?")
"""A plain decimal degree, the only number format the pinned CSV uses."""

SEAT_CENTROID_FALLBACK: dict[int, tuple[float, float]] = {
    1505551: (-7.7391, -50.1207),
    2613107: (-8.3276, -36.1505),
    3164431: (-21.0225, -42.5975),
    4105201: (-24.8778, -49.2846),
    4128625: (-23.5358, -53.824),
}
"""``(latitude, longitude)`` shipped instead of the source seat, by IBGE code.

Ported from tempest-react-sdk's ``src/br/data/br-seats.json`` (PR #679,
``centroidFallback``): the area-weighted centroid of each municipality's IBGE
2022 territory, as that repository computes it in ``gen-br-geodata.mjs``.

The kelvins seat of these five is outside the municipality's own boundary.
Measured point-in-polygon over all 5571 seats against the IBGE 2022 mesh at
full resolution (``/api/v3/malhas/estados/<UF>?intrarregiao=municipio&
qualidade=maxima``, 27 requests; distance to the nearest edge, equirectangular):
17 seats fall outside, 12 of them within 1 km of the edge (a town on a river or
the coast), and these five far beyond it:

* 1505551 Pau D'Arco (PA): 749.67 km, near Belém;
* 4105201 Cerro Azul (PR): 359.66 km;
* 4128625 Alto Paraíso (PR): 287.65 km;
* 3164431 São Sebastião da Vargem Alegre (MG): 157.07 km;
* 2613107 São Caitano (PE): 7.48 km outside its boundary.

Each replacement point is inside its municipality on the same mesh.
"""

TARGET: Path = (
    Path(__file__).resolve().parents[1]
    / "tempest_fastapi_sdk"
    / "geo"
    / "_br_municipalities_data.py"
)
"""The generated module."""

HEADER: str = '''"""Brazilian municipalities, generated. Do not edit by hand.

Regenerate with ``uv run python scripts/regen_br_municipalities.py``.

One CSV row per municipality: ``ibge_code,name,uf,latitude,longitude,capital,
aliases`` (aliases separated by ``|``). The name and the state come from the
IBGE localities API (public data); latitude, longitude, the capital flag and
the aliases (an older spelling the IBGE no longer uses) come from
kelvins/municipios-brasileiros at the pinned commit ``KELVINS_COMMIT``, except
for the municipalities in ``CENTROID_FALLBACK_IBGE_CODES``, whose source seat
lies outside the municipality and which carry the territory centroid instead.
The kelvins data is used under this license:

    MIT License

    Copyright (c) 2016 Kelvin S. do Prado

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the
    following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
    NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
    DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
    OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
    USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

'''
"""Module docstring of the generated file, license notice included."""


def _read(source: str | None, url: str, sha256: str | None = None) -> str:
    """Return a source's text, from a local file or the network, as untrusted bytes.

    Args:
        source (str | None): A local path, or ``None`` to download.
        url (str): The URL to download from.
        sha256 (str | None): The digest the bytes must have, or ``None`` for
            a source that cannot be pinned (the live IBGE API).

    Returns:
        str: The content, decoded as UTF-8 (a leading BOM dropped).

    Raises:
        ValueError: When the source is larger than :data:`MAX_SOURCE_BYTES`
            or its SHA-256 is not ``sha256``.
    """
    if source is not None:
        with Path(source).open("rb") as handle:
            raw = handle.read(MAX_SOURCE_BYTES + 1)
    else:
        with urllib.request.urlopen(url, timeout=120) as response:
            raw = response.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError(f"{url}: larger than {MAX_SOURCE_BYTES} bytes")
    digest = hashlib.sha256(raw).hexdigest()
    if sha256 is not None and digest != sha256:
        raise ValueError(f"{url}: sha256 {digest}, pinned {sha256}")
    return raw.decode("utf-8-sig")


def parse_kelvins(text: str) -> dict[int, dict[str, str]]:
    """Parse the kelvins CSV strictly, keyed by IBGE code.

    The pinned file has no quoted field, so a quote, a header other than
    :data:`KELVINS_HEADER`, a row that is not nine columns, a code that is not
    seven digits, a duplicate code, a coordinate that is not a plain decimal or
    lies outside Brazil's bounding box, or a capital flag other than ``0``/``1``
    is an error: anything else would be guessing at a format the file does not
    use.

    Args:
        text (str): The CSV.

    Returns:
        dict[int, dict[str, str]]: Each row as column name to cell.

    Raises:
        ValueError: On the first row that breaks the format.
    """
    lines = [line for line in text.splitlines() if line]
    if not lines or lines[0] != KELVINS_HEADER:
        raise ValueError(f"unexpected header: {lines[0] if lines else ''!r}")
    columns = KELVINS_HEADER.split(",")
    rows: dict[int, dict[str, str]] = {}
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split(",")
        if '"' in line or len(cells) != len(columns):
            raise ValueError(f"line {number}: not a plain {len(columns)}-column row")
        row = dict(zip(columns, cells, strict=True))
        if not IBGE_CODE.fullmatch(row["codigo_ibge"]):
            raise ValueError(f"line {number}: code {row['codigo_ibge']!r}")
        latitude, longitude = row["latitude"], row["longitude"]
        if not (COORDINATE.fullmatch(latitude) and COORDINATE.fullmatch(longitude)):
            raise ValueError(f"line {number}: coordinate {latitude!r}, {longitude!r}")
        if not (-34 <= float(latitude) <= 6 and -74 <= float(longitude) <= -28):
            raise ValueError(f"line {number}: {latitude}, {longitude} outside Brazil")
        if row["capital"] not in {"0", "1"}:
            raise ValueError(f"line {number}: capital {row['capital']!r}")
        code = int(row["codigo_ibge"])
        if code in rows:
            raise ValueError(f"line {number}: duplicate {code}")
        rows[code] = row
    return rows


def build(ibge_text: str, kelvins_text: str) -> str:
    """Join the two sources into the generated module's source.

    Args:
        ibge_text (str): The IBGE JSON.
        kelvins_text (str): The kelvins CSV.

    Returns:
        str: The module source.

    Raises:
        ValueError: When the two sources do not list the same codes, or a
            code of :data:`SEAT_CENTROID_FALLBACK` is not among them.
    """
    ibge = {int(row["municipio-id"]): row for row in json.loads(ibge_text)}
    kelvins = parse_kelvins(kelvins_text)
    if set(ibge) != set(kelvins):
        raise ValueError(
            f"sources disagree: {sorted(set(ibge) ^ set(kelvins))[:10]} ..."
        )
    unknown = sorted(set(SEAT_CENTROID_FALLBACK) - set(ibge))
    if unknown:
        raise ValueError(f"fallback codes not in the sources: {unknown}")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for code in sorted(ibge):
        official = ibge[code]
        extra = kelvins[code]
        name = str(official["municipio-nome"])
        alias = str(extra["nome"])
        writer.writerow(
            [
                code,
                name,
                official["UF-sigla"],
                *(
                    map(repr, SEAT_CENTROID_FALLBACK[code])
                    if code in SEAT_CENTROID_FALLBACK
                    else (extra["latitude"], extra["longitude"])
                ),
                extra["capital"],
                alias if alias != name else "",
            ]
        )
    body = out.getvalue()
    return (
        HEADER
        + 'BR_MUNICIPALITIES_CSV: str = """\\\n'
        + body
        + '"""\n'
        + '"""Every municipality, one CSV row each; parsed once, on first use."""\n'
        + "\n"
        + f'KELVINS_COMMIT: str = "{KELVINS_COMMIT}"\n'
        + '"""The kelvins/municipios-brasileiros commit the coordinates come from."""\n'
        + "\n"
        + "KELVINS_CSV_SHA256: str = (\n"
        + f'    "{KELVINS_CSV_SHA256}"\n'
        + ")\n"
        + '"""SHA-256 of the kelvins CSV at ``KELVINS_COMMIT``."""\n'
        + "\n"
        + "CENTROID_FALLBACK_IBGE_CODES: frozenset[int] = frozenset(\n"
        + "    {\n"
        + "".join(f"        {code},\n" for code in sorted(SEAT_CENTROID_FALLBACK))
        + "    }\n"
        + ")\n"
        + '"""Municipalities whose row holds the territory centroid, not the seat.\n'
        + "\n"
        + "The source places their seat outside the municipality itself; see\n"
        + "``SEAT_CENTROID_FALLBACK`` in ``scripts/regen_br_municipalities.py``.\n"
        + '"""\n'
    )


def main() -> None:
    """Fetch (or read) the sources and write the generated module."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ibge", help="Local copy of the IBGE JSON.")
    parser.add_argument("--kelvins", help="Local copy of the kelvins CSV.")
    args = parser.parse_args()
    if args.kelvins is None:
        _read(None, KELVINS_LICENSE_URL, KELVINS_LICENSE_SHA256)
    source = build(
        _read(args.ibge, IBGE_URL),
        _read(args.kelvins, KELVINS_URL, KELVINS_CSV_SHA256),
    )
    TARGET.write_text(source, encoding="utf-8")
    print(f"wrote {TARGET} ({source.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
