"""Read ``.xlsx`` workbooks as rows — an upload, a file, a downloaded export.

The inverse of :mod:`~tempest_fastapi_sdk.spreadsheet.writer`. Each tab is
read the way :mod:`~tempest_fastapi_sdk.spreadsheet.google` reads a CSV
export: row 1 is the header, every later row becomes a ``dict`` keyed by
it, blank rows are skipped without shifting the numbering, and the
``_as`` variant validates each row into a Pydantic model and reports the
first failure by its row number in the sheet.

What changes from CSV is the cell. A CSV carries the *formatted* text
(``"R$ 1.234,56"``, ``"04/10/2026"``, depending on the sheet's locale);
an ``.xlsx`` stores the value, so a cell comes back as the Python type
``openpyxl`` reads — see :data:`XlsxCellValue`.

An ``.xlsx`` is a ZIP, so every reader is bounded before the engine runs:
the decompressed size and the compression ratio are checked against the
ZIP's central directory, and the rows of a tab are counted as they are
read. Past a limit the reader raises :class:`SpreadsheetTooLargeError`; it
never returns part of a sheet. The defaults, and the measurements behind
them, are :data:`DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`,
:data:`DEFAULT_XLSX_MAX_COMPRESSION_RATIO` and :data:`DEFAULT_XLSX_MAX_ROWS`.

    from pydantic import BaseModel

    from tempest_fastapi_sdk.spreadsheet import read_xlsx_as


    class Sale(BaseModel):
        produto: str
        total: float


    def load(data: bytes) -> list[Sale]:
        return read_xlsx_as(data, Sale, sheet="Vendas")

Needs the ``[spreadsheet]`` extra (``openpyxl``). The engine is imported
at first use, so importing this module works without it; the call raises
:class:`ImportError` naming the extra.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from os import PathLike
from typing import IO, TYPE_CHECKING, Any, Final, TypeVar

from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.upload import FileTooLargeException

if TYPE_CHECKING:
    from openpyxl.workbook import Workbook

ModelT = TypeVar("ModelT", bound=BaseModel)
CellT = TypeVar("CellT")

XlsxCellValue = str | int | float | bool | datetime | time | timedelta | None
"""What a cell of a read ``.xlsx`` holds.

The type ``openpyxl`` (``data_only=True``) hands back, unchanged:

* a number is ``int`` or ``float`` — whichever the file wrote. The Google
  export writes ``30.0`` and ``50`` in the same column, so declare the
  field ``float`` or ``Decimal``, not ``int``. Currency is a plain number
  (``84.39``); a percentage is the **ratio** (``0.4`` for ``40%``).
* a date is a ``datetime`` — also for a date-only cell, at midnight; a
  time-only cell is a ``time``, a duration format a ``timedelta``.
* a formula is its **cached result**; a formula whose result is the empty
  string reads as ``None``, like an empty cell.
