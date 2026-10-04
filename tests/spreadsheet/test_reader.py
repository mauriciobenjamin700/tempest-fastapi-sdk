"""Tests for the ``.xlsx`` reader.

Every workbook is generated in the test by the SDK's own writer (round
trip) or by ``openpyxl`` directly for the shapes the writer does not make
(merged ranges, chart sheets, headers typed as numbers). What the Google
export writes — no ``<dimension>`` tag, cached formula values, ``30.0``
and ``50`` side by side in one column — was measured against public
sheets and is covered in ``test_google.py``.
"""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from openpyxl.chart import BarChart, Reference
from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    BR_DATE_FORMAT,
    BR_PERCENT_FORMAT,
    Column,
    GoogleSheetRowError,
    InvalidSpreadsheetError,
    SheetNotFoundError,
    SheetWriter,
    SpreadsheetRowError,
    new_workbook,
    read_xlsx,
    read_xlsx_as,
    read_xlsx_sheets,
    workbook_to_bytes,
)


def _priced_workbook() -> bytes:
    """Build a two-tab workbook with the writer: typed cells and a blank row.

    Returns:
        bytes: The ``.xlsx`` file.
    """
    workbook = new_workbook("Vendas", "Vazia")
    writer = SheetWriter(
        workbook["Vendas"],
        columns=[
            Column("produto"),
            Column("quantidade"),
            Column("preco", number_format=BR_CURRENCY_FORMAT),
            Column("data", number_format=BR_DATE_FORMAT),
            Column("pago"),
            Column("margem", number_format=BR_PERCENT_FORMAT),
        ],
    )
    writer.header_row()
    writer.write_row(
        ["Café", 2, Decimal("10.50"), date(2026, 10, 4), True, 0.3],
    )
    writer.blank_rows()
    writer.write_row(["Bolo", 1, Decimal("7"), date(2026, 10, 5), False, None])
    return workbook_to_bytes(workbook)


class Sale(BaseModel):
    """A row of the ``Vendas`` tab."""

    produto: str
    quantidade: int
    preco: Decimal
    data: date
    pago: bool
    margem: float | None = None


