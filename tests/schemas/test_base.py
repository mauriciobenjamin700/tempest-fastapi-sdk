"""Tests for tempest_fastapi_sdk.schemas.base.BaseSchema."""

import json

from pydantic import Field

from tempest_fastapi_sdk.schemas import BaseSchema


class SampleSchema(BaseSchema):
    name: str = Field(default="alice")
    age: int | None = None


class TestBaseSchemaConfig:
    def test_ignores_extra_fields(self) -> None:
        result = SampleSchema(name="bob", extra_field="dropped")  # type: ignore[call-arg]
        assert not hasattr(result, "extra_field")

    def test_strips_whitespace(self) -> None:
        result = SampleSchema(name="  bob  ")
        assert result.name == "bob"

    def test_from_attributes(self) -> None:
        class Obj:
            name = "carol"
            age = 30

        result = SampleSchema.model_validate(Obj())
        assert result.name == "carol"
        assert result.age == 30


class TestToDict:
    def test_drops_none_values(self) -> None:
        result = SampleSchema(name="bob").to_dict()
        assert "age" not in result

    def test_exclude_removes_keys(self) -> None:
        result = SampleSchema(name="bob", age=30).to_dict(exclude=["age"])
        assert "age" not in result
        assert result["name"] == "bob"

    def test_include_merges_extra(self) -> None:
        result = SampleSchema(name="bob").to_dict(include={"role": "admin"})
        assert result["role"] == "admin"


class TestToJson:
    def test_returns_valid_json(self) -> None:
        raw = SampleSchema(name="bob", age=30).to_json()
        data = json.loads(raw)
        assert data == {"name": "bob", "age": 30}
