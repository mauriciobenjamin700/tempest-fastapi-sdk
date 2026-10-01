"""The zap-api webhook receiver: signature, parsing and status ordering.

The two bodies below are the README examples of the gateway (sections
"Callbacks de status" and "Webhook de entrada"), serialized the way the
gateway serializes them — ``JSON.stringify(row.payload)``, compact. The
signatures were produced by the gateway's **own** ``signPayload``
(``src/utils/signature.ts``, zap-api commit ``d0477d8``) run through
``tsx`` with the secret ``meu-segredo``, so a pass here is a
cross-implementation check, not this SDK agreeing with itself.
"""

from __future__ import annotations

import hashlib
import hmac
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.api.webhooks import WebhookSignatureVerifier
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZAP_INBOUND_EVENT,
    ZAP_WEBHOOK_SIGNATURE_HEADER,
    ZAP_WEBHOOK_SIGNATURE_PREFIX,
    AcceptedResponseStatus,
    ZapInboundMediaType,
    ZapInboundMessage,
    ZapStatusCallback,
    ZapWebhookDelivery,
    ZapWebhookEvent,
    is_forward_transition,
    make_zap_webhook_dependency,
    webhook_verifier,
)

SECRET: str = "meu-segredo"
"""The secret the README registers the webhook with."""

INBOUND_BODY: bytes = (
    '{"event":"message.received","messageId":"ABCD1234",'
    '"from":"5511999999999@s.whatsapp.net","chatKey":"5511999999999",'
    '"pushName":"Fulano","text":"Olá!","mediaType":null,"mediaUrl":null,'
    '"timestamp":"2026-04-21T18:30:00.000Z"}'
).encode()
"""The README's inbound example, as the gateway puts it on the wire."""

INBOUND_SIGNATURE: str = (
    "sha256=6728bcf881a4e6eeeb16e4987d235970d4c406ad8ee365e59e2ea7d68aff0f99"
)
"""``signPayload("meu-segredo", INBOUND_BODY)`` computed by the gateway."""

STATUS_BODY: bytes = (
    b'{"event":"message.delivered","id":"outbound-uuid","consumer":"billing-api",'
    b'"to":"5511999999999","kind":"text","status":"delivered",'
    b'"waMessageId":"WAMID...","error":null,'
    b'"timestamp":"2026-06-27T18:30:00.000Z"}'
)
"""The README's status example, as the gateway puts it on the wire."""

STATUS_SIGNATURE: str = (
    "sha256=2ece7a5f319b5885f563c5bffe9c4f5f91ba2b4e58bc87ae83843daab38b4830"
)
"""``signPayload("meu-segredo", STATUS_BODY)`` computed by the gateway."""