* an error cell (``#DIV/0!``) is its text.
"""

XlsxSource = bytes | str | PathLike[str] | IO[bytes]
"""Where a workbook is read from.

``bytes`` (an upload read into memory, an HTTP body), a filesystem path,
or a binary file object — ``UploadFile.file`` included.
"""

DEFAULT_XLSX_MAX_ROWS: Final[int] = 100_000
"""Data rows a tab may hold before the readers refuse it.

Counted per tab, blank rows excluded, while the tab is streamed: row
``max_rows + 1`` raises before it is kept, so the refused part of the sheet
is never held in memory.

The arithmetic, measured with ``openpyxl`` 3.1.5 on CPython 3.11 (one run
per size): a generated tab of 8 columns (id, two texts, three numbers, a
date, a status) read by :func:`read_xlsx` peaked at 291.5 MB of RSS with
200 000 rows and at 959.1 MB with 1 000 000 rows — about **834 bytes per
row** — and took 15.6 s and 78.1 s, about **78 µs per row**, on top of the
~125 MB the interpreter and the engine already hold. With this default the
200 000-row tab was refused at row 100 001 after 10.8 s, peaking at 214 MB.
The real workbook measured (a public Google Sheet, 16 tabs, 2 580 rows in
all) is 39 times below it.
"""

DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES: Final[int] = 100 * 1024 * 1024
"""Bytes the workbook's parts may add up to once decompressed (100 MiB).

An ``.xlsx`` is a ZIP, so the size of the upload says nothing about the
size the engine parses: a 1.7 MB file measured here inflates to 505 MB of
sheet XML and peaked at 2 278 MB of RSS in the unguarded reader. The sum is
taken from the ZIP's central directory before ``openpyxl`` opens anything.

The arithmetic: the same 8-column tab costs ~404 bytes of sheet XML per row
and ~834 bytes of RSS per row once read, so memory grows by about **2 bytes
per byte of XML**: a workbook right under 100 MiB holds about 215 MB of
rows (estimated from that ratio, not measured). A tab of the default
:data:`DEFAULT_XLSX_MAX_ROWS` rows in that shape is ~40 MB of XML, so for
narrow sheets the row limit trips first; this one binds wide sheets, many
tabs and large shared-string tables. The 16-tab Google Sheet measured is
6.1 MB decompressed.

It also bounds time. Without a ``<dimension>`` tag — the Google export
writes none — ``openpyxl`` scans the whole sheet XML when it opens the
workbook, before the first row is read (a 505 MB sheet took 45.8 s). A file
built to sit just under this limit, with the ratio check off, was refused
at row 100 001 after 11.4 s, peaking at 248 MB: the worst case the defaults
allow, as measured.

The central directory can lie, and ``zipfile`` is what keeps the lie
harmless: it stops decompressing a member at the size the directory
declares and checks the CRC there (measured on CPython 3.11 to 3.14). A member
that claims 4 KiB and holds 50 MB reads as 4 KiB and then fails — the CRC
does not match, or the XML is cut short — and either way surfaces as
:class:`InvalidSpreadsheetError`, never as 50 MB in memory.
"""

DEFAULT_XLSX_MAX_COMPRESSION_RATIO: Final[float] = 100.0
"""Decompressed-to-compressed size a single ZIP member may reach.

Checked on members of at least 1 MiB decompressed — a small member cannot
hurt, and its ratio says little. Measured on legitimate workbooks: the
sheets of the 16-tab Google Sheet compress 7.8 to 8.1 times (its worst member,
``styles.xml``, 11.6), ``openpyxl`` output with distinct rows 7.2, and a
tab of 200 000 identical rows — the most repetitive legitimate content —
14.7. A sheet XML built to inflate compressed 294.5 times, and deflate
reaches about 1 032 at most. 100 leaves the legitimate files more than six
times of headroom and refuses the bomb even when its total stays under
:data:`DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`.
"""

_RATIO_FLOOR_BYTES: Final[int] = 1024 * 1024
"""Members smaller than this (decompressed) skip the ratio check."""

_MISSING_DEPENDENCY: Final[str] = (
    "openpyxl is required to read .xlsx files. Install the extra: "
    'pip install "tempest-fastapi-sdk[spreadsheet]"'
)


class InvalidSpreadsheetError(AppException):
    """The source is not an ``.xlsx`` workbook.

    Raised for anything ``openpyxl`` cannot open as one: a CSV or an old
    binary ``.xls`` renamed to ``.xlsx``, an empty upload, a ZIP that is
    not an Office document. ``details["reason"]`` carries the engine's
    message for whoever debugs it.

    The status is ``422``: in the common case — a user uploading a file
    to an endpoint — the input is what is wrong.
    """

    message: str = "The file is not a valid .xlsx spreadsheet."
    code: str = "SPREADSHEET_INVALID"
    status_code: int = 422


class SheetNotFoundError(AppException):
    """The workbook has no tab with the requested name or index.

    ``details["sheet"]`` is what was asked for and ``details["available"]``
    lists the tab names the workbook does have, in tab order. Note that a
    tab name keeps its whitespace: a tab shown as ``Setembro`` may be
    called ``"Setembro "``.
    """

    message: str = "The spreadsheet has no such sheet."
    code: str = "SPREADSHEET_SHEET_NOT_FOUND"
    status_code: int = 422


class SpreadsheetRowError(AppException):
    """One row of a workbook tab did not validate against the target model.

    The same contract as
    :class:`~tempest_fastapi_sdk.spreadsheet.google.GoogleSheetRowError`
    (which is a subclass, so ``except SpreadsheetRowError`` covers both
    readers): ``details["row"]`` is the row number the spreadsheet shows —
    header = 1 — and ``details["errors"]`` is Pydantic's error list without
    the ``url`` and ``ctx`` keys. ``details["sheet"]`` names the tab.
    """

    message: str = "A row of the spreadsheet failed validation."
    code: str = "SPREADSHEET_ROW_INVALID"
    status_code: int = 422


class SpreadsheetTooLargeError(FileTooLargeException):
    """The workbook is past one of the limits the readers enforce.

    Raised before the work the limit protects: the decompressed size and
    the compression ratio are read from the ZIP's central directory before
    ``openpyxl`` opens the file, the row limit trips on the first row past
    it, and the download limit stops the transfer as soon as the body
    passes it. Nothing is truncated: a sheet over a limit is refused, never
    read in part.

    ``details["limit"]`` names the limit — ``"uncompressed_bytes"``,
    ``"compression_ratio"``, ``"rows"`` or ``"download_bytes"`` —,
    ``details["max"]`` its value, and ``details["actual"]`` what was found
    (for ``"rows"`` and ``"download_bytes"``, the count at which reading
    stopped). ``details["member"]`` names the ZIP member for the ratio and
    ``details["sheet"]`` the tab for the row limit.

    A subclass of :class:`~tempest_fastapi_sdk.exceptions.FileTooLargeException`,
    so the status is ``413`` like the SDK's upload limit, and
    ``except FileTooLargeException`` covers both.
    """

    message: str = "The spreadsheet is too large to read."
    code: str = "SPREADSHEET_TOO_LARGE"
    status_code: int = 413


def _check_limit(name: str, value: float | None) -> None:
    """Reject a limit that is not positive.

    Args:
        name (str): The parameter's name, for the message.
        value (float | None): The limit; ``None`` disables it.

    Raises:
        ValueError: If ``value`` is zero or negative.
    """
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be positive or None, got {value!r}.")


def _is_blank(value: object) -> bool:
    """Tell whether a cell counts as empty.

    ``None`` (an empty ``.xlsx`` cell) and ``""`` (an empty CSV cell) are
    blank; ``0`` and ``False`` are values.

    Args:
        value (object): The cell value.

    Returns:
        bool: ``True`` when the cell is empty.
    """
    return value is None or value == ""


def _number_rows(
    header: Sequence[str],
    records: Iterable[Sequence[CellT]],
    pad: CellT,
    *,
    max_rows: int | None = None,
    sheet: str | None = None,
) -> list[tuple[int, dict[str, CellT]]]:
    """Key the records under a header row and number them as the sheet does.

    Shared by the CSV and the ``.xlsx`` readers, so both define the edge
    cases the same way: a record blank within the header's width is
    skipped but still counts toward the numbering; a record shorter than
    the header is padded with ``pad``; cells beyond the header's width are
    ignored. Header names are taken verbatim, so an empty header cell is
    the key ``""`` and a repeated name keeps the **last** column's value.

    ``records`` is consumed lazily, so with ``max_rows`` the records after
    the first one over the limit are never read.

    Args:
        header (Sequence[str]): The column names (row 1).
        records (Iterable[Sequence[CellT]]): The rows after the header, in
            order, one per spreadsheet row — blank ones included.
        pad (CellT): Value for the cells a short record lacks.
        max_rows (int | None): Most data rows (blank ones excluded) to
            accept; ``None`` accepts any number.
        sheet (str | None): The tab's name, reported in the error.

    Returns:
        list[tuple[int, dict[str, CellT]]]: ``(row_number, row)`` pairs,
        the header being row 1.

    Raises:
        SpreadsheetTooLargeError: On the data row past ``max_rows``, with
            ``details["limit"] == "rows"``.
    """
    width: int = len(header)
    rows: list[tuple[int, dict[str, CellT]]] = []
    for offset, record in enumerate(records):
        cells: list[CellT] = list(record[:width])
        if all(_is_blank(cell) for cell in cells):
            continue
        if max_rows is not None and len(rows) >= max_rows:
            raise SpreadsheetTooLargeError(
                message=(
                    f"Sheet {sheet!r} has more than {max_rows} data rows "
                    f"(reading stopped at row {offset + 2})."
                ),
                details={
                    "limit": "rows",
                    "max": max_rows,
                    "actual": len(rows) + 1,
                    "sheet": sheet,
                    "row": offset + 2,
                },
            )
        cells.extend([pad] * (width - len(cells)))
        rows.append((offset + 2, dict(zip(header, cells, strict=True))))
    return rows


def _validate_rows(
    rows: Iterable[tuple[int, dict[str, CellT]]],
    schema: type[ModelT],
    *,
    omit_blank: bool,
    row_error: Callable[[int, list[dict[str, Any]]], AppException],
) -> list[ModelT]:
    """Validate numbered rows into ``schema``, stopping at the first failure.

    Args:
        rows (Iterable[tuple[int, dict[str, CellT]]]): Output of
            :func:`_number_rows`.
        schema (type[ModelT]): The model each row validates into.
        omit_blank (bool): Drop blank cells (``None`` / ``""``) before
            validating, so a blank cell means *absent*.
        row_error (Callable[[int, list[dict[str, Any]]], AppException]):
            Builds the exception for a failing row from its number and
            Pydantic's error list.

    Returns:
        list[ModelT]: One instance per row, in order.

    Raises:
        AppException: Whatever ``row_error`` builds, chained to the
            :class:`pydantic.ValidationError`.
    """
    models: list[ModelT] = []
    for row_number, row in rows:
        payload: dict[str, CellT] = (
            {key: value for key, value in row.items() if not _is_blank(value)}
            if omit_blank
            else row
        )
        try:
            models.append(schema.model_validate(payload))
        except ValidationError as exc:
            errors: list[dict[str, Any]] = [
                dict(error)
                for error in exc.errors(include_url=False, include_context=False)
            ]
            raise row_error(row_number, errors) from exc
    return models


def _header_name(value: XlsxCellValue) -> str:
    """Turn a header cell into a column name.

    Args:
        value (XlsxCellValue): The header cell.

    Returns:
        str: The text as written; ``""`` for an empty cell; ``str(value)``
        for a header typed as a number or a date.
    """
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _require_openpyxl() -> None:
    """Fail early, naming the extra, when ``openpyxl`` is not installed.

    Called before any work that would be wasted without the engine — the
    Google reader calls it before downloading the workbook.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
    """
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise ImportError(_MISSING_DEPENDENCY) from exc


def _check_archive(
    source: XlsxSource,
    *,
    max_uncompressed_bytes: int | None,
    max_compression_ratio: float | None,
) -> None:
    """Refuse a ZIP whose members would decompress past the limits.

    Reads only the central directory — no member is decompressed. The
    sizes there can lie; see :data:`DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`
    for why a lie does not get past ``zipfile``. A binary file object is
    left at the position it had.

    Args:
        source (XlsxSource): Bytes, a path, or a binary file object.
        max_uncompressed_bytes (int | None): Most bytes all members may add
            up to; ``None`` skips the check.
        max_compression_ratio (float | None): Highest decompressed-to-
            compressed ratio for a member of at least 1 MiB; ``None`` skips
            the check.

    Raises:
        SpreadsheetTooLargeError: If either limit is passed.
        InvalidSpreadsheetError: If the source is not a ZIP.
        FileNotFoundError: If a path does not exist.
    """
    if max_uncompressed_bytes is None and max_compression_ratio is None:
        return
    stream: IO[bytes] | None = (
        None if isinstance(source, bytes | str | PathLike) else source
    )
    position: int = 0 if stream is None else stream.tell()
    target: str | PathLike[str] | IO[bytes] = (
        io.BytesIO(source) if isinstance(source, bytes) else source
    )
    try:
        with zipfile.ZipFile(target) as archive:
            members: list[zipfile.ZipInfo] = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise InvalidSpreadsheetError(details={"reason": str(exc)}) from exc
    finally:
        if stream is not None:
            stream.seek(position)
    total: int = sum(member.file_size for member in members)
    if max_uncompressed_bytes is not None and total > max_uncompressed_bytes:
        raise SpreadsheetTooLargeError(
            message=(
                f"The spreadsheet decompresses to {total} bytes; the limit is "
                f"{max_uncompressed_bytes}."
            ),
            details={
                "limit": "uncompressed_bytes",
                "max": max_uncompressed_bytes,
                "actual": total,
            },
        )
    if max_compression_ratio is None:
        return
    for member in members:
        if member.file_size < _RATIO_FLOOR_BYTES:
            continue
        ratio: float = member.file_size / max(member.compress_size, 1)
        if ratio > max_compression_ratio:
            raise SpreadsheetTooLargeError(
                message=(
                    f"{member.filename!r} decompresses {ratio:.1f} times its "
                    f"stored size; the limit is {max_compression_ratio}."
                ),
                details={
                    "limit": "compression_ratio",
                    "max": max_compression_ratio,
                    "actual": round(ratio, 1),
                    "member": member.filename,
                },
            )


def _open_workbook(source: XlsxSource) -> Workbook:
    """Open a workbook read-only, with formulas as their cached values.

    Args:
        source (XlsxSource): Bytes, a path, or a binary file object.

    Returns:
        Workbook: The workbook. The caller closes it.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
        InvalidSpreadsheetError: If the source is not an ``.xlsx``.
        FileNotFoundError: If a path does not exist.
    """
    _require_openpyxl()
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    target: str | PathLike[str] | IO[bytes] = (
        io.BytesIO(source) if isinstance(source, bytes) else source
    )
    try:
        return load_workbook(target, read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, SyntaxError) as exc:
        raise InvalidSpreadsheetError(details={"reason": str(exc)}) from exc


@contextmanager
def _workbook(
    source: XlsxSource,
    *,
    max_uncompressed_bytes: int | None,
    max_compression_ratio: float | None,
) -> Iterator[Workbook]:
    """Check the archive, open the workbook, and close it on the way out.

    A malformed XML part surfaces only when its tab is read — ``openpyxl``
    parses a worksheet lazily — so the parse error is translated here, for
    the whole read, and not only at open time. It is a ``SyntaxError``:
    that is the base of both ``xml.etree.ElementTree.ParseError`` and
    ``lxml``'s ``XMLSyntaxError``, whichever parser ``openpyxl`` picked.

    Args:
        source (XlsxSource): Bytes, a path, or a binary file object.
        max_uncompressed_bytes (int | None): See :func:`_check_archive`.
        max_compression_ratio (float | None): See :func:`_check_archive`.

    Yields:
        Workbook: The open workbook.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
        SpreadsheetTooLargeError: If the archive passes a limit.
        InvalidSpreadsheetError: If the source is not an ``.xlsx``, or a
            part of it is not well-formed XML.
        FileNotFoundError: If a path does not exist.
    """
    _check_limit("max_uncompressed_bytes", max_uncompressed_bytes)
    _check_limit("max_compression_ratio", max_compression_ratio)
    _require_openpyxl()
    _check_archive(
        source,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    )
    workbook: Workbook = _open_workbook(source)
    try:
        yield workbook
    except SyntaxError as exc:
        raise InvalidSpreadsheetError(details={"reason": str(exc)}) from exc
    finally:
        workbook.close()


def _sheet_rows(
    worksheet: Any,
    max_rows: int | None,
) -> tuple[str, list[tuple[int, dict[str, XlsxCellValue]]]]:
    """Read one tab into numbered rows.

    The declared dimensions are reset first: ``openpyxl`` in read-only mode
    trusts the ``<dimension>`` tag, and a stale one truncates the rows it
    yields. The Google export writes no such tag at all.

    Args:
        worksheet (Any): An ``openpyxl`` read-only worksheet. Typed as
            ``Any`` because the engine is imported lazily.
        max_rows (int | None): Most data rows to accept; ``None`` accepts
            any number.

    Returns:
        tuple[str, list[tuple[int, dict[str, XlsxCellValue]]]]: The tab's
        title and its ``(row_number, row)`` pairs.

    Raises:
        SpreadsheetTooLargeError: On the data row past ``max_rows``.
    """
    worksheet.reset_dimensions()
    records: Iterable[Sequence[XlsxCellValue]] = worksheet.iter_rows(values_only=True)
    iterator = iter(records)
    first: Sequence[XlsxCellValue] | None = next(iterator, None)
    if first is None:
        return worksheet.title, []
    header: list[str] = [_header_name(cell) for cell in first]
    return worksheet.title, _number_rows(
        header, iterator, None, max_rows=max_rows, sheet=worksheet.title
    )


def _pick_sheet(workbook: Workbook, sheet: str | int) -> Any:
    """Find a tab by name or by position.

    Args:
        workbook (Workbook): The open workbook.
        sheet (str | int): Tab name, or 0-based position in tab order.

    Returns:
        Any: The read-only worksheet.

    Raises:
        SheetNotFoundError: If no worksheet has that name or position.
    """
    worksheets: list[Any] = list(workbook.worksheets)
    if isinstance(sheet, str):
        for worksheet in worksheets:
            if worksheet.title == sheet:
                return worksheet
    elif -len(worksheets) <= sheet < len(worksheets):
        return worksheets[sheet]
    raise SheetNotFoundError(
        message=f"The spreadsheet has no sheet {sheet!r}.",
        message_params={"sheet": sheet},
        details={
            "sheet": sheet,
            "available": [worksheet.title for worksheet in worksheets],
        },
    )


def _read_sheet(
    source: XlsxSource,
    sheet: str | int,
    *,
    max_rows: int | None,
    max_uncompressed_bytes: int | None,
    max_compression_ratio: float | None,
) -> tuple[str, list[tuple[int, dict[str, XlsxCellValue]]]]:
    """Open the workbook, read one tab and close it.

    Args:
        source (XlsxSource): Bytes, a path, or a binary file object.
        sheet (str | int): Tab name or 0-based position.
        max_rows (int | None): Most data rows the tab may hold.
        max_uncompressed_bytes (int | None): Most bytes the workbook may
            decompress to.
        max_compression_ratio (float | None): Highest ratio for a member.

    Returns:
        tuple[str, list[tuple[int, dict[str, XlsxCellValue]]]]: The tab's
        title and its numbered rows.
    """
    _check_limit("max_rows", max_rows)
    with _workbook(
        source,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    ) as workbook:
        return _sheet_rows(_pick_sheet(workbook, sheet), max_rows)


def read_xlsx(
    source: XlsxSource,
    *,
    sheet: str | int = 0,
    max_rows: int | None = DEFAULT_XLSX_MAX_ROWS,
    max_uncompressed_bytes: int | None = DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = DEFAULT_XLSX_MAX_COMPRESSION_RATIO,
) -> list[dict[str, XlsxCellValue]]:
    """Read one tab of an ``.xlsx`` workbook as a list of rows.

    Row 1 is the header; each later row is a ``dict`` keyed by it, each
    value an :data:`XlsxCellValue` — an empty cell is ``None``. Blank rows
    are skipped. A tab with only a header, or nothing at all, returns
    ``[]``. Merged cells keep the value in their top-left cell only; the
    rest of the range reads ``None``.

    The three limits are checked before the work they protect, and a
    workbook over one is refused, never read in part. Raise them, or pass
    ``None``, for a file you trust.

    Args:
        source (XlsxSource): The workbook as bytes, a path, or a binary
            file object.
        sheet (str | int): The tab's name, or its 0-based position in tab
            order. Defaults to the first tab.
        max_rows (int | None): Most data rows (blank ones excluded) the tab
            may hold. Defaults to :data:`DEFAULT_XLSX_MAX_ROWS`; ``None``
            removes the limit.
        max_uncompressed_bytes (int | None): Most bytes the workbook's parts
            may add up to once decompressed. Defaults to
            :data:`DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`; ``None`` removes
            the limit.
        max_compression_ratio (float | None): Highest decompressed-to-
            compressed ratio for a part of at least 1 MiB. Defaults to
            :data:`DEFAULT_XLSX_MAX_COMPRESSION_RATIO`; ``None`` removes
            the limit.

    Returns:
        list[dict[str, XlsxCellValue]]: The data rows, in sheet order.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
        ValueError: If a limit is zero or negative.
        SpreadsheetTooLargeError: If the workbook passes a limit.
        InvalidSpreadsheetError: If the source is not an ``.xlsx``.
        SheetNotFoundError: If the workbook has no such tab.
        FileNotFoundError: If a path does not exist.
    """
    _, rows = _read_sheet(
        source,
        sheet,
        max_rows=max_rows,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    )
    return [row for _, row in rows]


def read_xlsx_sheets(
    source: XlsxSource,
    *,
    max_rows: int | None = DEFAULT_XLSX_MAX_ROWS,
    max_uncompressed_bytes: int | None = DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = DEFAULT_XLSX_MAX_COMPRESSION_RATIO,
) -> dict[str, list[dict[str, XlsxCellValue]]]:
    """Read every tab of an ``.xlsx`` workbook, keyed by tab name.

    Each tab follows the rules of :func:`read_xlsx`. Chart sheets, which
    hold no cells, are left out.

    Args:
        source (XlsxSource): The workbook as bytes, a path, or a binary
            file object.
        max_rows (int | None): Most data rows **each** tab may hold. The
            workbook as a whole is bounded by ``max_uncompressed_bytes``.
            Defaults to :data:`DEFAULT_XLSX_MAX_ROWS`; ``None`` removes the
            limit.
        max_uncompressed_bytes (int | None): Most bytes the workbook's parts
            may add up to once decompressed. Defaults to
            :data:`DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`; ``None`` removes
            the limit.
        max_compression_ratio (float | None): Highest decompressed-to-
            compressed ratio for a part of at least 1 MiB. Defaults to
            :data:`DEFAULT_XLSX_MAX_COMPRESSION_RATIO`; ``None`` removes
            the limit.

    Returns:
        dict[str, list[dict[str, XlsxCellValue]]]: The rows of each tab, in
        tab order. An empty tab maps to ``[]``.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
        ValueError: If a limit is zero or negative.
        SpreadsheetTooLargeError: If the workbook passes a limit.
        InvalidSpreadsheetError: If the source is not an ``.xlsx``.
        FileNotFoundError: If a path does not exist.
    """
    _check_limit("max_rows", max_rows)
    with _workbook(
        source,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    ) as workbook:
        sheets: dict[str, list[dict[str, XlsxCellValue]]] = {}
        for worksheet in workbook.worksheets:
            title, rows = _sheet_rows(worksheet, max_rows)
            sheets[title] = [row for _, row in rows]
        return sheets


def read_xlsx_as(
    source: XlsxSource,
    schema: type[ModelT],
    *,
    sheet: str | int = 0,
    omit_blank: bool = True,
    max_rows: int | None = DEFAULT_XLSX_MAX_ROWS,
    max_uncompressed_bytes: int | None = DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = DEFAULT_XLSX_MAX_COMPRESSION_RATIO,
) -> list[ModelT]:
    """Read one tab of an ``.xlsx`` workbook and validate each row.

    The header names are the keys handed to ``schema.model_validate``, so
    a column maps to the field of the same name (use
    ``validation_alias`` for a header that is not a valid identifier).
    Cells arrive typed (:data:`XlsxCellValue`), so a currency column
    validates into ``float`` or ``Decimal`` without undoing any formatting.

    The limits are those of :func:`read_xlsx`, checked before any row is
    validated.

    Args:
        source (XlsxSource): The workbook as bytes, a path, or a binary
            file object.
        schema (type[ModelT]): The Pydantic model each row validates into.
        sheet (str | int): The tab's name, or its 0-based position.
            Defaults to the first tab.
        omit_blank (bool): Drop empty cells from the row before
            validating, so a blank cell means *absent*: the field falls
            back to its default, and a required field reports ``missing``.
            ``False`` passes ``None`` through.
        max_rows (int | None): Most data rows the tab may hold. Defaults to
            :data:`DEFAULT_XLSX_MAX_ROWS`; ``None`` removes the limit.
        max_uncompressed_bytes (int | None): Most bytes the workbook may
            decompress to. Defaults to
            :data:`DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`; ``None`` removes
            the limit.
        max_compression_ratio (float | None): Highest decompressed-to-
            compressed ratio for a part of at least 1 MiB. Defaults to
            :data:`DEFAULT_XLSX_MAX_COMPRESSION_RATIO`; ``None`` removes
            the limit.

    Returns:
        list[ModelT]: One instance per data row, in sheet order. ``[]``
        when the tab has no data row.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed.
        ValueError: If a limit is zero or negative.
        SpreadsheetTooLargeError: If the workbook passes a limit.
        InvalidSpreadsheetError: If the source is not an ``.xlsx``.
        SheetNotFoundError: If the workbook has no such tab.
        SpreadsheetRowError: On the first row that fails validation;
            ``details["row"]`` is its number in the sheet (header = 1) and
            ``details["sheet"]`` the tab's name.
        FileNotFoundError: If a path does not exist.
    """
    title, rows = _read_sheet(
        source,
        sheet,
        max_rows=max_rows,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    )

    def row_error(row_number: int, errors: list[dict[str, Any]]) -> AppException:
        """Build the error for a failing row of this tab.

        Args:
            row_number (int): The row's number in the sheet.
            errors (list[dict[str, Any]]): Pydantic's errors for the row.

        Returns:
            AppException: The :class:`SpreadsheetRowError`.
        """
        return SpreadsheetRowError(
            message=f"Row {row_number} of sheet {title!r} failed validation.",
            message_params={"row": row_number, "sheet": title},
            details={"row": row_number, "sheet": title, "errors": errors},
        )

    return _validate_rows(rows, schema, omit_blank=omit_blank, row_error=row_error)


__all__: list[str] = [
    "DEFAULT_XLSX_MAX_COMPRESSION_RATIO",
    "DEFAULT_XLSX_MAX_ROWS",
    "DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES",
    "InvalidSpreadsheetError",
    "SheetNotFoundError",
    "SpreadsheetRowError",
    "SpreadsheetTooLargeError",
    "XlsxCellValue",
    "XlsxSource",
    "read_xlsx",
    "read_xlsx_as",
    "read_xlsx_sheets",
]
