"""Tests for the Brazilian document formatting helpers.

The value-in-words table is pinned case by case because it is the part
nobody re-derives when it drifts: a receipt still renders, still looks
right, and says the wrong amount in the line that exists precisely to
make the amount hard to alter.
"""

from __future__ import annotations

from datetime import date

import pytest

from tempest_fastapi_sdk.pdf.formatting import (
    MAX_EXTENSO_CENTS,
    format_cents,
    format_date,
    format_date_long,
    format_document,
    format_quantity,
    valor_por_extenso,
)


class TestFormatCents:
    def test_groups_thousands_with_dots_and_comma_decimals(self) -> None:
        """Brazilian separators are the inverse of the C locale's."""
        assert format_cents(123456) == "R$ 1.234,56"
        assert format_cents(100) == "R$ 1,00"
        assert format_cents(5) == "R$ 0,05"
        assert format_cents(100000000) == "R$ 1.000.000,00"

    def test_sign_precedes_the_symbol(self) -> None:
        """A credit reads ``-R$ 1,00`` on a Brazilian statement."""
        assert format_cents(-100) == "-R$ 1,00"

    def test_symbol_can_be_dropped(self) -> None:
        """Table cells often carry the symbol in the header instead."""
        assert format_cents(123456, symbol=False) == "1.234,56"


class TestValorPorExtenso:
    @pytest.mark.parametrize(
        "cents,expected",
        [
            (0, "zero real"),
            (1, "um centavo"),
            (100, "um real"),
            (150, "um real e cinquenta centavos"),
            (10000, "cem reais"),
            (100000, "mil reais"),
            (101500, "mil e quinze reais"),
            (120000, "mil e duzentos reais"),
            (121500, "mil, duzentos e quinze reais"),
            (
                123456,
                "mil, duzentos e trinta e quatro reais e cinquenta e seis centavos",
            ),
            (200000, "dois mil reais"),
            (1_000_000_00, "um milhão de reais"),
            (2_500_000_00, "dois milhões e quinhentos mil reais"),
            (1_001_500_00, "um milhão, mil e quinhentos reais"),
            (1_000_000_000_00, "um bilhão de reais"),
        ],
    )
    def test_spells_the_amount(self, cents: int, expected: str) -> None:
        """Each shape of the connector and the scale nouns is pinned."""
        assert valor_por_extenso(cents) == expected

    def test_de_only_when_the_number_ends_on_a_scale(self) -> None:
        """``de`` belongs to ``um milhão de reais``, not to ``mil reais``.

        And not when a smaller group follows the scale word — that is
        the case a naive "value >= 10**6" test gets wrong.
        """
        assert valor_por_extenso(100000).endswith("mil reais")
        assert "de reais" not in valor_por_extenso(100000)
        assert valor_por_extenso(1_000_000_00).endswith("de reais")
        assert "de reais" not in valor_por_extenso(2_500_000_00)

    def test_refuses_negative_and_out_of_scale(self) -> None:
        """Silence would drop the most significant group."""
        with pytest.raises(ValueError, match="negative"):
            valor_por_extenso(-1)
        with pytest.raises(ValueError, match="handles up to"):
            valor_por_extenso(MAX_EXTENSO_CENTS + 1)

    def test_largest_supported_value_still_spells(self) -> None:
        """The documented ceiling is inclusive."""
        assert valor_por_extenso(MAX_EXTENSO_CENTS).startswith("nove")


class TestFormatQuantity:
    def test_whole_quantities_drop_the_decimal_part(self) -> None:
        """``2,000 h`` on an invoice line reads as a mistake."""
        assert format_quantity(2.0) == "2"
        assert format_quantity(40) == "40"

    def test_fractional_quantities_use_a_comma(self) -> None:
        """A dot reads as a thousands separator to a Brazilian reader."""
        assert format_quantity(2.5) == "2,5"
        assert format_quantity(0.125) == "0,125"

    def test_trailing_zeros_are_dropped(self) -> None:
        """``2,50`` and ``2,5`` are the same quantity."""
        assert format_quantity(2.50) == "2,5"


class TestFormatDocument:
    def test_formats_cpf_and_cnpj(self) -> None:
        """Both lengths get their conventional punctuation."""
        assert format_document("12345678901") == "123.456.789-01"
        assert format_document("12345678000195") == "12.345.678/0001-95"

    def test_accepts_already_punctuated_input(self) -> None:
        """Re-formatting a formatted document is a no-op."""
        assert format_document("123.456.789-01") == "123.456.789-01"

    def test_leaves_anything_else_untouched(self) -> None:
        """A passport number must not be mangled into a CPF shape."""
        assert format_document("AB123456") == "AB123456"
        assert format_document("") == ""


class TestFormatDate:
    def test_short_and_long_forms(self) -> None:
        """The long form is what a signed document spells out."""
        value = date(2026, 8, 13)
        assert format_date(value) == "13/08/2026"
        assert format_date_long(value) == "13 de agosto de 2026"
