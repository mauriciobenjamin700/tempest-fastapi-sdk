"""How the admin templates show a column name and a cell value.

The list, the detail page and the form used to print ``str(value)`` under
the raw column key: ``total_cents`` as a header, ``True`` as a cell, a
datetime with microseconds and ``+00:00``, a UUID wrapping over four
lines. These helpers turn each value into a :class:`CellView` — a kind the
template styles (badge, monospace, muted dash) plus the text it prints and
the full value for the ``title`` tooltip — and each key into a label.

Nothing here touches the stored value: export, the form's ``value=`` and
the SQL console keep the exact representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, Literal
from uuid import UUID

CellKind = Literal["none", "bool", "datetime", "uuid", "enum", "number", "text"]
"""What a cell is, so the template picks the styling."""

UUID_PREFIX_LENGTH: int = 8
"""Characters of a UUID kept in a cell; the whole value is in the tooltip."""

ACRONYMS: frozenset[str] = frozenset(
    {"api", "cpf", "cnpj", "id", "ip", "json", "mfa", "sku", "sql", "url", "uuid"}
)
"""Words a label prints in upper case (``ip_address`` -> ``IP address``)."""


@dataclass(frozen=True, slots=True)
class CellView:
    """One value, ready for the template.

    Attributes:
        kind (CellKind): Drives the markup — ``"bool"`` and ``"enum"`` render
            as badges, ``"uuid"`` in monospace, ``"none"`` as a muted dash.
        text (str): What the cell prints.
        title (str | None): The full value, for the ``title`` tooltip, when
            :attr:`text` shortens it.
        state (str | None): For ``"bool"``, ``"yes"`` or ``"no"``, so the
            badge is coloured by value.
        suffix (str | None): For an aware ``"datetime"``, its offset
            (``"UTC"``, ``"-03:00"``), printed muted after :attr:`text` so
            the hour is never read in the wrong zone.
    """

    kind: CellKind
    text: str
    title: str | None = None
    state: str | None = None
    suffix: str | None = None


def humanize_field(name: str) -> str:
    """Turn a column key into the label a person reads.

    Sentence case, a trailing ``_id`` dropped (``category_id`` is the
    category), the words in :data:`ACRONYMS` upper-cased. ``id`` alone
    stays ``ID``.

    Args:
        name (str): The column key, such as ``"customer_email"``.

    Returns:
        str: The label, such as ``"Customer email"``.
    """
    key = name.strip("_").lower()
    if key.endswith("_id") and key != "_id":
        key = key.removesuffix("_id")
    words = [word for word in key.split("_") if word]
    if not words:
        return name
    shown = [word.upper() if word in ACRONYMS else word for word in words]
    first = shown[0]
    shown[0] = first if first.isupper() else first.capitalize()
    return " ".join(shown)


def format_datetime(value: datetime) -> str:
    """Print a datetime to the minute.

    Seconds and microseconds go to the tooltip; the offset is
    :func:`offset_label`, printed beside it.

    Args:
        value (datetime): The value, already in the admin's
            ``display_timezone`` when one is set.

    Returns:
        str: ``YYYY-MM-DD HH:MM``.
    """
    return value.strftime("%Y-%m-%d %H:%M")


def offset_label(value: datetime) -> str | None:
    """Name the zone offset of an aware datetime, compactly.

    Args:
        value (datetime): The value.

    Returns:
        str | None: ``"UTC"`` for a zero offset, ``"+HH:MM"`` / ``"-HH:MM"``
        otherwise, ``None`` for a naive datetime.
    """
    offset = value.utcoffset()
    if offset is None:
        return None
    if not offset:
        return "UTC"
    stamp = value.strftime("%z")
    return f"{stamp[:3]}:{stamp[3:5]}"


def cell_view(value: Any) -> CellView:
    """Classify a value and pick what the cell prints.

    Args:
        value (Any): The ORM attribute value.

    Returns:
        CellView: The kind, the text and, when shortened, the full value.
    """
    if value is None:
        return CellView(kind="none", text="—")
    if isinstance(value, bool):
        return CellView(
            kind="bool",
            text="Yes" if value else "No",
            state="yes" if value else "no",
        )
    if isinstance(value, Enum):
        return CellView(kind="enum", text=humanize_field(str(value.name)))
    if isinstance(value, datetime):
        return CellView(
            kind="datetime",
            text=format_datetime(value),
            title=value.isoformat(),
            suffix=offset_label(value),
        )
    if isinstance(value, date | time):
        return CellView(kind="datetime", text=value.isoformat())
    if isinstance(value, UUID):
        full = str(value)
        return CellView(kind="uuid", text=full[:UUID_PREFIX_LENGTH], title=full)
    if isinstance(value, int | float):
        return CellView(kind="number", text=format_number(value))
    return CellView(kind="text", text=str(value))


def format_number(value: Any) -> str:
    """Print a number without the ``.0`` of a whole float.

    ``MetricPartition`` and ``MetricTrend`` carry floats, so a count of 17
    reached the dashboard as ``17.0``. A whole float prints as an integer;
    anything else keeps up to two decimals.

    Args:
        value (Any): The value; non-numbers come back as ``str(value)``.

    Returns:
        str: The printed number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def short_identity(identity: Any) -> str:
    """Shorten a record identity for a page title.

    Args:
        identity (Any): The value from the detail URL.

    Returns:
        str: The first :data:`UUID_PREFIX_LENGTH` characters of a UUID, or
        the identity unchanged.
    """
    text = str(identity)
    try:
        UUID(text)
    except ValueError:
        return text
    return text[:UUID_PREFIX_LENGTH]


__all__: list[str] = [
    "ACRONYMS",
    "UUID_PREFIX_LENGTH",
    "CellKind",
    "CellView",
    "cell_view",
    "format_datetime",
    "format_number",
    "humanize_field",
    "offset_label",
    "short_identity",
]
