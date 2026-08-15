"""Spreadsheet generation — ``.xlsx`` documents a recipient can work with.

The counterpart of :mod:`tempest_fastapi_sdk.pdf`. A PDF is what you send
when the numbers are final; a spreadsheet is what you send when the
recipient has to sort, filter, re-total or audit them — a budget, a price
table, a reconciliation, an export.

Three pieces, each usable on its own:

* :mod:`~tempest_fastapi_sdk.spreadsheet.formats` — Excel number formats
  pinned to pt-BR, so the file renders the same under any locale.
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

Needs the ``[spreadsheet]`` extra (``openpyxl``); the engine is imported at
first use, so importing this package without it still works.

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

__all__: list[str] = [
    "BR_CURRENCY_FORMAT",
    "BR_CURRENCY_FORMAT_NO_SYMBOL",
    "BR_DATETIME_FORMAT",
    "BR_DATE_FORMAT",
    "BR_INTEGER_FORMAT",
    "BR_PERCENT_FORMAT",
    "BR_QUANTITY_FORMAT",
    "DEFAULT_SHEET_STYLE",
    "TEXT_FORMAT",
    "CellValue",
    "Column",
    "SheetStyle",
    "SheetWriter",
    "new_workbook",
    "workbook_to_bytes",
]
