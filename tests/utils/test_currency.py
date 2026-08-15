"""Tests for Brazilian currency parsing and formatting."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tempest_fastapi_sdk.utils import (
    format_currency_br,
    format_percent_br,
    format_quantity_br,
    parse_currency_br,
    quantize_money,
)


class TestParseCurrencyBR:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("R$ 2.930,00", "2930.00"),
            ("2.930,00", "2930.00"),
            ("R$2.930,00", "2930.00"),
            ("  R$ 691.950,96  ", "691950.96"),
            ("0,01", "0.01"),
            ("1.234.567,89", "1234567.89"),
        ],
    )
    def test_reads_brazilian_notation(self, text: str, expected: str) -> None:
        assert parse_currency_br(text) == Decimal(expected)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2930.00", "2930.00"),
            ("2,930.00", "2930.00"),
            ("1,234,567.89", "1234567.89"),
        ],
    )
    def test_reads_us_notation(self, text: str, expected: str) -> None:
        assert parse_currency_br(text) == Decimal(expected)

    def test_lone_dot_with_three_digits_is_thousands(self) -> None:
        assert parse_currency_br("2.930") == Decimal("2930")

    def test_lone_dot_with_two_digits_is_decimal(self) -> None:
        assert parse_currency_br("2.93") == Decimal("2.93")

    def test_no_separator(self) -> None:
        assert parse_currency_br("2930") == Decimal("2930")

    def test_negative_keeps_sign(self) -> None:
        assert parse_currency_br("-R$ 0,01") == Decimal("-0.01")

    @pytest.mark.parametrize("text", ["", "   ", "R$", "sem valor", "-"])
    def test_returns_none_without_digits(self, text: str) -> None:
        assert parse_currency_br(text) is None

    def test_zero_is_not_none(self) -> None:
        """Priced at zero and no price printed are different facts."""
        assert parse_currency_br("R$ 0,00") == Decimal("0.00")

    def test_result_is_exact(self) -> None:
        """The whole reason to parse the string instead of taking a float."""
        parsed = parse_currency_br("R$ 0,10")
        assert parsed is not None
        assert parsed * 3 == Decimal("0.30")


class TestQuantizeMoney:
    def test_rounds_half_up_not_half_even(self) -> None:
        assert quantize_money(Decimal("1.005")) == Decimal("1.01")
        assert quantize_money(Decimal("1.015")) == Decimal("1.02")

    def test_negative_rounds_away_from_zero(self) -> None:
        assert quantize_money(Decimal("-1.005")) == Decimal("-1.01")

    def test_pads_to_two_places(self) -> None:
        assert str(quantize_money(Decimal("3"))) == "3.00"


class TestFormatCurrencyBR:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("484365.84", "R$ 484.365,84"),
            ("1234567.89", "R$ 1.234.567,89"),
            ("0", "R$ 0,00"),
            ("0.5", "R$ 0,50"),
            ("999.999", "R$ 1.000,00"),
            ("1000", "R$ 1.000,00"),
            ("100", "R$ 100,00"),
        ],
    )
    def test_groups_and_separates(self, amount: str, expected: str) -> None:
        assert format_currency_br(Decimal(amount)) == expected

    def test_negative_sign_precedes_symbol(self) -> None:
        assert format_currency_br(Decimal("-0.01")) == "-R$ 0,01"

    def test_without_symbol(self) -> None:
        assert format_currency_br(Decimal("2930"), symbol=False) == "2.930,00"

    def test_negative_without_symbol(self) -> None:
        assert format_currency_br(Decimal("-2930"), symbol=False) == "-2.930,00"

    def test_round_trips_through_the_parser(self) -> None:
        amount = Decimal("691950.96")
        assert parse_currency_br(format_currency_br(amount)) == amount


class TestFormatPercentBR:
    def test_ratio_becomes_percentage(self) -> None:
        assert format_percent_br(Decimal("0.30")) == "30,00%"

    def test_keeps_requested_places(self) -> None:
        assert format_percent_br(Decimal("0.2999998"), places=5) == "29,99998%"

    def test_zero_places_drops_separator(self) -> None:
        assert format_percent_br(Decimal("0.30"), places=0) == "30%"

    def test_negative(self) -> None:
        assert format_percent_br(Decimal("-0.05")) == "-5,00%"


class TestFormatQuantityBR:
    def test_groups_thousands(self) -> None:
        assert format_quantity_br(Decimal("1250")) == "1.250,00"

    def test_zero_places(self) -> None:
        assert format_quantity_br(Decimal("12"), places=0) == "12"

    def test_negative(self) -> None:
        assert format_quantity_br(Decimal("-3.5")) == "-3,50"


class TestFormatCentsDelegation:
    def test_matches_the_decimal_formatter(self) -> None:
        """``pdf.format_cents`` and ``format_currency_br`` must not drift."""
        from tempest_fastapi_sdk.pdf import format_cents

        for cents in (0, 1, 99, 100, 123456, -1, -123456):
            assert format_cents(cents) == format_currency_br(Decimal(cents) / 100)