class TestReadXlsx:
    """``read_xlsx`` returns typed rows keyed by the header."""

    def test_round_trip_keeps_cell_types(self) -> None:
        """Numbers, dates, booleans and ratios come back as values."""
        rows = read_xlsx(_priced_workbook())
        assert rows == [
            {
                "produto": "Café",
                "quantidade": 2,
                "preco": 10.5,
                "data": datetime(2026, 10, 4),
                "pago": True,
                "margem": 0.3,
            },
            {
                "produto": "Bolo",
                "quantidade": 1,
                "preco": 7,
                "data": datetime(2026, 10, 5),
                "pago": False,
                "margem": None,
            },
        ]

    def test_time_cell_is_a_time(self) -> None:
        """A time-only cell reads as ``datetime.time``."""
        workbook = new_workbook("A")
        workbook["A"].append(["inicio"])
        workbook["A"].append([time(8, 15)])
        assert read_xlsx(workbook_to_bytes(workbook)) == [{"inicio": time(8, 15)}]

    def test_reads_path_and_file_object(self, tmp_path: Path) -> None:
        """A filesystem path and a binary file object read the same rows."""
        data = _priced_workbook()
        path = tmp_path / "vendas.xlsx"
        path.write_bytes(data)
        expected = read_xlsx(data)
        assert read_xlsx(path) == expected
        assert read_xlsx(str(path)) == expected
        assert read_xlsx(io.BytesIO(data)) == expected

    def test_sheet_by_name_and_index(self) -> None:
        """A tab is picked by name or by 0-based position, negative included."""
        data = _priced_workbook()
        assert read_xlsx(data, sheet="Vendas") == read_xlsx(data)
        assert read_xlsx(data, sheet="Vazia") == []
        assert read_xlsx(data, sheet=1) == []
        assert read_xlsx(data, sheet=-1) == []

    @pytest.mark.parametrize("sheet", ["Compras", 2, -3])
    def test_missing_sheet_is_a_typed_error(self, sheet: str | int) -> None:
        """An unknown tab raises ``SheetNotFoundError`` listing the real ones."""
        with pytest.raises(SheetNotFoundError) as caught:
            read_xlsx(_priced_workbook(), sheet=sheet)
        error = caught.value
        assert error.status_code == 422
        assert error.code == "SPREADSHEET_SHEET_NOT_FOUND"
        assert error.details == {"sheet": sheet, "available": ["Vendas", "Vazia"]}

    def test_blank_row_skipped_short_row_padded_extra_ignored(self) -> None:
        """Blank rows go, short rows pad with ``None``, extra cells drop."""
        workbook = new_workbook("A")
        sheet = workbook["A"]
        sheet.append(["a", "b", "c"])
        sheet.append([1, 2, 3])
        sheet.append([None, None, None])
        sheet.append([4])
        sheet.append([5, 6, 7, 8])
        sheet.append([None, None, None, "only beyond the header"])
        assert read_xlsx(workbook_to_bytes(workbook)) == [
            {"a": 1, "b": 2, "c": 3},
            {"a": 4, "b": None, "c": None},
            {"a": 5, "b": 6, "c": 7},
        ]

    def test_zero_and_false_are_not_blank(self) -> None:
        """A row holding only ``0`` and ``False`` is data, not a blank row."""
        workbook = new_workbook("A")
        workbook["A"].append(["n", "ok"])
        workbook["A"].append([0, False])
        assert read_xlsx(workbook_to_bytes(workbook)) == [{"n": 0, "ok": False}]

    def test_header_edge_cases(self) -> None:
        """Empty header is ``""``, a repeat keeps the last, numbers become text."""
        workbook = new_workbook("A")
        workbook["A"].append(["nome", None, "nome", 2026])
        workbook["A"].append(["a", "b", "c", "d"])
        assert read_xlsx(workbook_to_bytes(workbook)) == [
            {"nome": "c", "": "b", "2026": "d"},
        ]

    def test_merged_range_keeps_value_in_top_left_only(self) -> None:
        """The rest of a merged range reads ``None``."""
        workbook = new_workbook("A")
        sheet = workbook["A"]
        sheet.append(["grupo", "item"])
        sheet.append(["Bebidas", "Café"])
        sheet.append([None, "Chá"])
        sheet.merge_cells("A2:A3")
        assert read_xlsx(workbook_to_bytes(workbook)) == [
            {"grupo": "Bebidas", "item": "Café"},
            {"grupo": None, "item": "Chá"},
        ]

    def test_formula_without_cached_value_reads_none(self) -> None:
        """Only the value the file cached is read; ``openpyxl`` caches none."""
        workbook = new_workbook("A")
        workbook["A"].append(["a", "dobro"])
        workbook["A"].append([2, "=A2*2"])
        assert read_xlsx(workbook_to_bytes(workbook)) == [{"a": 2, "dobro": None}]

    def test_empty_tab_is_an_empty_list(self) -> None:
        """A tab with nothing, or only a header, returns ``[]``."""
        workbook = new_workbook("Nada", "Cabecalho")
        workbook["Cabecalho"].append(["a", "b"])
        data = workbook_to_bytes(workbook)
        assert read_xlsx(data, sheet="Nada") == []
        assert read_xlsx(data, sheet="Cabecalho") == []

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"item,valor\r\nCafe,1\r\n",
            b"<html>login</html>",
        ],
    )
    def test_not_an_xlsx_is_a_typed_error(self, data: bytes) -> None:
        """Anything ``openpyxl`` cannot open raises ``InvalidSpreadsheetError``."""
        with pytest.raises(InvalidSpreadsheetError) as caught:
            read_xlsx(data)
        assert caught.value.status_code == 422
        assert caught.value.code == "SPREADSHEET_INVALID"
        assert caught.value.details["reason"]

    def test_zip_that_is_not_an_office_document(self) -> None:
        """A valid ZIP without the Office manifest is refused the same way."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "not a workbook")
        with pytest.raises(InvalidSpreadsheetError):
            read_xlsx(buffer.getvalue())

    def test_missing_path_is_not_wrapped(self, tmp_path: Path) -> None:
        """A path that does not exist is the caller's bug, not bad input."""
        with pytest.raises(FileNotFoundError):
            read_xlsx(tmp_path / "missing.xlsx")


class TestReadXlsxSheets:
    """``read_xlsx_sheets`` reads every worksheet, keyed by name."""

    def test_every_tab_in_order(self) -> None:
        """Tabs come back in tab order; an empty one maps to ``[]``."""
        sheets = read_xlsx_sheets(_priced_workbook())
        assert list(sheets) == ["Vendas", "Vazia"]
        assert len(sheets["Vendas"]) == 2
        assert sheets["Vazia"] == []

    def test_chart_sheet_left_out(self) -> None:
        """A chart sheet holds no cells and is not listed."""
        workbook = new_workbook("Dados")
        workbook["Dados"].append(["a"])
        workbook["Dados"].append([1])
        chart = BarChart()
        chart.add_data(Reference(workbook["Dados"], min_col=1, min_row=1, max_row=2))
        workbook.create_chartsheet("Grafico").add_chart(chart)
        assert read_xlsx_sheets(workbook_to_bytes(workbook)) == {"Dados": [{"a": 1}]}

    def test_tab_names_kept_verbatim(self) -> None:
        """A trailing space in a tab name is part of the key."""
        workbook = new_workbook("Setembro ")
        workbook["Setembro "].append(["a"])
        assert list(read_xlsx_sheets(workbook_to_bytes(workbook))) == ["Setembro "]


