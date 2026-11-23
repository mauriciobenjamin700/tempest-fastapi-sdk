"""QR codes with a logo in the middle.

The logo hides modules of the symbol; error-correction level ``H``
(30 % recovery) is what keeps it readable. How much logo fits was
measured with ``segno`` 1.6.6 and ``zxing-cpp`` 3.1.1 at ``scale=10``,
square opaque logo on a white frame, for a 30-character link (version 4)
and a 64-character one (version 7), on the clean image and on four
shrunk-and-blurred copies imitating a camera photo:

======================  ==========================  ==========================
Logo side / QR side     30 chars: clean / degraded  64 chars: clean / degraded
======================  ==========================  ==========================
0.20 to 0.35            reads / 4 of 4              reads / 4 of 4
0.40                    fails / 0 of 4              reads / 2 of 4
0.45                    fails / 0 of 4              fails / 0 of 4
======================  ==========================  ==========================

:data:`DEFAULT_QR_LOGO_FRACTION` sits two steps below the first failure
and :data:`MAX_QR_LOGO_FRACTION` is the last value that read everywhere.
``tests/share/test_qr.py`` decodes the output at both.

Needs the ``[image]`` extra (``pillow`` + ``segno``); both are imported
at the first call.
"""

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PillowImage

DEFAULT_QR_LOGO_FRACTION: float = 0.25
"""Default side of the logo as a fraction of the QR side (6 % of the area)."""

MAX_QR_LOGO_FRACTION: float = 0.35
"""Largest fraction accepted; 0.40 already failed on a 30-character link."""

QR_LOGO_PADDING_MODULES: float = 1.0
"""White frame around the logo, in modules, so it does not touch them."""

MAX_QR_LOGO_PIXELS: int = 25_000_000
"""Largest logo, in pixels, decoded before scaling."""


def _require_imaging() -> None:
    """Fail with the extra's name when Pillow or segno is missing.

    Raises:
        ImportError: Without the ``[image]`` extra.
    """
    try:
        import segno  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "QR rendering requires the [image] extra (pillow, segno). "
            "Install with `pip install tempest-fastapi-sdk[image]`."
        ) from exc


def decode_logo(logo: bytes | Path) -> "PillowImage":
    """Decode a logo into RGBA, refusing oversized or broken images.

    Args:
        logo (bytes | Path): PNG/JPEG/WebP bytes or a file.

    Returns:
        PillowImage: The logo in RGBA.

    Raises:
        ValueError: When the image does not decode or exceeds
            :data:`MAX_QR_LOGO_PIXELS`.
    """
    from PIL import Image

    data = logo.read_bytes() if isinstance(logo, Path) else logo
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_QR_LOGO_PIXELS:
                raise ValueError("logo is larger than MAX_QR_LOGO_PIXELS")
            return source.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"logo is not a decodable image: {exc}") from exc


def qr_image(
    url: str,
    *,
    logo: "PillowImage | None",
    logo_fraction: float,
    scale: int,
    border: int,
    dark: tuple[int, int, int] = (0, 0, 0),
) -> "PillowImage":
    """Draw the QR code of ``url`` as an RGB image, logo pasted in the middle.

    Args:
        url (str): The payload.
        logo (PillowImage | None): Decoded RGBA logo, or ``None``.
        logo_fraction (float): Logo side over QR side (border excluded).
        scale (int): Pixels per module.
        border (int): Quiet zone, in modules.
        dark (tuple[int, int, int]): Module color; light is white.

    Returns:
        PillowImage: The RGB image.

    Raises:
        ValueError: When ``logo_fraction`` is not in
            ``(0, MAX_QR_LOGO_FRACTION]``, ``scale`` is below 1 or
            ``border`` is negative.
    """
    import segno
    from PIL import Image

    if not 0 < logo_fraction <= MAX_QR_LOGO_FRACTION:
        raise ValueError(
            f"logo_fraction must be in (0, {MAX_QR_LOGO_FRACTION}], got {logo_fraction}"
        )
    if scale < 1:
        raise ValueError("scale must be at least 1")
    if border < 0:
        raise ValueError("border must not be negative")
    qr = segno.make(url, error="h", micro=False)
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border, dark=dark, light="white")
    image = Image.open(buffer).convert("RGB")
    if logo is None:
        return image
    side = qr.symbol_size(scale=scale, border=0)[0]
    logo_side = max(1, int(side * logo_fraction))
    mark = logo.copy()
    mark.thumbnail((logo_side, logo_side), Image.Resampling.LANCZOS)
    padding = max(1, round(QR_LOGO_PADDING_MODULES * scale))
    frame = Image.new(
        "RGB", (mark.width + 2 * padding, mark.height + 2 * padding), "white"
    )
    frame.paste(mark, (padding, padding), mark)
    image.paste(
        frame,
        ((image.width - frame.width) // 2, (image.height - frame.height) // 2),
    )
    return image


def render_qr(
    url: str,
    *,
    logo: bytes | Path | None = None,
    logo_fraction: float = DEFAULT_QR_LOGO_FRACTION,
    scale: int = 10,
    border: int = 4,
) -> bytes:
    """Encode ``url`` as a PNG QR code, optionally with a logo in the middle.

    Always error-correction level ``H`` and never Micro QR (``segno``
    picks Micro QR for very short text, and phone readers generally do
    not support it).

    Args:
        url (str): The payload, usually the share page of an item.
        logo (bytes | Path | None): PNG/JPEG/WebP drawn in the middle on
            a white frame; transparency is kept.
        logo_fraction (float): Logo side over QR side, at most
            :data:`MAX_QR_LOGO_FRACTION`.
        scale (int): Pixels per module. Raise it for print instead of
            enlarging the PNG afterwards.
        border (int): Quiet zone in modules; 4 is what ISO/IEC 18004
            asks for.

    Returns:
        bytes: The PNG.

    Raises:
        ImportError: Without the ``[image]`` extra.
        ValueError: On a bad ``logo_fraction``, ``scale`` or ``border``,
            or a logo that does not decode.

    Example:
        >>> png = render_qr("https://app.example.com/s/42")
        >>> png[:4]
        b'\\x89PNG'
    """
    _require_imaging()
    decoded = None if logo is None else decode_logo(logo)
    image = qr_image(
        url, logo=decoded, logo_fraction=logo_fraction, scale=scale, border=border
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


__all__: list[str] = [
    "DEFAULT_QR_LOGO_FRACTION",
    "MAX_QR_LOGO_FRACTION",
    "MAX_QR_LOGO_PIXELS",
    "QR_LOGO_PADDING_MODULES",
    "decode_logo",
    "qr_image",
    "render_qr",
]
