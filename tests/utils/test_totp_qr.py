"""Tests for the provisioning QR codes in ``tempest_fastapi_sdk.utils.totp``.

Every rendering is read back to a module matrix and decoded with
``zxing-cpp`` (already a dev dependency, used by ``tests/pdf/test_qr.py``),
so the assertions prove the drawing carries the exact ``otpauth://`` URI
rather than comparing ``segno`` with itself.
"""

from __future__ import annotations

import base64
import io
import re
import sys
import xml.etree.ElementTree as ET

import pytest

from tempest_fastapi_sdk import (
    TOTPHelper,
    provisioning_qr_data_uri,
    provisioning_qr_terminal,
)
from tempest_fastapi_sdk.utils.totp import PROVISIONING_QR_BORDER

URI: str = (
    "otpauth://totp/Tempest:ana%40example.com"
    "?secret=5XSTSGJNLYDCI7I2MDN5XMV477Q5J47Y&issuer=Tempest"
)
"""A 95-character URI, the shape ``TOTPHelper`` emits for a 32-char secret."""

LONG_URI: str = TOTPHelper(issuer="Acme Corporation Admin").provisioning_uri(
    "5XSTSGJNLYDCI7I2MDN5XMV477Q5J47Y",
    "maria.silva.santos@empresa-exemplo.com.br",
)
"""A 159-character URI: long issuer and long e-mail."""

HALF_BLOCKS: dict[str, tuple[bool, bool]] = {
    "█": (True, True),
    "▀": (True, False),
    "▄": (False, True),
    " ": (False, False),
}


def _decode_matrix(dark: list[list[bool]]) -> list[str]:
    """Rasterize a module matrix and decode it with ``zxing-cpp``.

    Args:
        dark (list[list[bool]]): ``True`` for a dark module.

    Returns:
        list[str]: The decoded texts, one per symbol found.
    """
    zxingcpp = pytest.importorskip("zxingcpp")
    image_module = pytest.importorskip("PIL.Image")
    scale = 8
    pad = 4
    size = len(dark[0])
    image = image_module.new(
        "L", ((size + 2 * pad) * scale, (len(dark) + 2 * pad) * scale), 255
    )
    for y, row in enumerate(dark):
        for x, cell in enumerate(row):
            if cell:
                for dy in range(scale):
                    for dx in range(scale):
                        image.putpixel(
                            ((x + pad) * scale + dx, (y + pad) * scale + dy), 0
                        )
    return [barcode.text for barcode in zxingcpp.read_barcodes(image)]


def _terminal_to_dark(drawing: str, *, drawn_is_dark: bool) -> list[list[bool]]:
    """Expand half-block characters back into a module matrix.

    Args:
        drawing (str): The output of :func:`provisioning_qr_terminal`.
        drawn_is_dark (bool): Whether a drawn cell is a dark module.

    Returns:
        list[list[bool]]: ``True`` for a dark module.
    """
    rows: list[list[bool]] = []
    for line in drawing.split("\n"):
        top = [HALF_BLOCKS[char][0] == drawn_is_dark for char in line]
        bottom = [HALF_BLOCKS[char][1] == drawn_is_dark for char in line]
        rows.extend([top, bottom])
    return rows


def _svg_to_dark(svg: bytes) -> list[list[bool]]:
    """Read the ``segno`` SVG path back into a module matrix.

    ``segno`` draws each run of dark modules as a horizontal line
    (``h``) in module units under ``transform="scale(n)"``, moving with
    absolute ``M`` once and relative ``m`` afterwards.

    Args:
        svg (bytes): The decoded SVG document.

    Returns:
        list[list[bool]]: ``True`` for a dark module.
    """
    root = ET.fromstring(svg)
    namespace = "{http://www.w3.org/2000/svg}"
    path = root.find(f"{namespace}path")
    assert path is not None
    match = re.fullmatch(r"scale\((\d+)\)", path.get("transform", "scale(1)"))
    assert match is not None
    size = int(root.get("width", "0")) // int(match.group(1))
    dark = [[False] * size for _ in range(size)]
    x = 0.0
    y = 0.0
    for command, args in re.findall(r"([MmHh])([^MmHh]*)", path.get("d", "")):
        numbers = [float(n) for n in args.split()]
        if command == "M":
            x, y = numbers
        elif command == "m":
            x, y = x + numbers[0], y + numbers[1]
        else:
            for column in range(int(x), int(x + numbers[0])):
                dark[int(y)][column] = True
            x += numbers[0]
    return dark