def _sign(body: bytes, secret: str = SECRET) -> str:
    """Sign a body the way the gateway does.

    Args:
        body (bytes): The exact bytes to sign.
        secret (str): The webhook secret.

    Returns:
        str: ``sha256=<hex>``, as the header carries it.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _app(**dependency_kwargs: Any) -> FastAPI:
    """Build an app whose one route is guarded by the dependency.

    Args:
        **dependency_kwargs (Any): Forwarded to
            :func:`make_zap_webhook_dependency`; defaults to the README
            secret.

    Returns:
        FastAPI: An app echoing what the dependency parsed.
    """
    app = FastAPI()
    verify = make_zap_webhook_dependency(**(dependency_kwargs or {"secret": SECRET}))

    @app.post("/zap/webhook")
    async def hook(
        delivery: ZapWebhookDelivery = Depends(verify),
    ) -> dict[str, Any]:
        """Echo the parsed delivery."""
        return {
            "event_name": delivery.event_name,
            "event": delivery.event.value if delivery.event else None,
            "is_enum": isinstance(delivery.event, ZapWebhookEvent),
            "inbound": delivery.inbound.message_id if delivery.inbound else None,
            "status": delivery.status.id if delivery.status else None,
            "keys": sorted(delivery.payload),
            "body_len": len(delivery.body),
        }

    return app


async def _post(
    body: bytes,
    headers: dict[str, str],
    app: FastAPI | None = None,
) -> httpx.Response:
    """POST a body to the guarded route.

    Args:
        body (bytes): The request body.
        headers (dict[str, str]): The request headers.
        app (FastAPI | None): The app; a default one when ``None``.

    Returns:
        httpx.Response: The response.
    """
    transport = httpx.ASGITransport(app=app or _app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/zap/webhook", content=body, headers=headers)


class TestPortedConstants:
    """Each constant is pinned to the gateway source it was ported from."""

    def test_signature_header(self) -> None:
        """``SIGNATURE_HEADER`` in ``src/utils/signature.ts``."""
        assert ZAP_WEBHOOK_SIGNATURE_HEADER == "x-zap-signature"

    def test_signature_prefix(self) -> None:
        """``signPayload`` returns ``sha256=<hex>``."""
        assert ZAP_WEBHOOK_SIGNATURE_PREFIX == "sha256="

    def test_inbound_event(self) -> None:
        """``INBOUND_EVENT`` in ``src/services/webhook.service.ts``."""
        assert ZAP_INBOUND_EVENT == "message.received"
        assert ZapWebhookEvent.MESSAGE_RECEIVED.value == ZAP_INBOUND_EVENT

    def test_event_values(self) -> None:
        """``message.${CallbackStatus}`` plus the inbound event."""
        assert [member.value for member in ZapWebhookEvent] == [
            "message.received",
            "message.sent",
            "message.delivered",
            "message.read",
            "message.failed",
        ]

    def test_media_type_values(self) -> None:
        """``NormalizedMediaType`` in ``src/services/message-normalizer.ts``."""
        assert [member.value for member in ZapInboundMediaType] == [
            "image",
            "video",
            "audio",
            "document",
            "sticker",
        ]


class TestVerifier:
    """``webhook_verifier`` agrees with the gateway's ``signPayload``."""

    def test_accepts_the_gateway_signatures(self) -> None:
        """Both signatures came out of the gateway's own code."""
        verifier = webhook_verifier(SECRET)
        assert verifier.verify(INBOUND_BODY, INBOUND_SIGNATURE)
        assert verifier.verify(STATUS_BODY, STATUS_SIGNATURE)

    def test_is_configured_for_zap(self) -> None:
        """Header, algorithm, encoding and prefix are the gateway's."""
        verifier = webhook_verifier(SECRET)
        assert isinstance(verifier, WebhookSignatureVerifier)
        assert verifier.header_name == ZAP_WEBHOOK_SIGNATURE_HEADER
        assert verifier.algorithm == "sha256"
        assert verifier.encoding == "hex"
        assert verifier.prefix == ZAP_WEBHOOK_SIGNATURE_PREFIX

    def test_compares_in_constant_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The comparison goes through ``hmac.compare_digest``."""
        calls: list[tuple[str, str]] = []
        real = hmac.compare_digest

        def spy(a: str, b: str) -> bool:
            """Record the call and defer to the real comparison."""
            calls.append((a, b))
            return real(a, b)

        monkeypatch.setattr("tempest_fastapi_sdk.api.webhooks.hmac.compare_digest", spy)
        assert webhook_verifier(SECRET).verify(INBOUND_BODY, INBOUND_SIGNATURE)
        assert len(calls) == 1


class TestDependencySignature:
    """The dependency accepts the gateway's signature and nothing else."""

    async def test_valid_signature_passes(self) -> None:
        """The README inbound body, signed by the gateway, is accepted."""
        response = await _post(
            INBOUND_BODY, {ZAP_WEBHOOK_SIGNATURE_HEADER: INBOUND_SIGNATURE}
        )
        assert response.status_code == 200
        assert response.json()["event"] == "message.received"

    async def test_header_name_is_case_insensitive(self) -> None:
        """The README spells it ``X-Zap-Signature``; both reach the route."""
        response = await _post(STATUS_BODY, {"X-Zap-Signature": STATUS_SIGNATURE})
        assert response.status_code == 200

    @pytest.mark.parametrize(
        ("body", "headers"),
        [
            pytest.param(
                INBOUND_BODY.replace(b"Fulano", b"Ciclano"),
                {ZAP_WEBHOOK_SIGNATURE_HEADER: INBOUND_SIGNATURE},
                id="altered-body",
            ),
            pytest.param(
                INBOUND_BODY,
                {ZAP_WEBHOOK_SIGNATURE_HEADER: _sign(INBOUND_BODY, "outro-segredo")},
                id="wrong-secret",
            ),
            pytest.param(INBOUND_BODY, {}, id="missing-header"),
            pytest.param(
                INBOUND_BODY,
                {
                    ZAP_WEBHOOK_SIGNATURE_HEADER: INBOUND_SIGNATURE.removeprefix(
                        "sha256="
                    )
                },
                id="missing-prefix",
            ),
            pytest.param(
                INBOUND_BODY,
                {ZAP_WEBHOOK_SIGNATURE_HEADER: "sha256="},
                id="empty-digest",
            ),
        ],
    )
    async def test_rejected_with_401(
        self, body: bytes, headers: dict[str, str]
    ) -> None:
        """Each tampering answers 401 before the route runs."""
        response = await _post(body, headers)
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({}, id="missing-header"),
            pytest.param(
                {
                    ZAP_WEBHOOK_SIGNATURE_HEADER: INBOUND_SIGNATURE.removeprefix(
                        "sha256="
                    )
                },
                id="missing-prefix",
            ),
            pytest.param(
                {ZAP_WEBHOOK_SIGNATURE_HEADER: _sign(INBOUND_BODY, "x")},
                id="wrong-secret",
            ),
        ],
    )
    async def test_raises_unauthorized_exception(self, headers: dict[str, str]) -> None:
        """The 401 is the SDK's ``UnauthorizedException``, raised by the dependency."""
        from starlette.requests import Request

        dependency = make_zap_webhook_dependency(secret=SECRET)

        async def receive() -> dict[str, Any]:
            """Hand the body over in one message."""
            return {"type": "http.request", "body": INBOUND_BODY, "more_body": False}

        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": "/zap/webhook",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in headers.items()
            ],
        }
        with pytest.raises(UnauthorizedException):
            await dependency(Request(scope, receive))

    async def test_custom_error_message(self) -> None:
        """``error_message`` reaches the raised exception."""
        response = await _post(
            INBOUND_BODY, {}, app=_app(secret=SECRET, error_message="nope")
        )
        assert response.status_code == 401
        assert "nope" in response.text

    async def test_injected_verifier(self) -> None:
        """A verifier passed in replaces the one built from ``secret``."""
        app = _app(verifier=webhook_verifier("rotated"))
        response = await _post(
            INBOUND_BODY,
            {ZAP_WEBHOOK_SIGNATURE_HEADER: _sign(INBOUND_BODY, "rotated")},
            app=app,
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({}, id="neither"),
            pytest.param({"secret": ""}, id="empty-secret"),
            pytest.param(
                {"secret": SECRET, "verifier": webhook_verifier(SECRET)}, id="both"
            ),
        ],
    )
    def test_refuses_to_build_without_exactly_one_key(
        self, kwargs: dict[str, Any]
    ) -> None:
        """A webhook without a secret is delivered unsigned; refuse that shape."""
        with pytest.raises(ValueError):
            make_zap_webhook_dependency(**kwargs)


