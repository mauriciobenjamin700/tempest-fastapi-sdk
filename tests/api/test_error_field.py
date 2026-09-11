"""The envelope names the input a 4xx is about.

``make_app_exception_handler`` serialized exactly
``{"detail", "code", "details"}``, so only the ``422`` could say which
input failed (through ``loc``). A ``409`` or a ``401`` over the same
input could not, and the client re-derived the mapping at the call site
— knowledge the raiser had and the caller was guessing.

The key is emitted only when the exception names a field, never as
``null``, so ``"field" in body`` keeps meaning *there is a culprit
input*.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    AppException,
    ConflictException,
    UnauthorizedException,
    ValidationException,
    error_responses,
    register_exception_handlers,
)


class EmailTakenException(ConflictException):
    """A conflict that knows which input caused it."""

    code = "EMAIL_TAKEN"
    message = "This e-mail is already registered."
    field = "email"
    details_example: ClassVar[dict[str, Any]] = {"email": "ana@example.com"}


class QuotaExceededException(ConflictException):
    """A conflict about no input in particular."""

    code = "QUOTA_EXCEEDED"
    message = "Plan quota exceeded."


def _app() -> FastAPI:
    """Build an app that raises each shape.

    Returns:
        FastAPI: The application under test.
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/signup")
    async def signup() -> None:
        raise EmailTakenException()

    @app.post("/quota")
    async def quota() -> None:
        raise QuotaExceededException()

    @app.post("/rule")
    async def rule() -> None:
        raise ValidationException(
            "CPF does not match the holder.",
            field="cpf_cnpj",
        )

    @app.post("/login")
    async def login() -> None:
        raise UnauthorizedException("Invalid credentials.")

    return app


class TestEnvelopeField:
    """What reaches the wire."""

    def test_declared_field_reaches_the_body(self) -> None:
        response = TestClient(_app()).post("/signup")
        body: dict[str, Any] = response.json()

        assert response.status_code == 409
        assert body["code"] == "EMAIL_TAKEN"
        assert body["field"] == "email"

    def test_absent_field_is_omitted_not_null(self) -> None:
        """``null`` would force every client into a special case."""
        response = TestClient(_app()).post("/quota")
        body: dict[str, Any] = response.json()

        assert response.status_code == 409
        assert "field" not in body

    def test_raise_site_can_name_the_field(self) -> None:
        response = TestClient(_app()).post("/rule")
        body: dict[str, Any] = response.json()

        assert response.status_code == 422
        assert body["field"] == "cpf_cnpj"

    def test_invalid_credentials_name_no_field(self) -> None:
        """Naming one here is an account-enumeration oracle."""
        response = TestClient(_app()).post("/login")
        body: dict[str, Any] = response.json()

        assert response.status_code == 401
        assert "field" not in body

    def test_other_envelope_keys_are_unchanged(self) -> None:
        response = TestClient(_app()).post("/signup")
        body: dict[str, Any] = response.json()

        assert set(body) == {"detail", "code", "details", "field"}


class TestExceptionAttribute:
    """The attribute itself, without HTTP."""

    def test_class_default_is_none(self) -> None:
        assert AppException.field is None
        assert AppException().field is None

    def test_class_body_declaration_is_introspectable(self) -> None:
        """Readable without instantiating, like ``code``."""
        assert EmailTakenException.field == "email"

    def test_instance_override_does_not_touch_the_class(self) -> None:
        instance = EmailTakenException(field="e_mail")

        assert instance.field == "e_mail"
        assert EmailTakenException.field == "email"


class TestOpenAPIExample:
    """What ``error_responses`` documents."""

    def test_example_carries_the_field(self) -> None:
        responses = error_responses(EmailTakenException)
        example = responses[409]["content"]["application/json"]["examples"]

        assert example["EMAIL_TAKEN"]["value"]["field"] == "email"

    def test_example_omits_it_when_undeclared(self) -> None:
        responses = error_responses(QuotaExceededException)
        example = responses[409]["content"]["application/json"]["examples"]

        assert "field" not in example["QUOTA_EXCEEDED"]["value"]

    def test_schema_declares_the_optional_key(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)

        @app.post("/signup", responses=error_responses(EmailTakenException))
        async def signup() -> None:
            raise EmailTakenException()

        schema = app.openapi()["components"]["schemas"]["ErrorResponseSchema"]

        assert "field" in schema["properties"]
        assert "field" not in schema.get("required", [])
