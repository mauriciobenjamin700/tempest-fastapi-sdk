"""Tests for schema-constrained structured output."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.genai import OllamaGenerator, parse_structured
from tempest_fastapi_sdk.utils.http_client import HTTPClient


class Person(BaseModel):
    """Tiny schema used across the structured-output tests."""

    name: str
    age: int


class TestParseStructured:
    def test_plain_json(self) -> None:
        person = parse_structured('{"name": "Alice", "age": 30}', Person)
        assert person == Person(name="Alice", age=30)

    def test_markdown_fenced_json(self) -> None:
        text = '```json\n{"name": "Bob", "age": 5}\n```'
        assert parse_structured(text, Person) == Person(name="Bob", age=5)

    def test_json_embedded_in_prose(self) -> None:
        text = 'Here you go: {"name": "Cid", "age": 9}. Hope it helps!'
        assert parse_structured(text, Person) == Person(name="Cid", age=9)

    def test_no_json_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="no JSON object"):
            parse_structured("there is nothing here", Person)

    def test_malformed_json_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="could not parse"):
            parse_structured("{name: Alice, age: }", Person)

    def test_schema_violation_raises_validationerror(self) -> None:
        with pytest.raises(ValidationError):
            parse_structured('{"name": "Alice"}', Person)


class TestOllamaStructured:
    async def test_sends_format_schema_and_parses_instance(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"response": '{"name": "Alice", "age": 30}', "done": True},
            )

        client = HTTPClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client)
        result = await gen.generate_structured("Give me a person.", Person)
        await client.aclose()

        assert result == Person(name="Alice", age=30)
        body = captured["body"]
        assert body["format"] == Person.model_json_schema()  # type: ignore[index]
