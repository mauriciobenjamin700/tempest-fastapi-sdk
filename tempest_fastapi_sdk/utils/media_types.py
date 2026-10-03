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


__all__: list[str] = [
    "DOCX_MEDIA_TYPE",
    "PPTX_MEDIA_TYPE",
    "XLSX_MEDIA_TYPE",
    "guess_media_type",
]
