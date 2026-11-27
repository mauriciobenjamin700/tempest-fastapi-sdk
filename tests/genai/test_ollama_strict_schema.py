"""The schema ``OllamaGenerator`` posts as ``format`` requires every property.

Pydantic lists in ``required`` only the fields without a default, and the
daemon's constrained decoder lets the model skip the rest. The reply then
validates — the default fills the field — and is wrong. Measured in the
2026-11-26/27 local-LLM study (Ollama 0.30.11, 33 extraction cases per
model): ``gpt-oss:20b`` never emitted a ``Field(default_factory=list)``
field under the plain schema, and per-field accuracy went from 70.8 to 95.4
once every property was required.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.genai import OllamaGenerator


class _ItemSchema(BaseModel):
    """One order line, nested through ``$defs`` and a list."""

    description: str
    quantity: int = 1


class _AddressSchema(BaseModel):
    """An optional nested object."""

    city: str
    zip_code: str | None = None


class _OrderSchema(BaseModel):
    """Fields with and without defaults at every nesting level."""

    customer: str
    email: str | None = None
    items: list[_ItemSchema] = Field(default_factory=list)
    address: _AddressSchema | None = None
    priority: str = "normal"
    properties: dict[str, str] = Field(default_factory=dict)


_ORDER_REPLY: dict[str, Any] = {
    "customer": "Ana",
    "email": None,
    "items": [{"description": "pen", "quantity": 2}],
    "address": None,
    "priority": "high",
    "properties": {},
}


def _object_nodes(node: object) -> list[dict[str, Any]]:
    """Collect every JSON-schema node that declares ``properties``.

    Args:
        node (object): A schema node.

    Returns:
        list[dict[str, Any]]: The object nodes, the root included.
    """
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict) and all(
            isinstance(value, dict) for value in properties.values()
        ):
            found.append(node)
            for child in properties.values():
                found.extend(_object_nodes(child))
        for key, value in node.items():
            if key != "properties":
                found.extend(_object_nodes(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_object_nodes(value))
    return found


def _capture(bodies: list[dict[str, Any]]) -> httpx.MockTransport:
    """Return a transport that records each body and answers an order.

    Args:
        bodies (list[dict[str, Any]]): Receives every request body.

    Returns:
        httpx.MockTransport: The transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": json.dumps(_ORDER_REPLY)}
            },
        )

    return httpx.MockTransport(handler)


def _assert_strict(schema: dict[str, Any]) -> None:
    """Assert every object node requires all of its properties.

    Args:
        schema (dict[str, Any]): The posted ``format`` schema.
    """
    nodes = _object_nodes(schema)
    assert len(nodes) == 3
    for node in nodes:
        assert node["required"] == list(node["properties"])


class TestStrictFormatSchema:
    async def test_chat_structured_requires_every_property_at_every_level(
        self,
    ) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", transport=_capture(bodies))
        order = await gen.chat_structured(
            [{"role": "user", "content": "Ana wants 2 pens"}], _OrderSchema
        )
        assert order.items[0].quantity == 2
        schema = bodies[0]["format"]
        _assert_strict(schema)
        assert schema["required"] == [
            "customer",
            "email",
            "items",
            "address",
            "priority",
            "properties",
        ]
        assert schema["$defs"]["_ItemSchema"]["required"] == [
            "description",
            "quantity",
        ]
        assert schema["$defs"]["_AddressSchema"]["required"] == ["city", "zip_code"]

    async def test_generate_structured_requires_every_property(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", transport=_capture(bodies))
        await gen.generate_structured("Ana wants 2 pens", _OrderSchema)
        _assert_strict(bodies[0]["format"])

    async def test_optional_fields_stay_nullable(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", transport=_capture(bodies))
        await gen.chat_structured([{"role": "user", "content": "x"}], _OrderSchema)
        schema = bodies[0]["format"]
        assert {"type": "null"} in schema["properties"]["email"]["anyOf"]
        assert {"type": "null"} in schema["properties"]["address"]["anyOf"]
        zip_code = schema["$defs"]["_AddressSchema"]["properties"]["zip_code"]
        assert {"type": "null"} in zip_code["anyOf"]

    async def test_the_model_schema_itself_is_not_modified(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", transport=_capture(bodies))
        await gen.chat_structured([{"role": "user", "content": "x"}], _OrderSchema)
        assert _OrderSchema.model_json_schema()["required"] == ["customer"]

    async def test_a_field_named_properties_is_a_property_not_a_keyword(
        self,
    ) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", transport=_capture(bodies))
        await gen.chat_structured([{"role": "user", "content": "x"}], _OrderSchema)
        properties_map = bodies[0]["format"]["properties"]
        assert "required" not in properties_map
        assert "required" not in properties_map["properties"]
