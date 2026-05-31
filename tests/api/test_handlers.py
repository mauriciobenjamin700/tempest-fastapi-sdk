"""Tests for tempest_fastapi_sdk.api.handlers."""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    AppException,
    ConflictException,
    NotFoundException,
    RequestIDMiddleware,
    register_exception_handlers,
)


def _make_app(
    *,
    include_traceback: bool = False,
    log_traceback: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(
        app,
        include_traceback=include_traceback,
        log_traceback=log_traceback,
    )

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundException(message="user not found", details={"id": "x"})

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictException()

    @app.get("/boom")
    async def boom() -> None:
        raise AppException(message="boom")

    @app.get("/unhandled")
    async def unhandled() -> None:
        raise RuntimeError("kaboom")

    @app.get("/divbyzero")
    async def divbyzero() -> None:
        _ = 1 / 0

    @app.get("/raw500")
    async def raw500() -> None:
        raise HTTPException(status_code=500, detail="Internal Server Error Test")

    @app.get("/raw504")
    async def raw504() -> None:
        raise HTTPException(status_code=504, detail="Gateway timeout")

    @app.get("/raw404")
    async def raw404() -> None:
        raise HTTPException(status_code=404, detail="missing")

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


class TestUnhandledExceptionHandler:
    def test_runtime_error_caught_and_logged(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            response = client.get(
                "/unhandled",
                headers={"X-Request-ID": "trace-runtime"},
            )
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "INTERNAL_SERVER_ERROR"
        assert body["detail"] == "Internal server error"
        assert body["details"]["request_id"] == "trace-runtime"
        assert "traceback" not in body["details"]
        records = [r for r in caplog.records if "Unhandled exception" in r.message]
        assert records, "unhandled exception handler did not log"
        assert records[0].exc_info is not None
        assert records[0].exc_info[0] is RuntimeError
        assert str(records[0].exc_info[1]) == "kaboom"

    def test_zero_division_caught(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        response = client.get("/divbyzero")
        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL_SERVER_ERROR"

    def test_include_traceback_dev_mode(self) -> None:
        client = TestClient(
            _make_app(include_traceback=True),
            raise_server_exceptions=False,
        )
        response = client.get("/unhandled")
        body = response.json()
        assert response.status_code == 500
        assert "traceback" in body["details"]
        joined = "".join(body["details"]["traceback"])
        assert "RuntimeError: kaboom" in joined

    def test_request_id_attached(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        response = client.get("/unhandled", headers={"X-Request-ID": "trace-42"})
        body = response.json()
        assert body["details"]["request_id"] == "trace-42"

    def test_app_exception_still_routes_to_envelope(self) -> None:
        """Domain exceptions take priority over the catch-all."""
        client = TestClient(_make_app())
        response = client.get("/missing")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "NOT_FOUND"
        assert "traceback" not in body["details"]

    def test_log_traceback_default_emits_exc_info(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``log_traceback=True`` (default) attaches the trace to the record."""
        client = TestClient(_make_app(), raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            client.get("/unhandled")
        record = next(r for r in caplog.records if "Unhandled exception" in r.message)
        assert record.exc_info is not None
        assert record.exc_info[0] is RuntimeError

    def test_log_traceback_disabled_omits_exc_info(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``log_traceback=False`` keeps the log line but drops the trace."""
        client = TestClient(
            _make_app(log_traceback=False),
            raise_server_exceptions=False,
        )
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            response = client.get("/unhandled")
        assert response.status_code == 500
        record = next(r for r in caplog.records if "Unhandled exception" in r.message)
        assert record.exc_info is None


class TestHttpExceptionHandler:
    def test_raw_http_500_logged_and_enveloped(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``raise HTTPException(500, ...)`` must log + return SDK envelope."""
        client = TestClient(_make_app(), raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            response = client.get("/raw500", headers={"X-Request-ID": "trace-500"})
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "INTERNAL_SERVER_ERROR"
        assert body["detail"] == "Internal Server Error Test"
        assert body["details"]["request_id"] == "trace-500"
        records = [r for r in caplog.records if "HTTPException 500" in r.message]
        assert records, "5xx HTTPException did not log"
        assert records[0].exc_info is not None

    def test_raw_http_504_treated_as_5xx(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            response = client.get("/raw504")
        assert response.status_code == 504
        body = response.json()
        assert body["code"] == "INTERNAL_SERVER_ERROR"
        records = [r for r in caplog.records if "HTTPException 504" in r.message]
        assert records, "5xx HTTPException did not log"

    def test_raw_http_404_pass_through_no_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = TestClient(_make_app())
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            response = client.get("/raw404")
        assert response.status_code == 404
        assert response.json() == {"detail": "missing"}
        records = [r for r in caplog.records if "HTTPException" in r.message]
        assert not records, "4xx HTTPException should not log"
