"""Tests for parsing a submitted form back into its schema."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, EmailStr, Field, model_validator

from tempest_fastapi_sdk.ui.forms import FormResult, parse_form


class Role(StrEnum):
    """Roles submitted through a select."""

    ADMIN = "admin"
    USER = "user"


class SignupSchema(BaseModel):
    """The schema every parse test validates against."""

    email: EmailStr
    password: str = Field(min_length=8)
    role: Role = Role.USER
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    note: str | None = None


def _app(**options: Any) -> TestClient:
    """Build a client posting to a route that parses a form.

    Args:
        **options (Any): Forwarded to :func:`parse_form`.

    Returns:
        TestClient: A client bound to the parsing route.
    """
    app = FastAPI()

    @app.post("/signup")
    async def signup(request: Request) -> Response:
        """Parse the submission and echo the outcome as JSON."""
        result = await parse_form(SignupSchema, request, **options)
        if not result.ok:
            return JSONResponse(
                {
                    "errors": result.errors,
                    "form_errors": result.form_errors,
                    "values": {
                        key: value
                        for key, value in result.values.items()
                        if isinstance(value, (str, list))
                    },
                },
                status_code=422,
            )
        return JSONResponse(result.unwrap().model_dump(mode="json"))

    return TestClient(app)


def test_valid_submission_returns_the_model() -> None:
    response = _app().post(
        "/signup",
        data={
            "email": "ana@example.com",
            "password": "12345678",
            "role": "admin",
            "tags": ["x", "y"],
            "active": "true",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "email": "ana@example.com",
        "password": "12345678",
        "role": "admin",
        "tags": ["x", "y"],
        "active": True,
        "note": None,
    }


def test_unchecked_checkbox_means_false() -> None:
    body = (
        _app()
        .post(
            "/signup",
            data={"email": "ana@example.com", "password": "12345678"},
        )
        .json()
    )
    assert body["active"] is False


def test_absent_key_falls_back_to_the_schema_default() -> None:
    """A select never submitted must not become ``None``."""
    body = (
        _app()
        .post(
            "/signup",
            data={"email": "ana@example.com", "password": "12345678"},
        )
        .json()
    )
    assert body["role"] == "user"
    assert body["tags"] == []


def test_empty_optional_becomes_none() -> None:
    body = (
        _app()
        .post(
            "/signup",
            data={"email": "ana@example.com", "password": "12345678", "note": ""},
        )
        .json()
    )
    assert body["note"] is None


def test_invalid_submission_reports_errors_per_field() -> None:
    response = _app().post("/signup", data={"email": "nope", "password": "123"})
    assert response.status_code == 422
    body = response.json()
    assert set(body["errors"]) == {"email", "password"}
    assert "at least 8 characters" in body["errors"]["password"][0]


def test_missing_required_field_reports_against_itself() -> None:
    body = _app().post("/signup", data={"password": "12345678"}).json()
    assert body["errors"]["email"] == ["Field required"]


def test_raw_values_survive_for_a_re_render() -> None:
    body = _app().post("/signup", data={"email": "nope", "password": "123"}).json()
    assert body["values"]["email"] == "nope"
    assert body["values"]["password"] == "123"


def test_model_level_errors_land_in_form_errors() -> None:
    class PairSchema(BaseModel):
        """Rejects mismatched passwords at model level."""

        password: str
        confirm: str

        @model_validator(mode="after")
        def _match(self) -> PairSchema:
            """Reject a mismatch.

            Returns:
                PairSchema: The validated model.

            Raises:
                ValueError: When the two fields differ.
            """
            if self.password != self.confirm:
                raise ValueError("as senhas não conferem")
            return self

    app = FastAPI()

    @app.post("/pair")
    async def pair(request: Request) -> Response:
        """Parse a pair submission."""
        result = await parse_form(PairSchema, request)
        return JSONResponse(
            {"errors": result.errors, "form_errors": result.form_errors},
        )

    body = TestClient(app).post("/pair", data={"password": "a", "confirm": "b"}).json()
    assert body["errors"] == {}
    assert "as senhas não conferem" in body["form_errors"][0]


def test_extra_values_override_the_body() -> None:
    body = (
        _app(extra={"role": Role.ADMIN})
        .post(
            "/signup",
            data={"email": "ana@example.com", "password": "12345678", "role": "user"},
        )
        .json()
    )
    assert body["role"] == "admin"


def test_excluded_fields_are_never_read_from_the_body() -> None:
    body = (
        _app(exclude=["role"], extra={"role": Role.ADMIN})
        .post(
            "/signup",
            data={"email": "ana@example.com", "password": "12345678", "role": "user"},
        )
        .json()
    )
    assert body["role"] == "admin"


def test_include_reads_only_the_named_fields() -> None:
    body = (
        _app(include=["email", "password"], extra={"role": Role.ADMIN})
        .post(
            "/signup",
            data={"email": "ana@example.com", "password": "12345678", "role": "user"},
        )
        .json()
    )
    assert body["role"] == "admin"


def test_unknown_body_keys_are_ignored() -> None:
    response = _app().post(
        "/signup",
        data={
            "email": "ana@example.com",
            "password": "12345678",
            "csrf_token": "x",
            "unexpected": "y",
        },
    )
    assert response.status_code == 200


def test_error_message_hook_rewrites_messages() -> None:
    client = _app(error_message=lambda error: f"campo inválido ({error['type']})")
    body = client.post("/signup", data={"email": "nope", "password": "123"}).json()
    assert body["errors"]["email"] == ["campo inválido (value_error)"]


def test_textarea_list_splits_on_lines() -> None:
    body = (
        _app()
        .post(
            "/signup",
            data={
                "email": "ana@example.com",
                "password": "12345678",
                "tags": "a\nb\n\n",
            },
        )
        .json()
    )
    assert body["tags"] == ["a", "b"]


def test_unwrap_on_an_invalid_result_raises() -> None:
    result: FormResult[SignupSchema] = FormResult(errors={"email": ["bad"]})
    assert result.ok is False
    with pytest.raises(ValueError, match="failed validation"):
        result.unwrap()
