"""Tests for the worksheet writer, column specs and workbook helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest

from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    BR_DATE_FORMAT,
    BR_PERCENT_FORMAT,
    DEFAULT_SHEET_STYLE,
    Column,
    SheetStyle,
    SheetWriter,
    new_workbook,
    workbook_to_bytes,
)

openpyxl = pytest.importorskip("openpyxl")

COLUMNS = [
    Column("Item", width=48, wrap=True),
    Column("Qtd.", width=12, horizontal="center"),
    Column("Valor", width=18, number_format=BR_CURRENCY_FORMAT),
]


class TestNewWorkbook:
    def test_creates_named_sheets_in_order(self) -> None:
        workbook = new_workbook("Análise", "Orçamento", "Exequibilidade")
        assert workbook.sheetnames == ["Análise", "Orçamento", "Exequibilidade"]

    def test_removes_the_default_sheet(self) -> None:
        """openpyxl always ships a stray 'Sheet' tab; forgetting it looks sloppy."""
        assert "Sheet" not in new_workbook("Dados").sheetnames

    def test_without_titles_keeps_the_default(self) -> None:
        assert new_workbook().sheetnames == ["Sheet"]


class TestWorkbookToBytes:
    def test_produces_a_readable_xlsx(self) -> None:
        workbook = new_workbook("Dados")
        SheetWriter(workbook["Dados"], COLUMNS).write_row(["x", 1, Decimal("2.50")])
        data = workbook_to_bytes(workbook)

        reopened = openpyxl.load_workbook(BytesIO(data))
        assert reopened["Dados"].cell(row=1, column=3).value == 2.5

    def test_starts_with_the_zip_magic(self) -> None:
        assert workbook_to_bytes(new_workbook("Dados")).startswith(b"PK")

    def test_the_pinned_mask_survives_the_round_trip(self) -> None:
        """The language code is what the file carries; assert it is in there."""
        workbook = new_workbook("Dados")
        SheetWriter(workbook["Dados"], COLUMNS).write_row(["x", 1, Decimal("2.50")])
        reopened = openpyxl.load_workbook(BytesIO(workbook_to_bytes(workbook)))

        cell = reopened["Dados"].cell(row=1, column=3)
        assert cell.number_format == "[$R$-416] #,##0.00"
        assert cell.value == 2.5

    def test_a_percent_cell_round_trips_as_the_ratio(self) -> None:
        """Excel multiplies by 100 itself, so the stored value is the ratio."""
        columns = [Column("Deságio", number_format=BR_PERCENT_FORMAT)]
        workbook = new_workbook("Dados")
        SheetWriter(workbook["Dados"], columns).write_row([Decimal("0.30")])
        reopened = openpyxl.load_workbook(BytesIO(workbook_to_bytes(workbook)))

        cell = reopened["Dados"].cell(row=1, column=1)
        assert cell.value == 0.3
        assert cell.number_format == "0.00%"


class TestSheetWriterCursor:
    def test_starts_at_row_one(self) -> None:
        workbook = new_workbook("Dados")
        assert SheetWriter(workbook["Dados"], COLUMNS).row == 1

    def test_start_row_is_honoured(self) -> None:
        workbook = new_workbook("Dados")
        writer = SheetWriter(workbook["Dados"], COLUMNS, start_row=5)
        writer.write_row(["x", 1, Decimal("1")])
        assert workbook["Dados"].cell(row=5, column=1).value == "x"

    def test_each_write_returns_the_next_free_row(self) -> None:
        workbook = new_workbook("Dados")
        writer = SheetWriter(workbook["Dados"], COLUMNS)
        assert writer.title_block(["A", "B"]) == 3
        assert writer.header_row() == 4
        assert writer.write_row(["x", 1, Decimal("1")]) == 5
        assert writer.blank_rows(2) == 7

    def test_column_count_falls_back_to_one(self) -> None:
        workbook = new_workbook("Dados")
        assert SheetWriter(workbook["Dados"]).column_count == 1


class TestTitleBlock:
    def test_merges_across_the_table_width(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).title_block(["PREFEITURA", "Pregão 1/2026"])

        merged = {str(cells) for cells in sheet.merged_cells.ranges}
        assert merged == {"A1:C1", "A2:C2"}
        assert sheet.cell(row=1, column=1).value == "PREFEITURA"

    def test_explicit_span_wins(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).title_block(["X"], span=2)
        assert str(next(iter(sheet.merged_cells.ranges))) == "A1:B1"

    def test_is_bold_and_centred(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).title_block(["X"])
        cell = sheet.cell(row=1, column=1)
        assert cell.font.bold is True
        assert cell.font.size == DEFAULT_SHEET_STYLE.title_size
        assert cell.alignment.horizontal == "center"


class TestHeaderRow:
    def test_uses_column_titles_by_default(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).header_row()
        assert [sheet.cell(row=1, column=i).value for i in (1, 2, 3)] == [
            "Item",
            "Qtd.",
            "Valor",
        ]

    def test_explicit_titles_win(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).header_row(["A", "B"])
        assert sheet.cell(row=1, column=1).value == "A"
        assert sheet.cell(row=1, column=3).value is None

    def test_paints_the_theme(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).header_row()
        cell = sheet.cell(row=1, column=1)
        assert cell.fill.fgColor.rgb.endswith(DEFAULT_SHEET_STYLE.header_background)
        assert cell.font.color.rgb.endswith(DEFAULT_SHEET_STYLE.header_foreground)
        assert cell.border.left.style == "thin"


class TestWriteRow:
    def test_applies_the_column_number_format(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).write_row(["Serviço", 2, Decimal("2930.00")])
        assert sheet.cell(row=1, column=3).number_format == BR_CURRENCY_FORMAT
        assert sheet.cell(row=1, column=1).number_format == "General"

    def test_per_row_formats_override_the_column(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).write_row(
            ["Deságio", None, Decimal("0.30")],
            formats=[None, None, BR_PERCENT_FORMAT],
        )
        assert sheet.cell(row=1, column=3).number_format == BR_PERCENT_FORMAT

    def test_writes_numbers_not_strings(self) -> None:
        """A preformatted string cannot be sorted, filtered or summed."""
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).write_row(["x", 2, Decimal("2930.00")])
        assert sheet.cell(row=1, column=3).value == Decimal("2930.00")

    def test_formula_stays_a_formula(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).write_row(["Total", None, "=SUM(C1:C9)"])
        assert sheet.cell(row=1, column=3).value == "=SUM(C1:C9)"

    def test_formula_survives_the_round_trip_as_a_formula(self) -> None:
        """In memory any string survives; what matters is the written file."""
        workbook = new_workbook("Dados")
        SheetWriter(workbook["Dados"], COLUMNS).write_row(
            ["Total", None, "=SUM(C1:C9)"],
        )
        reopened = openpyxl.load_workbook(BytesIO(workbook_to_bytes(workbook)))

        cell = reopened["Dados"].cell(row=1, column=3)
        assert cell.value == "=SUM(C1:C9)"
        assert cell.data_type == "f"

    def test_dates_are_supported(self) -> None:
        columns = [Column("Data", number_format=BR_DATE_FORMAT)]
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, columns).write_row([date(2026, 8, 14)])
        assert sheet.cell(row=1, column=1).number_format == BR_DATE_FORMAT

    def test_alignment_comes_from_the_column(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).write_row(["x", 1, Decimal("1")])
        assert sheet.cell(row=1, column=1).alignment.wrap_text is True
        assert sheet.cell(row=1, column=2).alignment.horizontal == "center"

    def test_values_beyond_the_declared_columns_are_written_unstyled(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).write_row(["a", 1, Decimal("1"), "extra"])
        assert sheet.cell(row=1, column=4).value == "extra"
        assert sheet.cell(row=1, column=4).number_format == "General"

    def test_border_can_be_dropped(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).write_row(["x", 1, Decimal("1")], border=False)
        assert sheet.cell(row=1, column=1).border.left.style is None


class TestEmphasisRows:
    def test_group_row_uses_the_group_fill(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).group_row(["GRUPO 1"])
        cell = sheet.cell(row=1, column=1)
        assert cell.font.bold is True
        assert cell.fill.fgColor.rgb.endswith(DEFAULT_SHEET_STYLE.group_background)

    def test_total_row_uses_the_total_fill(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).total_row(["Total", None, Decimal("10")])
        cell = sheet.cell(row=1, column=3)
        assert cell.font.bold is True
        assert cell.fill.fgColor.rgb.endswith(DEFAULT_SHEET_STYLE.total_background)

    def test_none_cells_keep_their_fill(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).total_row(["Total", None, Decimal("10")])
        middle = sheet.cell(row=1, column=2)
        assert middle.value is None
        assert middle.fill.fgColor.rgb.endswith(DEFAULT_SHEET_STYLE.total_background)


class TestLayout:
    def test_apply_widths_sets_declared_columns_only(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        writer = SheetWriter(sheet, [Column("A", width=42), Column("B")])
        writer.apply_widths()
        assert sheet.column_dimensions["A"].width == 42
        assert "B" not in sheet.column_dimensions

    def test_freeze_below_defaults_to_the_cursor(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        writer = SheetWriter(sheet, COLUMNS)
        writer.header_row()
        writer.freeze_below()
        assert sheet.freeze_panes == "A2"

    def test_freeze_below_accepts_an_explicit_row(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        SheetWriter(sheet, COLUMNS).freeze_below(5)
        assert sheet.freeze_panes == "A5"


class TestSheetStyle:
    def test_is_plain_data(self) -> None:
        """A theme must be definable without openpyxl installed."""
        style = SheetStyle(header_background="000000", font_name="Arial")
        assert style.header_background == "000000"
        assert style.font_name == "Arial"

    def test_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            DEFAULT_SHEET_STYLE.header_background = "FFFFFF"  # type: ignore[misc]

    def test_custom_style_reaches_the_cells(self) -> None:
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        style = SheetStyle(header_background="112233", font_name="Arial")
        SheetWriter(sheet, COLUMNS, style=style).header_row()
        cell = sheet.cell(row=1, column=1)
        assert cell.fill.fgColor.rgb.endswith("112233")
        assert cell.font.name == "Arial"
