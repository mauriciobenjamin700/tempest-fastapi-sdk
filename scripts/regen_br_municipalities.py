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
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from pathlib import Path

IBGE_URL: str = (
    "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
)
"""The IBGE localities API, one flat record per municipality."""

KELVINS_URL: str = (
    "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/"
    "municipios.csv"
)
"""Coordinates and capital flag, MIT-licensed."""

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
kelvins/municipios-brasileiros, under this license:

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


def _read(source: str | None, url: str) -> str:
    """Return a source's text, from a local file or the network.

    Args:
        source (str | None): A local path, or ``None`` to download.
        url (str): The URL to download from.

    Returns:
        str: The content, decoded as UTF-8.
    """
    if source is not None:
        return Path(source).read_text(encoding="utf-8-sig")
    with urllib.request.urlopen(url, timeout=120) as response:
        return str(response.read().decode("utf-8-sig"))


def build(ibge_text: str, kelvins_text: str) -> str:
    """Join the two sources into the generated module's source.

    Args:
        ibge_text (str): The IBGE JSON.
        kelvins_text (str): The kelvins CSV.

    Returns:
        str: The module source.

    Raises:
        ValueError: When the two sources do not list the same codes.
    """
    ibge = {int(row["municipio-id"]): row for row in json.loads(ibge_text)}
    kelvins = {
        int(row["codigo_ibge"]): row
        for row in csv.DictReader(io.StringIO(kelvins_text))
    }
    if set(ibge) != set(kelvins):
        raise ValueError(
            f"sources disagree: {sorted(set(ibge) ^ set(kelvins))[:10]} ..."
        )
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
                extra["latitude"],
                extra["longitude"],
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
    )


def main() -> None:
    """Fetch (or read) the sources and write the generated module."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ibge", help="Local copy of the IBGE JSON.")
    parser.add_argument("--kelvins", help="Local copy of the kelvins CSV.")
    args = parser.parse_args()
    source = build(_read(args.ibge, IBGE_URL), _read(args.kelvins, KELVINS_URL))
    TARGET.write_text(source, encoding="utf-8")
    print(f"wrote {TARGET} ({source.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
