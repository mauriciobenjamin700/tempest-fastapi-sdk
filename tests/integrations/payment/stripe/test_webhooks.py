"""Webhook verification: the signed payload, the window, and rotation."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.api.handlers import register_exception_handlers
from tempest_fastapi_sdk.integrations.payment.stripe import (
    STRIPE_SIGNATURE_HEADER,
    StripeEvent,
    StripeWebhookEvent,
    make_stripe_webhook_dependency,
    parse_event,
    parse_signature_header,
    sign_payload,
    verify_signature,
)

SECRET: str = "whsec_test_secret"
NOW: int = 1_770_000_000
BODY: bytes = json.dumps(
    {
        "id": "evt_123",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_123", "amount": 1999, "currency": "brl"}},
    }
).encode()


class TestHeaderParsing:
    def test_splits_timestamp_and_signature(self) -> None:
        """The header is a comma-separated key=value list."""
        timestamp, signatures = parse_signature_header("t=123,v1=abc")

        assert timestamp == 123
        assert signatures == ("abc",)

    def test_keeps_every_signature_during_rotation(self) -> None:
        """Stripe sends one v1 per active secret while a rotation is open."""
        _, signatures = parse_signature_header("t=123,v1=abc,v1=def")

        assert signatures == ("abc", "def")

    def test_ignores_schemes_it_does_not_know(self) -> None:
        """``v0`` appears on test-mode deliveries and is not the one to check."""
        timestamp, signatures = parse_signature_header("t=123,v0=zzz,v1=abc")

        assert timestamp == 123
        assert signatures == ("abc",)

    def test_unparseable_timestamp_yields_none(self) -> None:
        """A malformed header must not raise on the way to being rejected."""
        timestamp, _ = parse_signature_header("t=not-a-number,v1=abc")

        assert timestamp is None


class TestVerification:
    def test_accepts_a_genuine_delivery(self) -> None:
        """The happy path, with time pinned."""
        header = sign_payload(BODY, SECRET, timestamp=NOW)

        assert verify_signature(BODY, header, SECRET, now=NOW) is True

    def test_signs_timestamp_dot_body_not_body(self) -> None:
        """The single most common hand-rolled mistake, pinned.

        A verifier that hashed the body alone would accept this header;
        this one must reject it.
        """
        body_only = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        header = f"t={NOW},v1={body_only}"

        assert verify_signature(BODY, header, SECRET, now=NOW) is False

    def test_rejects_a_tampered_body(self) -> None:
        """One byte changed and the signature no longer matches."""
        header = sign_payload(BODY, SECRET, timestamp=NOW)

        assert verify_signature(BODY + b" ", header, SECRET, now=NOW) is False

    def test_rejects_another_secret(self) -> None:
        """A delivery for a different endpoint is not this endpoint's."""
        header = sign_payload(BODY, "whsec_other", timestamp=NOW)

        assert verify_signature(BODY, header, SECRET, now=NOW) is False

    def test_rejects_a_stale_delivery(self) -> None:
        """Replay protection: a captured delivery expires."""
        header = sign_payload(BODY, SECRET, timestamp=NOW - 3600)

        assert verify_signature(BODY, header, SECRET, now=NOW) is False

    def test_rejects_a_future_delivery(self) -> None:
        """The window is symmetric, so a forged future timestamp is no help."""
        header = sign_payload(BODY, SECRET, timestamp=NOW + 3600)

        assert verify_signature(BODY, header, SECRET, now=NOW) is False

    def test_accepts_inside_the_window(self) -> None:
        """Ordinary clock drift does not drop deliveries."""
        header = sign_payload(BODY, SECRET, timestamp=NOW - 120)

        assert verify_signature(BODY, header, SECRET, now=NOW) is True

    def test_zero_tolerance_disables_the_window(self) -> None:
        """An escape hatch for replaying a fixture, and it is documented as one."""
        header = sign_payload(BODY, SECRET, timestamp=NOW - 86_400)

        assert (
            verify_signature(BODY, header, SECRET, tolerance_seconds=0, now=NOW) is True
        )

    def test_accepts_any_signature_during_rotation(self) -> None:
        """The new secret's signature validates while the old one is still live."""
        old = sign_payload(BODY, "whsec_old", timestamp=NOW).split("v1=")[1]
        header = f"{sign_payload(BODY, SECRET, timestamp=NOW)},v1={old}"

        assert verify_signature(BODY, header, SECRET, now=NOW) is True

    def test_missing_signature_is_rejected(self) -> None:
        """A header with a timestamp and nothing else proves nothing."""
        assert verify_signature(BODY, f"t={NOW}", SECRET, now=NOW) is False


