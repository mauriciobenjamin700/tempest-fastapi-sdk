"""Tests for tempest_fastapi_sdk.utils.dict."""

from tempest_fastapi_sdk.utils import modify_dict


class TestModifyDict:
    def test_no_changes(self) -> None:
        data = {"a": 1, "b": 2}
        assert modify_dict(data) == data

    def test_exclude_keys(self) -> None:
        result = modify_dict({"a": 1, "b": 2, "c": 3}, exclude=["b"])
        assert result == {"a": 1, "c": 3}

    def test_include_merges_over(self) -> None:
        result = modify_dict({"a": 1}, include={"b": 2, "a": 99})
        assert result == {"a": 99, "b": 2}

    def test_exclude_and_include_combine(self) -> None:
        result = modify_dict({"a": 1, "b": 2}, exclude=["a"], include={"c": 3})
        assert result == {"b": 2, "c": 3}

    def test_does_not_mutate_input(self) -> None:
        data = {"a": 1}
        modify_dict(data, exclude=["a"], include={"b": 2})
        assert data == {"a": 1}
