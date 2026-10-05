"""The ready zap-api webhook route: dispatch, refusal and the retry answer.

Every test here POSTs through a real :class:`TestClient` at a router built
by :func:`make_zap_webhook_router`, signing the body the way the gateway
does. The gateway's own ``signPayload`` is cross-checked in
``test_webhooks.py``; what matters here is the shape of the route: which
handler a delivery reaches, that an invalid signature never becomes a
``500``, that an event nobody claimed answers ``200``, and that a handler
which raises leaves the ``500`` standing — because that is what makes the
gateway re-deliver.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZAP_WEBHOOK_SIGNATURE_HEADER,
    ZapInboundMessage,
    ZapStatusCallback,
    ZapWebhookAckSchema,
    ZapWebhookEvent,
    make_zap_webhook_dependency,
    make_zap_webhook_router,
    webhook_verifier,
)

SECRET: str = "meu-segredo"
"""The secret the webhook would be registered with on the gateway."""

WHEN: str = "2026-04-21T18:30:00.000Z"
"""A fixed ``timestamp``, so bodies stay byte-stable across runs."""

ROUTER_LOGGER: str = "tempest_fastapi_sdk.integrations.messaging.zap.router"
"""Logger the route logs the deliveries it claims nobody for."""

INBOUND_BODY: bytes = (
    '{"event":"message.received","messageId":"ABCD1234",'
    '"from":"5511999999999@s.whatsapp.net","chatKey":"5511999999999",'
    '"pushName":"Fulano","text":"Olá!","mediaType":null,"mediaUrl":null,'
    f'"timestamp":"{WHEN}"}}'
).encode()
"""A ``message.received`` delivery, as the gateway puts it on the wire."""

STATUS_BODY: bytes = (
    b'{"event":"message.delivered","id":"outbound-uuid","consumer":"billing-api",'
    b'"to":"5511999999999","kind":"text","status":"delivered",'
    b'"waMessageId":"WAMID...","error":null,'
    b'"timestamp":"2026-06-27T18:30:00.000Z"}'
)
"""A status delivery, as the gateway puts it on the wire."""

UNKNOWN_BODY: bytes = b'{"event":"message.edited","messageId":"E1"}'
"""An event a gateway release can grow and this SDK does not model."""


def sign(body: bytes, secret: str = SECRET) -> str:
    """Sign a body the way the gateway does.

    Args:
        body (bytes): The exact bytes going on the wire.
        secret (str): The webhook secret.

    Returns:
        str: ``sha256=<hex>``, the value of the signature header.
    """
    digest: str = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class Recorder:
    """Collects what the handlers were called with."""

    def __init__(self) -> None:
        """Start with both sides uncalled."""
        self.inbound: list[ZapInboundMessage] = []
        self.status: list[ZapStatusCallback] = []

    async def on_inbound(self, message: ZapInboundMessage) -> None:
        """Record an inbound delivery.

        Args:
            message (ZapInboundMessage): The validated body.
        """
        self.inbound.append(message)

    async def on_status(self, callback: ZapStatusCallback) -> None:
        """Record a status delivery.

        Args:
            callback (ZapStatusCallback): The validated body.
        """
        self.status.append(callback)


def client_for(router: APIRouter, **kwargs: Any) -> TestClient:
    """Mount a router on a bare app and open a client on it.

    Args:
        router (APIRouter): The router under test.
        **kwargs (Any): Forwarded to :class:`TestClient` — the failing-handler
            case needs ``raise_server_exceptions=False`` to see the ``500``.

    Returns:
        TestClient: A client whose app serves ``router``.
    """
    app: FastAPI = FastAPI()
    app.include_router(router)
    return TestClient(app, **kwargs)


class TestSignedDeliveryReachesItsHandler:
    """A verified delivery is handed to the handler of its event."""

    def test_inbound_reaches_on_inbound(self) -> None:
        """``message.received`` calls ``on_inbound`` with the parsed body."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(
                secret=SECRET,
                on_inbound=recorder.on_inbound,
                on_status=recorder.on_status,
            )
        )

        response = client.post(
            "/webhooks/zap",
            content=INBOUND_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "event": "message.received"}
        assert len(recorder.inbound) == 1
        message: ZapInboundMessage = recorder.inbound[0]
        assert message.message_id == "ABCD1234"
        assert message.chat_key == "5511999999999"
        assert message.text == "Olá!"
        assert recorder.status == []

    def test_status_reaches_on_status(self) -> None:
        """``message.delivered`` calls ``on_status`` with the parsed body."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(
                secret=SECRET,
                on_inbound=recorder.on_inbound,
                on_status=recorder.on_status,
            )
        )

        response = client.post(
            "/webhooks/zap",
            content=STATUS_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(STATUS_BODY)},
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "event": "message.delivered"}
        assert len(recorder.status) == 1
        callback: ZapStatusCallback = recorder.status[0]
        assert callback.id == "outbound-uuid"
        assert callback.kind == "text"
        assert recorder.inbound == []

    @pytest.mark.parametrize(
        ("status_value", "event"),
        [
            pytest.param("sent", "message.sent", id="sent"),
            pytest.param("delivered", "message.delivered", id="delivered"),
            pytest.param("read", "message.read", id="read"),
            pytest.param("failed", "message.failed", id="failed"),
        ],
    )
    def test_every_status_event(self, status_value: str, event: str) -> None:
        """The four status events all reach ``on_status``."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(secret=SECRET, on_status=recorder.on_status)
        )
        body: bytes = json.dumps(
            {
                "event": event,
                "id": "out-1",
                "consumer": "bot",
                "to": "5511999999999",
                "kind": "text",
                "status": status_value,
                "timestamp": WHEN,
            }
        ).encode()

        response = client.post(
            "/webhooks/zap",
            content=body,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(body)},
        )

        assert response.status_code == 200
        assert [callback.status for callback in recorder.status] == [status_value]


