"""The card renderer: size, fit, fonts, logo and the missing extra."""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

from tempest_fastapi_sdk.share import (
    OG_IMAGE_HEIGHT,
    OG_IMAGE_WIDTH,
    ShareCardLineSchema,
    ShareCardRenderer,
    ShareCardSchema,
)
from tempest_fastapi_sdk.share import card as card_module
from tempest_fastapi_sdk.share.card import MARGIN

pytestmark = pytest.mark.skipif(
    not any(
        Path(regular).is_file() for regular, _ in card_module.DEFAULT_FONT_CANDIDATES
    ),
    reason="no system TrueType font (install fonts-dejavu-core)",
)


def _decode(png: bytes) -> Image.Image:
    """Open rendered bytes as an image.

    Args:
        png (bytes): The PNG.

    Returns:
        Image.Image: The decoded image.
    """
    return Image.open(io.BytesIO(png))


def _logo_png() -> bytes:
    """Build a small red PNG with transparency.

    Returns:
        bytes: The PNG.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(buffer, "PNG")
    return buffer.getvalue()


class TestRender:
    """What comes out of :meth:`ShareCardRenderer.render`."""

    def test_png_of_open_graph_size(self) -> None:
        """A PNG of 1200x630."""
        png = ShareCardRenderer().render(ShareCardSchema(title="Carona"))
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert _decode(png).size == (OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT)

    def test_same_card_same_bytes(self) -> None:
        """Rendering is deterministic within one process."""
        renderer = ShareCardRenderer()
        card = ShareCardSchema(title="Teresina → Fortaleza", chips=["3 vagas"])
        assert renderer.render(card) == renderer.render(card)

    def test_long_text_stays_inside_margins(self) -> None:
        """Nothing is drawn in the right margin, however long the text."""
        background = "#000000"
        renderer = ShareCardRenderer(background=background)
        long = "Palavra " * 80
        png = renderer.render(
            ShareCardSchema(
                brand=long,
                kicker=long,
                title=long,
                lines=[ShareCardLineSchema(icon="→", text=long)] * 10,
                chips=[long, long],
                footer=long,
            )
        )
        image = _decode(png).convert("RGB")
        margins = (
            (OG_IMAGE_WIDTH - MARGIN + 4, 0, OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT),
            (0, 0, MARGIN - 4, OG_IMAGE_HEIGHT),
            (0, OG_IMAGE_HEIGHT - 20, OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT),
        )
        for box in margins:
            assert image.crop(box).getbbox() is None, box

    def test_wide_logo_stays_inside(self) -> None:
        """A banner-shaped logo is capped and the brand still fits."""
        buffer = io.BytesIO()
        Image.new("RGBA", (1000, 10), (255, 0, 0, 255)).save(buffer, "PNG")
        renderer = ShareCardRenderer(background="#000000", logo=buffer.getvalue())
        png = renderer.render(ShareCardSchema(brand="BusCar", title="x"))
        image = _decode(png).convert("RGB")
        assert image.crop((0, 0, MARGIN - 4, OG_IMAGE_HEIGHT)).getbbox() is None
        red = image.getpixel((OG_IMAGE_WIDTH - MARGIN - 10, 50))
        assert red == (255, 0, 0)
        assert image.getpixel((OG_IMAGE_WIDTH - MARGIN - 360, 50)) != (255, 0, 0)

    def test_logo_is_drawn(self) -> None:
        """The logo lands at the top right."""
        png = ShareCardRenderer(background="#000000", logo=_logo_png()).render(
            ShareCardSchema(title="x")
        )
        pixel = _decode(png).convert("RGB").getpixel((OG_IMAGE_WIDTH - MARGIN - 10, 80))
        assert pixel == (255, 0, 0)


class TestOneLine:
    """Line breaks never reach Pillow, which refuses to measure them."""

    @pytest.mark.parametrize("field", ["title", "brand", "kicker", "footer"])
    def test_card_field(self, field: str) -> None:
        """A ``\\n`` in a card text renders instead of raising."""
        card = ShareCardSchema.model_validate(
            {"title": "x", field: "linha um\nlinha   dois\r\n"}
        )
        assert getattr(card, field) == "linha um linha dois"
        assert ShareCardRenderer().render(card).startswith(b"\x89PNG")

    def test_line_and_chip(self) -> None:
        """Lines and chips are collapsed too; empty chips disappear."""
        card = ShareCardSchema(
            title="x",
            lines=[ShareCardLineSchema(text="a\nb", icon="\n")],
            chips=["c\nd", "  ", ""],
        )
        assert card.lines[0].text == "a b"
        assert card.chips == ["c d"]
        assert ShareCardRenderer().render(card).startswith(b"\x89PNG")

    def test_guard_fires_on_raw_newline(self) -> None:
        """Pillow does raise on the shape the schema now prevents."""
        from PIL import ImageDraw

        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        with pytest.raises(ValueError, match="multiline"):
            draw.textlength("a\nb")


class TestFingerprint:
    """The fingerprint follows what changes the drawing."""

    def test_changes_with_color(self) -> None:
        """Another accent color, another fingerprint."""
        assert (
            ShareCardRenderer(accent_color="#ff0000").fingerprint
            != ShareCardRenderer(accent_color="#00ff00").fingerprint
        )

    def test_changes_with_logo(self) -> None:
        """A logo changes the fingerprint."""
        assert (
            ShareCardRenderer().fingerprint
            != ShareCardRenderer(logo=_logo_png()).fingerprint
        )

    def test_stable(self) -> None:
        """Same arguments, same fingerprint."""
        assert ShareCardRenderer().fingerprint == ShareCardRenderer().fingerprint


class TestConstruction:
    """Errors surface when the renderer is built, not at the first share."""

    def test_unknown_color(self) -> None:
        """A color Pillow does not parse names the argument."""
        with pytest.raises(ValueError, match="accent_color"):
            ShareCardRenderer(accent_color="not-a-color")

    def test_bad_logo(self) -> None:
        """Bytes that are not an image are refused."""
        with pytest.raises(ValueError, match="logo"):
            ShareCardRenderer(logo=b"not an image")

    def test_missing_explicit_font(self, tmp_path: Path) -> None:
        """An explicit font path that does not exist is an error."""
        with pytest.raises(FileNotFoundError, match="font file not found"):
            ShareCardRenderer(font_path=tmp_path / "missing.ttf")

    def test_no_system_font(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without a candidate the error says how to install one."""
        monkeypatch.setattr(
            card_module,
            "DEFAULT_FONT_CANDIDATES",
            ((str(tmp_path / "a.ttf"), str(tmp_path / "b.ttf")),),
        )
        with pytest.raises(FileNotFoundError, match="fonts-dejavu-core"):
            ShareCardRenderer()

    def test_explicit_font(self) -> None:
        """An explicit font is used for both weights."""
        regular = Path(
            next(r for r, _ in card_module.DEFAULT_FONT_CANDIDATES if Path(r).is_file())
        )
        png = ShareCardRenderer(font_path=regular).render(ShareCardSchema(title="x"))
        assert _decode(png).size == (OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT)

    def test_without_pillow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without Pillow the error names the ``[image]`` extra."""
        monkeypatch.setitem(sys.modules, "PIL", None)
        with pytest.raises(ImportError, match=r"\[image\]"):
            ShareCardRenderer()