class TestReadXlsxAs:
    """``read_xlsx_as`` validates rows with the CSV reader's contract."""

    def test_rows_validate_into_the_model(self) -> None:
        """Typed cells feed ``Decimal``, ``date`` and ``bool`` fields directly."""
        sales = read_xlsx_as(_priced_workbook(), Sale)
        assert sales == [
            Sale(
                produto="Café",
                quantidade=2,
                preco=Decimal("10.5"),
                data=date(2026, 10, 4),
                pago=True,
                margem=0.3,
            ),
            Sale(
                produto="Bolo",
                quantidade=1,
                preco=Decimal("7"),
                data=date(2026, 10, 5),
                pago=False,
            ),
        ]

    def test_error_names_row_and_sheet(self) -> None:
        """The failing row is reported by its number, counting the blank row."""

        class Strict(BaseModel):
            """Types the price as text, which the typed cell is not."""

            produto: str
            preco: str

        with pytest.raises(SpreadsheetRowError) as caught:
            read_xlsx_as(_priced_workbook(), Strict)
        error = caught.value
        assert error.status_code == 422
        assert error.code == "SPREADSHEET_ROW_INVALID"
        assert error.details["row"] == 2
        assert error.details["sheet"] == "Vendas"
        assert error.message_params == {"row": 2, "sheet": "Vendas"}
        assert error.details["errors"][0]["loc"] == ("preco",)
        assert error.details["errors"][0]["input"] == 10.5
        assert "url" not in error.details["errors"][0]

    def test_row_number_counts_skipped_blank_rows(self) -> None:
        """The blank row 3 still counts: the second sale is row 4."""

        class Paid(BaseModel):
            """Only accepts paid sales."""

            produto: str
            pago: bool

            def model_post_init(self, context: object, /) -> None:
                """Refuse unpaid sales.

                Args:
                    context (object): Pydantic's validation context.

                Raises:
                    ValueError: When the sale is not paid.
                """
                if not self.pago:
                    raise ValueError("unpaid")

        with pytest.raises(SpreadsheetRowError) as caught:
            read_xlsx_as(_priced_workbook(), Paid)
        assert caught.value.details["row"] == 4

    def test_omit_blank_false_passes_none(self) -> None:
        """With ``omit_blank=False`` the empty cell reaches the validator."""

        class DefaultMargin(BaseModel):
            """``margem`` defaults to zero when absent."""

            produto: str
            margem: float = 0.0

        defaulted = read_xlsx_as(_priced_workbook(), DefaultMargin)
        assert defaulted[1].margem == 0.0
        with pytest.raises(SpreadsheetRowError) as caught:
            read_xlsx_as(_priced_workbook(), DefaultMargin, omit_blank=False)
        assert caught.value.details["row"] == 4
        assert caught.value.details["errors"][0]["loc"] == ("margem",)
        assert caught.value.details["errors"][0]["input"] is None

    def test_blank_required_cell_reports_missing(self) -> None:
        """A blank cell in a required column surfaces as ``missing``."""

        class NeedsMargin(BaseModel):
            """``margem`` is required here."""

            produto: str
            margem: float

        with pytest.raises(SpreadsheetRowError) as caught:
            read_xlsx_as(_priced_workbook(), NeedsMargin)
        assert caught.value.details["row"] == 4
        assert caught.value.details["errors"][0]["type"] == "missing"

    def test_empty_tab_validates_to_empty_list(self) -> None:
        """No data row is success."""
        assert read_xlsx_as(_priced_workbook(), Sale, sheet="Vazia") == []

    def test_google_row_error_is_a_spreadsheet_row_error(self) -> None:
        """One ``except SpreadsheetRowError`` covers the CSV reader too."""
        assert issubclass(GoogleSheetRowError, SpreadsheetRowError)
        assert GoogleSheetRowError.code == "GOOGLE_SHEET_ROW_INVALID"


def test_round_trip_through_openpyxl_load() -> None:
    """The fixture really is an ``.xlsx`` ``openpyxl`` opens on its own."""
    workbook = openpyxl.load_workbook(io.BytesIO(_priced_workbook()))
    assert workbook.sheetnames == ["Vendas", "Vazia"]


def test_missing_extra_names_it() -> None:
    """Without ``openpyxl`` the module imports and the call names the extra."""
    code = (
        "import sys; sys.modules['openpyxl'] = None\n"
        "from tempest_fastapi_sdk.spreadsheet import read_xlsx, read_google_sheet\n"
        "try:\n"
        "    read_xlsx(b'')\n"
        "except ImportError as exc:\n"
        "    print(exc)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "tempest-fastapi-sdk[spreadsheet]" in result.stdout
