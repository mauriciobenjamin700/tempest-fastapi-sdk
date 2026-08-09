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


HOSTILE_DESCRIPTION: str = (
    'The payer\'s "reference" — encode the characters (%, \\#, /) before '
    "sending it, because the gateway rejects the request otherwise and the "
    "error it answers with does not say which character was at fault.\n"
    "\tSecond line, indented with a tab."
)
"""A description carrying every construct a Python literal must escape.

Taken from the shape a real specification uses: a block scalar spanning
lines, an apostrophe, a pair of double quotes, a backslash that is not a
Python escape, a tab, and enough prose to overrun the line budget.
"""

HOSTILE_ENUM_VALUE: str = (
    "the-provider-spells-this-status-out-in-full-including-the-reason-"
    "the-charge-was-refused"
)
"""An enum value long enough that ``MEMBER = "value"`` overruns the budget."""


def _hostile_document() -> dict[str, Any]:
    """Build a specification whose text and names attack the emitters.

    Every construct here comes from a defect found generating against a
    real specification: text needing escapes, prose past the line budget,
    two component names colliding on one Python class, a path parameter
    declared but never interpolated, a placeholder nothing declares, path
    parameters listed out of template order, and a property whose wire
    name starts with a digit.

    Returns:
        dict[str, Any]: The OpenAPI document.
    """
    return {
        "openapi": "3.0.3",
        "info": {"title": "Hostile API", "version": "1.0.0"},
        "servers": [{"url": "https://api.hostile.example.com"}],
        "paths": {
            "/charges": {
                "get": {
                    "operationId": "listCharges",
                    "summary": HOSTILE_DESCRIPTION,
                    "description": "Lists charges. Escape (%, \\#, /) in filters.",
                    "responses": {"204": {"description": "no content"}},
                }
            },
            "/accounts/{accountId}/charges/{chargeId}": {
                "get": {
                    "operationId": "getAccountCharge",
                    "summary": "Fetch one charge of one account.",
                    "parameters": [
                        {
                            "name": "chargeId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "accountId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "expand",
                            "in": "path",
                            "required": True,
                            "description": "Declared as a path parameter by "
                            "mistake — the template never interpolates it.",
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/transaction"
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/receipts/{receiptId}": {
                "get": {
                    "operationId": "getReceipt",
                    "summary": "Fetch a receipt.",
                    "responses": {"204": {"description": "no content"}},
                }
            },
        },
        "components": {
            "schemas": {
                "transaction": {
                    "type": "object",
                    "description": HOSTILE_DESCRIPTION,
                    "required": ["reference"],
                    "properties": {
                        "reference": {
                            "type": "string",
                            "title": 'The "reference"',
                            "description": HOSTILE_DESCRIPTION,
                            "example": {
                                "value": 'a "quoted" one',
                                "escapes": ["\\#", "line\nbreak"],
                            },
                        },
                        "2fa": {
                            "type": "boolean",
                            "description": "Wire name starting with a digit.",
                        },
                        "status": {"$ref": "#/components/schemas/ChargeStatus"},
                    },
                },
                "Transaction": {
                    "type": "object",
                    "description": "Collides with `transaction` on one class name.",
                    "properties": {"id": {"type": "string"}},
                },
                "ChargeStatus": {
                    "type": "string",
                    "description": "Lifecycle state of the charge.",
                    "enum": ["paid", HOSTILE_ENUM_VALUE],
                },
            }
        },
    }


@pytest.fixture
def hostile_document() -> dict[str, Any]:
    """The hostile specification as a ``dict``.

    Returns:
        dict[str, Any]: A fresh copy per test, so a test may mutate it.
    """
    return _hostile_document()


@pytest.fixture
def hostile_spec_file(tmp_path: Path) -> Path:
    """The hostile specification written to a JSON file.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: Path to the written ``hostile.json``.
    """
    path = tmp_path / "hostile.json"
    path.write_text(json.dumps(_hostile_document()), encoding="utf-8")
    return path


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
