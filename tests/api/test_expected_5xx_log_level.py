"""A 5xx the application answers on purpose is not logged as a failure (#702).

The ``AppException`` handler logged every ``status_code >= 500`` at the
handler's level with a traceback and the ``http_500`` marker, so
``503 INTEGRATION_DISABLED`` — an integration switched off by configuration —
filled ``500.log`` and the admin's "500" source: in one service, 8 of 11
records there, burying the one real bug.
"""

from __future__ import annotations

import logging
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from tempest_fastapi_sdk import (
    AppException,
    EmailDeliveryException,
    IntegrationDisabledException,
    register_exception_handlers,
)
from tempest_fastapi_sdk.core.logging import HTTP_500_MARKER

HANDLER_LOGGER: str = "tempest_fastapi_sdk.api.handlers"


class MaintenanceException(AppException):
    """A service's own planned 503, declared as expected."""

    status_code: int = 503
    message: str = "Scheduled maintenance"
    code: str = "MAINTENANCE"
    log_level: ClassVar[int | None] = logging.INFO


class BrokenUpstreamException(AppException):
    """A plain 5xx that keeps the failure treatment."""

    status_code: int = 502
    message: str = "Upstream broke"
    code: str = "UPSTREAM_BROKE"


def _app(calls: list[str]) -> FastAPI:
    """Build an app with one route per exception under test.

    Args:
        calls (list[str]): Receives the code of each ``on_server_error`` call.

    Returns:
        FastAPI: The app.
    """

    async def record(request: Request, exc: Exception) -> None:
        calls.append(getattr(exc, "code", type(exc).__name__))

    app = FastAPI()
    register_exception_handlers(app, on_server_error=record)

    @app.get("/disabled")
    async def disabled() -> None:
        raise IntegrationDisabledException("storage")

    @app.get("/email")
    async def email() -> None:
        raise EmailDeliveryException()

    @app.get("/maintenance")
    async def maintenance() -> None:
        raise MaintenanceException()

    @app.get("/broken")
    async def broken() -> None:
        raise BrokenUpstreamException()

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("bug")

    return app


def _records(
    caplog: pytest.LogCaptureFixture, path: str, calls: list[str] | None = None
) -> tuple[int, list[logging.LogRecord]]:
    """Request ``path`` and return its status and the handler's records.

    Args:
        caplog (pytest.LogCaptureFixture): The log capture.
        path (str): The route to call.
        calls (list[str] | None): ``on_server_error`` sink.

    Returns:
        tuple[int, list[logging.LogRecord]]: Status code and records.
    """
    client = TestClient(
        _app(calls if calls is not None else []), raise_server_exceptions=False
    )
    with caplog.at_level(logging.DEBUG, logger=HANDLER_LOGGER):
        response = client.get(path)
    records = [r for r in caplog.records if r.name == HANDLER_LOGGER]
    return response.status_code, records


@pytest.mark.parametrize(
    ("path", "code"),
    [("/disabled", "INTEGRATION_DISABLED"), ("/email", "EMAIL_UNAVAILABLE")],
)
def test_sdk_expected_503_is_a_warning(
    caplog: pytest.LogCaptureFixture, path: str, code: str
) -> None:
    calls: list[str] = []
    status, records = _records(caplog, path, calls)

    assert status == 503
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert not getattr(record, HTTP_500_MARKER, False)
    assert calls == []


def test_the_envelope_does_not_change(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(_app([]), raise_server_exceptions=False)
    body = client.get("/disabled").json()
    assert body["code"] == "INTEGRATION_DISABLED"
    assert body["details"] == {"integration": "storage"}


def test_service_exception_can_declare_its_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    status, records = _records(caplog, "/maintenance")

    assert status == 503
    assert [r.levelno for r in records] == [logging.INFO]
    assert records[0].exc_info is None
    assert not getattr(records[0], HTTP_500_MARKER, False)


def test_plain_5xx_keeps_the_failure_treatment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    status, records = _records(caplog, "/broken", calls)

    assert status == 502
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
    assert getattr(records[0], HTTP_500_MARKER, False) is True
    assert calls == ["UPSTREAM_BROKE"]


def test_unhandled_exception_is_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[str] = []
    status, records = _records(caplog, "/crash", calls)

    assert status == 500
    unhandled = [r for r in records if "Unhandled exception" in r.getMessage()]
    assert len(unhandled) == 1
    assert unhandled[0].levelno == logging.ERROR
    assert unhandled[0].exc_info is not None
    assert calls == ["RuntimeError"]