class TestTerminal:
    def test_decodes_to_the_exact_uri_on_a_dark_terminal(self) -> None:
        drawing = provisioning_qr_terminal(URI)

        assert _decode_matrix(_terminal_to_dark(drawing, drawn_is_dark=False)) == [URI]

    def test_decodes_to_the_exact_uri_on_a_light_terminal(self) -> None:
        drawing = provisioning_qr_terminal(URI, background="light")

        assert _decode_matrix(_terminal_to_dark(drawing, drawn_is_dark=True)) == [URI]

    def test_light_background_draws_the_complement(self) -> None:
        dark = provisioning_qr_terminal(URI).split("\n")
        light = provisioning_qr_terminal(URI, background="light").split("\n")

        flip = {"█": " ", " ": "█", "▀": "▄", "▄": "▀"}
        assert ["".join(flip[c] for c in line) for line in dark[:-1]] == light[:-1]

    def test_uses_only_half_blocks_and_spaces(self) -> None:
        drawing = provisioning_qr_terminal(URI)

        assert set(drawing) <= {*HALF_BLOCKS, "\n"}
        assert not drawing.endswith("\n")

    def test_typical_uri_fits_80_columns(self) -> None:
        lines = provisioning_qr_terminal(URI).split("\n")

        assert len(URI) == 95
        assert {len(line) for line in lines} == {49}
        assert len(lines) == 25

    def test_long_uri_still_fits_80_columns(self) -> None:
        lines = provisioning_qr_terminal(LONG_URI).split("\n")

        assert len(LONG_URI) == 159
        assert {len(line) for line in lines} == {61}
        assert _decode_matrix(
            _terminal_to_dark("\n".join(lines), drawn_is_dark=False)
        ) == [LONG_URI]

    def test_quiet_zone_is_drawn_on_a_dark_terminal(self) -> None:
        lines = provisioning_qr_terminal(URI).split("\n")

        assert set(lines[0]) == {"█"}
        assert all(
            line[:PROVISIONING_QR_BORDER] == "█" * PROVISIONING_QR_BORDER
            for line in lines
        )

    def test_empty_uri_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            provisioning_qr_terminal("")

    def test_missing_segno_raises_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "segno", None)

        with pytest.raises(ImportError, match=r"\[mfa\]"):
            provisioning_qr_terminal(URI)


class TestDataUri:
    def test_svg_is_valid_xml_and_decodes_to_the_exact_uri(self) -> None:
        data_uri = provisioning_qr_data_uri(URI)

        prefix = "data:image/svg+xml;base64,"
        assert data_uri.startswith(prefix)
        svg = base64.b64decode(data_uri.removeprefix(prefix), validate=True)
        root = ET.fromstring(svg)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert root.get("width") == str((41 + 2 * PROVISIONING_QR_BORDER) * 5)
        assert _decode_matrix(_svg_to_dark(svg)) == [URI]

    def test_png_decodes_to_the_exact_uri(self) -> None:
        zxingcpp = pytest.importorskip("zxingcpp")
        image_module = pytest.importorskip("PIL.Image")
        data_uri = provisioning_qr_data_uri(URI, kind="png")

        prefix = "data:image/png;base64,"
        assert data_uri.startswith(prefix)
        image = image_module.open(
            io.BytesIO(base64.b64decode(data_uri.removeprefix(prefix)))
        )
        assert [barcode.text for barcode in zxingcpp.read_barcodes(image)] == [URI]

    def test_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="kind must be"):
            provisioning_qr_data_uri(URI, kind="gif")  # type: ignore[arg-type]

    def test_empty_uri_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            provisioning_qr_data_uri("")

    def test_missing_segno_raises_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "segno", None)

        with pytest.raises(ImportError, match=r"\[mfa\]"):
            provisioning_qr_data_uri(URI)
