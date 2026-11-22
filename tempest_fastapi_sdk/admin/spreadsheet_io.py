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

import asyncio
import csv
import importlib.util
import io
import posixpath
import zipfile
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Final
from xml.etree import ElementTree

from tempest_fastapi_sdk.admin.csv_import import ADMIN_IMPORT_MAX_BYTES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tempest_fastapi_sdk.spreadsheet import CellValue

ADMIN_SPREADSHEET_EXTRA: Final[str] = "tempest-fastapi-sdk[spreadsheet]"
"""The install target the admin names when ``openpyxl`` is missing."""

XLSX_MEDIA_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
"""``Content-Type`` of an ``.xlsx`` workbook."""

ADMIN_XLSX_IMPORT_MAX_ROWS: Final[int] = ADMIN_IMPORT_MAX_BYTES // 16
"""Data rows an ``.xlsx`` import may hold (32 768 with the 512 KiB cap).

The import signs the CSV the workbook becomes, which may not pass
:data:`~tempest_fastapi_sdk.admin.csv_import.ADMIN_IMPORT_MAX_BYTES`, so a
sheet past this row count is refused while it is streamed instead of being
read whole first. It assumes a data row of at least 16 bytes of CSV: a
sheet of shorter rows can be refused although its CSV would fit.
"""

ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES: Final[int] = 32 * ADMIN_IMPORT_MAX_BYTES
"""Bytes the workbook's parts may decompress to on import (16 MiB).

Measured with openpyxl 3.1.5 on sheets whose CSV is about 512 KiB, the
parts decompressed to 5.8 to 27.1 times the CSV (27.1 with one-character
cells), so 32 times the cap admits every one of them while refusing far
less than the reader's general 100 MiB.
"""

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


class XlsxUncachedFormulaError(Exception):
    """Formula cells of an ``.xlsx`` upload carry no calculated value.

    A workbook written by a library (openpyxl, pandas) stores the formula
    but not its result (no ``<v>`` element); read for values, the cell is
    empty, and importing it would store ``NULL`` without a word. The import
    refuses instead, naming the cells.

    Attributes:
        cells (list[str]): The cell coordinates, in sheet order (``B2``).
    """

    def __init__(self, cells: list[str]) -> None:
        """Keep the offending cells.

        Args:
            cells (list[str]): The cell coordinates.
        """
        super().__init__(", ".join(cells))
        self.cells: list[str] = cells


_MAIN_NS: Final[str] = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RELS_NS: Final[str] = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL_NS: Final[str] = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
)


def _part_path(base: str, target: str) -> str:
    """Resolve a relationship target against the part that declares it.

    Args:
        base (str): Directory of the declaring part (``"xl"``), ``""`` at
            the package root.
        target (str): The ``Target`` attribute, absolute (``/xl/...``) or
            relative to ``base``.

    Returns:
        str: The member name inside the ZIP.
    """
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(base, target))


def _first_sheet_part(archive: zipfile.ZipFile) -> str | None:
    """Find the ZIP member of the workbook's first worksheet.

    Args:
        archive (zipfile.ZipFile): The opened workbook.

    Returns:
        str | None: The member name, or ``None`` when the package does not
        say (the reader already opened it, so this only skips the check).
    """
    root_rels = ElementTree.fromstring(archive.read("_rels/.rels"))
    workbook = next(
        (
            _part_path("", rel.get("Target", ""))
            for rel in root_rels.iter(f"{_RELS_NS}Relationship")
            if rel.get("Type", "").endswith("/officeDocument")
        ),
        None,
    )
    if workbook is None:
        return None
    base = posixpath.dirname(workbook)
    rels_name = posixpath.join(base, "_rels", posixpath.basename(workbook) + ".rels")
    targets = {
        rel.get("Id"): rel
        for rel in ElementTree.fromstring(archive.read(rels_name)).iter(
            f"{_RELS_NS}Relationship"
        )
    }
    for sheet in ElementTree.fromstring(archive.read(workbook)).iter(
        f"{_MAIN_NS}sheet"
    ):
        rel = targets.get(sheet.get(f"{_DOC_REL_NS}id"))
        if rel is not None and rel.get("Type", "").endswith("/worksheet"):
            return _part_path(base, rel.get("Target", ""))
    return None


def _column_letters(reference: str) -> str:
    """Return the column part of a cell reference (``"AB12"`` → ``"AB"``).

    Args:
        reference (str): The ``r`` attribute of a cell.

    Returns:
        str: The column letters.
    """
    return "".join(ch for ch in reference if ch.isalpha())


def _letters_for(index: int) -> str:
    """Return the column letters of a 1-based column index.

    Args:
        index (int): The column index (``1`` is ``A``).

    Returns:
        str: The letters.
    """
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _column_index(letters: str) -> int:
    """Return the 1-based index of column letters (``"A"`` is ``1``).

    Args:
        letters (str): The column letters.

    Returns:
        int: The index.
    """
    index = 0
    for ch in letters.upper():
        index = index * 26 + ord(ch) - ord("A") + 1
    return index


