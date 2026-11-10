"""Strip the metadata a user's photo carries before it is stored or forwarded.

A photo taken on a phone carries EXIF: GPS coordinates, the device make,
model and sometimes serial number, the capture time, and occasionally a
thumbnail of the uncropped original. PNG and WEBP carry the same through
``eXIf``/``EXIF`` chunks, XMP packets and free-text chunks. A service that
stores the upload, or forwards it to a third party, ships all of that along
with the pixels unless something removes it.

:func:`strip_image_metadata` re-encodes the image through Pillow instead of
cutting segments by hand: every format has its own long tail of where
metadata can hide, and Pillow's decoders already parse it. Only the pixels
(and the transparency they need) reach the encoder, so nothing the source
carried is written back.

Pillow is the ``[image]`` extra, imported on the first call.
"""

from __future__ import annotations

import io
import struct
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.exceptions.image import (
    ImageDecodeException,
    ImageFormatUnsupportedException,
    ImageTooLargeException,
    ImageTypeMismatchException,
)
from tempest_fastapi_sdk.utils.media_types import ImageMediaTypeEnum

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

DEFAULT_MAX_IMAGE_PIXELS: int = 50_000_000
"""Default ceiling on ``width * height`` accepted by :func:`strip_image_metadata`.

Fits the full-resolution output of a 48 or 50 megapixel phone sensor
(8064 x 6048 is 48.8 million pixels). Memory grows with the pixel count:
measured on Pillow 13.0.0 with an 8000 x 6000 JPEG (48 million pixels),
the call's peak memory rose by about 186 MB for an upright photo and
368 MB for one its orientation rotates by 90 degrees (Pillow holds RGB at
four bytes per pixel, and the rotation keeps two copies for a moment).
Raise the ceiling only with that cost in mind.
"""

DEFAULT_JPEG_QUALITY: int = 90
"""Default JPEG quality used when re-encoding (Pillow's own default is 75)."""

DEFAULT_WEBP_QUALITY: int = 90
"""Default quality for a lossy WEBP re-encode (Pillow's own default is 80).

A lossless source is re-encoded lossless, where quality does not apply.
"""

_PILLOW_FORMATS: dict[ImageMediaTypeEnum, str] = {
    ImageMediaTypeEnum.JPEG: "JPEG",
    ImageMediaTypeEnum.PNG: "PNG",
    ImageMediaTypeEnum.GIF: "GIF",
    ImageMediaTypeEnum.BMP: "BMP",
    ImageMediaTypeEnum.WEBP: "WEBP",
}
"""Media type → Pillow format name, for every type this module re-encodes."""

_MEDIA_TYPES_BY_FORMAT: dict[str, str] = {
    pillow_format: media_type.value
    for media_type, pillow_format in _PILLOW_FORMATS.items()
}
"""Pillow format name → media type, to name what was detected in an error."""

_KEPT_INFO_KEYS: frozenset[str] = frozenset({"transparency"})
"""Keys of ``Image.info`` that survive the re-encode.

Transparency is part of the picture, not metadata: dropping it would turn
the transparent pixels of a palette or ``L``/``RGB`` image opaque.
"""

_DECODE_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    SyntaxError,
    ValueError,
    EOFError,
    struct.error,
)
"""What Pillow raises for truncated or corrupt content.

``OSError`` for a truncated stream (``UnidentifiedImageError``, for bytes
that are no image at all, is one of its subclasses), ``SyntaxError`` from several plugin
parsers, ``ValueError``/``EOFError``/``struct.error`` from a malformed EXIF
block read during the orientation step.
"""

_WEBP_HEADER_BYTES: int = 12
"""Length of the ``RIFF`` + size + ``WEBP`` header that precedes the chunks."""

_WEBP_CHUNK_HEADER_BYTES: int = 8
"""Length of a RIFF chunk header: four-byte FourCC plus a little-endian size."""

_WEBP_ANMF_HEADER_BYTES: int = 16
"""Fixed fields of an ``ANMF`` frame chunk before its own sub-chunks."""


