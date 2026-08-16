"""Bracket-notation form encoding, and the values that are easy to get wrong."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

from tempest_fastapi_sdk import form_encode


class _Colour(Enum):
    """Enum used to check members reach the wire as their value."""

    RED = "red"


class TestFlattening:
    def test_flat_fields_keep_their_names(self) -> None:
        """A flat payload is just stringified."""
        assert form_encode({"amount": 1000, "currency": "brl"}) == {
            "amount": "1000",
            "currency": "brl",
        }

    def test_nested_mapping_uses_brackets(self) -> None:
        """``metadata[user_id]`` is the shape these APIs read."""
        assert form_encode({"metadata": {"user_id": 42}}) == {"metadata[user_id]": "42"}

    def test_list_of_objects_is_indexed(self) -> None:
        """Line items keep their order through numeric indices."""
        payload = {"items": [{"price": "price_1", "quantity": 2}, {"price": "price_2"}]}

        assert form_encode(payload) == {
            "items[0][price]": "price_1",
            "items[0][quantity]": "2",
            "items[1][price]": "price_2",
        }

    def test_deep_nesting_keeps_every_level(self) -> None:
        """Depth is not capped at two."""
        assert form_encode({"a": {"b": {"c": ["x"]}}}) == {"a[b][c][0]": "x"}

    def test_scalar_list_is_indexed_too(self) -> None:
        """``expand`` is a list of strings, and Stripe reads it indexed."""
        assert form_encode({"expand": ["customer", "latest_charge"]}) == {
            "expand[0]": "customer",
            "expand[1]": "latest_charge",
        }


class TestValueSpelling:
    def test_booleans_are_lowercase(self) -> None:
        """``str(True)`` would send ``"True"``, which these APIs reject."""
        assert form_encode({"paid": True, "refunded": False}) == {
            "paid": "true",
            "refunded": "false",
        }

    def test_none_is_dropped_not_emptied(self) -> None:
        """An empty string is a real value on these APIs — it clears a field."""
        assert form_encode({"note": None, "kept": "yes"}) == {"kept": "yes"}

    def test_empty_containers_disappear(self) -> None:
        """Nothing to occupy a bracket path means nothing on the wire."""
        assert form_encode({"metadata": {}, "items": []}) == {}

    def test_enum_members_send_their_value(self) -> None:
        """Otherwise ``str()`` would send ``"_Colour.RED"``."""
        assert form_encode({"colour": _Colour.RED}) == {"colour": "red"}

    def test_decimal_keeps_its_exact_text(self) -> None:
        """Going through float is how a cent goes missing."""
        assert form_encode({"amount": Decimal("10.50")}) == {"amount": "10.50"}

    def test_dates_are_iso_8601(self) -> None:
        """A date rendered by ``str()`` is already ISO, a datetime is not."""
        moment = datetime(2026, 8, 16, 12, 30, tzinfo=UTC)

        encoded = form_encode({"day": date(2026, 8, 16), "at": moment})

        assert encoded["day"] == "2026-08-16"
        assert encoded["at"] == "2026-08-16T12:30:00+00:00"

    def test_strings_are_not_treated_as_sequences(self) -> None:
        """A string is a scalar, not a list of characters."""
        assert form_encode({"name": "ana"}) == {"name": "ana"}


class TestEdges:
    def test_none_payload_is_an_empty_mapping(self) -> None:
        """Callers forward an optional body without branching."""
        assert form_encode(None) == {}

    def test_zero_and_empty_string_survive(self) -> None:
        """Only ``None`` is dropped — ``0`` and ``""`` are real values."""
        assert form_encode({"amount": 0, "note": ""}) == {"amount": "0", "note": ""}
