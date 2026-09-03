"""The 422 inside the SDK's error envelope, and what it stops leaking.

``RequestValidationError`` is intercepted by FastAPI's own default
handler, so the 422 — the error a client sees most — was the only one
outside the envelope: pydantic's raw list, no ``code``, and never through
the ``MessageCatalog`` a service configured. Measured on 0.283.1 with a
catalog installed and ``Accept-Language: pt-BR``, the 422 still answered
in English.

Off by default, because changing a response body breaks a generated
client. The baseline class below is the guard that the default did not
move.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, SecretStr, field_validator

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


class Login(BaseModel):
    """A body whose failing fields are the ones worth not echoing."""

    email: str
    password: str = Field(min_length=12)
    token: SecretStr = Field(min_length=12)


class Strict(BaseModel):
    """A body whose validator raises, which puts an exception in ``ctx``."""

    value: int

    @field_validator("value")
    @classmethod
    def always_rejects(cls, value: int) -> int:
        """Reject every value.

        Args:
            value (int): The submitted value.

        Returns:
            int: Never returns.

        Raises:
            ValueError: Always.
        """
        raise ValueError("never accepted")


PAYLOAD: dict[str, str] = {
    "email": "a@b.c",
    "password": "hunter2",
    "token": "s3cr3t",
}


def _app(*, envelope: bool) -> FastAPI:
    """Build an app with the flag in one state.

    Args:
        envelope (bool): Value for ``envelope_validation_errors``.

    Returns:
        FastAPI: The application.
    """
    app = FastAPI()
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),
        default_locale="pt-BR",
        envelope_validation_errors=envelope,
    )

    @app.post("/login")
    async def login(body: Login) -> dict[str, str]:
        return {}

    @app.post("/strict")
    async def strict(body: Strict) -> dict[str, str]:
        return {}

    return app


class TestTheDefaultDidNotMove:
    """Flag off must reproduce FastAPI's own 422, byte shape included."""

    def test_the_body_is_still_pydantic_s_list(self) -> None:
        with TestClient(_app(envelope=False)) as client:
            body: Any = client.post("/login", json=PAYLOAD).json()

        assert isinstance(body["detail"], list)
        assert "code" not in body

    def test_the_schema_still_documents_an_array(self) -> None:
        app = _app(envelope=False)
        component = app.openapi()["components"]["schemas"]["HTTPValidationError"]

        assert component["properties"]["detail"]["type"] == "array"


class TestTheEnvelope:
    """Flag on gives every request error one shape to parse."""

    def test_the_response_carries_a_code(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.post("/login", json=PAYLOAD).json()

        assert body["code"] == "VALIDATION_ERROR"
        assert isinstance(body["detail"], str)

    def test_each_entry_keeps_loc_and_type(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.post("/login", json=PAYLOAD).json()

        errors = body["details"]["errors"]
        assert [entry["loc"] for entry in errors] == [
            ["body", "password"],
            ["body", "token"],
        ]
        assert [entry["type"] for entry in errors] == [
            "string_too_short",
            "too_short",
        ]

    def test_the_status_code_is_unchanged(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            response = client.post("/login", json=PAYLOAD)

        assert response.status_code == 422


class TestTheSubmittedValueStopsBeingEchoed:
    """FastAPI's 422 puts the failing field's ``input`` in the body.

    Measured: a ``password`` that fails ``min_length`` sends the password
    back, and ``SecretStr`` does **not** prevent it, because validation
    runs before the secret wrapper is built. This is not a warning to put
    in a recipe — the handler simply never emits ``input``.
    """

    def test_the_default_echoes_both_secrets(self) -> None:
        with TestClient(_app(envelope=False)) as client:
            text = client.post("/login", json=PAYLOAD).text

        assert "hunter2" in text
        assert "s3cr3t" in text

    def test_the_envelope_echoes_neither(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            response = client.post("/login", json=PAYLOAD)

        assert "hunter2" not in response.text
        assert "s3cr3t" not in response.text
        assert "input" not in response.text


class TestTheMessagesComeFromTheCatalog:
    """Keyed by pydantic error type, so a new field arrives translated."""

    def test_portuguese_is_negotiated_and_interpolated(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.post(
                "/login",
                json=PAYLOAD,
                headers={"Accept-Language": "pt-BR"},
            ).json()

        messages = [entry["msg"] for entry in body["details"]["errors"]]
        assert messages[0] == "O texto deve ter no mínimo 12 caractere(s)"
        assert "12" in messages[1]

    def test_english_falls_back_to_pydantic(self) -> None:
        """No English table to drift: upstream's ``msg`` is the message."""
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.post(
                "/login",
                json=PAYLOAD,
                headers={"Accept-Language": "en-US"},
            ).json()

        messages = [entry["msg"] for entry in body["details"]["errors"]]
        assert messages[0] == "String should have at least 12 characters"

    def test_the_top_level_detail_is_localized_too(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.post(
                "/login",
                json=PAYLOAD,
                headers={"Accept-Language": "pt-BR"},
            ).json()

        assert body["detail"] == "Erro de validação"


class TestAnExceptionInTheContextStaysA422:
    """``exc.errors()`` is not JSON-serializable on its own.

    A validator that raises puts the live ``ValueError`` in ``ctx``, and
    ``loc`` is a tuple. Handing that straight to ``JSONResponse`` raises
    ``TypeError: Object of type ValueError is not JSON serializable`` —
    inside the exception handler, so the 422 the route promises becomes a
    500. The handler runs the body through ``jsonable_encoder``.
    """

    @pytest.mark.parametrize("envelope", [False, True])
    def test_it_does_not_become_a_five_hundred(self, envelope: bool) -> None:
        with TestClient(
            _app(envelope=envelope),
            raise_server_exceptions=False,
        ) as client:
            response = client.post("/strict", json={"value": 1})

        assert response.status_code == 422


class TestTheSchemaAgreesWithTheRuntime:
    """A documented shape the runtime contradicts is the worse defect.

    Every route with a body references one ``HTTPValidationError``
    component, so rewriting it covers the whole app.
    """

    def test_detail_is_documented_as_a_string(self) -> None:
        app = _app(envelope=True)
        component = app.openapi()["components"]["schemas"]["HTTPValidationError"]

        assert component["properties"]["detail"]["type"] == "string"
        assert component["properties"]["code"]["default"] == "VALIDATION_ERROR"
        assert component["required"] == ["detail", "code", "details"]

    def test_the_item_schema_drops_input(self) -> None:
        app = _app(envelope=True)
        item = app.openapi()["components"]["schemas"]["ValidationError"]

        assert "input" not in item["properties"]

    def test_regenerating_the_schema_keeps_the_rewrite(self) -> None:
        """The patch wraps ``app.openapi``, so it is not a one-shot edit."""
        app = _app(envelope=True)
        app.openapi()
        app.openapi_schema = None
        component = app.openapi()["components"]["schemas"]["HTTPValidationError"]

        assert component["properties"]["detail"]["type"] == "string"


class TestTheLogLine:
    """A 422 is normal client flow, so it logs at INFO with a code."""

    def test_it_logs_once_at_info_with_the_code(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            caplog.at_level(
                logging.INFO,
                logger="tempest_fastapi_sdk.api.handlers",
            ),
            TestClient(_app(envelope=True)) as client,
        ):
            client.post("/login", json=PAYLOAD)

        records = [
            record
            for record in caplog.records
            if "Request validation failed" in record.getMessage()
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.INFO
        assert records[0].code == "VALIDATION_ERROR"
