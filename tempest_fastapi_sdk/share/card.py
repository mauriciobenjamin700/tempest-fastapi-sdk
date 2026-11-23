"""The 1200x630 PNG a link preview shows.

:class:`ShareCardRenderer` draws a :class:`~tempest_fastapi_sdk.share.ShareCardSchema`
with Pillow (the ``[image]`` extra). Fonts and logo are resolved when the
renderer is built, so a missing font fails at boot and not at the first
share. There is no silent fallback to Pillow's bundled font: measured on
Pillow 13.0.0, that font draws ``ã`` and ``→`` with the same box as the
missing-glyph character, so a Portuguese preview would come out as
squares without any error.
"""

import hashlib
import io
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.share.schemas import ShareCardSchema

if TYPE_CHECKING:
    from PIL.Image import Image as PillowImage
    from PIL.ImageDraw import ImageDraw as PillowDraw
    from PIL.ImageFont import FreeTypeFont

OG_IMAGE_WIDTH: int = 1200
"""Width Open Graph recommends for ``summary_large_image`` previews."""

OG_IMAGE_HEIGHT: int = 630
"""Height Open Graph recommends for ``summary_large_image`` previews."""

DEFAULT_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
)
"""System font pairs (regular, bold) tried in order: Debian/Ubuntu
(``fonts-dejavu-core``), Fedora, Arch, then Liberation."""

MARGIN: int = 72
"""Horizontal and vertical margin of the card, in pixels."""

LOGO_HEIGHT: int = 72
"""Height the logo is scaled to, keeping its aspect ratio."""

MAX_LOGO_WIDTH: int = 352
"""Widest the scaled logo may be, a third of the usable width; a wider
logo is scaled down further and ends up shorter than :data:`LOGO_HEIGHT`."""

MAX_LOGO_PIXELS: int = 25_000_000
"""Largest logo, in pixels, decoded before scaling."""


def _resolve_fonts(
    font_path: Path | None, bold_font_path: Path | None
) -> tuple[Path, Path]:
    """Pick the regular and bold font files.

    An explicit path wins; a missing explicit path is an error rather
    than a cue to look elsewhere, because a typo there would otherwise
    render with a different font in production than in review.

    Args:
        font_path (Path | None): Regular font chosen by the caller.
        bold_font_path (Path | None): Bold font chosen by the caller;
            ``None`` reuses the regular one when ``font_path`` is set.

    Returns:
        tuple[Path, Path]: Regular and bold font files.

    Raises:
        FileNotFoundError: When an explicit path does not exist, or when
            none is given and no system candidate is installed.
    """
    if font_path is not None:
        bold = bold_font_path or font_path
        for path in (font_path, bold):
            if not path.is_file():
                raise FileNotFoundError(f"font file not found: {path}")
        return font_path, bold
    for regular, bold_candidate in DEFAULT_FONT_CANDIDATES:
        regular_path = Path(regular)
        if regular_path.is_file():
            bold_path = Path(bold_candidate)
            return regular_path, bold_path if bold_path.is_file() else regular_path
    raise FileNotFoundError(
        "no TrueType font found for the share card. Install one "
        "(`apt-get install fonts-dejavu-core` on Debian/Ubuntu) or pass "
        "font_path= to ShareCardRenderer."
    )


def _read_logo(logo: bytes | Path | None) -> bytes | None:
    """Load the logo bytes.

    Args:
        logo (bytes | Path | None): Image bytes, a file, or nothing.

    Returns:
        bytes | None: The bytes, or ``None`` without a logo.
    """
    if logo is None:
        return None
    if isinstance(logo, Path):
        return logo.read_bytes()
    return logo


