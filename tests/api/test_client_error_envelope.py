"""Every 4xx in the SDK envelope, not just the ``422``.

v0.284.0 put the ``422`` in the envelope and left the rest: measured with
a catalog installed and ``Accept-Language: pt-BR``, a raw
``HTTPException(403)``, the ``401`` from a security dependency, an unknown
route and a wrong method all answered a bare
``{"detail": "<English phrase>"}`` with no ``code``. All of them reach
this SDK's handler, so one branch covers them.

The load-bearing distinction is which ``detail`` may be replaced. A
message the caller wrote is more informative than any catalog entry, so
only the framework's own status phrase is localized.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBearer
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


class Login(BaseModel):
    """A body used to reach the ``422`` path.

    Declared at module level: a model defined inside a test function is a
    forward reference FastAPI cannot resolve, and the route raises
    ``PydanticUserError`` instead of validating.
    """

    password: str = Field(min_length=12)


PT = {"Accept-Language": "pt-BR"}
EN = {"Accept-Language": "en-US"}


def _app(*, envelope: bool, catalog: bool = True) -> FastAPI:
    """Build an app that fails in each 4xx way.

    Args:
        envelope (bool): Value for ``envelope_client_errors``.
        catalog (bool): Whether to install the built-in catalog.

    Returns:
        FastAPI: The application.
    """
    app = FastAPI()
    register_exception_handlers(
        app,
        catalog=default_message_catalog() if catalog else None,
        default_locale="pt-BR",
        envelope_client_errors=envelope,
    )

    @app.get("/authored")
    async def authored() -> None:
        raise HTTPException(404, "order 42 does not exist")

    @app.get("/generic403")
    async def generic403() -> None:
        raise HTTPException(403)

    @app.get("/generic404")
    async def generic404() -> None:
        raise HTTPException(404)

    @app.get("/generic409")
    async def generic409() -> None:
        raise HTTPException(409)

    @app.get("/bearer")
    async def bearer(
        credentials: Annotated[object, Depends(HTTPBearer())],
    ) -> dict[str, str]:
        return {}

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {}

    return app


class TestTheDefaultDidNotMove:
    """Flag off must reproduce Starlette's bare body."""

    @pytest.mark.parametrize(
        ("path", "status", "detail"),
        [
            ("/generic403", 403, "Forbidden"),
            ("/generic404", 404, "Not Found"),
            ("/bearer", 401, "Not authenticated"),
        ],
    )
    def test_no_code_and_the_english_phrase(
        self,
        path: str,
        status: int,
        detail: str,
    ) -> None:
        with TestClient(_app(envelope=False)) as client:
            response = client.get(path, headers=PT)

        assert response.status_code == status
        assert response.json() == {"detail": detail}


class TestEveryClientErrorGetsACode:
    """One shape to parse, across every 4xx the framework can produce."""

    @pytest.mark.parametrize(
        ("path", "status", "code"),
        [
            ("/authored", 404, "NOT_FOUND"),
            ("/generic403", 403, "FORBIDDEN"),
            ("/generic404", 404, "NOT_FOUND"),
            ("/generic409", 409, "CONFLICT"),
            ("/bearer", 401, "UNAUTHORIZED"),
        ],
    )
    def test_the_declared_code_reaches_the_body(
        self,
        path: str,
        status: int,
        code: str,
    ) -> None:
        with TestClient(_app(envelope=True)) as client:
            response = client.get(path, headers=PT)

        assert response.status_code == status
        assert response.json()["code"] == code

    def test_an_unknown_route_and_a_wrong_method_too(self) -> None:
        """Neither is raised by the service, and both reach the handler."""
        with TestClient(_app(envelope=True)) as client:
            missing: Any = client.get("/no-such-path", headers=PT).json()
            wrong: Any = client.put("/ok", headers=PT).json()

        assert missing["code"] == "NOT_FOUND"
        assert wrong["code"] == "HTTP_405"

    def test_a_status_the_sdk_does_not_model_is_still_parseable(self) -> None:
        """``HTTP_<status>`` beats no ``code`` at all.

        No :class:`AppException` subclass carries a 405 code, so there is
        nothing to map it to and nothing in the catalog to translate.
        """
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.put("/ok", headers=PT).json()

        assert body["code"] == "HTTP_405"
        assert body["detail"] == "Method Not Allowed"


