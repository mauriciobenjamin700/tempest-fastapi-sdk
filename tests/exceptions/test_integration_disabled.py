"""``IntegrationDisabledException`` on the wire."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    AppException,
    IntegrationDisabledException,
    default_message_catalog,
    register_exception_handlers,
)


def test_is_a_503_app_exception() -> None:
    error = IntegrationDisabledException("storage")
    assert isinstance(error, AppException)
    assert error.status_code == 503
    assert error.code == "INTEGRATION_DISABLED"
    assert error.details == {"integration": "storage"}
    assert error.integration == "storage"


def raise_disabled() -> None:
    """Route body that hits a switched-off integration.

    Raises:
        IntegrationDisabledException: Always.
    """
    raise IntegrationDisabledException("email")


def build_app(*, localized: bool) -> FastAPI:
    """Mount one route raising the exception, behind the SDK handlers.

    Args:
        localized (bool): Whether to register the default message catalog.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(
        app, catalog=default_message_catalog() if localized else None
    )
    app.add_api_route("/send", raise_disabled, methods=["POST"])
    return app


def test_envelope_carries_code_and_integration() -> None:
    response = TestClient(build_app(localized=False)).post("/send")
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "INTEGRATION_DISABLED"
    assert body["details"] == {"integration": "email"}


def test_catalog_names_the_integration_in_both_languages() -> None:
    client = TestClient(build_app(localized=True))
    portuguese = client.post("/send", headers={"Accept-Language": "pt-BR"})
    english = client.post("/send", headers={"Accept-Language": "en-US"})
    assert portuguese.json()["detail"] == (
        "A integração email está desligada neste ambiente"
    )
    assert english.json()["detail"] == (
        "The email integration is disabled in this environment"
    )