def _uncached_formulas(raw: bytes) -> list[str]:
    """Find formula cells of the first tab that store no calculated value.

    Reads the worksheet's XML once, streaming. A formula cell (``<f>``)
    counts as cached when its ``<v>`` holds text, or when it is empty on a
    string-typed cell (``t="str"``): that is how Excel stores a formula that
    evaluated to empty text, such as ``=IF(A2="","",...)``. No ``<v>``, or
    an empty one on any other type, is what a library writes without
    calculating — openpyxl 3.1.5 writes ``<f>1+1</f><v></v>`` with no
    ``t``. Only the columns that have a header in row 1 are checked: a
    column the import ignores does not refuse the file.

    Args:
        raw (bytes): The workbook, already opened and size-checked by the
            reader.

    Returns:
        list[str]: Coordinates (``B2``) of the formula cells without a
        cached value, in sheet order.
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        part = _first_sheet_part(archive)
        if part is None or part not in archive.namelist():
            return []
        headed: set[str] = set()
        missing: list[tuple[str, str]] = []
        row_number = 0
        column_number = 0
        with archive.open(part) as stream:
            for event, element in ElementTree.iterparse(
                stream, events=("start", "end")
            ):
                tag = element.tag
                if event == "start":
                    if tag == f"{_MAIN_NS}row":
                        declared = element.get("r")
                        row_number = int(declared) if declared else row_number + 1
                        column_number = 0
                    continue
                if tag != f"{_MAIN_NS}c":
                    if tag == f"{_MAIN_NS}row":
                        element.clear()
                    continue
                reference = element.get("r")
                if reference:
                    column_number = _column_index(_column_letters(reference))
                else:
                    column_number += 1
                letters = _letters_for(column_number)
                value = element.find(f"{_MAIN_NS}v")
                has_value = (
                    value is not None or element.find(f"{_MAIN_NS}is") is not None
                )
                if row_number == 1:
                    if has_value:
                        headed.add(letters)
                    continue
                if element.find(f"{_MAIN_NS}f") is None:
                    continue
                cached = value is not None and (
                    bool(value.text) or element.get("t") == "str"
                )
                if not cached:
                    missing.append((letters, f"{letters}{row_number}"))
        return [cell for letters, cell in missing if letters in headed]


def _convert(raw: bytes) -> str:
    """Read the first tab and turn it into CSV text, in the calling thread.

    Args:
        raw (bytes): The uploaded workbook.

    Returns:
        str: The CSV text, header first; ``""`` when the tab has no data
        row.

    Raises:
        InvalidSpreadsheetError: When the bytes are not an ``.xlsx``.
        SpreadsheetTooLargeError: When the workbook passes a reader limit.
        XlsxUncachedFormulaError: When a formula cell has no cached value.
    """
    from tempest_fastapi_sdk.spreadsheet import read_xlsx

    records = read_xlsx(
        raw,
        max_rows=ADMIN_XLSX_IMPORT_MAX_ROWS,
        max_uncompressed_bytes=ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES,
    )
    uncached = _uncached_formulas(raw)
    if uncached:
        raise XlsxUncachedFormulaError(uncached)
    if not records:
        return ""
    header = list(records[0])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for record in records:
        writer.writerow([_cell_text(record.get(name)) for name in header])
    return buffer.getvalue()


async def xlsx_to_csv(raw: bytes) -> str:
    """Read the first tab of an ``.xlsx`` upload as CSV text.

    Row 1 is the header, as in a CSV import; blank rows are skipped. The
    whole conversion — the reader's parse, capped at
    :data:`ADMIN_XLSX_IMPORT_MAX_ROWS` rows and
    :data:`ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES` decompressed, and the
    scan for formulas without a cached value (:func:`_uncached_formulas`) —
    runs in one worker thread behind the same per-loop semaphore as
    :func:`~tempest_fastapi_sdk.spreadsheet.read_xlsx_async`. The scan runs
    only after the reader accepted the file, so it never reads past those
    limits.

    Args:
        raw (bytes): The uploaded workbook.

    Returns:
        str: The CSV text, header first; ``""`` when the tab has no data
        row.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
        InvalidSpreadsheetError: When the bytes are not an ``.xlsx``.
        SpreadsheetTooLargeError: When the workbook passes a reader limit.
        XlsxUncachedFormulaError: When a formula cell of a column with a
            header has no cached value.
    """
    from tempest_fastapi_sdk.spreadsheet.reader import _read_semaphore

    async with _read_semaphore(None):
        return await asyncio.to_thread(_convert, raw)


__all__: list[str] = [
    "ADMIN_SPREADSHEET_EXTRA",
    "ADMIN_XLSX_IMPORT_MAX_ROWS",
    "ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES",
    "XLSX_MEDIA_TYPE",
    "XlsxUncachedFormulaError",
    "build_xlsx",
    "is_xlsx_upload",
    "spreadsheet_available",
    "xlsx_to_csv",
]
