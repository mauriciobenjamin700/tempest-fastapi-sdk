"""The Mercado Pago webhook dependency: verify before the handler runs."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.api.handlers import register_exception_handlers
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    MERCADO_PAGO_DATA_ID_QUERY,
    MERCADO_PAGO_REQUEST_ID_HEADER,
    MERCADO_PAGO_SIGNATURE_HEADER,
    MercadoPagoEvent,
    MercadoPagoWebhookEvent,
    make_mercado_pago_webhook_dependency,
    sign_manifest,
)

SECRET: str = "mp-webhook-secret"
DATA_ID: str = "123456789"
REQUEST_ID: str = "bb56a2f1-6aae-46ac-982e-9dcd3581d08e"
TIMESTAMP: str = "1742505638"
BODY: bytes = json.dumps(
    {
        "action": "payment.updated",
        "type": "payment",
        "data": {"id": DATA_ID},
    }
).encode()


def _signature(
    *,
    data_id: str = DATA_ID,
    request_id: str = REQUEST_ID,
    timestamp: str = TIMESTAMP,
    secret: str = SECRET,
) -> str:
    """Build the ``x-signature`` value Mercado Pago would send.

    Args:
        data_id (str): The ``data.id`` the signature covers.
        request_id (str): The ``x-request-id`` the signature covers.
        timestamp (str): The ``ts`` component.
        secret (str): The secret to sign with.

    Returns:
        str: A ``ts=...,v1=...`` header value.
    """
    digest = sign_manifest(
        secret=secret, data_id=data_id, request_id=request_id, timestamp=timestamp
    )
    return f"ts={timestamp},v1={digest}"


class TestDependency:
    """End to end through FastAPI, with a handler that records every call."""

    def _app(self, **options: Any) -> tuple[FastAPI, list[MercadoPagoWebhookEvent]]:
        """Build an app whose single route requires a verified notification.

        Args:
            **options (Any): Forwarded to the factory.

        Returns:
            tuple[FastAPI, list[MercadoPagoWebhookEvent]]: The application and
            the list the handler appends each event it receives to.
        """
        app = FastAPI()
        register_exception_handlers(app)
        calls: list[MercadoPagoWebhookEvent] = []
        verified = make_mercado_pago_webhook_dependency(SECRET, **options)

        @app.post("/webhooks/mercado-pago")
        async def receive(
            event: MercadoPagoWebhookEvent = Depends(verified),
        ) -> dict[str, Any]:
            """Record and echo what the dependency returned.

            Args:
                event (MercadoPagoWebhookEvent): The verified notification.

            Returns:
                dict[str, Any]: The data id, topic and parsed event.
            """
            calls.append(event)
            return {
                "data_id": event.data_id,
                "topic": event.topic,
                "event": event.event,
            }

        return app, calls

    def _post(
        self,
        app: FastAPI,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes = BODY,
    ) -> Any:
        """Deliver one notification to the app.

        Args:
            app (FastAPI): The application under test.
            params (dict[str, str] | None): Query string; defaults to the
                ``data.id`` and ``type`` Mercado Pago appends.
            headers (dict[str, str] | None): Headers; defaults to a valid
                signature and request id.
            body (bytes): Raw body.

        Returns:
            Any: The ``httpx`` response.
        """
        client = TestClient(app)
        return client.post(
            "/webhooks/mercado-pago",
            params=(
                params
                if params is not None
                else {MERCADO_PAGO_DATA_ID_QUERY: DATA_ID, "type": "payment"}
            ),
            headers=(
                headers
                if headers is not None
                else {
                    MERCADO_PAGO_SIGNATURE_HEADER: _signature(),
                    MERCADO_PAGO_REQUEST_ID_HEADER: REQUEST_ID,
                }
            ),
            content=body,
        )

    def test_valid_delivery_returns_the_event(self) -> None:
        """A signed delivery reaches the handler with the parsed event."""
        app, calls = self._app()

        response = self._post(app)

        assert response.status_code == 200
        assert response.json() == {
            "data_id": DATA_ID,
            "topic": "payment",
            "event": "payment",
        }
        assert len(calls) == 1
        event = calls[0]
        assert event.event is MercadoPagoEvent.PAYMENT
        assert event.payload["action"] == "payment.updated"
        assert event.body == BODY

    def test_wrong_secret_is_401_and_handler_never_runs(self) -> None:
        """A signature from another secret is refused before the handler."""
        app, calls = self._app()

        response = self._post(
            app,
            headers={
                MERCADO_PAGO_SIGNATURE_HEADER: _signature(secret="other"),
                MERCADO_PAGO_REQUEST_ID_HEADER: REQUEST_ID,
            },
        )

        assert response.status_code == 401
        assert calls == []

    def test_tampered_data_id_is_401(self) -> None:
        """Pointing a valid signature at another resource does not verify."""
        app, calls = self._app()

        response = self._post(
            app, params={MERCADO_PAGO_DATA_ID_QUERY: "999", "type": "payment"}
        )

        assert response.status_code == 401
        assert calls == []

    def test_missing_signature_header_is_401(self) -> None:
        """An unsigned POST is not authenticated."""
        app, calls = self._app()

        response = self._post(app, headers={MERCADO_PAGO_REQUEST_ID_HEADER: REQUEST_ID})

        assert response.status_code == 401
        assert calls == []

    def test_missing_request_id_header_is_401(self) -> None:
        """Dropping a header the signature covered changes the manifest."""
        app, calls = self._app()

        response = self._post(
            app, headers={MERCADO_PAGO_SIGNATURE_HEADER: _signature()}
        )

        assert response.status_code == 401
        assert calls == []

    def test_missing_data_id_query_is_401(self) -> None:
        """Dropping the query parameter the signature covered is refused."""
        app, calls = self._app()

        response = self._post(app, params={"type": "payment"})

        assert response.status_code == 401
        assert calls == []

    def test_data_id_is_read_from_the_query_not_the_body(self) -> None:
        """The signed ``data.id`` is the query's, even when the body differs."""
        app, calls = self._app()
        body = json.dumps({"type": "payment", "data": {"id": "from-body"}}).encode()

        response = self._post(app, body=body)

        assert response.status_code == 200
        assert calls[0].data_id == DATA_ID

    def test_delivery_signed_without_data_id_verifies(self) -> None:
        """The manifest omits an absent ``data.id``, and so does the check."""
        app, calls = self._app()

        response = self._post(
            app,
            params={"type": "payment"},
            headers={
                MERCADO_PAGO_SIGNATURE_HEADER: _signature(data_id=""),
                MERCADO_PAGO_REQUEST_ID_HEADER: REQUEST_ID,
            },
        )

        assert response.status_code == 200
        assert calls[0].data_id == ""

    def test_empty_secret_refuses_everything(self) -> None:
        """No configured secret is not an open endpoint."""
        app = FastAPI()
        register_exception_handlers(app)
        verified = make_mercado_pago_webhook_dependency("")

        @app.post("/webhooks/mercado-pago")
        async def receive(
            event: MercadoPagoWebhookEvent = Depends(verified),
        ) -> dict[str, str]:
            """Echo the data id.

            Args:
                event (MercadoPagoWebhookEvent): The verified notification.

            Returns:
                dict[str, str]: The data id.
            """
            return {"data_id": event.data_id}

        response = self._post(
            app,
            headers={
                MERCADO_PAGO_SIGNATURE_HEADER: _signature(secret=""),
                MERCADO_PAGO_REQUEST_ID_HEADER: REQUEST_ID,
            },
        )

        assert response.status_code == 401

    def test_tolerance_rejects_a_stale_delivery(self) -> None:
        """With a window set, a replayed old ``ts`` is refused."""
        app, calls = self._app(tolerance_seconds=300.0)

        response = self._post(app)

        assert response.status_code == 401
        assert calls == []

    def test_tolerance_accepts_a_fresh_delivery(self) -> None:
        """With a window set, a current ``ts`` verifies."""
        app, calls = self._app(tolerance_seconds=300.0)
        fresh = str(int(time.time()))

        response = self._post(
            app,
            headers={
                MERCADO_PAGO_SIGNATURE_HEADER: _signature(timestamp=fresh),
                MERCADO_PAGO_REQUEST_ID_HEADER: REQUEST_ID,
            },
        )

        assert response.status_code == 200
        assert len(calls) == 1

    @pytest.mark.parametrize(
        ("body", "params", "topic", "event"),
        [
            (b"not json", {"type": "payment"}, "payment", MercadoPagoEvent.PAYMENT),
            (
                json.dumps({"type": "brand_new_topic"}).encode(),
                {},
                "brand_new_topic",
                MercadoPagoEvent.UNKNOWN,
            ),
            (b"{}", {}, "", None),
        ],
    )
    def test_topic_resolution(
        self,
        body: bytes,
        params: dict[str, str],
        topic: str,
        event: MercadoPagoEvent | None,
    ) -> None:
        """A verified delivery is never dropped over its body or topic.

        Args:
            body (bytes): Raw body delivered.
            params (dict[str, str]): Query besides ``data.id``.
            topic (str): Expected ``topic``.
            event (MercadoPagoEvent | None): Expected ``event``.
        """
        app, calls = self._app()

        response = self._post(
            app, params={MERCADO_PAGO_DATA_ID_QUERY: DATA_ID, **params}, body=body
        )

        assert response.status_code == 200
        assert calls[0].topic == topic
        assert calls[0].event is event
        assert calls[0].body == body