class TestParsing:
    """Both README payloads parse field by field."""

    async def test_inbound_readme_payload(self) -> None:
        """Every field of the README inbound example."""
        message = ZapInboundMessage.model_validate_json(INBOUND_BODY)
        assert message.event == ZapWebhookEvent.MESSAGE_RECEIVED
        assert message.message_id == "ABCD1234"
        assert message.from_ == "5511999999999@s.whatsapp.net"
        assert message.chat_key == "5511999999999"
        assert message.push_name == "Fulano"
        assert message.text == "Olá!"
        assert message.media_type is None
        assert message.media_url is None
        assert message.timestamp == datetime(2026, 4, 21, 18, 30, tzinfo=UTC)

    async def test_status_readme_payload(self) -> None:
        """Every field of the README status example."""
        callback = ZapStatusCallback.model_validate_json(STATUS_BODY)
        assert callback.event == ZapWebhookEvent.MESSAGE_DELIVERED
        assert callback.id == "outbound-uuid"
        assert callback.consumer == "billing-api"
        assert callback.to == "5511999999999"
        assert callback.kind == "text"
        assert callback.status == AcceptedResponseStatus.DELIVERED
        assert callback.wa_message_id == "WAMID..."
        assert callback.error is None
        assert callback.timestamp == datetime(2026, 6, 27, 18, 30, tzinfo=UTC)

    async def test_serializes_back_to_the_wire_names(self) -> None:
        """``by_alias`` writes the camelCase the gateway sent."""
        message = ZapInboundMessage.model_validate_json(INBOUND_BODY)
        dumped = message.model_dump(by_alias=True)
        assert {
            "messageId",
            "from",
            "chatKey",
            "pushName",
            "mediaType",
            "mediaUrl",
        } <= set(dumped)

    async def test_text_is_not_stripped(self) -> None:
        """What the person typed reaches the service unchanged."""
        message = ZapInboundMessage.model_validate(
            {
                "event": "message.received",
                "messageId": "X",
                "from": "5511999999999@s.whatsapp.net",
                "text": "  indentado\n",
                "timestamp": "2026-04-21T18:30:00.000Z",
            }
        )
        assert message.text == "  indentado\n"

    async def test_null_chat_key_and_lost_media(self) -> None:
        """A group message whose audio download failed still parses."""
        body = (
            b'{"event":"message.received","messageId":"G1",'
            b'"from":"120363000000000000@g.us","chatKey":null,"pushName":null,'
            b'"text":null,"mediaType":"audio","mediaUrl":null,'
            b'"timestamp":"2026-04-21T18:30:00.000Z"}'
        )
        response = await _post(body, {ZAP_WEBHOOK_SIGNATURE_HEADER: _sign(body)})
        assert response.status_code == 200
        assert response.json()["event"] == "message.received"
        assert response.json()["inbound"] == "G1"
        message = ZapInboundMessage.model_validate_json(body)
        assert message.chat_key is None
        assert message.media_type == ZapInboundMediaType.AUDIO
        assert message.media_url is None