class TestRefusedDelivery:
    """An unverifiable delivery is ``401`` and reaches no handler."""

    @pytest.mark.parametrize(
        ("body", "headers"),
        [
            pytest.param(INBOUND_BODY, {}, id="missing-signature"),
            pytest.param(
                INBOUND_BODY,
                {ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY, "outro")},
                id="wrong-secret",
            ),
            pytest.param(
                INBOUND_BODY,
                {
                    ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY).removeprefix(
                        "sha256="
                    )
                },
                id="missing-prefix",
            ),
        ],
    )
    def test_401_and_no_handler(
        self, body: bytes, headers: dict[str, str], caplog: pytest.LogCaptureFixture
    ) -> None:
        """The ``401`` is the signature failure, never a ``500``."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(
                secret=SECRET,
                on_inbound=recorder.on_inbound,
                on_status=recorder.on_status,
            )
        )

        with caplog.at_level(logging.DEBUG, logger=ROUTER_LOGGER):
            response = client.post("/webhooks/zap", content=body, headers=headers)

        assert response.status_code == 401
        assert "Invalid zap-api webhook signature" in response.text
        assert recorder.inbound == []
        assert recorder.status == []

    def test_a_body_that_is_not_json_is_200(self) -> None:
        """Nothing signed is JSON, and nothing here needs it to be."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(secret=SECRET, on_inbound=recorder.on_inbound)
        )
        body: bytes = b"not json at all"

        response = client.post(
            "/webhooks/zap",
            content=body,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(body)},
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "event": ""}
        assert recorder.inbound == []


