"""Amounts, and the currencies where dividing by 100 is a billing bug."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tempest_fastapi_sdk.integrations.payment.stripe import (
    THREE_DECIMAL_CURRENCIES,
    ZERO_DECIMAL_CURRENCIES,
    currency_exponent,
    format_amount,
    from_minor_units,
    to_minor_units,
)


class TestExponent:
    @pytest.mark.parametrize(
        ("currency", "expected"),
        [
            ("BRL", 2),
            ("usd", 2),
            ("JPY", 0),
            ("krw", 0),
            ("BHD", 3),
            ("kwd", 3),
        ],
    )
    def test_exponent_per_currency(self, currency: str, expected: int) -> None:
        """Case does not matter; the currency does.

        Args:
            currency (str): The code under test.
            expected (int): Decimal places Stripe uses for it.
        """
        assert currency_exponent(currency) == expected

    def test_unknown_currency_defaults_to_two(self) -> None:
        """A code this release has never heard of behaves like the majority."""
        assert currency_exponent("xyz") == 2

    def test_the_two_tables_do_not_overlap(self) -> None:
        """A currency cannot have both zero and three decimals."""
        assert ZERO_DECIMAL_CURRENCIES.isdisjoint(THREE_DECIMAL_CURRENCIES)

    def test_tables_are_lower_case(self) -> None:
        """Stripe emits lower-case codes; the lookup must match."""
        assert all(code == code.lower() for code in ZERO_DECIMAL_CURRENCIES)
        assert all(code == code.lower() for code in THREE_DECIMAL_CURRENCIES)


class TestToMinorUnits:
    def test_two_decimal_currency(self) -> None:
        """The ordinary case: reais to cents."""
        assert to_minor_units(Decimal("10.50"), "brl") == 1050

    def test_zero_decimal_currency_is_not_multiplied(self) -> None:
        """1050 yen is 1050, not 105000 — this is the whole point of the module."""
        assert to_minor_units(Decimal("1050"), "jpy") == 1050

    def test_three_decimal_currency(self) -> None:
        """Dinars carry three decimals."""
        assert to_minor_units(Decimal("10.505"), "bhd") == 10505

    def test_accepts_a_string(self) -> None:
        """Strings avoid the float that loses a cent."""
        assert to_minor_units("199.90", "brl") == 19990

    def test_float_input_still_lands_on_the_cent(self) -> None:
        """0.1 + 0.2 style error must not reach the charge."""
        assert to_minor_units(19.99, "brl") == 1999

    def test_rounds_half_up(self) -> None:
        """Banker's rounding would bill a customer a cent less, unpredictably."""
        assert to_minor_units(Decimal("0.005"), "brl") == 1

    def test_zero_is_zero(self) -> None:
        """A zero-amount intent is legitimate (setup, trial)."""
        assert to_minor_units(Decimal("0"), "brl") == 0


class TestFromMinorUnits:
    def test_round_trips_two_decimals(self) -> None:
        """What went in comes back."""
        assert from_minor_units(1050, "brl") == Decimal("10.50")

    def test_zero_decimal_currency_comes_back_whole(self) -> None:
        """Dividing yen by 100 is how a display shows ¥10.50 for a ¥1050 charge."""
        assert from_minor_units(1050, "jpy") == Decimal("1050")

    def test_three_decimal_currency(self) -> None:
        """Dinars keep their third decimal."""
        assert from_minor_units(10505, "bhd") == Decimal("10.505")

    def test_result_is_exact_not_a_float(self) -> None:
        """A ledger cannot take a binary float."""
        assert isinstance(from_minor_units(1999, "usd"), Decimal)

    @pytest.mark.parametrize("currency", ["brl", "jpy", "bhd"])
    def test_round_trip_is_stable(self, currency: str) -> None:
        """Converting both ways lands where it started.

        Args:
            currency (str): The code under test.
        """
        assert to_minor_units(from_minor_units(12345, currency), currency) == 12345


class TestFormatting:
    def test_two_decimal_currency(self) -> None:
        """Display keeps the cents."""
        assert format_amount(1050, "brl") == "10.50 BRL"

    def test_zero_decimal_currency(self) -> None:
        """No decimal point where the currency has no subunit."""
        assert format_amount(1050, "jpy") == "1050 JPY"

    def test_code_is_upper_cased(self) -> None:
        """Stripe stores lower case; humans read upper."""
        assert format_amount(100, "usd").endswith("USD")