class ShareCardRenderer:
    """Draws share cards as 1200x630 PNG.

    Layout, top to bottom: ``brand`` (accent) with the logo at the right,
    ``kicker`` (muted), ``title`` (bold, shrinks from 72 to 48 px, wraps
    to two lines, then is cut with ``…``), the ``lines`` (icon in the
    accent color + text), the ``chips`` and the ``footer``. Text never
    leaves the margins: what does not fit shrinks, is cut, or — for
    lines and chips — is dropped.

    Rendering is CPU-bound and synchronous; the share router calls
    :meth:`render` through :func:`asyncio.to_thread`.

    Attributes:
        width (int): Image width, :data:`OG_IMAGE_WIDTH`.
        height (int): Image height, :data:`OG_IMAGE_HEIGHT`.
    """

    width: int = OG_IMAGE_WIDTH
    height: int = OG_IMAGE_HEIGHT

    def __init__(
        self,
        *,
        background: str | tuple[str, str] = ("#0b3d91", "#061f4a"),
        text_color: str = "#ffffff",
        muted_color: str = "#c7d4f0",
        accent_color: str = "#ffd166",
        chip_color: str = "#ffffff",
        chip_text_color: str = "#0b3d91",
        logo: bytes | Path | None = None,
        font_path: Path | None = None,
        bold_font_path: Path | None = None,
    ) -> None:
        """Validate colors, load the fonts and decode the logo.

        Args:
            background (str | tuple[str, str]): One color, or the top and
                bottom of a vertical gradient. Any color Pillow parses
                (``"#0b3d91"``, ``"rgb(11, 61, 145)"``, ``"navy"``).
            text_color (str): Title and line text.
            muted_color (str): Kicker and footer.
            accent_color (str): Brand and line icons.
            chip_color (str): Chip fill.
            chip_text_color (str): Chip text.
            logo (bytes | Path | None): PNG/JPEG/WebP drawn at the top
                right, scaled to :data:`LOGO_HEIGHT`; transparency is
                kept.
            font_path (Path | None): Regular TrueType font. ``None`` tries
                :data:`DEFAULT_FONT_CANDIDATES`.
            bold_font_path (Path | None): Bold TrueType font; ``None``
                reuses ``font_path`` (or the candidate's bold).

        Raises:
            ImportError: Without the ``[image]`` extra (Pillow).
            ValueError: When a color does not parse or the logo is not an
                image Pillow decodes.
            FileNotFoundError: When no font file is found.
        """
        try:
            from PIL import Image, ImageColor
        except ImportError as exc:
            raise ImportError(
                "ShareCardRenderer requires the [image] extra (pillow). "
                "Install with `pip install tempest-fastapi-sdk[image]`."
            ) from exc
        top, bottom = (
            (background, background) if isinstance(background, str) else background
        )
        colors: dict[str, str] = {
            "background_top": top,
            "background_bottom": bottom,
            "text_color": text_color,
            "muted_color": muted_color,
            "accent_color": accent_color,
            "chip_color": chip_color,
            "chip_text_color": chip_text_color,
        }
        parsed: dict[str, tuple[int, int, int]] = {}
        for name, value in colors.items():
            try:
                rgb = ImageColor.getrgb(value)
            except ValueError as exc:
                raise ValueError(f"{name}: unknown color {value!r}") from exc
            parsed[name] = (rgb[0], rgb[1], rgb[2])
        self._colors: dict[str, tuple[int, int, int]] = parsed
        self._regular_path, self._bold_path = _resolve_fonts(font_path, bold_font_path)
        self._fonts: dict[tuple[bool, int], FreeTypeFont] = {}
        logo_bytes = _read_logo(logo)
        self._logo: PillowImage | None = None
        if logo_bytes is not None:
            try:
                with Image.open(io.BytesIO(logo_bytes)) as source:
                    if source.width * source.height > MAX_LOGO_PIXELS:
                        raise ValueError("logo is larger than MAX_LOGO_PIXELS")
                    image = source.convert("RGBA")
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError(f"logo is not a decodable image: {exc}") from exc
            ratio = min(LOGO_HEIGHT / image.height, MAX_LOGO_WIDTH / image.width)
            self._logo = image.resize(
                (
                    max(1, round(image.width * ratio)),
                    max(1, round(image.height * ratio)),
                ),
                Image.Resampling.LANCZOS,
            )
        digest = hashlib.sha256()
        for name in sorted(parsed):
            digest.update(f"{name}={parsed[name]};".encode())
        for path in (self._regular_path, self._bold_path):
            digest.update(path.read_bytes())
        if logo_bytes is not None:
            digest.update(logo_bytes)
        self._fingerprint: str = digest.hexdigest()

    @property
    def fingerprint(self) -> str:
        """Hash of colors, fonts and logo.

        Enters the image ETag, so changing the brand colors or the font
        invalidates cached cards even when the item did not change.

        Returns:
            str: Hex SHA-256.
        """
        return self._fingerprint

    def _font(self, *, bold: bool, size: int) -> "FreeTypeFont":
        """Load (once) a font at a size.

        Args:
            bold (bool): Bold or regular file.
            size (int): Size in pixels.

        Returns:
            FreeTypeFont: The loaded font.
        """
        from PIL import ImageFont

        key = (bold, size)
        font = self._fonts.get(key)
        if font is None:
            path = self._bold_path if bold else self._regular_path
            font = ImageFont.truetype(str(path), size)
            self._fonts[key] = font
        return font

    def _clip(
        self, draw: "PillowDraw", text: str, font: "FreeTypeFont", width: int
    ) -> str:
        """Cut ``text`` with ``…`` until it fits ``width``.

        Args:
            draw (PillowDraw): The drawing, to measure.
            text (str): The text.
            font (FreeTypeFont): The font it is drawn with.
            width (int): Available width in pixels.

        Returns:
            str: ``text`` itself when it fits, else a cut ending in ``…``.
        """
        if draw.textlength(text, font=font) <= width:
            return text
        clipped = text
        while clipped and draw.textlength(clipped.rstrip() + "…", font=font) > width:
            clipped = clipped[:-1]
        return clipped.rstrip() + "…"

    def _fit(
        self,
        draw: "PillowDraw",
        text: str,
        *,
        bold: bool,
        size: int,
        min_size: int,
        width: int,
    ) -> tuple[str, "FreeTypeFont"]:
        """Shrink the font until ``text`` fits, then cut it.

        Args:
            draw (PillowDraw): The drawing, to measure.
            text (str): The text.
            bold (bool): Bold or regular.
            size (int): Starting size.
            min_size (int): Smallest size before cutting.
            width (int): Available width in pixels.

        Returns:
            tuple[str, FreeTypeFont]: The (possibly cut) text and its font.
        """
        current = size
        while (
            current > min_size
            and draw.textlength(text, font=self._font(bold=bold, size=current)) > width
        ):
            current -= 2
        font = self._font(bold=bold, size=current)
        return self._clip(draw, text, font, width), font

    def _wrap_title(
        self, draw: "PillowDraw", text: str, width: int
    ) -> tuple[list[str], "FreeTypeFont"]:
        """Lay the title out in one line, or two, shrinking before wrapping.

        Args:
            draw (PillowDraw): The drawing, to measure.
            text (str): The title.
            width (int): Available width in pixels.

        Returns:
            tuple[list[str], FreeTypeFont]: One or two lines and their font.
        """
        for size in range(72, 47, -4):
            font = self._font(bold=True, size=size)
            if draw.textlength(text, font=font) <= width:
                return [text], font
        font = self._font(bold=True, size=56)
        words = text.split()
        first = ""
        rest_index = len(words)
        for index, word in enumerate(words):
            candidate = f"{first} {word}".strip()
            if draw.textlength(candidate, font=font) > width:
                rest_index = index
                break
            first = candidate
        if not first:
            return [self._clip(draw, text, font, width)], font
        rest = " ".join(words[rest_index:])
        if not rest:
            return [first], font
        return [first, self._clip(draw, rest, font, width)], font

    def _chip(
        self, draw: "PillowDraw", x: int, y: int, text: str, limit: int
    ) -> int | None:
        """Draw one chip if it fits before ``limit``.

        Args:
            draw (PillowDraw): The drawing.
            x (int): Left edge.
            y (int): Top edge.
            text (str): Chip text.
            limit (int): Right edge it must not cross.

        Returns:
            int | None: The chip's right edge, or ``None`` when it did not
            fit and was not drawn.
        """
        font = self._font(bold=True, size=32)
        label = self._clip(draw, text, font, limit - x - 52)
        right = x + int(draw.textlength(label, font=font)) + 52
        if right > limit or label == "…":
            return None
        draw.rounded_rectangle(
            (x, y, right, y + 64), radius=32, fill=self._colors["chip_color"]
        )
        draw.text(
            (x + 26, y + 14), label, font=font, fill=self._colors["chip_text_color"]
        )
        return right

    def render(self, card: ShareCardSchema) -> bytes:
        """Draw ``card`` as a PNG.

        The output is a pure function of the card and the renderer's
        colors, fonts and logo — same inputs, same bytes in the same
        Pillow and FreeType versions.

        Args:
            card (ShareCardSchema): What to draw.

        Returns:
            bytes: The PNG, :data:`OG_IMAGE_WIDTH` x :data:`OG_IMAGE_HEIGHT`.
        """
        from PIL import Image, ImageDraw

        width, height = self.width, self.height
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)
        top, bottom = self._colors["background_top"], self._colors["background_bottom"]
        for row in range(height):
            ratio = row / (height - 1)
            color = (
                round(top[0] + (bottom[0] - top[0]) * ratio),
                round(top[1] + (bottom[1] - top[1]) * ratio),
                round(top[2] + (bottom[2] - top[2]) * ratio),
            )
            draw.line([(0, row), (width, row)], fill=color)
        usable = width - 2 * MARGIN
        header_width = usable
        if self._logo is not None:
            logo_x = width - MARGIN - self._logo.width
            image.paste(self._logo, (logo_x, 48), self._logo)
            header_width = max(0, logo_x - MARGIN - 24)
        y = 52
        if card.brand:
            brand, font = self._fit(
                draw, card.brand, bold=True, size=44, min_size=32, width=header_width
            )
            draw.text((MARGIN, y), brand, font=font, fill=self._colors["accent_color"])
            y += 64
        if card.kicker:
            kicker, font = self._fit(
                draw, card.kicker, bold=False, size=32, min_size=26, width=header_width
            )
            draw.text((MARGIN, y), kicker, font=font, fill=self._colors["muted_color"])
            y += 52
        y = max(y + 8, 140)
        title_lines, title_font = self._wrap_title(draw, card.title, usable)
        step = int(title_font.size * 1.15)
        for line in title_lines:
            draw.text(
                (MARGIN, y), line, font=title_font, fill=self._colors["text_color"]
            )
            y += step
        y += 16
        chips_top = height - 168 if card.chips else height - MARGIN
        footer_top = height - 76
        lines_bottom = min(chips_top, footer_top if card.footer else height) - 12
        icon_font = self._font(bold=True, size=34)
        icon_column = max(
            (
                int(draw.textlength(e.icon, font=icon_font))
                for e in card.lines
                if e.icon
            ),
            default=0,
        )
        for entry in card.lines:
            if not entry.text:
                continue
            if y + 44 > lines_bottom:
                break
            x = MARGIN
            if icon_column:
                draw.text(
                    (x, y),
                    entry.icon or "",
                    font=icon_font,
                    fill=self._colors["accent_color"],
                )
                x += icon_column + 16
            text, font = self._fit(
                draw,
                entry.text,
                bold=False,
                size=34,
                min_size=26,
                width=width - MARGIN - x,
            )
            draw.text((x, y), text, font=font, fill=self._colors["text_color"])
            y += 52
        if card.chips:
            x = MARGIN
            for chip in card.chips:
                right = self._chip(draw, x, chips_top, chip, width - MARGIN)
                if right is None:
                    break
                x = right + 20
        if card.footer:
            footer, font = self._fit(
                draw, card.footer, bold=False, size=30, min_size=24, width=usable
            )
            draw.text(
                (MARGIN, footer_top),
                footer,
                font=font,
                fill=self._colors["muted_color"],
            )
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def card_etag(renderer: ShareCardRenderer, version: str, item_id: str) -> str:
    """Build the weak ETag of a card from its version.

    Weak because two replicas with different FreeType builds can draw
    different bytes for the same card: the tag says "same content", not
    "same bytes".

    Args:
        renderer (ShareCardRenderer): Contributes its fingerprint.
        version (str): The preview's version.
        item_id (str): The item, so two items with the same version never
            share a tag.

    Returns:
        str: ``W/"<32 hex>"``.
    """
    parts: Sequence[str] = (renderer.fingerprint, item_id, version)
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]
    return f'W/"{digest}"'


__all__: list[str] = [
    "DEFAULT_FONT_CANDIDATES",
    "LOGO_HEIGHT",
    "MARGIN",
    "MAX_LOGO_PIXELS",
    "MAX_LOGO_WIDTH",
    "OG_IMAGE_HEIGHT",
    "OG_IMAGE_WIDTH",
    "ShareCardRenderer",
    "card_etag",
]