class TestDeliveryNobodyClaims:
    """A delivery with no handler behind it answers ``200`` and logs."""

    def test_unknown_event_is_200_and_calls_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An event this SDK does not model must not make the gateway retry."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(
                secret=SECRET,
                on_inbound=recorder.on_inbound,
                on_status=recorder.on_status,
            )
        )

        with caplog.at_level(logging.DEBUG, logger=ROUTER_LOGGER):
            response = client.post(
                "/webhooks/zap",
                content=UNKNOWN_BODY,
                headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(UNKNOWN_BODY)},
            )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "event": "message.edited"}
        assert recorder.inbound == []
        assert recorder.status == []
        assert "message.edited" in caplog.text

    @pytest.mark.parametrize(
        ("body", "event"),
        [
            pytest.param(INBOUND_BODY, "message.received", id="without-on_inbound"),
            pytest.param(STATUS_BODY, "message.delivered", id="without-on_status"),
        ],
    )
    def test_known_event_without_its_handler_is_200(
        self, body: bytes, event: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A modelled event whose handler was not passed is still a ``200``."""
        client: TestClient = client_for(make_zap_webhook_router(secret=SECRET))

        with caplog.at_level(logging.DEBUG, logger=ROUTER_LOGGER):
            response = client.post(
                "/webhooks/zap",
                content=body,
                headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(body)},
            )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "event": event}
        assert event in caplog.text

    def test_a_body_that_does_not_match_its_event_model_is_200(self) -> None:
        """A known event whose body is incomplete is not the parser's error."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(secret=SECRET, on_inbound=recorder.on_inbound)
        )
        body: bytes = b'{"event":"message.received","messageId":"V1"}'

        response = client.post(
            "/webhooks/zap",
            content=body,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(body)},
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "event": "message.received"}
        assert recorder.inbound == []

    def test_the_log_names_the_fields_without_their_values(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """On a webhook the values are somebody's message; log the shape."""
        client: TestClient = client_for(make_zap_webhook_router(secret=SECRET))

        with caplog.at_level(logging.DEBUG, logger=ROUTER_LOGGER):
            response = client.post(
                "/webhooks/zap",
                content=INBOUND_BODY,
                headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
            )

        assert response.status_code == 200
        assert "message.received" in caplog.text
        assert "chatKey" in caplog.text
        assert "5511999999999" not in caplog.text
        assert "Olá!" not in caplog.text

    def test_the_router_accepts_no_handlers_at_all(self) -> None:
        """Registering before the handlers exist must not be a construction error."""
        client: TestClient = client_for(make_zap_webhook_router(secret=SECRET))

        response = client.post(
            "/webhooks/zap",
            content=INBOUND_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
        )

        assert response.status_code == 200


class TestFailingHandler:
    """A handler that raises leaves the ``500``, which is what retries."""

    def test_becomes_500_so_the_gateway_redelivers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Propagating is the choice: the gateway re-POSTs the same bytes."""
        recorder: Recorder = Recorder()

        async def explode(message: ZapInboundMessage) -> None:
            """Fail the way a cold database does.

            Args:
                message (ZapInboundMessage): The validated body.

            Raises:
                RuntimeError: Always.
            """
            recorder.inbound.append(message)
            raise RuntimeError("banco frio")

        client: TestClient = client_for(
            make_zap_webhook_router(secret=SECRET, on_inbound=explode),
            raise_server_exceptions=False,
        )

        with caplog.at_level(logging.ERROR):
            response = client.post(
                "/webhooks/zap",
                content=INBOUND_BODY,
                headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
            )

        assert response.status_code == 500
        assert len(recorder.inbound) == 1

    def test_a_handled_failure_answers_200(self) -> None:
        """Catching inside the handler is how a consumer stops the retries."""
        handled: list[str] = []

        async def swallow(message: ZapInboundMessage) -> None:
            """Accept the delivery and record the failure instead.

            Args:
                message (ZapInboundMessage): The validated body.
            """
            try:
                raise RuntimeError("banco frio")
            except RuntimeError:
                handled.append(message.message_id)

        client: TestClient = client_for(
            make_zap_webhook_router(secret=SECRET, on_inbound=swallow)
        )

        response = client.post(
            "/webhooks/zap",
            content=INBOUND_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
        )

        assert response.status_code == 200
        assert handled == ["ABCD1234"]


class TestConstruction:
    """A route without a way to verify a signature is refused to be built."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({}, id="neither"),
            pytest.param({"secret": ""}, id="empty-secret"),
            pytest.param({"secret": b""}, id="empty-bytes-secret"),
        ],
    )
    def test_refuses_without_a_secret_or_verify(self, kwargs: dict[str, Any]) -> None:
        """The gateway POSTs unsigned without a secret; refuse that shape."""
        with pytest.raises(ValueError):
            make_zap_webhook_router(**kwargs)

    def test_refuses_both_a_secret_and_a_verify(self) -> None:
        """Two ways to verify the same delivery is a mistake, not a merge."""
        with pytest.raises(ValueError):
            make_zap_webhook_router(
                secret=SECRET,
                verify=make_zap_webhook_dependency(secret=SECRET),
            )

    def test_accepts_a_verify_dependency_instead_of_a_secret(self) -> None:
        """A rotated secret checked by a custom verifier reaches the route."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(
                verify=make_zap_webhook_dependency(
                    verifier=webhook_verifier("rotated")
                ),
                on_inbound=recorder.on_inbound,
            )
        )

        response = client.post(
            "/webhooks/zap",
            content=INBOUND_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY, "rotated")},
        )

        assert response.status_code == 200
        assert [message.message_id for message in recorder.inbound] == ["ABCD1234"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"on_inbound": "não é chamável"}, id="on_inbound"),
            pytest.param({"on_status": 42}, id="on_status"),
        ],
    )
    def test_refuses_a_handler_it_cannot_call(self, kwargs: dict[str, Any]) -> None:
        """A handler that is not callable fails at construction, not at delivery."""
        with pytest.raises(TypeError):
            make_zap_webhook_router(secret=SECRET, **kwargs)


class TestMounting:
    """What the factory puts on the application."""

    def test_default_path(self) -> None:
        """``/webhooks/zap`` is the path the gateway is registered with."""
        router: APIRouter = make_zap_webhook_router(secret=SECRET)
        assert [route.path for route in router.routes] == ["/webhooks/zap"]

    def test_custom_path_is_mounted_where_asked(self) -> None:
        """``path`` moves the route and nothing else."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(
                secret=SECRET,
                on_inbound=recorder.on_inbound,
                path="/api/hooks/whatsapp",
            )
        )

        assert client.post("/webhooks/zap", content=INBOUND_BODY).status_code == 404
        response = client.post(
            "/api/hooks/whatsapp",
            content=INBOUND_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
        )

        assert response.status_code == 200
        assert len(recorder.inbound) == 1

    def test_default_tags(self) -> None:
        """The provider name groups it in the OpenAPI document."""
        router: APIRouter = make_zap_webhook_router(secret=SECRET)
        assert router.tags == ["zap"]

    def test_custom_tags(self) -> None:
        """``tags`` replaces the default rather than adding to it."""
        router: APIRouter = make_zap_webhook_router(secret=SECRET, tags=["webhooks"])
        assert router.tags == ["webhooks"]

    def test_in_the_openapi_document_by_default(self) -> None:
        """The route documents the acknowledgement it answers with."""
        app: FastAPI = FastAPI()
        app.include_router(make_zap_webhook_router(secret=SECRET))

        schema: dict[str, Any] = app.openapi()
        operation: dict[str, Any] = schema["paths"]["/webhooks/zap"]["post"]

        assert operation["tags"] == ["zap"]
        assert operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/ZapWebhookAckSchema"}
        assert schema["components"]["schemas"]["ZapWebhookAckSchema"]["properties"] == {
            "ok": {"title": "Ok", "type": "boolean"},
            "event": {"title": "Event", "type": "string"},
        }

    def test_out_of_the_openapi_document_on_request(self) -> None:
        """``include_in_schema=False`` hides it without unmounting it."""
        app: FastAPI = FastAPI()
        app.include_router(
            make_zap_webhook_router(secret=SECRET, include_in_schema=False)
        )

        assert "/webhooks/zap" not in app.openapi()["paths"]
        client: TestClient = TestClient(app)
        response = client.post(
            "/webhooks/zap",
            content=INBOUND_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
        )
        assert response.status_code == 200

    def test_the_acknowledgement_is_a_schema(self) -> None:
        """The body is typed, so the consumer sees ``ok`` in its client."""
        ack: ZapWebhookAckSchema = ZapWebhookAckSchema(ok=True, event="message.read")
        assert ack.model_dump() == {"ok": True, "event": "message.read"}


