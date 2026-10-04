"""Spreadsheet generation — ``.xlsx`` documents a recipient can work with.

The counterpart of :mod:`tempest_fastapi_sdk.pdf`. A PDF is what you send
when the numbers are final; a spreadsheet is what you send when the
recipient has to sort, filter, re-total or audit them — a budget, a price
table, a reconciliation, an export.

Three writing pieces, each usable on its own:

* :mod:`~tempest_fastapi_sdk.spreadsheet.formats` — Excel number formats
  pinned to pt-BR by the ``[$-416]`` language code, so the file does not
  render differently on a reader whose machine is en-US or de-DE.
* :mod:`~tempest_fastapi_sdk.spreadsheet.styles` — the visual theme as
  plain data (hex colours, point sizes), importable without ``openpyxl``.
* :mod:`~tempest_fastapi_sdk.spreadsheet.writer` — a row cursor with column
  specs, so callers append rows instead of tracking ``(row, column)`` pairs.

    from decimal import Decimal
    from tempest_fastapi_sdk.spreadsheet import (
        BR_CURRENCY_FORMAT, Column, SheetWriter, new_workbook,
        workbook_to_bytes,
    )

    workbook = new_workbook("Orçamento")
    writer = SheetWriter(
        workbook["Orçamento"],
        columns=[
            Column("Item", width=48, wrap=True),
            Column("Valor", width=18, number_format=BR_CURRENCY_FORMAT),
        ],
    )
    writer.header_row()
    writer.write_row(["Serviço de instalação", Decimal("2930.00")])
    writer.apply_widths()
    data = workbook_to_bytes(workbook)

Reading goes the other way:

* :mod:`~tempest_fastapi_sdk.spreadsheet.reader` reads an ``.xlsx`` — an
  upload, a file, a downloaded export — one tab (:func:`read_xlsx`,
  :func:`read_xlsx_as`) or all of them (:func:`read_xlsx_sheets`), cells
  typed as the file stores them.
* :mod:`~tempest_fastapi_sdk.spreadsheet.google` reads a Google Sheet
  shared as *Anyone with the link*: one tab as CSV
  (:func:`read_google_sheet`, :func:`read_google_sheet_as`), which runs on
  ``httpx`` and the standard ``csv`` module only and needs no extra, or
  the whole workbook as ``.xlsx`` (:func:`read_google_sheet_xlsx`,
  :func:`download_google_sheet_xlsx`).

Writing and reading ``.xlsx`` need the ``[spreadsheet]`` extra
(``openpyxl``); the engine is imported at first use, so importing this
package without it still works.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit re-export form
combined with ``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.spreadsheet import SheetWriter`` without a
diagnostic.
"""

from tempest_fastapi_sdk.spreadsheet.formats import (
    BR_CURRENCY_FORMAT as BR_CURRENCY_FORMAT,
)
from tempest_fastapi_sdk.spreadsheet.formats import (
    BR_CURRENCY_FORMAT_NO_SYMBOL as BR_CURRENCY_FORMAT_NO_SYMBOL,
)
from tempest_fastapi_sdk.spreadsheet.formats import (
    BR_DATE_FORMAT as BR_DATE_FORMAT,
)
from tempest_fastapi_sdk.spreadsheet.formats import (
    BR_DATETIME_FORMAT as BR_DATETIME_FORMAT,
)
from tempest_fastapi_sdk.spreadsheet.formats import (
    BR_INTEGER_FORMAT as BR_INTEGER_FORMAT,
)
from tempest_fastapi_sdk.spreadsheet.formats import (
    BR_PERCENT_FORMAT as BR_PERCENT_FORMAT,
)
from tempest_fastapi_sdk.spreadsheet.formats import (
    BR_QUANTITY_FORMAT as BR_QUANTITY_FORMAT,
)
from tempest_fastapi_sdk.spreadsheet.formats import (
    TEXT_FORMAT as TEXT_FORMAT,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES as DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES as DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    GoogleSheetAccessError as GoogleSheetAccessError,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    GoogleSheetRowError as GoogleSheetRowError,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    download_google_sheet_xlsx as download_google_sheet_xlsx,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    google_sheet_export_url as google_sheet_export_url,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    read_google_sheet as read_google_sheet,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    read_google_sheet_as as read_google_sheet_as,
)
from tempest_fastapi_sdk.spreadsheet.google import (
    read_google_sheet_xlsx as read_google_sheet_xlsx,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    DEFAULT_XLSX_MAX_COMPRESSION_RATIO as DEFAULT_XLSX_MAX_COMPRESSION_RATIO,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    DEFAULT_XLSX_MAX_ROWS as DEFAULT_XLSX_MAX_ROWS,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES as DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    InvalidSpreadsheetError as InvalidSpreadsheetError,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    SheetNotFoundError as SheetNotFoundError,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    SpreadsheetRowError as SpreadsheetRowError,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    SpreadsheetTooLargeError as SpreadsheetTooLargeError,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    XlsxCellValue as XlsxCellValue,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    XlsxSource as XlsxSource,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    read_xlsx as read_xlsx,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    read_xlsx_as as read_xlsx_as,
)
from tempest_fastapi_sdk.spreadsheet.reader import (
    read_xlsx_sheets as read_xlsx_sheets,
)
from tempest_fastapi_sdk.spreadsheet.styles import (
    DEFAULT_SHEET_STYLE as DEFAULT_SHEET_STYLE,
)
from tempest_fastapi_sdk.spreadsheet.styles import (
    SheetStyle as SheetStyle,
)
from tempest_fastapi_sdk.spreadsheet.writer import (
    CellValue as CellValue,
)
from tempest_fastapi_sdk.spreadsheet.writer import (
    Column as Column,
)
from tempest_fastapi_sdk.spreadsheet.writer import (
    SheetWriter as SheetWriter,
)
from tempest_fastapi_sdk.spreadsheet.writer import (
    new_workbook as new_workbook,
)
from tempest_fastapi_sdk.spreadsheet.writer import (
    workbook_to_bytes as workbook_to_bytes,
)
from tempest_fastapi_sdk.utils.media_types import (
    XLSX_MEDIA_TYPE as XLSX_MEDIA_TYPE,
)

__all__: list[str] = [
    "BR_CURRENCY_FORMAT",
    "BR_CURRENCY_FORMAT_NO_SYMBOL",
    "BR_DATETIME_FORMAT",
    "BR_DATE_FORMAT",
    "BR_INTEGER_FORMAT",
    "BR_PERCENT_FORMAT",
    "BR_QUANTITY_FORMAT",
    "DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES",
    "DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES",
    "DEFAULT_SHEET_STYLE",
    "DEFAULT_XLSX_MAX_COMPRESSION_RATIO",
    "DEFAULT_XLSX_MAX_ROWS",
    "DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES",
    "TEXT_FORMAT",
    "XLSX_MEDIA_TYPE",
    "CellValue",
    "Column",
    "GoogleSheetAccessError",
    "GoogleSheetRowError",
    "InvalidSpreadsheetError",
    "SheetNotFoundError",
    "SheetStyle",
    "SheetWriter",
    "SpreadsheetRowError",
    "SpreadsheetTooLargeError",
    "XlsxCellValue",
    "XlsxSource",
    "download_google_sheet_xlsx",
    "google_sheet_export_url",
    "new_workbook",
    "read_google_sheet",
    "read_google_sheet_as",
    "read_google_sheet_xlsx",
    "read_xlsx",
    "read_xlsx_as",
    "read_xlsx_sheets",
    "workbook_to_bytes",
]
