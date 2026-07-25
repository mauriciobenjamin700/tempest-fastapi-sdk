"""Tests for tempest_fastapi_sdk.schemas.errors."""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    ConflictException,
    ErrorResponseSchema,
    register_exception_handlers,
)


class TestErrorResponseSchema:
    """The declared envelope matches what the handlers actually emit."""

    def test_details_defaults_to_empty_dict(self) -> None:
        """A failure with no context still validates."""
        envelope = ErrorResponseSchema(detail="boom", code="BOOM")
        assert envelope.details == {}

    def test_json_schema_documents_every_field(self) -> None:
        """Swagger/ReDoc render a description per field."""
        schema = ErrorResponseSchema.model_json_schema()
        assert set(schema["properties"]) == {"detail", "code", "details"}
        assert schema["required"] == ["detail", "code"]
        assert all(
            "description" in prop and "title" in prop
            for prop in schema["properties"].values()
        )

    def test_matches_the_app_exception_handler_body(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The schema validates the real handler output, not an ideal of it.

        Guards against the envelope and its declaration drifting apart:
        the response body is parsed straight back through the model.
        """

        class CategoryInUseException(ConflictException):
            """Deleting a category that services still reference."""

            code = "CATEGORY_IN_USE"

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/boom")
        async def boom() -> None:
            """Always fail, to capture the serialized envelope."""
            raise CategoryInUseException(
                "Cannot delete a category that still has services.",
                details={"category_id": "abc"},
            )

        with caplog.at_level(logging.INFO):
            response = TestClient(app, raise_server_exceptions=False).get("/boom")

        assert response.status_code == 409
        envelope = ErrorResponseSchema.model_validate(json.loads(response.text))
        assert envelope.code == "CATEGORY_IN_USE"
        assert envelope.details == {"category_id": "abc"}
        assert envelope.detail == "Cannot delete a category that still has services."
