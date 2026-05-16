"""Tests for tempest_fastapi_sdk.api.handlers."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    AppException,
    ConflictException,
    NotFoundException,
    register_exception_handlers,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundException(message="user not found", details={"id": "x"})

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictException()

    @app.get("/boom")
    async def boom() -> None:
        raise AppException(message="boom")

    return app


class TestHandlerEnvelope:
    def test_not_found_response_shape(self) -> None:
        client = TestClient(_make_app())
        response = client.get("/missing")
        assert response.status_code == 404
        body = response.json()
        assert body == {
            "detail": "user not found",
            "code": "NOT_FOUND",
            "details": {"id": "x"},
        }

    def test_conflict_defaults(self) -> None:
        client = TestClient(_make_app())
        response = client.get("/conflict")
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "CONFLICT"

    def test_base_app_exception(self) -> None:
        client = TestClient(_make_app())
        response = client.get("/boom")
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "INTERNAL_SERVER_ERROR"
        assert body["detail"] == "boom"
