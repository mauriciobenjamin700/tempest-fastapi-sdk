"""Parsing survives components that are reached from inside another field.

The parser attributes each "could not model this" note to the field it came
from by opening a capture sink around the field's type rendering. That
rendering is re-entrant: resolving a ``$ref`` builds the referenced
component, whose own fields open their own sinks. Removing a sink by
equality then removes the wrong one, because two sinks that collected
nothing are both ``[]``.

Nobody noticed until the generator was pointed at Stripe's specification,
where it aborted the whole run with
``ValueError: list.remove(x): x not in list``.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.openapi.parse import parse_spec


def _nested_document() -> dict[str, Any]:
    """Build a specification whose fields resolve refs to other components.

    The chain matters: ``Order.customer`` renders a union that resolves
    ``Customer``, whose ``address`` field renders a union that resolves
    ``Address``. Three capture sinks are open at the deepest point, and the
    two innermost start empty.

    Returns:
        dict[str, Any]: The OpenAPI document.
    """
    return {
        "openapi": "3.0.3",
        "info": {"title": "Nested API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/orders": {
                "get": {
                    "operationId": "listOrders",
                    "summary": "List orders.",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Order"},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Order": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "customer": {
                            "anyOf": [
                                {"type": "string"},
                                {"$ref": "#/components/schemas/Customer"},
                            ]
                        },
                    },
                },
                "Customer": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "address": {
                            "anyOf": [
                                {"type": "string"},
                                {"$ref": "#/components/schemas/Address"},
                            ]
                        },
                        "tags": {"type": ["string", "integer"]},
                    },
                },
                "Address": {
                    "type": "object",
                    "properties": {
                        "line1": {"type": "string"},
                        "coordinates": {"type": ["number", "string"]},
                    },
                },
            }
        },
    }


class TestReentrantParsing:
    def test_nested_component_resolution_does_not_abort(self) -> None:
        """The parse completes instead of dying on sink bookkeeping."""
        spec = parse_spec(_nested_document(), client_name="nested")

        assert {"Order", "Customer", "Address"} <= {s.name for s in spec.schemas}

    def test_notes_still_reach_the_field_that_raised_them(self) -> None:
        """The fix keeps what the sinks are for: per-field attribution.

        ``Address.coordinates`` and ``Customer.tags`` are multi-type
        schemas the parser cannot model, so each one carries its own note.
        Removing sinks by identity must not lose that.
        """
        spec = parse_spec(_nested_document(), client_name="nested")
        by_name = {schema.name: schema for schema in spec.schemas}

        address = by_name["Address"]
        coordinates = next(f for f in address.fields if f.name == "coordinates")
        customer = by_name["Customer"]
        tags = next(f for f in customer.fields if f.name == "tags")

        assert coordinates.unsupported
        assert tags.unsupported

    def test_unrelated_fields_carry_no_note(self) -> None:
        """A sink removed out of order used to bleed notes across fields."""
        spec = parse_spec(_nested_document(), client_name="nested")
        address = next(schema for schema in spec.schemas if schema.name == "Address")

        line1 = next(f for f in address.fields if f.name == "line1")

        assert line1.unsupported == ()