class TestEventNamesInTheAnswer:
    """The acknowledgement echoes the name as delivered."""

    @pytest.mark.parametrize(
        ("body", "event"),
        [
            pytest.param(INBOUND_BODY, "message.received", id="inbound"),
            pytest.param(STATUS_BODY, "message.delivered", id="status"),
            pytest.param(UNKNOWN_BODY, "message.edited", id="unknown"),
            pytest.param(b"[1, 2, 3]", "", id="not-an-object"),
        ],
    )
    def test_echoes_the_wire_name(self, body: bytes, event: str) -> None:
        """The name survives even where no handler claimed the delivery."""
        client: TestClient = client_for(make_zap_webhook_router(secret=SECRET))

        response = client.post(
            "/webhooks/zap",
            content=body,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(body)},
        )

        assert response.json() == {"ok": True, "event": event}

    def test_the_enum_is_what_the_route_dispatches_on(self) -> None:
        """``ZapWebhookEvent`` names the same strings the parser fills."""
        recorder: Recorder = Recorder()
        client: TestClient = client_for(
            make_zap_webhook_router(secret=SECRET, on_inbound=recorder.on_inbound)
        )

        response = client.post(
            "/webhooks/zap",
            content=INBOUND_BODY,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: sign(INBOUND_BODY)},
        )

        assert response.json()["event"] == ZapWebhookEvent.MESSAGE_RECEIVED.value
        assert len(recorder.inbound) == 1
