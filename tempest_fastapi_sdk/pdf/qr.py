"""Encode a QR code as the ``data:`` URI the bundled templates embed.

Every service that printed a voucher with a QR code wrote the same three
lines around ``segno`` and picked its own error correction and margin —
blind, because the box the image lands in belongs to the SDK's
``voucher.html``, not to the caller. The defaults below are calibrated
against that box, so a change to the template and a change to them land
in the same commit.

Needs the ``[pdf]`` extra, which carries ``segno``: pure Python, no
runtime dependency and no upper bound. ``segno`` is imported at the first
call, so importing this module does not need it.
"""

from __future__ import annotations

from typing import Final, Literal

QrErrorLevel = Literal["l", "m", "q", "h"]
"""ISO/IEC 18004 error-correction level: 7, 15, 25 or 30 % recovery."""

QR_ERROR_CORRECTION: Final[QrErrorLevel] = "h"
"""Default error correction: ``H``, which recovers 30 % of the symbol.

A voucher is printed, folded, carried in a pocket and scanned off a
crease — damage is the normal case, not the edge case. The price is
density: ``H`` needs the most modules for a given payload, and the
voucher's box is a fixed ``26mm`` square, so the modules get smaller.
With ``segno`` 1.6.6 and the default margin (deterministic, so exact):

=========================  =====  =======  ========
Payload                    Level  Version  Module
=========================  =====  =======  ========
66-character URL           H      8        0.49 mm
66-character URL           M      5        0.63 mm
197-character Pix BR Code  H      15       0.32 mm
197-character Pix BR Code  M      10       0.43 mm
=========================  =====  =======  ========

Both payloads at ``H``, rendered through the voucher by WeasyPrint and
rasterized at 100 dpi, decode with ``zxing-cpp``;
``tests/pdf/test_qr.py`` repeats that at 150 dpi. A caller printing on a
low-resolution thermal printer can pass ``error="m"`` to trade recovery
for bigger modules.
"""

QR_SCALE: Final[int] = 8
"""Default pixels per module in the PNG.

The template sizes the image to ``26mm`` whatever its pixel count, so
the scale sets resolution, not size, and decoding did not change with
it: scales 1, 2, 4 and 8 decoded the same payloads at the same dpi. What
eight buys is margin against a viewer or print pipeline that smooths the
image — the template asks it not to (``image-rendering: pixelated``
makes WeasyPrint write ``/Interpolate false``; without it, ``true``),
and not every consumer honors the flag. It costs about 4 KiB per
document: the voucher with a Pix BR Code measured 13 KiB at scale 8
against 9 KiB at scale 1.
"""

QR_BORDER: Final[int] = 2
"""Default quiet zone, in modules, drawn inside the PNG.

ISO/IEC 18004 asks for four. The voucher supplies the rest: the image
sits inside ``6mm`` of white padding with a ``5mm`` gap to the text, and
two modules are at most ``2.08mm`` — version 1, the largest module the
box can hold (``26mm`` over 25 modules). Drawing all four inside the
image shrinks every module to pay for white the page already has: the
66-character URL at version 8 spans 53 modules with two a side and 57
with four, so each module loses 7 %.
"""


def qr_data_uri(
    content: str,
    *,
    error: QrErrorLevel = QR_ERROR_CORRECTION,
    scale: int = QR_SCALE,
    border: int = QR_BORDER,
) -> str:
    """Encode ``content`` as a PNG QR code in a ``data:`` URI.

    The result matches the ``pattern`` of
    :attr:`~tempest_fastapi_sdk.pdf.VoucherDocument.qr_data_uri`, so it
    can be passed there directly; :attr:`VoucherDocument.qr_content
    <tempest_fastapi_sdk.pdf.VoucherDocument.qr_content>` calls this with
    the defaults.

    Example:

        >>> from tempest_fastapi_sdk.pdf import qr_data_uri
        >>> qr_data_uri("https://example.com/v/42").startswith(
        ...     "data:image/png;base64,"
        ... )
        True

    Args:
        content (str): The text to encode — a verification URL, a Pix BR
            Code. Encoded exactly as given, never "optimized" into a
            Micro QR.
        error (QrErrorLevel): Error-correction level. See
            :data:`QR_ERROR_CORRECTION` for why the default is ``"h"``.
        scale (int): Pixels per module. See :data:`QR_SCALE`.
        border (int): Quiet zone in modules. See :data:`QR_BORDER`.

    Returns:
        str: ``data:image/png;base64,...``.

    Raises:
        ImportError: When ``segno`` (the ``[pdf]`` extra) is missing.
        ValueError: When ``content`` is empty, or too long for any QR
            version at ``error`` — ``segno`` raises
            ``DataOverflowError``, a ``ValueError``.
    """
    if not content:
        raise ValueError("QR content must not be empty")
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - extra-gated
        raise ImportError(
            "QR encoding needs segno. Install the extra: "
            'pip install "tempest-fastapi-sdk[pdf]"',
        ) from exc
    symbol = segno.make_qr(content, error=error)
    uri: str = symbol.png_data_uri(scale=scale, border=border)
    return uri


__all__: list[str] = [
    "QR_BORDER",
    "QR_ERROR_CORRECTION",
    "QR_SCALE",
    "QrErrorLevel",
    "qr_data_uri",
]
