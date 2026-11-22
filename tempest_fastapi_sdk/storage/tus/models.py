"""The upload record and the header codecs of the tus router.

Nothing here does I/O: :class:`TusUpload` is the state a store persists, and
the functions parse and build the ``Upload-Metadata`` and date headers the
protocol puts on the wire.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

from tempest_fastapi_sdk.exceptions.tus import TusInvalidHeaderException

TUS_VERSION: str = "1.0.0"
"""The tus protocol version the router speaks, sent as ``Tus-Resumable``."""

TUS_UPLOAD_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
"""Shape of an upload id a store accepts.

Generated ids are 32 characters of URL-safe base64 (192 random bits). The
pattern keeps a path parameter from ever naming a file outside the store:
no ``/``, no ``.``, no ``\\``.
"""

_METADATA_KEY_PATTERN: re.Pattern[str] = re.compile(r"^[^\s,]+$")
"""A metadata key: no spaces and no commas, as tus 1.0.0 requires."""

_FILENAME_KEYS: tuple[str, ...] = ("filename", "name")
"""Metadata keys read as the file's name, in order.

``filename`` is what ``tempest-react-sdk``'s examples send; ``name`` is what
``tus-js-client`` and Uppy send.
"""

_FILENAME_EXTRA_CHARS: frozenset[str] = frozenset({"-", "_", ".", " ", "(", ")"})
"""Non-alphanumeric characters kept by :attr:`TusUpload.filename`."""

_FILENAME_MAX_LENGTH: int = 255
"""Longest name :attr:`TusUpload.filename` returns, the common filesystem cap."""


def parse_upload_metadata(header: str | None) -> dict[str, str]:
    """Decode an ``Upload-Metadata`` header.

    The value is a comma-separated list of ``key base64(value)`` pairs; a key
    may come alone, meaning an empty value. Values are decoded as UTF-8,
    which is what ``tempest-react-sdk`` encodes before base64 so a name with
    accents survives the header.

    Args:
        header (str | None): The raw header. ``None`` or blank yields ``{}``.

    Returns:
        dict[str, str]: The decoded pairs.

    Raises:
        TusInvalidHeaderException: When a key is repeated or malformed, or a
            value is not padded base64 of UTF-8 text.
    """
    if header is None or not header.strip():
        return {}
    result: dict[str, str] = {}
    for raw_pair in header.split(","):
        pair = raw_pair.strip()
        key, _, encoded = pair.partition(" ")
        encoded = encoded.strip()
        if not key or not _METADATA_KEY_PATTERN.match(key) or key in result:
            raise TusInvalidHeaderException(details={"header": "Upload-Metadata"})
        try:
            value = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise TusInvalidHeaderException(
                details={"header": "Upload-Metadata"},
            ) from None
        result[key] = value
    return result


def encode_upload_metadata(metadata: dict[str, str]) -> str:
    """Encode metadata back into an ``Upload-Metadata`` header value.

    Args:
        metadata (dict[str, str]): Plain pairs.

    Returns:
        str: ``key base64(value)`` pairs joined by commas; ``""`` for no pairs.
    """
    return ",".join(
        f"{key} {base64.b64encode(value.encode('utf-8')).decode('ascii')}"
        for key, value in metadata.items()
    )


def http_date(moment: datetime) -> str:
    """Format a moment as an RFC 9110 date, the form ``Upload-Expires`` uses.

    Args:
        moment (datetime): An aware datetime.

    Returns:
        str: E.g. ``"Wed, 25 Jun 2026 16:00:00 GMT"``.
    """
    return format_datetime(moment.astimezone(UTC), usegmt=True)


def sanitize_upload_filename(name: str | None) -> str | None:
    """Reduce a client-supplied file name to a safe base name.

    The name comes from ``Upload-Metadata``, which the client writes freely:
    it can carry directory parts (``../../etc/passwd``), control characters
    that split a header, or nothing at all. Only the last path component is
    kept, normalized to NFC, with letters, digits and ``-_. ()`` — accents
    stay. Leading dots are dropped so the name is never hidden or ``..``.

    Args:
        name (str | None): The raw name.

    Returns:
        str | None: The cleaned name, or ``None`` when nothing usable is left.
    """
    if not name:
        return None
    base = re.split(r"[\\/]", name)[-1]
    normalized = unicodedata.normalize("NFC", base)
    cleaned = "".join(
        char for char in normalized if char.isalnum() or char in _FILENAME_EXTRA_CHARS
    ).strip()
    cleaned = cleaned.lstrip(".").strip()
    if not cleaned:
        return None
    return cleaned[:_FILENAME_MAX_LENGTH]


@dataclass
class TusUpload:
    """One resumable upload, as a tus store keeps it.

    The record is the authority on how many bytes are stored: a chunk is
    written first and the record saved after, so a crash between the two
    leaves ``offset`` at the last confirmed byte and the next ``PATCH``
    overwrites whatever was half-written past it.

    Attributes:
        id (str): Unguessable id, the last segment of the upload URL.
        length (int): Total size the client declared in ``Upload-Length``.
        offset (int): Bytes stored so far.
        metadata (dict[str, str]): Decoded ``Upload-Metadata`` pairs, as the
            client sent them. Read the name through :attr:`filename`, never
            from here, before using it in a path.
        owner (str | None): ``str()`` of what ``current_user_id`` resolved
            when the upload was created; ``None`` for an anonymous upload.
        created_at (datetime): Creation time, UTC.
        expires_at (datetime | None): When an incomplete upload stops being
            resumable. Renewed by every accepted ``PATCH``; ``None`` when the
            router has no ``expiration``.
        idempotency_digest (str | None): SHA-256 of owner and
            ``Idempotency-Key``, when the creation carried one.
        notified (bool): Whether ``on_complete`` returned without raising.
            A complete upload with ``notified`` false is one the service
            still has to handle.
    """

    id: str
    length: int
    offset: int = 0
    metadata: dict[str, str] = field(default_factory=dict)
    owner: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    idempotency_digest: str | None = None
    notified: bool = False

    @property
    def completed(self) -> bool:
        """Whether every declared byte is stored.

        Returns:
            bool: ``offset == length``.
        """
        return self.offset >= self.length

    @property
    def filename(self) -> str | None:
        """The client's file name, sanitized for use in a path or header.

        Read from the ``filename`` metadata key, then ``name``, through
        :func:`sanitize_upload_filename`.

        Returns:
            str | None: The safe name, or ``None`` when absent or unusable.
        """
        for key in _FILENAME_KEYS:
            cleaned = sanitize_upload_filename(self.metadata.get(key))
            if cleaned is not None:
                return cleaned
        return None

    def is_expired(self, now: datetime | None = None) -> bool:
        """Whether this upload is incomplete and past ``expires_at``.

        A complete upload never expires: its bytes are the service's now.

        Args:
            now (datetime | None): The moment to compare with. Defaults to
                the current UTC time.

        Returns:
            bool: ``True`` when the upload can no longer be resumed.
        """
        if self.completed or self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at

    def to_json(self) -> str:
        """Serialize the record for a store.

        Returns:
            str: A JSON object.
        """
        payload: dict[str, Any] = {
            "id": self.id,
            "length": self.length,
            "offset": self.offset,
            "metadata": self.metadata,
            "owner": self.owner,
            "created_at": self.created_at.isoformat(),
            "expires_at": None
            if self.expires_at is None
            else self.expires_at.isoformat(),
            "idempotency_digest": self.idempotency_digest,
            "notified": self.notified,
        }
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> TusUpload:
        """Rebuild a record written by :meth:`to_json`.

        Args:
            raw (str | bytes): The stored JSON.

        Returns:
            TusUpload: The record.
        """
        data: dict[str, Any] = json.loads(raw)
        expires_raw: str | None = data.get("expires_at")
        return cls(
            id=str(data["id"]),
            length=int(data["length"]),
            offset=int(data["offset"]),
            metadata={
                str(k): str(v) for k, v in dict(data.get("metadata") or {}).items()
            },
            owner=None if data.get("owner") is None else str(data["owner"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            expires_at=None
            if expires_raw is None
            else datetime.fromisoformat(expires_raw),
            idempotency_digest=data.get("idempotency_digest"),
            notified=bool(data.get("notified", False)),
        )


__all__: list[str] = [
    "TUS_UPLOAD_ID_PATTERN",
    "TUS_VERSION",
    "TusUpload",
    "encode_upload_metadata",
    "http_date",
    "parse_upload_metadata",
    "sanitize_upload_filename",
]
