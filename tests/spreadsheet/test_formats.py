"""Tests for the pt-BR number formats.

Two things are asserted here, and only one of them is a string comparison.

The literal values are fixed because the **language code is the whole
point**: `[$R$-416]` renders `1.234,56` in any locale, while a plain
`#,##0.00` renders `1,234.56` for a reader whose machine is en-US. Dropping
the `416` is a silent, invisible regression — the mask still looks right in
review and the document is wrong on someone else's laptop. A literal test is
what turns that into a failure.

The rest asserts behaviour that survives the file: what a cell actually
holds once the workbook has been written and read back.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO

import pytest

from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    BR_CURRENCY_FORMAT_NO_SYMBOL,
    BR_DATE_FORMAT,
    BR_DATETIME_FORMAT,
    BR_INTEGER_FORMAT,
    BR_PERCENT_FORMAT,
    BR_QUANTITY_FORMAT,
    TEXT_FORMAT,
    Column,
    SheetWriter,
    new_workbook,
    workbook_to_bytes,
)

openpyxl = pytest.importorskip("openpyxl")


def write_and_reopen(value: object, number_format: str) -> object:
    """Write one value through a formatted column and read the file back.

    Args:
        value (object): The value to write into the single body cell.
        number_format (str): The column's Excel number format.

    Returns:
        object: The cell as it comes back from the saved workbook.
    """
    workbook = new_workbook("Dados")
    writer = SheetWriter(workbook["Dados"], [Column("X", number_format=number_format)])
    writer.write_row([value])  # type: ignore[list-item]
    reopened = openpyxl.load_workbook(BytesIO(workbook_to_bytes(workbook)))
    return reopened["Dados"].cell(row=1, column=1)


class TestFormatLiterals:
    @pytest.mark.parametrize(
        ("mask", "expected"),
        [
            (BR_CURRENCY_FORMAT, "[$R$-416] #,##0.00"),
            (BR_CURRENCY_FORMAT_NO_SYMBOL, "[$-416]#,##0.00"),
            (BR_QUANTITY_FORMAT, "[$-416]#,##0.00"),
            (BR_INTEGER_FORMAT, "[$-416]#,##0"),
            (BR_PERCENT_FORMAT, "0.00%"),
            (BR_DATE_FORMAT, "DD/MM/YYYY"),
            (BR_DATETIME_FORMAT, "DD/MM/YYYY HH:MM"),
            (TEXT_FORMAT, "@"),
        ],
    )
    def test_mask_is_exactly_what_the_docs_promise(
        self,
        mask: str,
        expected: str,
    ) -> None:
        assert mask == expected

    @pytest.mark.parametrize(
        "mask",
        [
            BR_CURRENCY_FORMAT,
            BR_CURRENCY_FORMAT_NO_SYMBOL,
            BR_QUANTITY_FORMAT,
            BR_INTEGER_FORMAT,
        ],
    )
    def test_numeric_masks_carry_the_language_code(self, mask: str) -> None:
        """Without `416` the separators follow the reader's locale."""
        assert "-416" in mask


class TestFormatsSurviveTheFile:
    @pytest.mark.parametrize(
        "mask",
        [
            BR_CURRENCY_FORMAT,
            BR_CURRENCY_FORMAT_NO_SYMBOL,
            BR_QUANTITY_FORMAT,
            BR_INTEGER_FORMAT,
            BR_PERCENT_FORMAT,
        ],
    )
    def test_mask_is_written_into_the_workbook(self, mask: str) -> None:
        assert write_and_reopen(Decimal("1234.56"), mask).number_format == mask  # type: ignore[attr-defined]

    def test_currency_cell_stays_numeric(self) -> None:
        cell = write_and_reopen(Decimal("1234.56"), BR_CURRENCY_FORMAT)
        assert cell.value == 1234.56  # type: ignore[attr-defined]
        assert cell.data_type == "n"  # type: ignore[attr-defined]

    def test_datetime_cell_stays_a_datetime(self) -> None:
        cell = write_and_reopen(datetime(2026, 8, 14, 19, 30), BR_DATETIME_FORMAT)
        assert cell.value == datetime(2026, 8, 14, 19, 30)  # type: ignore[attr-defined]
        assert cell.number_format == BR_DATETIME_FORMAT  # type: ignore[attr-defined]


class TestTextFormat:
    def test_leading_zeros_survive(self) -> None:
        """The documented reason TEXT_FORMAT exists."""
        cell = write_and_reopen("0001/2026", TEXT_FORMAT)
        assert cell.value == "0001/2026"  # type: ignore[attr-defined]
        assert cell.data_type == "s"  # type: ignore[attr-defined]

    def test_a_cpf_keeps_its_zeros(self) -> None:
        cell = write_and_reopen("01234567890", TEXT_FORMAT)
        assert cell.value == "01234567890"  # type: ignore[attr-defined]

    def test_the_mask_reaches_the_cell(self) -> None:
        assert write_and_reopen("0001/2026", TEXT_FORMAT).number_format == "@"  # type: ignore[attr-defined]
