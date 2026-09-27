"""HTTP validators and byte ranges shared by the SDK's response builders.

Private helpers behind the conditional-GET and ``Range`` handling of
:meth:`tempest_fastapi_sdk.AsyncMinIOClient.download_response` and the
``ETag`` check of the response-cache middleware. They implement the parts
of RFC 9110 those callers need: ``If-None-Match`` (weak comparison),
``If-Modified-Since``, ``If-Range`` and a **single** ``bytes`` range. A
multi-range request is deliberately reported as "serve the whole
representation", which RFC 9110 section 14.2 allows, instead of building a
``multipart/byteranges`` body.

Everything here is pure: no I/O, no Starlette import, so it is cheap to
import from anywhere in the package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime


@dataclass(frozen=True, slots=True)
class ByteRange:
    """One satisfiable byte range of a representation.

    Attributes:
        start (int): First byte offset, inclusive.
        end (int): Last byte offset, inclusive.
        size (int): Size of the whole representation in bytes.
    """

    start: int
    end: int
    size: int

    @property
    def length(self) -> int:
        """Return how many bytes the range covers.

        Returns:
            int: ``end - start + 1``.
        """
        return self.end - self.start + 1

    def content_range(self) -> str:
        """Return the ``Content-Range`` value for a ``206`` response.

        Returns:
            str: ``bytes <start>-<end>/<size>``.
        """
        return f"bytes {self.start}-{self.end}/{self.size}"


class RangeNotSatisfiableError(Exception):
    """A single ``bytes`` range that lies entirely outside the object.

    Raised by :func:`resolve_byte_range`; the caller answers ``416`` with
    ``Content-Range: bytes */<size>``.

    Attributes:
        size (int): Size of the whole representation in bytes.
    """

    def __init__(self, size: int) -> None:
        """Store the representation size for the ``416`` response.

        Args:
            size (int): Size of the whole representation in bytes.
        """
        super().__init__(f"range not satisfiable for a {size}-byte object")
        self.size: int = size

    def content_range(self) -> str:
        """Return the ``Content-Range`` value for a ``416`` response.

        Returns:
            str: ``bytes */<size>``.
        """
        return f"bytes */{self.size}"


def etag_matches(if_none_match: str, etag: str) -> bool:
    """Return whether ``If-None-Match`` covers ``etag``.

    Uses the weak comparison RFC 9110 prescribes for ``If-None-Match``:
    a ``W/`` prefix on either side is ignored.

    Args:
        if_none_match (str): The raw ``If-None-Match`` header value.
        etag (str): The current ETag, quoted (``'"abc"'``).

    Returns:
        bool: ``True`` for ``*`` or when ``etag`` is one of the listed tags.
    """
    candidate = if_none_match.strip()
    if candidate == "*":
        return True
    tags = {tag.strip().removeprefix("W/") for tag in candidate.split(",")}
    return etag.removeprefix("W/") in tags


def to_http_datetime(value: datetime) -> datetime:
    """Normalise a timestamp to what an HTTP-date can carry.

    HTTP-dates have one-second resolution and are always UTC, so the
    microseconds are dropped and a naive value is read as UTC.

    Args:
        value (datetime): The timestamp, aware or naive.

    Returns:
        datetime: An aware UTC timestamp without microseconds.
    """
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(microsecond=0)


def format_http_date(value: datetime) -> str:
    """Format a timestamp as an IMF-fixdate (``Last-Modified`` style).

    Args:
        value (datetime): The timestamp, aware or naive (read as UTC).

    Returns:
        str: For example ``"Thu, 01 Jan 2026 00:00:00 GMT"``.
    """
    return format_datetime(to_http_datetime(value), usegmt=True)


def parse_http_date(value: str) -> datetime | None:
    """Parse an HTTP-date header value.

    Args:
        value (str): The raw header value.

    Returns:
        datetime | None: The aware UTC timestamp, or ``None`` when the
        value is not a valid date — RFC 9110 says to ignore it then.
    """
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None
    return to_http_datetime(parsed)


def is_not_modified(
    *,
    if_none_match: str | None,
    if_modified_since: str | None,
    etag: str | None,
    last_modified: datetime | None,
) -> bool:
    """Decide whether a ``GET`` can be answered with ``304 Not Modified``.

    Follows the precedence of RFC 9110 section 13.2.2: when
    ``If-None-Match`` is present it alone decides, and ``If-Modified-Since``
    is only consulted in its absence.

    Args:
        if_none_match (str | None): The ``If-None-Match`` header, if sent.
        if_modified_since (str | None): The ``If-Modified-Since`` header,
            if sent.
        etag (str | None): The current quoted ETag, or ``None`` when the
            object has none.
        last_modified (datetime | None): The object's modification time.

    Returns:
        bool: ``True`` when the client's cached copy is still current.
    """
    if if_none_match is not None:
        return etag is not None and etag_matches(if_none_match, etag)
    if if_modified_since is None or last_modified is None:
        return False
    since = parse_http_date(if_modified_since)
    if since is None:
        return False
    return to_http_datetime(last_modified) <= since


def if_range_allows(
    if_range: str | None,
    *,
    etag: str | None,
    last_modified: datetime | None,
) -> bool:
    """Decide whether the ``Range`` header may be honoured.

    ``If-Range`` is what a resuming download manager sends: "give me the
    rest only if the file is still the one I started". A mismatch means the
    range is ignored and the whole object is served, so the client never
    stitches bytes of two different versions together. The ETag form uses
    strong comparison (a weak tag never matches), the date form requires an
    exact match, as RFC 9110 section 13.1.5 prescribes.

    Args:
        if_range (str | None): The ``If-Range`` header, if sent.
        etag (str | None): The current quoted ETag.
        last_modified (datetime | None): The object's modification time.

    Returns:
        bool: ``True`` when there is no ``If-Range`` or it still matches.
    """
    if if_range is None:
        return True
    candidate = if_range.strip()
    if candidate.startswith(('"', "W/")):
        return etag is not None and candidate == etag and not etag.startswith("W/")
    if last_modified is None:
        return False
    since = parse_http_date(candidate)
    return since is not None and since == to_http_datetime(last_modified)


def _is_digits(value: str, *, allow_empty: bool) -> bool:
    """Return whether ``value`` is a run of ASCII digits.

    ``str.isdigit`` alone accepts characters such as ``"²"`` that ``int``
    then rejects, so the ASCII check is what keeps a hostile header from
    raising instead of being ignored.

    Args:
        value (str): The candidate text.
        allow_empty (bool): Whether ``""`` counts as valid.

    Returns:
        bool: ``True`` for ASCII digits (or an allowed empty string).
    """
    if not value:
        return allow_empty
    return value.isascii() and value.isdigit()


def resolve_byte_range(header: str | None, size: int) -> ByteRange | None:
    """Resolve a ``Range`` header against a representation of ``size`` bytes.

    Accepts the three single-range forms — ``bytes=a-b``, ``bytes=a-`` and
    ``bytes=-n`` — clamping an end past the object to its last byte and a
    suffix longer than the object to the whole object.

    Args:
        header (str | None): The raw ``Range`` header value.
        size (int): Size of the whole representation in bytes.

    Returns:
        ByteRange | None: The range to serve with ``206``, or ``None`` when
        the whole object should be served with ``200``: no header, a unit
        other than ``bytes``, a syntactically invalid value (RFC 9110 says
        to ignore it), or more than one range.

    Raises:
        RangeNotSatisfiableError: When the single range starts at or past
            the end of the object, or is an empty suffix (``bytes=-0``).
    """
    if header is None:
        return None
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes" or not spec:
        return None
    if "," in spec:
        return None
    first, dash, last = spec.strip().partition("-")
    first, last = first.strip(), last.strip()
    if not dash or not _is_digits(first, allow_empty=True):
        return None
    if not _is_digits(last, allow_empty=True):
        return None
    if not first:
        if not last:
            return None
        suffix = int(last)
        if suffix == 0 or size == 0:
            raise RangeNotSatisfiableError(size)
        return ByteRange(start=max(size - suffix, 0), end=size - 1, size=size)
    start = int(first)
    end = int(last) if last else size - 1
    if last and end < start:
        return None
    if start >= size:
        raise RangeNotSatisfiableError(size)
    return ByteRange(start=start, end=min(end, size - 1), size=size)


__all__: list[str] = [
    "ByteRange",
    "RangeNotSatisfiableError",
    "etag_matches",
    "format_http_date",
    "if_range_allows",
    "is_not_modified",
    "parse_http_date",
    "resolve_byte_range",
    "to_http_datetime",
]
