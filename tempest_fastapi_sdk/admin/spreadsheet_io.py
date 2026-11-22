"""``.xlsx`` export and import of the admin.

Built on :mod:`tempest_fastapi_sdk.spreadsheet`.

The export writes the same rows as the CSV export into one sheet through
:class:`~tempest_fastapi_sdk.spreadsheet.SheetWriter`, which already keeps a
text cell that starts with ``=`` as text. The import reads the first tab with
:func:`~tempest_fastapi_sdk.spreadsheet.read_xlsx_async` and turns it into the
CSV text the existing two-step import validates, previews and signs, so an
``.xlsx`` goes through exactly the rules a CSV does.

Both need the ``[spreadsheet]`` extra (``openpyxl``). It is looked up, never
imported, by :func:`spreadsheet_available`, so the admin keeps working without
it: the ``.xlsx`` button is not rendered and the routes answer a message that
names the extra.
"""

from __future__ import annotations

import csv
import importlib.util
import io
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tempest_fastapi_sdk.spreadsheet import CellValue

ADMIN_SPREADSHEET_EXTRA: Final[str] = "tempest-fastapi-sdk[spreadsheet]"
"""The install target the admin names when ``openpyxl`` is missing."""

XLSX_MEDIA_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
"""``Content-Type`` of an ``.xlsx`` workbook."""

_ZIP_SIGNATURE: Final[bytes] = b"PK\x03\x04"
"""Leading bytes of a ZIP archive, which every ``.xlsx`` is."""

_SHEET_TITLE_MAX: Final[int] = 31
"""Longest worksheet name Excel opens."""

_SHEET_TITLE_FORBIDDEN: Final[str] = "[]:*?/\\"
"""Characters Excel refuses in a worksheet name."""


def spreadsheet_available() -> bool:
    """Whether the ``[spreadsheet]`` extra is installed.

    Returns:
        bool: ``True`` when ``openpyxl`` can be imported. The module is
        located, not imported, so asking costs nothing.
    """
    return importlib.util.find_spec("openpyxl") is not None


def is_xlsx_upload(filename: str | None, raw: bytes) -> bool:
    """Tell an ``.xlsx`` upload from a CSV one.

    Args:
        filename (str | None): The name the browser sent.
        raw (bytes): The uploaded bytes.

    Returns:
        bool: ``True`` when the name ends in ``.xlsx`` or the bytes open as
        a ZIP archive — a CSV never starts with ``PK\\x03\\x04``.
    """
    named = (filename or "").lower().endswith(".xlsx")
    return named or raw.startswith(_ZIP_SIGNATURE)


def build_xlsx(
    title: str, columns: Sequence[str], rows: Sequence[Sequence[CellValue]]
) -> bytes:
    """Write a header row and the data rows into a one-sheet workbook.

    Args:
        title (str): The sheet name; characters Excel refuses are dropped
            and it is cut at 31 characters.
        columns (Sequence[str]): Header texts, left to right.
        rows (Sequence[Sequence[CellValue]]): Cell values per row.

    Returns:
        bytes: The ``.xlsx`` file.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
    """
    from tempest_fastapi_sdk.spreadsheet import (
        Column,
        SheetWriter,
        new_workbook,
        workbook_to_bytes,
    )

    clean = "".join(ch for ch in title if ch not in _SHEET_TITLE_FORBIDDEN)
    sheet_title = clean[:_SHEET_TITLE_MAX] or "export"
    workbook = new_workbook(sheet_title)
    writer = SheetWriter(
        workbook[sheet_title], columns=[Column(name) for name in columns]
    )
    writer.header_row()
    for values in rows:
        writer.write_row(values)
    writer.freeze_below()
    return workbook_to_bytes(workbook)


def _cell_text(value: object) -> str:
    """Write one ``.xlsx`` cell as the text a CSV cell would hold.

    Args:
        value (object): The cell as ``openpyxl`` typed it.

    Returns:
        str: ``""`` for an empty cell, ``true`` / ``false`` for a boolean,
        ISO 8601 for a date, time or datetime (a datetime the file stores
        with no offset, as Excel does, stays without one), a whole float
        without its ``.0`` (``3.0`` is ``3``), ``str()`` otherwise.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


async def xlsx_to_csv(raw: bytes) -> str:
    """Read the first tab of an ``.xlsx`` upload as CSV text.

    Row 1 is the header, as in a CSV import; blank rows are skipped. The
    parse runs in a worker thread
    (:func:`~tempest_fastapi_sdk.spreadsheet.read_xlsx_async`) with that
    reader's default limits on rows and decompressed size.

    Args:
        raw (bytes): The uploaded workbook.

    Returns:
        str: The CSV text, header first; ``""`` when the tab has no data
        row.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
        InvalidSpreadsheetError: When the bytes are not an ``.xlsx``.
        SpreadsheetTooLargeError: When the workbook passes a reader limit.
    """
    from tempest_fastapi_sdk.spreadsheet import read_xlsx_async

    records = await read_xlsx_async(raw)
    if not records:
        return ""
    header = list(records[0])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for record in records:
        writer.writerow([_cell_text(record.get(name)) for name in header])
    return buffer.getvalue()


__all__: list[str] = [
    "ADMIN_SPREADSHEET_EXTRA",
    "XLSX_MEDIA_TYPE",
    "build_xlsx",
    "is_xlsx_upload",
    "spreadsheet_available",
    "xlsx_to_csv",
]
