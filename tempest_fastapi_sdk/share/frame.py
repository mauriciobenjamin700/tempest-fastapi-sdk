"""A shareable image: QR code in the middle, brand, person and link around it.

The Open Graph card (:class:`~tempest_fastapi_sdk.share.ShareCardRenderer`)
is the preview a link gets; this frame is the **image** someone posts —
on a status, in a group — carrying its own way back: a QR code for whoever
sees it on another screen and the link written out for whoever cannot
scan. :class:`ShareFrameRenderer` shares the card's colors, fonts and
logo rules, and draws two layouts:

* :attr:`~tempest_fastapi_sdk.share.ShareFrameLayoutEnum.STORY`, 1080x1920;
* :attr:`~tempest_fastapi_sdk.share.ShareFrameLayoutEnum.SQUARE`, 1080x1080.

``tests/share/test_frame.py`` decodes the QR code inside both layouts.
"""

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.share.card import _BrandRenderer
from tempest_fastapi_sdk.share.qr import (
    DEFAULT_QR_LOGO_FRACTION,
    _require_imaging,
    qr_image,
)
from tempest_fastapi_sdk.share.schemas import ShareFrameLayoutEnum, ShareFrameSchema

if TYPE_CHECKING:
    from PIL.Image import Image as PillowImage
    from PIL.ImageDraw import ImageDraw as PillowDraw

QR_QUIET_ZONE_MODULES: int = 4
"""Light margin around the QR code, in modules, as ISO/IEC 18004 asks.

The white panel is the quiet zone: the code is drawn without a border
and scaled so at least this many modules of panel surround it, which
keeps the panel's rounded corners visible."""


@dataclass(frozen=True)
class _Layout:
    """Pixel geometry of one frame layout.

    Attributes:
        width (int): Canvas width.
        height (int): Canvas height.
        margin (int): Side margin.
        header_y (int): Top of the logo/brand row.
        header_height (int): Logo height in the header.
        photo_diameter (int): Diameter of the round photo.
        photo_beside_title (bool): Photo left of the title (square) or
            centered above it (story).
        people_y (int): Top of the photo/title block.
        title_size (int): Title font size.
        subtitle_size (int): Subtitle font size.
        panel_size (int): Side of the white panel holding the QR code.
        panel_y (int): Top of the panel.
        url_size (int): Font size of the written link.
        caption_size (int): Font size of the caption.
    """

    width: int
    height: int
    margin: int
    header_y: int
    header_height: int
    photo_diameter: int
    photo_beside_title: bool
    people_y: int
    title_size: int
    subtitle_size: int
    panel_size: int
    panel_y: int
    url_size: int
    caption_size: int


LAYOUTS: dict[ShareFrameLayoutEnum, _Layout] = {
    ShareFrameLayoutEnum.STORY: _Layout(
        width=1080,
        height=1920,
        margin=80,
        header_y=120,
        header_height=96,
        photo_diameter=320,
        photo_beside_title=False,
        people_y=310,
        title_size=64,
        subtitle_size=40,
        panel_size=780,
        panel_y=880,
        url_size=40,
        caption_size=40,
    ),
    ShareFrameLayoutEnum.SQUARE: _Layout(
        width=1080,
        height=1080,
        margin=64,
        header_y=48,
        header_height=72,
        photo_diameter=150,
        photo_beside_title=True,
        people_y=150,
        title_size=52,
        subtitle_size=34,
        panel_size=600,
        panel_y=330,
        url_size=34,
        caption_size=30,
    ),
}
"""Geometry of each layout, in pixels."""

MAX_PHOTO_PIXELS: int = 50_000_000
"""Largest photo, in pixels, decoded before cropping."""


def _initials(name: str) -> str:
    """First letters of the first and last words, for a missing photo.

    Args:
        name (str): The title.

    Returns:
        str: One or two upper-case letters, or ``"?"`` for an empty name.
    """
    words = name.split()
    if not words:
        return "?"
    letters = words[0][0] + (words[-1][0] if len(words) > 1 else "")
    return letters.upper()


