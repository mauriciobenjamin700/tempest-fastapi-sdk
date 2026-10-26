"""Shapes shared by the presigned links of both storage backends.

:class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` and
:class:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient` answer the
same calls, so a service swaps one for the other through settings. The
size-capped upload (:class:`PresignedPost`) and the download overrides
(:func:`download_overrides`) live here so the two backends cannot drift on
what a link carries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tempest_fastapi_sdk.utils.download import build_content_disposition

MEDIA_TYPE_PATTERN: re.Pattern[str] = re.compile(
    r"[A-Za-z0-9][\w.+-]*/[A-Za-z0-9][\w.+-]*"
)
"""A bare ``type/subtype``: what a signed ``Content-Type`` override may be.

Parameters (``; charset=...``) are refused along with anything else, so a
value can never carry a line break into the header the store or the local
route answers with.
"""

POST_FILE_FIELD: str = "file"
"""Form field the browser sends the bytes in, last in the form.

S3 ignores every field after the file, so the file goes after
:attr:`PresignedPost.fields`.
"""


@dataclass(frozen=True, slots=True)
class PresignedPost:
    """A form upload the store accepts only within the signed limits.

    The browser sends ``multipart/form-data`` to :attr:`url` with every
    pair of :attr:`fields` and then the file in :data:`POST_FILE_FIELD`.
    A body outside ``[min_bytes, max_bytes]``, a different key or a
    different ``Content-Type`` is refused by the store itself.

    Attributes:
        url (str): Where the form is posted.
        fields (dict[str, str]): Form fields to send, unchanged, before the
            file.
        max_bytes (int): The largest file the store accepts.
    """

    url: str
    max_bytes: int
    fields: dict[str, str] = field(default_factory=dict)


def check_post_limits(*, min_bytes: int, max_bytes: int) -> None:
    """Refuse an empty or inverted size range before anything is signed.

    Args:
        min_bytes (int): Smallest accepted body.
        max_bytes (int): Largest accepted body.

    Raises:
        ValueError: When ``min_bytes`` is negative, ``max_bytes`` is not
            positive, or ``min_bytes`` exceeds ``max_bytes``.
    """
    if min_bytes < 0:
        raise ValueError("min_bytes must not be negative")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if min_bytes > max_bytes:
        raise ValueError("min_bytes must not exceed max_bytes")


def download_overrides(
    key: str,
    *,
    filename: str | None,
    media_type: str | None,
    as_attachment: bool | None,
) -> dict[str, str]:
    """Build the S3 ``response-*`` overrides a download link signs.

    Nothing is overridden unless asked: with every argument ``None`` the
    store answers with what it stored, as a plain presigned link always
    did. ``Content-Disposition`` is signed when ``filename`` or
    ``as_attachment`` is given, through
    :func:`~tempest_fastapi_sdk.utils.download.build_content_disposition`,
    so ``inline`` is granted only to an inline-safe ``media_type``.

    Args:
        key (str): Object key; its basename names the file when
            ``filename`` is ``None``.
        filename (str | None): Name presented to the client.
        media_type (str | None): Content type the store answers with —
            the one the server checked, not the one the uploader declared.
        as_attachment (bool | None): ``True`` forces a download, ``False``
            asks for ``inline``. ``None`` with a ``filename`` means
            ``True``.

    Returns:
        dict[str, str]: ``response-content-disposition`` and/or
        ``response-content-type``; empty when nothing was asked.

    Raises:
        ValueError: When ``media_type`` is not a bare ``type/subtype``.
    """
    if media_type is not None and not MEDIA_TYPE_PATTERN.fullmatch(media_type):
        raise ValueError(f"media_type must be type/subtype, got {media_type!r}")
    overrides: dict[str, str] = {}
    if filename is not None or as_attachment is not None:
        overrides["response-content-disposition"] = build_content_disposition(
            filename or key.rsplit("/", 1)[-1],
            as_attachment=True if as_attachment is None else as_attachment,
            media_type=media_type,
        )
    if media_type is not None:
        overrides["response-content-type"] = media_type
    return overrides


__all__: list[str] = [
    "MEDIA_TYPE_PATTERN",
    "POST_FILE_FIELD",
    "PresignedPost",
    "check_post_limits",
    "download_overrides",
]
