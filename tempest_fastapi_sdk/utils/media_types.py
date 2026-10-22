"""Media types the standard library does not know on its own.

``mimetypes.guess_type`` answers from two places: a table compiled into
Python and whatever system files it finds (``/etc/mime.types`` and
friends). The compiled table has no entry for the Office formats a service
most often hands out — measured on Python 3.11, 3.12 and 3.13 with
``mimetypes.MimeTypes(filenames=())``, ``.xlsx``, ``.docx``, ``.pptx``,
``.odt``, ``.ods`` and ``.ogg`` all come back ``None`` (``.webp`` too, on
3.11). A developer machine usually has ``/etc/mime.types``, so the guess
works there; ``python:3.13-slim`` — the base of the Dockerfile ``tempest
new`` generates — ships no such file, and the same guess returns ``None``
inside the container. A download that relied on it was served as
``application/octet-stream`` in production and correctly everywhere else.

:func:`guess_media_type` checks this module's table first, so the answer
does not depend on what the image happens to install.
"""

from __future__ import annotations

import mimetypes
from pathlib import PurePath

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.utils.upload import _MIME_ALIASES, sniff_mime

XLSX_MEDIA_TYPE: str = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
"""Media type of an Office Open XML workbook (``.xlsx``).

Ported from the ``media-types`` 10.1.0 package's ``/etc/mime.types``.
"""

DOCX_MEDIA_TYPE: str = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
"""Media type of an Office Open XML document (``.docx``).

Ported from the ``media-types`` 10.1.0 package's ``/etc/mime.types``.
"""

PPTX_MEDIA_TYPE: str = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
"""Media type of an Office Open XML presentation (``.pptx``).

Ported from the ``media-types`` 10.1.0 package's ``/etc/mime.types``.
"""

_KNOWN_MEDIA_TYPES: dict[str, str] = {
    ".xlsx": XLSX_MEDIA_TYPE,
    ".docx": DOCX_MEDIA_TYPE,
    ".pptx": PPTX_MEDIA_TYPE,
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ogg": "audio/ogg",
    ".webp": "image/webp",
}
"""Extension → media type for what Python's compiled table lacks.

Ported from the ``media-types`` 10.1.0 package's ``/etc/mime.types``. Only
extensions missing from the compiled table on some supported Python are
listed; everything else falls through to :mod:`mimetypes`.
"""


def guess_media_type(filename: str) -> str | None:
    """Guess a file's media type from its name, independent of the host.

    Looks the extension up in this module's table first, then asks
    :func:`mimetypes.guess_type`. The table is what keeps the answer the
    same on a developer machine and in a slim container image that has no
    ``/etc/mime.types``.

    Args:
        filename (str): A file name or path; only the extension is read,
            case-insensitively.

    Returns:
        str | None: The media type, or ``None`` when neither source knows
        the extension.
    """
    known = _KNOWN_MEDIA_TYPES.get(PurePath(filename).suffix.lower())
    if known is not None:
        return known
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed


class ImageMediaType(BaseStrEnum):
    """Image media types whose bytes :func:`sniff_mime` can verify.

    Exactly the ``image/*`` members of
    :data:`~tempest_fastapi_sdk.utils.upload.SNIFFABLE_MIMETYPES`, so
    every member can be checked against the upload's real content with
    :meth:`matches`. Use it as the type of a Pydantic field that carries a
    declared ``Content-Type``: the input is matched case-insensitively,
    with surrounding whitespace stripped, and the aliases the upload
    helpers accept (``image/jpg``, ``image/pjpeg`` → JPEG, ``image/x-png``
    → PNG) resolve to the canonical member. Anything else fails
    validation with Pydantic's usual ``enum`` error (``422`` in a route),
    and the JSON Schema lists the canonical values.

    Examples:
        >>> ImageMediaType(" Image/JPG ")
        <ImageMediaType.JPEG: 'image/jpeg'>
        >>> ImageMediaType.HEIC.extension
        'heic'
    """

    JPEG = "image/jpeg"
    PNG = "image/png"
    GIF = "image/gif"
    BMP = "image/bmp"
    WEBP = "image/webp"
    HEIC = "image/heic"

    @classmethod
    def _missing_(cls, value: object) -> ImageMediaType | None:
        """Resolve a non-canonical spelling to its member.

        Called by ``Enum`` (and by Pydantic's enum validator) when the
        exact value lookup fails. Strips whitespace, lowercases (media
        types are case-insensitive, RFC 9110 §8.3.1) and resolves the
        aliases :func:`sniff_mime` comparisons already treat as equal.

        Args:
            value (object): The raw value that matched no member.

        Returns:
            ImageMediaType | None: The matching member, or ``None`` so
            ``Enum`` raises its ``ValueError``.
        """
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        canonical = _MIME_ALIASES.get(normalized, normalized)
        for member in cls:
            if member.value == canonical:
                return member
        return None

    @property
    def extension(self) -> str:
        """The conventional file extension, lowercase and without the dot.

        Returns:
            str: ``"jpg"``, ``"png"``, ``"gif"``, ``"bmp"``, ``"webp"`` or
            ``"heic"``.
        """
        return _IMAGE_EXTENSIONS[self.value]

    def matches(self, data: bytes) -> bool:
        """Tell whether ``data`` carries this type's signature.

        The declared type is client-controlled; this checks it against
        what the bytes actually are, via :func:`sniff_mime`.

        Args:
            data (bytes): The file content, or at least its first 32
                bytes (enough for every signature, including the
                compatible brands of an HEIF ``ftyp`` box).

        Returns:
            bool: ``True`` when :func:`sniff_mime` detects exactly this
            type in ``data``.
        """
        return sniff_mime(data) == self.value


_IMAGE_EXTENSIONS: dict[str, str] = {
    ImageMediaType.JPEG.value: "jpg",
    ImageMediaType.PNG.value: "png",
    ImageMediaType.GIF.value: "gif",
    ImageMediaType.BMP.value: "bmp",
    ImageMediaType.WEBP.value: "webp",
    ImageMediaType.HEIC.value: "heic",
}
"""Media type → file extension for :attr:`ImageMediaType.extension`."""


__all__: list[str] = [
    "DOCX_MEDIA_TYPE",
    "PPTX_MEDIA_TYPE",
    "XLSX_MEDIA_TYPE",
    "ImageMediaType",
    "guess_media_type",
]
