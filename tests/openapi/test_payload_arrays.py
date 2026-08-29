"""An optional array means opposite things in the two directions.

Read back from a response, an absent array is an empty one — the repo rule
that "no matches" is a list, not a missing value. Sent in a request body it
is a claim: measured against Woovi, ``{"splits": []}`` is answered with
``400 O array de split precisa ter ao menos um item`` while the identical
body without the key is accepted.

The generator used to materialize both as ``default_factory=list``, so every
generated call asserted something the caller never said, and
``exclude_none`` had no ``None`` to drop. These tests fix the rule at the
level it is decided: a model reached only as a request body gets an
omissible field; one a response can reach keeps the list.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.openapi.ir import FieldIR, SchemaIR, SpecIR
from tempest_fastapi_sdk.openapi.parse import parse_spec

_ITEM: dict[str, Any] = {"type": "object", "properties": {"id": {"type": "string"}}}


def _document(*, request_of: list[str], response_of: list[str]) -> dict[str, Any]:
    """Build a document wiring components into the two directions.

    Args:
        request_of (list[str]): Components used as a request body.
        response_of (list[str]): Components used as a success response.

    Returns:
        dict[str, Any]: A loadable OpenAPI document whose components all
        carry one required scalar and one optional array.
    """
    names = sorted({*request_of, *response_of})
    components: dict[str, Any] = {
        name: {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "splits": {"type": "array", "items": _ITEM},
            },
        }
        for name in names
    }
    paths: dict[str, Any] = {}
    for index, name in enumerate(request_of):
        paths[f"/send/{index}"] = {
            "post": {
                "operationId": f"send{name}",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{name}"}
                        }
                    }
                },
                "responses": {"204": {"description": "done"}},
            }
        }
    for index, name in enumerate(response_of):
        paths[f"/read/{index}"] = {
            "get": {
                "operationId": f"read{name}",
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{name}"}
                            }
                        },
                    }
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1"},
        "paths": paths,
        "components": {"schemas": components},
    }


def _field(spec: SpecIR, class_name: str, field_name: str) -> FieldIR:
    """Return one generated field.

    Args:
        spec (SpecIR): The parsed specification.
        class_name (str): The generated class.
        field_name (str): The Python field name.

    Returns:
        FieldIR: The field.
    """
    schema: SchemaIR = next(s for s in spec.schemas if s.name == class_name)
    return next(f for f in schema.fields if f.name == field_name)


class TestOptionalArraysByDirection:
    def test_a_request_only_model_can_omit_the_array(self) -> None:
        """The shape the reported 400 needed."""
        spec = parse_spec(
            _document(request_of=["Payload"], response_of=[]),
            client_name="T",
        )
        field = _field(spec, "Payload", "splits")
        assert field.default == "None"
        assert field.default_is_factory is False
        assert field.annotation.endswith("| None")

    def test_a_response_model_still_materializes_the_array(self) -> None:
        """ "No matches" stays an empty list on the read side."""
        spec = parse_spec(
            _document(request_of=[], response_of=["Reply"]),
            client_name="T",
        )
        field = _field(spec, "Reply", "splits")
        assert field.default == "list"
        assert field.default_is_factory is True
        assert not field.annotation.endswith("| None")

    def test_a_model_used_both_ways_keeps_the_response_spelling(self) -> None:
        """Ambiguous direction is not guessed; ``_dump`` handles the send."""
        spec = parse_spec(
            _document(request_of=["Shared"], response_of=["Shared"]),
            client_name="T",
        )
        field = _field(spec, "Shared", "splits")
        assert field.default == "list"
        assert field.default_is_factory is True

    def test_a_required_array_is_untouched(self) -> None:
        """Only the optional half of the rule moves."""
        document = _document(request_of=["Payload"], response_of=[])
        document["components"]["schemas"]["Payload"]["required"] = ["id", "splits"]
        spec = parse_spec(document, client_name="T")
        field = _field(spec, "Payload", "splits")
        assert field.required is True
        assert field.default is None
