"""Tests for tempest_fastapi_sdk.core enum bases."""

from tempest_fastapi_sdk import BaseIntEnum, BaseStrEnum


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