def _webp_is_lossless(data: bytes) -> bool:
    """Tell whether a WEBP file's first frame is stored lossless.

    Walks the RIFF chunk headers (reading their FourCC and size only, never
    rewriting anything) until the first ``VP8L`` (lossless) or ``VP8 ``
    (lossy) bitstream. An animated file nests its frames in ``ANMF``
    chunks, so the walk steps into the first one.

    Args:
        data (bytes): The whole WEBP file.

    Returns:
        bool: ``True`` when the first bitstream found is ``VP8L``; ``False``
        for ``VP8 `` or when none is found.
    """
    offset = _WEBP_HEADER_BYTES
    while offset + _WEBP_CHUNK_HEADER_BYTES <= len(data):
        fourcc = data[offset : offset + 4]
        if fourcc == b"VP8L":
            return True
        if fourcc == b"VP8 ":
            return False
        if fourcc == b"ANMF":
            offset += _WEBP_CHUNK_HEADER_BYTES + _WEBP_ANMF_HEADER_BYTES
            continue
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        offset += _WEBP_CHUNK_HEADER_BYTES + size + (size & 1)
    return False


def _resolve_media_type(media_type: ImageMediaTypeEnum | str) -> ImageMediaTypeEnum:
    """Resolve the declared type to a member this module can re-encode.

    Args:
        media_type (ImageMediaTypeEnum | str): The declared type; strings
            go through :class:`ImageMediaTypeEnum`, so its aliases
            (``image/jpg``, surrounding whitespace, any case) resolve.

    Returns:
        ImageMediaTypeEnum: The resolved member.

    Raises:
        ImageFormatUnsupportedException: When the value is not an image
            media type, or is one Pillow cannot encode (``image/heic``).
    """
    try:
        resolved = ImageMediaTypeEnum(media_type)
    except ValueError as exc:
        raise ImageFormatUnsupportedException(
            details={"declared": str(media_type)}
        ) from exc
    if resolved not in _PILLOW_FORMATS:
        raise ImageFormatUnsupportedException(details={"declared": resolved.value})
    return resolved


def _encoder_options(
    image: PILImage,
    pillow_format: str,
    data: bytes,
    *,
    jpeg_quality: int,
    webp_quality: int,
) -> dict[str, Any]:
    """Build the keyword arguments passed to ``Image.save``.

    Never includes ``exif``, ``icc_profile``, ``xmp``, ``comment`` or
    ``pnginfo``: the only carried value is the transparency, which the
    picture needs.

    Args:
        image (PILImage): The cleaned image about to be saved.
        pillow_format (str): The Pillow format name.
        data (bytes): The source bytes, read for the WEBP lossless flag.
        jpeg_quality (int): Quality for a JPEG re-encode.
        webp_quality (int): Quality for a lossy WEBP re-encode.

    Returns:
        dict[str, Any]: The options for ``Image.save``.
    """
    options: dict[str, Any] = {
        key: image.info[key] for key in _KEPT_INFO_KEYS if key in image.info
    }
    if pillow_format == "JPEG":
        options["quality"] = jpeg_quality
    elif pillow_format == "WEBP":
        if _webp_is_lossless(data):
            options["lossless"] = True
        else:
            options["quality"] = webp_quality
    return options


