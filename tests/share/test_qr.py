"""``render_qr``: level ``H``, logo in the middle, the measured ceiling."""

import io
import sys

import pytest
import zxingcpp
from PIL import Image

from tempest_fastapi_sdk.share import (
    DEFAULT_QR_LOGO_FRACTION,
    MAX_QR_LOGO_FRACTION,
    render_qr,
)

URLS: tuple[str, ...] = (
    "https://app.buscar.com.br/s/42",
    "https://app.buscar.com.br/s/8f3c2a91-6b7e-4d2a-9c1f-2e5b7a0d4c63",
)
"""The two lengths the ceiling was measured on (QR versions 4 and 7)."""


def _logo() -> bytes:
    """Build an opaque square logo, the worst case for the reader.

    Returns:
        bytes: The PNG.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (400, 400), (11, 61, 145, 255)).save(buffer, "PNG")
    return buffer.getvalue()


def _decode(png: bytes) -> list[str]:
    """Decode every barcode in ``png``.

    Args:
        png (bytes): The image.

    Returns:
        list[str]: Decoded texts.
    """
    return [
        result.text for result in zxingcpp.read_barcodes(Image.open(io.BytesIO(png)))
    ]


class TestDecodes:
    """The output reads, with and without a logo."""

    @pytest.mark.parametrize("url", URLS)
    def test_without_logo(self, url: str) -> None:
        """A plain QR code decodes to the URL."""
        assert _decode(render_qr(url)) == [url]

    @pytest.mark.parametrize("url", URLS)
    @pytest.mark.parametrize(
        "fraction", [DEFAULT_QR_LOGO_FRACTION, MAX_QR_LOGO_FRACTION]
    )
    def test_with_logo(self, url: str, fraction: float) -> None:
        """At the default and at the ceiling the QR code still decodes."""
        assert _decode(render_qr(url, logo=_logo(), logo_fraction=fraction)) == [url]

    def test_short_text_is_not_micro_qr(self) -> None:
        """Very short text stays a regular QR code that phones read."""
        assert _decode(render_qr("42")) == ["42"]

    def test_ceiling_is_where_reading_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Past the ceiling the short link stops decoding, as measured.

        Args:
            monkeypatch (pytest.MonkeyPatch): Lifts the ceiling so 0.40
                can be drawn.
        """
        from tempest_fastapi_sdk.share import qr as qr_module

        monkeypatch.setattr(qr_module, "MAX_QR_LOGO_FRACTION", 0.5)
        png = render_qr(URLS[0], logo=_logo(), logo_fraction=0.40)
        assert _decode(png) == []


class TestArguments:
    """Bad arguments fail before drawing."""

    @pytest.mark.parametrize("fraction", [0, -0.1, MAX_QR_LOGO_FRACTION + 0.01])
    def test_fraction_out_of_range(self, fraction: float) -> None:
        """The fraction is capped at the measured ceiling."""
        with pytest.raises(ValueError, match="logo_fraction"):
            render_qr(URLS[0], logo=_logo(), logo_fraction=fraction)

    def test_bad_logo(self) -> None:
        """A logo that is not an image is refused."""
        with pytest.raises(ValueError, match="logo"):
            render_qr(URLS[0], logo=b"not an image")

    @pytest.mark.parametrize(("scale", "border"), [(0, 4), (10, -1)])
    def test_scale_and_border(self, scale: int, border: int) -> None:
        """Scale below 1 or a negative border is refused."""
        with pytest.raises(ValueError):
            render_qr(URLS[0], scale=scale, border=border)

    def test_without_segno(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without segno the error names the ``[image]`` extra."""
        monkeypatch.setitem(sys.modules, "segno", None)
        with pytest.raises(ImportError, match=r"\[image\]"):
            render_qr(URLS[0])
