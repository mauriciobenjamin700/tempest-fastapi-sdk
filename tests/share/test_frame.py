"""``ShareFrameRenderer``: both layouts, the QR inside them, the photo."""

import io
import sys
from functools import cache
from pathlib import Path

import pytest
import zxingcpp
from PIL import Image

from tempest_fastapi_sdk.share import (
    LAYOUTS,
    ShareFrameLayoutEnum,
    ShareFrameRenderer,
    ShareFrameSchema,
)
from tempest_fastapi_sdk.share import card as card_module
from tempest_fastapi_sdk.share.frame import _initials

pytestmark = pytest.mark.skipif(
    not any(
        Path(regular).is_file() for regular, _ in card_module.DEFAULT_FONT_CANDIDATES
    ),
    reason="no system TrueType font (install fonts-dejavu-core)",
)

URL: str = "https://app.buscar.com.br/s/42"
LONG: str = "Palavra " * 60


def _png(
    size: tuple[int, int], color: tuple[int, int, int, int], kind: str = "PNG"
) -> bytes:
    """Build a solid image.

    Args:
        size (tuple[int, int]): Width and height.
        color (tuple[int, int, int, int]): RGBA fill.
        kind (str): Pillow format.

    Returns:
        bytes: The encoded image.
    """
    buffer = io.BytesIO()
    image = Image.new("RGBA", size, color)
    (image.convert("RGB") if kind == "JPEG" else image).save(buffer, kind)
    return buffer.getvalue()


def _decode(png: bytes) -> list[str]:
    """Decode every barcode in ``png``.

    Args:
        png (bytes): The image.

    Returns:
        list[str]: Decoded texts.
    """
    return [r.text for r in zxingcpp.read_barcodes(Image.open(io.BytesIO(png)))]


def _frame(**overrides: str | bool | None) -> ShareFrameSchema:
    """Build a full frame with ``overrides`` applied.

    Args:
        **overrides (str | bool | None): Fields to replace.

    Returns:
        ShareFrameSchema: The frame.
    """
    values: dict[str, str | bool | None] = {
        "url": URL,
        "title": "Ana Souza",
        "subtitle": "Teresina → Fortaleza · sexta, 18h",
        "brand": "BusCar",
        "caption": "Escaneie e reserve sua vaga",
    }
    values.update(overrides)
    return ShareFrameSchema.model_validate(values)


@cache
def _renderer() -> ShareFrameRenderer:
    """Share one renderer with a logo across the module.

    Returns:
        ShareFrameRenderer: The renderer.
    """
    return ShareFrameRenderer(logo=_png((400, 400), (11, 61, 145, 255)))


@pytest.mark.parametrize("layout", list(ShareFrameLayoutEnum))
class TestLayouts:
    """Both layouts, every variation, the QR code still reads."""

    def test_size_and_qr(self, layout: ShareFrameLayoutEnum) -> None:
        """The canvas has the layout's size and the QR decodes to the URL."""
        png = _renderer().render(
            _frame(),
            layout=layout,
            photo=_png((600, 800), (200, 150, 120, 255), "JPEG"),
        )
        geometry = LAYOUTS[layout]
        assert Image.open(io.BytesIO(png)).size == (geometry.width, geometry.height)
        assert _decode(png) == [URL]

    def test_without_photo_or_logo(self, layout: ShareFrameLayoutEnum) -> None:
        """No photo draws the initials; no logo leaves the QR plain."""
        png = ShareFrameRenderer().render(_frame(brand=None), layout=layout)
        assert _decode(png) == [URL]

    def test_long_text_stays_inside(self, layout: ShareFrameLayoutEnum) -> None:
        """Long texts are cut; the side margins stay empty; the QR reads."""
        png = ShareFrameRenderer(background="#000000").render(
            _frame(title=LONG, subtitle=LONG, brand=LONG, caption=LONG), layout=layout
        )
        image = Image.open(io.BytesIO(png)).convert("RGB")
        geometry = LAYOUTS[layout]
        for box in (
            (0, 0, geometry.margin - 4, geometry.height),
            (geometry.width - geometry.margin + 4, 0, geometry.width, geometry.height),
        ):
            assert image.crop(box).getbbox() is None, box
        assert _decode(png) == [URL]

    def test_long_url_still_reads(self, layout: ShareFrameLayoutEnum) -> None:
        """A UUID link makes a denser code that still decodes."""
        url = "https://app.buscar.com.br/s/8f3c2a91-6b7e-4d2a-9c1f-2e5b7a0d4c63"
        png = _renderer().render(_frame(url=url), layout=layout)
        assert _decode(png) == [url]


class TestSchema:
    """What the frame accepts."""

    @pytest.mark.parametrize(
        "url", ["javascript:alert(1)", "/s/42", "app.buscar.com.br/s/42"]
    )
    def test_url_must_be_absolute_http(self, url: str) -> None:
        """The encoded URL is absolute ``http``/``https``."""
        with pytest.raises(ValueError):
            _frame(url=url)

    def test_one_line(self) -> None:
        """Line breaks collapse, as on the card."""
        assert _frame(title="Ana\nSouza").title == "Ana Souza"

    def test_show_url_false(self) -> None:
        """Without the written link the frame still renders and reads."""
        png = _renderer().render(
            _frame(show_url=False), layout=ShareFrameLayoutEnum.SQUARE
        )
        assert _decode(png) == [URL]


class TestPhoto:
    """The person's photo."""

    def test_bad_photo(self) -> None:
        """Bytes that are not an image are refused."""
        with pytest.raises(ValueError, match="photo"):
            _renderer().render(_frame(), photo=b"not an image")

    @pytest.mark.parametrize(
        ("name", "expected"),
        [("Ana Souza", "AS"), ("ana", "A"), ("Ana Maria de Souza", "AS"), ("", "?")],
    )
    def test_initials(self, name: str, expected: str) -> None:
        """Initials of the first and last words."""
        assert _initials(name) == expected

    def test_photo_is_round(self) -> None:
        """Outside the ring the photo's color does not show."""
        red = _png((500, 500), (255, 0, 0, 255))
        png = ShareFrameRenderer(background="#000000", accent_color="#00ff00").render(
            _frame(), layout=ShareFrameLayoutEnum.STORY, photo=red
        )
        image = Image.open(io.BytesIO(png)).convert("RGB")
        geometry = LAYOUTS[ShareFrameLayoutEnum.STORY]
        left = (geometry.width - geometry.photo_diameter) // 2
        center = (geometry.width // 2, geometry.people_y + geometry.photo_diameter // 2)
        assert image.getpixel(center) == (255, 0, 0)
        assert image.getpixel((left + 2, geometry.people_y + 2)) == (0, 0, 0)


def test_without_segno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without segno the error names the ``[image]`` extra."""
    monkeypatch.setitem(sys.modules, "segno", None)
    with pytest.raises(ImportError, match=r"\[image\]"):
        ShareFrameRenderer().render(_frame())