class ShareFrameRenderer(_BrandRenderer):
    """Draws the shareable frame as PNG, in the story or square layout.

    Top to bottom: logo and ``brand``; the person's photo in a circle
    ringed in the accent color (their initials when there is no photo),
    ``title`` and ``subtitle``; a white panel with the QR code of ``url``
    — the brand logo in its middle when the renderer has one; the link
    written out; the ``caption``. Text shrinks, then is cut with ``…``;
    nothing leaves the margins.

    Construction arguments are the card's: ``background``,
    ``text_color``, ``muted_color``, ``accent_color``, ``chip_color``,
    ``chip_text_color``, ``logo``, ``font_path`` and ``bold_font_path``
    (the chip colors are unused here). Fonts and colors fail at
    construction, not at the first render.

    Rendering is CPU-bound and synchronous; call it through
    :func:`asyncio.to_thread` from a route.
    """

    def _photo(self, photo: bytes | None, title: str, diameter: int) -> "PillowImage":
        """Crop the photo into a ringed circle, or draw the initials.

        Args:
            photo (bytes | None): PNG/JPEG/WebP of the person.
            title (str): Name used for the initials without a photo.
            diameter (int): Outer diameter, ring included.

        Returns:
            PillowImage: RGBA image of ``diameter`` x ``diameter``.

        Raises:
            ValueError: When the photo does not decode or exceeds
                :data:`MAX_PHOTO_PIXELS`.
        """
        from PIL import Image, ImageDraw, ImageOps

        supersample = 4
        ring = max(4, diameter // 40)
        inner = diameter - 2 * ring
        if photo is not None:
            try:
                with Image.open(io.BytesIO(photo)) as source:
                    if source.width * source.height > MAX_PHOTO_PIXELS:
                        raise ValueError("photo is larger than MAX_PHOTO_PIXELS")
                    picture = ImageOps.exif_transpose(source).convert("RGB")
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError(f"photo is not a decodable image: {exc}") from exc
            picture = ImageOps.fit(picture, (inner, inner), Image.Resampling.LANCZOS)
        else:
            picture = Image.new(
                "RGB", (inner, inner), self._colors["background_bottom"]
            )
            draw = ImageDraw.Draw(picture)
            font = self._font(bold=True, size=max(12, inner * 2 // 5))
            draw.text(
                (inner // 2, inner // 2),
                _initials(title),
                font=font,
                fill=self._colors["accent_color"],
                anchor="mm",
            )
        mask = Image.new("L", (inner * supersample, inner * supersample), 0)
        ImageDraw.Draw(mask).ellipse(
            (0, 0, inner * supersample - 1, inner * supersample - 1), fill=255
        )
        mask = mask.resize((inner, inner), Image.Resampling.LANCZOS)
        disc = Image.new("RGBA", (diameter * supersample, diameter * supersample), 0)
        ImageDraw.Draw(disc).ellipse(
            (0, 0, diameter * supersample - 1, diameter * supersample - 1),
            fill=(*self._colors["accent_color"], 255),
        )
        result = disc.resize((diameter, diameter), Image.Resampling.LANCZOS)
        result.paste(picture, (ring, ring), mask)
        return result

    def _centered(
        self,
        draw: "PillowDraw",
        text: str,
        *,
        y: int,
        width: int,
        margin: int,
        bold: bool,
        size: int,
        color: str,
    ) -> None:
        """Draw one line centered on the canvas, shrunk or cut to ``width``.

        Args:
            draw (PillowDraw): The drawing.
            text (str): The line.
            y (int): Top of the line.
            width (int): Canvas width.
            margin (int): Side margin taken off both sides.
            bold (bool): Bold or regular.
            size (int): Starting font size.
            color (str): Key of the renderer color.
        """
        fitted, font = self._fit(
            draw,
            text,
            bold=bold,
            size=size,
            min_size=max(12, size * 2 // 3),
            width=width - 2 * margin,
        )
        draw.text(
            (width // 2, y), fitted, font=font, fill=self._colors[color], anchor="mt"
        )

    def render(
        self,
        frame: ShareFrameSchema,
        *,
        layout: ShareFrameLayoutEnum = ShareFrameLayoutEnum.STORY,
        photo: bytes | None = None,
        qr_logo_fraction: float = DEFAULT_QR_LOGO_FRACTION,
    ) -> bytes:
        """Draw ``frame`` as a PNG.

        Args:
            frame (ShareFrameSchema): What to draw.
            layout (ShareFrameLayoutEnum): Story (1080x1920) or square
                (1080x1080).
            photo (bytes | None): The person's photo; ``None`` draws the
                initials of ``frame.title``. EXIF orientation is applied.
            qr_logo_fraction (float): Side of the logo inside the QR code
                over the QR side, at most
                :data:`~tempest_fastapi_sdk.share.MAX_QR_LOGO_FRACTION`.

        Returns:
            bytes: The PNG.

        Raises:
            ImportError: Without the ``[image]`` extra (``segno``).
            ValueError: When the photo does not decode, or
                ``qr_logo_fraction`` is out of range.
        """
        _require_imaging()
        import segno
        from PIL import Image, ImageDraw

        geometry = LAYOUTS[ShareFrameLayoutEnum(layout)]
        image = Image.new("RGB", (geometry.width, geometry.height))
        self._paint_background(image)
        draw = ImageDraw.Draw(image)
        margin = geometry.margin

        x = margin
        if self._logo_source is not None:
            mark = self._logo_source.copy()
            mark.thumbnail(
                (geometry.width // 3, geometry.header_height),
                Image.Resampling.LANCZOS,
            )
            image.paste(mark, (x, geometry.header_y), mark)
            x += mark.width + 24
        if frame.brand:
            brand, font = self._fit(
                draw,
                frame.brand,
                bold=True,
                size=geometry.header_height * 2 // 3,
                min_size=24,
                width=geometry.width - margin - x,
            )
            draw.text(
                (x, geometry.header_y + geometry.header_height // 2),
                brand,
                font=font,
                fill=self._colors["accent_color"],
                anchor="lm",
            )

        disc = self._photo(photo, frame.title, geometry.photo_diameter)
        if geometry.photo_beside_title:
            image.paste(disc, (margin, geometry.people_y), disc)
            text_x = margin + geometry.photo_diameter + 32
            text_width = geometry.width - margin - text_x
            middle = geometry.people_y + geometry.photo_diameter // 2
            title, font = self._fit(
                draw,
                frame.title,
                bold=True,
                size=geometry.title_size,
                min_size=geometry.title_size * 2 // 3,
                width=text_width,
            )
            title_y = middle if frame.subtitle is None else middle - 8
            draw.text(
                (text_x, title_y),
                title,
                font=font,
                fill=self._colors["text_color"],
                anchor="lm" if frame.subtitle is None else "ld",
            )
            if frame.subtitle:
                subtitle, font = self._fit(
                    draw,
                    frame.subtitle,
                    bold=False,
                    size=geometry.subtitle_size,
                    min_size=geometry.subtitle_size * 2 // 3,
                    width=text_width,
                )
                draw.text(
                    (text_x, middle + 8),
                    subtitle,
                    font=font,
                    fill=self._colors["muted_color"],
                    anchor="la",
                )
        else:
            image.paste(
                disc,
                ((geometry.width - geometry.photo_diameter) // 2, geometry.people_y),
                disc,
            )
            y = geometry.people_y + geometry.photo_diameter + 40
            self._centered(
                draw,
                frame.title,
                y=y,
                width=geometry.width,
                margin=margin,
                bold=True,
                size=geometry.title_size,
                color="text_color",
            )
            if frame.subtitle:
                self._centered(
                    draw,
                    frame.subtitle,
                    y=y + geometry.title_size + 24,
                    width=geometry.width,
                    margin=margin,
                    bold=False,
                    size=geometry.subtitle_size,
                    color="muted_color",
                )

        panel_x = (geometry.width - geometry.panel_size) // 2
        draw.rounded_rectangle(
            (
                panel_x,
                geometry.panel_y,
                panel_x + geometry.panel_size,
                geometry.panel_y + geometry.panel_size,
            ),
            radius=geometry.panel_size // 16,
            fill="white",
        )
        modules: int = int(
            segno.make(frame.url, error="h", micro=False).symbol_size(
                scale=1, border=0
            )[0]
        )
        scale = max(1, geometry.panel_size // (modules + 2 * QR_QUIET_ZONE_MODULES))
        code = qr_image(
            frame.url,
            logo=self._logo_source,
            logo_fraction=qr_logo_fraction,
            scale=scale,
            border=0,
        )
        image.paste(
            code,
            (
                (geometry.width - code.width) // 2,
                geometry.panel_y + (geometry.panel_size - code.height) // 2,
            ),
        )

        y = geometry.panel_y + geometry.panel_size + 28
        if frame.show_url:
            written = frame.url.split("://", 1)[1]
            self._centered(
                draw,
                written,
                y=y,
                width=geometry.width,
                margin=margin,
                bold=True,
                size=geometry.url_size,
                color="text_color",
            )
            y += geometry.url_size + 20
        if frame.caption and y + geometry.caption_size <= geometry.height - 16:
            self._centered(
                draw,
                frame.caption,
                y=y,
                width=geometry.width,
                margin=margin,
                bold=False,
                size=geometry.caption_size,
                color="muted_color",
            )
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


__all__: list[str] = [
    "LAYOUTS",
    "MAX_PHOTO_PIXELS",
    "QR_QUIET_ZONE_MODULES",
    "ShareFrameRenderer",
]
