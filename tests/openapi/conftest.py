"""Shared specification fixtures for the OpenAPI generator tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _billing_document() -> dict[str, Any]:
    """Build a specification exercising the constructs the generator claims.

    Deliberately packed: camelCase names, a reserved-word property, an enum
    component reached only through a ``$ref``, an ``allOf`` composition, a
    self-reference, ``additionalProperties``, a ``nullable`` property, an
    operation with no ``operationId``, a header parameter, a 204 response,
    and constraints of all three families.

    Returns:
        dict[str, Any]: The OpenAPI document.
    """
    return {
        "openapi": "3.0.3",
        "info": {"title": "Billing API", "version": "2.1.0"},
        "servers": [{"url": "https://api.billing.example.com/v2"}],
        "paths": {
            "/customers": {
                "get": {
                    "operationId": "listCustomers",
                    "summary": "List customers",
                    "parameters": [
                        {
                            "name": "pageSize",
                            "in": "query",
                            "required": False,
                            "description": "Rows per page.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 200},
                        },
                        {
                            "name": "status",
                            "in": "query",
                            "required": False,
                            "schema": {"$ref": "#/components/schemas/CustomerStatus"},
                        },
                        {
                            "name": "X-Trace",
                            "in": "header",
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/Customer"
                                        },
                                    }
                                }
                            },
                        },
                        "401": {"description": "Missing credentials"},
                    },
                },
                "post": {
                    "operationId": "createCustomer",
                    "summary": "Create a customer",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/CustomerCreate"
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Customer"}
                                }
                            },
                        },
                        "409": {"description": "Email already registered"},
                    },
                },
            },
            "/customers/{customerId}": {
                "get": {
                    "operationId": "getCustomer",
                    "summary": "Fetch one customer",
                    "parameters": [
                        {
                            "name": "customerId",
                            "in": "path",
                            "required": True,
                            "description": "The customer identifier.",
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Customer"}
                                }
                            },
                        },
                        "404": {"description": "No such customer"},
                    },
                },
                "delete": {
                    "summary": "Delete a customer",
                    "parameters": [
                        {
                            "name": "customerId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {"204": {"description": "deleted"}},
                },
            },
        },
        "components": {
            "schemas": {
                "Customer": {
                    "type": "object",
                    "description": "A billable customer account.",
                    "required": ["id", "emailAddress", "createdAt"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "format": "uuid",
                            "description": "Server-assigned id.",
                            "example": "8f2c1e40-0000-4000-8000-000000000000",
                        },
                        "emailAddress": {
                            "type": "string",
                            "format": "email",
                            "title": "Email",
                            "description": "Primary contact email.",
                            "example": "ana@example.com",
                        },
                        "displayName": {
                            "type": "string",
                            "description": "Shown in the dashboard.",
                            "maxLength": 120,
                        },
                        "createdAt": {
                            "type": "string",
                            "format": "date-time",
                            "description": "Creation instant.",
                        },
                        "balanceCents": {
                            "type": "integer",
                            "description": "Outstanding balance in cents.",
                            "minimum": 0,
                        },
                        "status": {"$ref": "#/components/schemas/CustomerStatus"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Free-form labels.",
                        },
                        "billingAddress": {"$ref": "#/components/schemas/Address"},
                        "metadata": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "referrer": {"$ref": "#/components/schemas/Customer"},
                        "class": {
                            "type": "string",
                            "description": "Reserved-word field name.",
                        },
                    },
                },
                "CustomerCreate": {
                    "description": "Payload to open a new customer account.",
                    "allOf": [
                        {
                            "type": "object",
                            "required": ["emailAddress"],
                            "properties": {
                                "emailAddress": {
                                    "type": "string",
                                    "format": "email",
                                    "description": "Primary contact email.",
                                }
                            },
                        },
                        {
                            "type": "object",
                            "properties": {
                                "displayName": {
                                    "type": "string",
                                    "description": "Shown in the dashboard.",
                                },
                                "billingAddress": {
                                    "$ref": "#/components/schemas/Address"
                                },
                            },
                        },
                    ],
                },
                "Address": {
                    "type": "object",
                    "description": "A postal address.",
                    "required": ["line1", "countryCode"],
                    "properties": {
                        "line1": {
                            "type": "string",
                            "description": "Street and number.",
                        },
                        "line2": {
                            "type": "string",
                            "nullable": True,
                            "description": "Complement.",
                        },
                        "countryCode": {
                            "type": "string",
                            "description": "ISO 3166-1 alpha-2.",
                            "pattern": "^[A-Z]{2}$",
                        },
                    },
                },
                "CustomerStatus": {
                    "type": "string",
                    "enum": ["active", "past_due", "canceled"],
                    "description": "Lifecycle state of the account.",
                },
            }
        },
    }


@pytest.fixture
def billing_document() -> dict[str, Any]:
    """The packed billing specification as a ``dict``.

    Returns:
        dict[str, Any]: A fresh copy per test, so a test may mutate it.
    """
    return _billing_document()


@pytest.fixture
def billing_spec_file(tmp_path: Path) -> Path:
    """The billing specification written to a JSON file.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: Path to the written ``spec.json``.
    """
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(_billing_document()), encoding="utf-8")
    return path