class TestDispatch:
    """The delivery is parsed by ``event``, and never 500s on the unknown."""

    async def test_inbound_lands_on_inbound(self) -> None:
        """``message.received`` fills ``inbound`` and nothing else."""
        response = await _post(
            INBOUND_BODY, {ZAP_WEBHOOK_SIGNATURE_HEADER: INBOUND_SIGNATURE}
        )
        payload = response.json()
        assert payload["is_enum"] is True
        assert payload["inbound"] == "ABCD1234"
        assert payload["status"] is None
        assert payload["body_len"] == len(INBOUND_BODY)

    async def test_status_lands_on_status(self) -> None:
        """``message.delivered`` fills ``status`` and nothing else."""
        response = await _post(
            STATUS_BODY, {ZAP_WEBHOOK_SIGNATURE_HEADER: STATUS_SIGNATURE}
        )
        payload = response.json()
        assert payload["event"] == "message.delivered"
        assert payload["status"] == "outbound-uuid"
        assert payload["inbound"] is None

    async def test_unknown_event_keeps_its_name(self) -> None:
        """A gateway release with a new event must not 500 the receiver."""
        body = b'{"event":"message.edited","messageId":"E1"}'
        response = await _post(body, {ZAP_WEBHOOK_SIGNATURE_HEADER: _sign(body)})
        assert response.status_code == 200
        payload = response.json()
        assert payload["event_name"] == "message.edited"
        assert payload["event"] is None
        assert payload["keys"] == ["event", "messageId"]

    async def test_known_event_with_unmodelled_body(self) -> None:
        """A body that does not match its model comes back with ``event=None``."""
        body = b'{"event":"message.received","messageId":"V1"}'
        response = await _post(body, {ZAP_WEBHOOK_SIGNATURE_HEADER: _sign(body)})
        assert response.status_code == 200
        payload = response.json()
        assert payload["event_name"] == "message.received"
        assert payload["event"] is None
        assert payload["inbound"] is None

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(b"not json at all", id="text"),
            pytest.param(b"\xff\xfe\x00", id="not-utf8"),
            pytest.param(b"[1, 2, 3]", id="json-array"),
            pytest.param(b"", id="empty"),
        ],
    )
    async def test_verified_non_json_body_does_not_raise(self, body: bytes) -> None:
        """It verified, so the gateway sent it; ``payload`` stays empty."""
        response = await _post(body, {ZAP_WEBHOOK_SIGNATURE_HEADER: _sign(body)})
        assert response.status_code == 200
        payload = response.json()
        assert payload["event"] is None
        assert payload["event_name"] == ""
        assert payload["keys"] == []
        assert payload["body_len"] == len(body)