class TestParsing:
    def test_extracts_the_typed_event(self) -> None:
        """The type resolves to an enum member, not a bare string."""
        event = parse_event(BODY)

        assert event.event is StripeEvent.PAYMENT_INTENT_SUCCEEDED
        assert event.event_id == "evt_123"

    def test_exposes_the_data_object(self) -> None:
        """Handlers read ``data.object``, so it is lifted out."""
        event = parse_event(BODY)

        assert event.data_object["id"] == "pi_123"

    def test_unknown_type_does_not_raise(self) -> None:
        """Stripe adds event types; a service must not 500 on the new one."""
        body = json.dumps({"id": "evt_9", "type": "widget.frobnicated"}).encode()

        event = parse_event(body)

        assert event.event is None
        assert event.event_type == "widget.frobnicated"

    def test_non_json_body_yields_an_empty_event(self) -> None:
        """It verified, so it came from Stripe — discarding it is worse."""
        event = parse_event(b"not json")

        assert isinstance(event, StripeWebhookEvent)
        assert event.payload == {}
        assert event.body == b"not json"


class TestDependency:
    def _app(self) -> FastAPI:
        """Build an app whose single route requires a verified delivery.

        Returns:
            FastAPI: The application under test.
        """
        app = FastAPI()
        register_exception_handlers(app)
        dependency = make_stripe_webhook_dependency(SECRET, tolerance_seconds=0)

        @app.post("/webhooks/stripe")
        async def receive(
            event: StripeWebhookEvent = Depends(dependency),
        ) -> dict[str, Any]:
            """Echo what the dependency parsed.

            Args:
                event (StripeWebhookEvent): The verified delivery.

            Returns:
                dict[str, Any]: The event id and type.
            """
            return {"id": event.event_id, "type": event.event_type}

        return app

    def test_valid_delivery_reaches_the_handler(self) -> None:
        """End to end through FastAPI."""
        client = TestClient(self._app())
        header = sign_payload(BODY, SECRET, timestamp=NOW)

        response = client.post(
            "/webhooks/stripe", content=BODY, headers={STRIPE_SIGNATURE_HEADER: header}
        )

        assert response.status_code == 200
        assert response.json() == {"id": "evt_123", "type": "payment_intent.succeeded"}

    def test_missing_header_is_401(self) -> None:
        """An unsigned POST to a webhook route is not authenticated."""
        client = TestClient(self._app())

        response = client.post("/webhooks/stripe", content=BODY)

        assert response.status_code == 401

    def test_wrong_secret_is_401(self) -> None:
        """A signature from another endpoint does not open this one."""
        client = TestClient(self._app())
        header = sign_payload(BODY, "whsec_other", timestamp=NOW)

        response = client.post(
            "/webhooks/stripe", content=BODY, headers={STRIPE_SIGNATURE_HEADER: header}
        )

        assert response.status_code == 401


def test_signature_is_hex_of_sha256() -> None:
    """The helper produces what Stripe's own docs describe.

    Computed here independently of the SDK's verifier so the test does not
    prove that the code agrees with itself.
    """
    expected = hmac.new(
        SECRET.encode(), f"{NOW}.".encode() + BODY, hashlib.sha256
    ).hexdigest()

    assert sign_payload(BODY, SECRET, timestamp=NOW) == f"t={NOW},v1={expected}"


@pytest.mark.parametrize("tolerance", [1, 300, 600])
def test_window_edges_are_inclusive(tolerance: int) -> None:
    """A delivery exactly at the edge is accepted, one second past is not.

    Args:
        tolerance (int): The window under test.
    """
    at_edge = sign_payload(BODY, SECRET, timestamp=NOW - tolerance)
    past_edge = sign_payload(BODY, SECRET, timestamp=NOW - tolerance - 1)

    assert (
        verify_signature(BODY, at_edge, SECRET, tolerance_seconds=tolerance, now=NOW)
        is True
    )
    assert (
        verify_signature(BODY, past_edge, SECRET, tolerance_seconds=tolerance, now=NOW)
        is False
    )