def strip_image_metadata(
    data: bytes,
    media_type: ImageMediaTypeEnum | str,
    *,
    max_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    webp_quality: int = DEFAULT_WEBP_QUALITY,
) -> bytes:
    """Re-encode an image in its own format, without any metadata.

    The image is decoded by Pillow, turned upright according to its EXIF
    orientation (``ImageOps.exif_transpose``, so a portrait photo does not
    come back lying on its side once the orientation tag is gone), and
    encoded again from its pixels alone. EXIF (GPS, device, capture time,
    embedded thumbnail), XMP, IPTC, the ICC color profile, comments and
    PNG text chunks are all left behind. The pixel size is kept (with
    width and height swapped when the orientation rotates by 90 degrees),
    and so are the mode and the transparency.

    Format notes:

    - **JPEG** is re-encoded at ``jpeg_quality``, which is lossy: the
      bytes differ from the source even for an image with no metadata.
    - **WEBP** stays lossless when the source is lossless; a lossy source
      is re-encoded at ``webp_quality``.
    - **GIF and WEBP animations come back as their first frame.**
      Re-encoding every frame would multiply the decode cost by a frame
      count the client controls; a service that must keep animations
      should not route them through this function.
    - Dropping the ICC profile means the pixels are read as sRGB. Nothing
      changes for an sRGB photo; a wide-gamut one (Display P3, common on
      recent phones) can shift slightly in saturation.
    - ``image/heic`` is refused: Pillow ships no HEIC codec.

    The function is CPU-bound and synchronous; from an async route, call
    it through ``asyncio.to_thread``.

    Args:
        data (bytes): The image file content.
        media_type (ImageMediaTypeEnum | str): The declared media type,
            usually the client's ``Content-Type``. A string resolves
            through :class:`ImageMediaTypeEnum`, aliases included.
        max_pixels (int): Ceiling on ``width * height``, checked from the
            header before any pixel is decoded. Pillow's own
            process-wide guard (``PIL.Image.MAX_IMAGE_PIXELS``) still runs
            when the file is opened and is never changed here: above it
            Pillow emits a ``DecompressionBombWarning``, above twice it
            Pillow refuses, and both cases surface as
            :class:`ImageTooLargeException` (the warning only when it is
            escalated to an error, otherwise this ceiling refuses next).
        jpeg_quality (int): JPEG quality, ``1`` to ``100``.
        webp_quality (int): Lossy WEBP quality, ``1`` to ``100``.

    Returns:
        bytes: The re-encoded image, in the declared format.

    Raises:
        ImportError: When the ``[image]`` extra (Pillow) is not installed.
        ValueError: When ``max_pixels`` is not positive or a quality is
            outside ``1``-``100``.
        ImageFormatUnsupportedException: When ``media_type`` is not an
            image type this function can re-encode (``415``).
        ImageDecodeException: When the bytes are not an image of an
            accepted format, or are truncated or corrupt (``422``).
        ImageTypeMismatchException: When the bytes are a different
            accepted format than the declared one (``415``).
        ImageTooLargeException: When the image has more than
            ``max_pixels`` pixels (``413``).

    Examples:
        >>> import io
        >>> from PIL import Image
        >>> buffer = io.BytesIO()
        >>> Image.new("RGB", (4, 2)).save(buffer, "JPEG", comment=b"secret")
        >>> clean = strip_image_metadata(buffer.getvalue(), "image/jpeg")
        >>> b"secret" in clean
        False
    """
    if max_pixels <= 0:
        raise ValueError("max_pixels must be positive")
    for name, quality in (
        ("jpeg_quality", jpeg_quality),
        ("webp_quality", webp_quality),
    ):
        if not 1 <= quality <= 100:
            raise ValueError(f"{name} must be between 1 and 100")
    declared = _resolve_media_type(media_type)
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ImportError(
            "strip_image_metadata requires the [image] extra (pillow). "
            "Install with `pip install tempest-fastapi-sdk[image]`."
        ) from exc
    pillow_format = _PILLOW_FORMATS[declared]
    try:
        source = Image.open(io.BytesIO(data), formats=list(_PILLOW_FORMATS.values()))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageTooLargeException(details={"max_pixels": max_pixels}) from exc
    except _DECODE_ERRORS as exc:
        raise ImageDecodeException(details={"declared": declared.value}) from exc
    with source:
        if source.format != pillow_format:
            raise ImageTypeMismatchException(
                details={
                    "declared": declared.value,
                    "detected": _MEDIA_TYPES_BY_FORMAT.get(str(source.format)),
                }
            )
        width, height = source.size
        if width * height > max_pixels:
            raise ImageTooLargeException(
                details={"width": width, "height": height, "max_pixels": max_pixels}
            )
        try:
            source.load()
            ImageOps.exif_transpose(source, in_place=True)
        except _DECODE_ERRORS as exc:
            raise ImageDecodeException(details={"declared": declared.value}) from exc
        source.info = {
            key: source.info[key] for key in _KEPT_INFO_KEYS if key in source.info
        }
        options = _encoder_options(
            source,
            pillow_format,
            data,
            jpeg_quality=jpeg_quality,
            webp_quality=webp_quality,
        )
        output = io.BytesIO()
        try:
            source.save(output, format=pillow_format, **options)
        except _DECODE_ERRORS as exc:
            raise ImageDecodeException(details={"declared": declared.value}) from exc
    return output.getvalue()


__all__: list[str] = [
    "DEFAULT_JPEG_QUALITY",
    "DEFAULT_MAX_IMAGE_PIXELS",
    "DEFAULT_WEBP_QUALITY",
    "strip_image_metadata",
]
