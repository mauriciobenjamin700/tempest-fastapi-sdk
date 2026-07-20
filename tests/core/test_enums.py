"""Tests for tempest_fastapi_sdk.core enum bases."""

import re

from tempest_fastapi_sdk import BaseIntEnum, BaseStrEnum, Locale


class Color(BaseStrEnum):
    RED = "red"
    GREEN = "green"


class Priority(BaseIntEnum):
    LOW = 1
    HIGH = 2


class TestBaseStrEnum:
    def test_member_is_str_instance(self) -> None:
        assert isinstance(Color.RED, str)

    def test_member_compares_equal_to_value(self) -> None:
        assert Color.RED == "red"

    def test_values(self) -> None:
        assert Color.values() == ["red", "green"]

    def test_keys(self) -> None:
        assert Color.keys() == ["RED", "GREEN"]

    def test_to_dict(self) -> None:
        assert Color.to_dict() == {"RED": "red", "GREEN": "green"}

    def test_choices(self) -> None:
        assert Color.choices() == [("red", "RED"), ("green", "GREEN")]

    def test_has_value(self) -> None:
        assert Color.has_value("red") is True
        assert Color.has_value("purple") is False

    def test_has_key(self) -> None:
        assert Color.has_key("RED") is True
        assert Color.has_key("PURPLE") is False

    def test_from_value_by_value(self) -> None:
        assert Color.from_value("red") is Color.RED

    def test_from_value_by_name(self) -> None:
        assert Color.from_value("GREEN") is Color.GREEN

    def test_from_value_by_name_case_insensitive(self) -> None:
        assert Color.from_value("green") is Color.GREEN

    def test_from_value_invalid_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            Color.from_value("purple")

    def test_from_value_invalid_returns_default(self) -> None:
        assert Color.from_value("purple", default=None) is None
        assert Color.from_value("purple", default=Color.RED) is Color.RED


class TestBaseIntEnum:
    def test_member_is_int_instance(self) -> None:
        assert isinstance(Priority.LOW, int)

    def test_member_compares_equal_to_value(self) -> None:
        assert Priority.HIGH == 2

    def test_values(self) -> None:
        assert Priority.values() == [1, 2]

    def test_keys(self) -> None:
        assert Priority.keys() == ["LOW", "HIGH"]

    def test_to_dict(self) -> None:
        assert Priority.to_dict() == {"LOW": 1, "HIGH": 2}

    def test_choices(self) -> None:
        assert Priority.choices() == [(1, "LOW"), (2, "HIGH")]

    def test_has_value(self) -> None:
        assert Priority.has_value(1) is True
        assert Priority.has_value(99) is False

    def test_from_value_by_value(self) -> None:
        assert Priority.from_value(2) is Priority.HIGH

    def test_from_value_by_name(self) -> None:
        assert Priority.from_value("LOW") is Priority.LOW

    def test_from_value_invalid_returns_default(self) -> None:
        assert Priority.from_value(99, default=None) is None


class TestLocale:
    """The general-purpose BCP-47 locale enum."""

    def test_member_is_str_and_equals_tag(self) -> None:
        assert isinstance(Locale.PT_BR, str)
        assert Locale.PT_BR == "pt-BR"
        assert Locale.EN_US == "en-US"

    def test_all_values_are_bcp47_tags(self) -> None:
        pattern = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
        assert all(pattern.match(value) for value in Locale.values())

    def test_values_are_unique(self) -> None:
        values = Locale.values()
        assert len(values) == len(set(values))

    def test_from_value_resolves_tag(self) -> None:
        assert Locale.from_value("pt-BR") is Locale.PT_BR

    def test_has_value(self) -> None:
        assert Locale.has_value("en-US") is True
        assert Locale.has_value("xx-YY") is False