class TestOnlyTheFrameworkPhraseIsReplaced:
    """A message the caller wrote survives; localizing it would lose it."""

    def test_an_authored_detail_is_preserved(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.get("/authored", headers=PT).json()

        assert body["detail"] == "order 42 does not exist"
        assert body["code"] == "NOT_FOUND"

    def test_fastapi_s_own_bearer_message_is_preserved(self) -> None:
        """``"Not authenticated"`` is not the 401 phrase, and says more."""
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.get("/bearer", headers=PT).json()

        assert body["detail"] == "Not authenticated"

    @pytest.mark.parametrize(
        ("path", "portuguese"),
        [
            ("/generic403", "Acesso negado"),
            ("/generic404", "Recurso não encontrado"),
            ("/generic409", "Conflito de recurso"),
        ],
    )
    def test_a_generic_detail_is_localized(
        self,
        path: str,
        portuguese: str,
    ) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.get(path, headers=PT).json()

        assert body["detail"] == portuguese

    def test_english_negotiates_to_the_catalog_s_english(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.get("/generic404", headers=EN).json()

        assert body["detail"] == "Resource not found"

    def test_without_a_catalog_the_phrase_stays(self) -> None:
        """The ``code`` is additive; localization needs a catalog."""
        with TestClient(_app(envelope=True, catalog=False)) as client:
            body: Any = client.get("/generic404", headers=PT).json()

        assert body["detail"] == "Not Found"
        assert body["code"] == "NOT_FOUND"


class TestTheEnvelopeKeepsWhatTheResponseNeeds:
    """Adding fields must not drop headers or change the status."""

    def test_the_www_authenticate_header_survives(self) -> None:
        """``HTTPBearer`` sets it, and a client uses it to retry."""
        with TestClient(_app(envelope=True)) as client:
            response = client.get("/bearer", headers=PT)

        assert response.headers["www-authenticate"] == "Bearer"

    def test_details_is_present_and_a_mapping(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.get("/generic404", headers=PT).json()

        assert isinstance(body["details"], dict)

    def test_the_request_id_reaches_details(self) -> None:
        with TestClient(_app(envelope=True)) as client:
            body: Any = client.get(
                "/generic404",
                headers={**PT, "X-Request-ID": "abc-123"},
            ).json()

        assert body["details"]["request_id"] == "abc-123"


class TestItImpliesTheValidationEnvelope:
    """``envelope_client_errors`` is the superset, so the 422 comes along.

    Two flags rather than one rename: a consumer that opted into the 422
    envelope in v0.284.0 must not silently get four more paths changed by
    upgrading.
    """

    def test_the_422_is_enveloped_too(self) -> None:
        app = FastAPI()
        register_exception_handlers(
            app,
            catalog=default_message_catalog(),
            default_locale="pt-BR",
            envelope_client_errors=True,
        )

        @app.post("/login")
        async def login(body: Login) -> dict[str, str]:
            return {}

        with TestClient(app) as client:
            body: Any = client.post("/login", json={"password": "x"}).json()

        assert body["code"] == "VALIDATION_ERROR"
        component = app.openapi()["components"]["schemas"]["HTTPValidationError"]
        assert component["properties"]["detail"]["type"] == "string"


class TestTheLogLineCarriesTheCode:
    """An operator greps the code, not the English phrase."""

    def test_the_code_is_on_the_record(
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
            client.get("/generic403", headers=PT)

        records = [
            record
            for record in caplog.records
            if "HTTPException 403" in record.getMessage()
        ]
        assert len(records) == 1
        assert records[0].code == "FORBIDDEN"