class TestForwardTransition:
    """``is_forward_transition`` never lets a late callback regress state."""

    @pytest.mark.parametrize(
        ("current", "new"),
        [
            (None, AcceptedResponseStatus.SENT),
            (None, AcceptedResponseStatus.FAILED),
            (AcceptedResponseStatus.QUEUED, AcceptedResponseStatus.SENDING),
            (AcceptedResponseStatus.SENDING, AcceptedResponseStatus.SENT),
            (AcceptedResponseStatus.SENT, AcceptedResponseStatus.DELIVERED),
            (AcceptedResponseStatus.DELIVERED, AcceptedResponseStatus.READ),
            (AcceptedResponseStatus.SENT, AcceptedResponseStatus.READ),
            (AcceptedResponseStatus.QUEUED, AcceptedResponseStatus.FAILED),
            (AcceptedResponseStatus.SENDING, AcceptedResponseStatus.FAILED),
            (AcceptedResponseStatus.SENT, AcceptedResponseStatus.FAILED),
        ],
    )
    def test_forward(
        self, current: AcceptedResponseStatus | None, new: AcceptedResponseStatus
    ) -> None:
        """Up the ladder, or into ``failed`` before the message arrived."""
        assert is_forward_transition(current, new) is True

    @pytest.mark.parametrize(
        ("current", "new"),
        [
            (AcceptedResponseStatus.READ, AcceptedResponseStatus.DELIVERED),
            (AcceptedResponseStatus.DELIVERED, AcceptedResponseStatus.SENT),
            (AcceptedResponseStatus.SENT, AcceptedResponseStatus.QUEUED),
        ],
    )
    def test_regression(
        self, current: AcceptedResponseStatus, new: AcceptedResponseStatus
    ) -> None:
        """A late ``delivered`` after ``read`` is the case the gateway still sends."""
        assert is_forward_transition(current, new) is False

    @pytest.mark.parametrize("status", list(AcceptedResponseStatus))
    def test_repetition(self, status: AcceptedResponseStatus) -> None:
        """A retried delivery of the same status is not a transition."""
        assert is_forward_transition(status, status) is False

    @pytest.mark.parametrize("new", list(AcceptedResponseStatus))
    def test_failed_is_terminal(self, new: AcceptedResponseStatus) -> None:
        """No receipt resurrects a failed send."""
        assert is_forward_transition(AcceptedResponseStatus.FAILED, new) is False

    @pytest.mark.parametrize(
        "current", [AcceptedResponseStatus.DELIVERED, AcceptedResponseStatus.READ]
    )
    def test_failed_does_not_overwrite_an_arrival(
        self, current: AcceptedResponseStatus
    ) -> None:
        """``delivered``/``read`` already prove the message arrived."""
        assert is_forward_transition(current, AcceptedResponseStatus.FAILED) is False

    def test_accepts_the_stored_string_value(self) -> None:
        """A ``BaseSchema`` field stores the value, so ``str`` must work."""
        callback = ZapStatusCallback.model_validate_json(STATUS_BODY)
        assert isinstance(callback.status, str)
        assert is_forward_transition("sent", callback.status) is True
        assert is_forward_transition("read", callback.status) is False

    def test_rejects_an_unknown_status(self) -> None:
        """A typo is an error, not a silent ``False``."""
        with pytest.raises(ValueError):
            is_forward_transition("sent", "delivred")


class TestImportingIsCheap:
    """The hand-written half keeps the namespace import model-free."""

    def test_importing_zap_leaves_schemas_unloaded(self) -> None:
        """``webhooks`` builds over ``schemas``, so it must resolve lazily too.

        Run in a subprocess because ``sys.modules`` is global and this
        session has already imported both.
        """
        code = (
            "import sys;"
            "import tempest_fastapi_sdk.integrations.messaging.zap as zap;"
            "assert zap is not None;"
            "print(sorted(n for n in sys.modules"
            " if n.endswith(('zap.schemas', 'zap.webhooks'))))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[4],
        )
        assert result.stdout.strip() == "[]"

    def test_first_access_resolves_the_hand_written_name(self) -> None:
        """Reaching a hand-written name loads ``webhooks`` on demand."""
        code = (
            "import sys;"
            "from tempest_fastapi_sdk.integrations.messaging.zap import"
            " make_zap_webhook_dependency;"
            "print(make_zap_webhook_dependency.__module__)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == (
            "tempest_fastapi_sdk.integrations.messaging.zap.webhooks"
        )
