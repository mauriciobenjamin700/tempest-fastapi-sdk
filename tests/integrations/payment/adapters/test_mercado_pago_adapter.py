"""``MercadoPagoPixProvider`` driven over the wire.

The transport is an ``httpx.MockTransport``, so every assertion is on bytes
the adapter put on the wire or bytes it was handed back. The payment bodies
below are **synthetic**: they carry only the keys the adapter reads, named
the way the provider's own SDK names them (``transaction_amount``,
``external_reference``, ``point_of_interaction.transaction_data``). They
test the mapping, not the provider. What the provider really sends is pinned
separately, from the sandbox, in ``test_mercado_pago_sandbox.py``.

The webhook half mounts a route: a status code is a fact about a service,
not about a function, and the defect this adapter is shaped around — a
notification that carries no payment state — only shows when the delivery
goes through the dependency the recipe tells a consumer to use.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import HTTPClient, register_exception_handlers
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixConfirmationOutcome,
    PixEventType,
    PixPayer,
    confirm_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoPixDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_pix_webhook_dependency,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    MercadoPagoEvent,
    MercadoPagoWebhookEvent,
    sign_manifest,
)
from tempest_fastapi_sdk.utils.retry import RetryPolicy

SECRET: str = "webhook-secret"
BUYER: PixPayer = PixPayer(email="buyer@example.com")
PAYMENT_ID: int = 1234567890


def _payment(**overrides: Any) -> dict[str, Any]:
    """Build a synthetic Pix payment body.

    Args:
        **overrides (Any): Keys to replace or add.

    Returns:
        dict[str, Any]: The body.
    """
    body: dict[str, Any] = {
        "id": PAYMENT_ID,
        "status": "pending",
        "status_detail": "pending_waiting_transfer",
        "payment_method_id": "pix",
        "transaction_amount": 19.9,
        "currency_id": "BRL",
        "external_reference": "order-1042",
        "date_of_expiration": "2026-10-10T12:00:00.000-04:00",
        "date_approved": None,
        "point_of_interaction": {
            "type": "CHECKOUT",
            "transaction_data": {
                "qr_code": "00020126580014br.gov.bcb.pix",
                "qr_code_base64": "iVBORw0KGgo=",
                "ticket_url": "https://example.com/ticket",
            },
        },
    }
    body.update(overrides)
    return body


class Recorder:
    """A scripted transport that keeps every request it saw.

    Attributes:
        requests (list[httpx.Request]): Requests in arrival order.
        responses (list[httpx.Response]): Answers, consumed in order; the
            last one repeats.
    """

    def __init__(self, *responses: httpx.Response) -> None:
        """Script the answers.

        Args:
            *responses (httpx.Response): Answers in order.
        """
        self.requests: list[httpx.Request] = []
        self.responses: list[httpx.Response] = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record and answer.

        Args:
            request (httpx.Request): The request the client built.

        Returns:
            httpx.Response: The next scripted answer.
        """
        self.requests.append(request)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def _provider(
    recorder: Recorder,
    *,
    retry_policy: RetryPolicy | None = None,
    **options: Any,
) -> MercadoPagoPixProvider:
    """Build the adapter over a scripted transport.

    Args:
        recorder (Recorder): The transport.
        retry_policy (RetryPolicy | None): Retry policy for the client.
        **options (Any): Forwarded to ``MercadoPagoPixProvider``.

    Returns:
        MercadoPagoPixProvider: The adapter.
    """
    http = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer TEST-fake"},
        transport=httpx.MockTransport(recorder),
        retry_policy=retry_policy,
    )
    return MercadoPagoPixProvider(http, **options)


class TestCreate:
    """What goes on the wire, and what comes back."""

    async def test_the_request_states_reais_and_pix(self) -> None:
        """Cents in the contract, reais on the wire, `pix` as the method."""
        recorder = Recorder(httpx.Response(201, json=_payment()))
        provider = _provider(recorder, notification_url="https://svc/webhooks/mp")

        await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990,
                reference="order-1042",
                description="Pedido 1042",
                payer=PixPayer(
                    name="Maria da Silva",
                    email="maria@example.com",
                    tax_id="123.456.789-09",
                ),
            )
        )

        request = recorder.requests[0]
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.url.path == "/v1/payments"
        assert body["transaction_amount"] == 19.9
        assert b'"transaction_amount":19.9' in request.content.replace(b" ", b"")
        assert body["payment_method_id"] == "pix"
        assert body["external_reference"] == "order-1042"
        assert body["description"] == "Pedido 1042"
        assert body["notification_url"] == "https://svc/webhooks/mp"
        assert body["payer"] == {
            "email": "maria@example.com",
            "first_name": "Maria da Silva",
            "identification": {"number": "12345678909", "type": "CPF"},
        }

    async def test_nothing_is_invented_for_the_payer(self) -> None:
        """Only what the request carries; a 13-digit tax id gets no type."""
        recorder = Recorder(httpx.Response(201, json=_payment()))
        provider = _provider(recorder)

        await provider.create_pix_charge(
            PixChargeRequest(amount_cents=100, reference="a", payer=BUYER)
        )
        await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=100,
                reference="b",
                payer=PixPayer(email="buyer@example.com", tax_id="1234567890123"),
            )
        )

        first = json.loads(recorder.requests[0].content)
        second = json.loads(recorder.requests[1].content)
        assert first["payer"] == {"email": "buyer@example.com"}
        assert "notification_url" not in first
        assert second["payer"]["identification"] == {"number": "1234567890123"}

    @pytest.mark.parametrize(
        "payer",
        [None, PixPayer(name="Maria"), PixPayer(tax_id="12345678909")],
        ids=["no-payer", "name-only", "tax-id-only"],
    )
    async def test_a_payer_without_email_is_refused_before_sending(
        self, payer: PixPayer | None
    ) -> None:
        """Measured: the provider answers 500 `payer_cannot_be_nil` instead."""
        recorder = Recorder(httpx.Response(201, json=_payment()))
        provider = _provider(recorder)

        with pytest.raises(ValueError, match=r"payer\.email"):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=100, reference="a", payer=payer)
            )

        assert recorder.requests == []

    async def test_expiry_is_an_absolute_utc_timestamp(self) -> None:
        """`expires_in` becomes `date_of_expiration`, milliseconds, UTC."""
        recorder = Recorder(httpx.Response(201, json=_payment()))
        provider = _provider(recorder)
        before = datetime.now(UTC)

        await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=100,
                reference="a",
                expires_in=timedelta(minutes=30),
                payer=BUYER,
            )
        )

        sent = json.loads(recorder.requests[0].content)["date_of_expiration"]
        deadline = datetime.fromisoformat(sent)
        assert sent.endswith("+00:00")
        assert len(sent.split(".")[1]) == len("000+00:00")
        assert timedelta(minutes=29) < deadline - before < timedelta(minutes=31)

    async def test_the_response_maps_into_the_contract(self) -> None:
        """QR from the undeclared object, amount back in cents."""
        recorder = Recorder(httpx.Response(201, json=_payment()))
        provider = _provider(recorder)

        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=1990, reference="order-1042", payer=BUYER)
        )

        assert charge.provider == "mercado_pago"
        assert charge.provider_charge_id == str(PAYMENT_ID)
        assert charge.reference == "order-1042"
        assert charge.amount_cents == 1990
        assert charge.status is PaymentStatus.PENDING
        assert charge.provider_status == "pending"
        assert charge.br_code == "00020126580014br.gov.bcb.pix"
        assert charge.qr_code_base64 == "iVBORw0KGgo="
        assert charge.qr_code_image_url is None
        assert charge.end_to_end_id is None
        assert charge.raw["point_of_interaction"]["type"] == "CHECKOUT"

    async def test_each_call_gets_its_own_idempotency_key(self) -> None:
        """Two creates, two keys: the default does not merge orders."""
        recorder = Recorder(httpx.Response(201, json=_payment()))
        provider = _provider(recorder)

        for _ in range(2):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=100, reference="same", payer=BUYER)
            )

        keys = [r.headers["X-Idempotency-Key"] for r in recorder.requests]
        assert keys[0] and keys[1]
        assert keys[0] != keys[1]

    async def test_a_transport_retry_reuses_the_key(self) -> None:
        """A POST retried after a 500 cannot create a second payment."""
        recorder = Recorder(
            httpx.Response(500, json={"message": "boom"}),
            httpx.Response(201, json=_payment()),
        )
        provider = _provider(
            recorder,
            retry_policy=RetryPolicy(max_attempts=2, backoff_initial_seconds=0.0),
        )

        await provider.create_pix_charge(
            PixChargeRequest(amount_cents=100, reference="a", payer=BUYER)
        )

        assert len(recorder.requests) == 2
        keys = {r.headers["X-Idempotency-Key"] for r in recorder.requests}
        assert len(keys) == 1

    async def test_the_key_can_follow_the_reference(self) -> None:
        """Opt-in: the same order collapses onto one payment."""
        recorder = Recorder(httpx.Response(201, json=_payment()))
        provider = _provider(recorder, idempotency_key=lambda r: r.reference)

        await provider.create_pix_charge(
            PixChargeRequest(amount_cents=100, reference="order-9", payer=BUYER)
        )

        assert recorder.requests[0].headers["X-Idempotency-Key"] == "order-9"

    async def test_a_body_without_amount_is_refused(self) -> None:
        """Unreadable amount is an error, never a charge of zero."""
        body = _payment()
        del body["transaction_amount"]
        provider = _provider(Recorder(httpx.Response(201, json=body)))

        with pytest.raises(ValueError, match="transaction_amount"):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=100, reference="a", payer=BUYER)
            )

    async def test_a_fraction_of_a_cent_is_refused(self) -> None:
        """`19.905` reais is not a whole number of cents."""
        provider = _provider(
            Recorder(httpx.Response(201, json=_payment(transaction_amount=19.905)))
        )

        with pytest.raises(ValueError, match="cents"):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=100, reference="a", payer=BUYER)
            )

    async def test_an_error_status_raises(self) -> None:
        """A 400 from the provider is not swallowed into a charge."""
        provider = _provider(Recorder(httpx.Response(400, json={"message": "bad"})))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=100, reference="a", payer=BUYER)
            )


class TestStatus:
    """The nine provider states, and the one that hides in `status_detail`."""

    @pytest.mark.parametrize(
        ("status", "detail", "expected"),
        [
            ("pending", "pending_waiting_transfer", PaymentStatus.PENDING),
            ("approved", "accredited", PaymentStatus.PAID),
            ("authorized", None, PaymentStatus.PENDING),
            ("in_process", None, PaymentStatus.IN_ANALYSIS),
            ("in_mediation", None, PaymentStatus.IN_ANALYSIS),
            ("rejected", None, PaymentStatus.FAILED),
            ("cancelled", "by_collector", PaymentStatus.CANCELLED),
            ("cancelled", "expired", PaymentStatus.EXPIRED),
            ("refunded", None, PaymentStatus.REFUNDED),
            ("charged_back", None, PaymentStatus.CHARGED_BACK),
            ("brand_new_state", None, PaymentStatus.UNKNOWN),
        ],
    )
    async def test_read_maps_the_state(
        self, status: str, detail: str | None, expected: PaymentStatus
    ) -> None:
        """Each state lands where the contract says, unknown stays visible."""
        recorder = Recorder(
            httpx.Response(200, json=_payment(status=status, status_detail=detail))
        )
        provider = _provider(recorder)

        charge = await provider.get_pix_charge(str(PAYMENT_ID))

        assert recorder.requests[0].method == "GET"
        assert recorder.requests[0].url.path == f"/v1/payments/{PAYMENT_ID}"
        assert charge.status is expected
        assert charge.provider_status == status

    async def test_paid_at_comes_from_date_approved(self) -> None:
        """Settlement time is the provider's approval time."""
        provider = _provider(
            Recorder(
                httpx.Response(
                    200,
                    json=_payment(
                        status="approved",
                        date_approved="2026-10-09T10:00:00.000-04:00",
                    ),
                )
            )
        )

        charge = await provider.get_pix_charge(str(PAYMENT_ID))

        assert charge.paid_at == datetime.fromisoformat("2026-10-09T10:00:00-04:00")


class TestCancel:
    """Cancellation goes through the update route, not `/cancellations`."""

    async def test_cancel_puts_the_status_on_the_payment(self) -> None:
        """`PUT /v1/payments/{id}` with `status: cancelled`."""
        recorder = Recorder(
            httpx.Response(
                200, json=_payment(status="cancelled", status_detail="by_collector")
            )
        )
        provider = _provider(recorder)

        charge = await provider.cancel_pix_charge(str(PAYMENT_ID))

        request = recorder.requests[0]
        assert request.method == "PUT"
        assert request.url.path == f"/v1/payments/{PAYMENT_ID}"
        assert json.loads(request.content) == {"status": "cancelled"}
        assert charge.status is PaymentStatus.CANCELLED
        assert charge.amount_cents == 1990


def _notification(
    *, topic: str = "payment", action: str = "payment.updated"
) -> MercadoPagoWebhookEvent:
    """Build a verified notification by hand.

    Args:
        topic (str): The delivery's topic.
        action (str): The body's ``action``.

    Returns:
        MercadoPagoWebhookEvent: The notification.
    """
    return MercadoPagoWebhookEvent(
        topic=topic,
        event=(
            MercadoPagoEvent.from_value(topic)
            if MercadoPagoEvent.has_value(topic)
            else MercadoPagoEvent.UNKNOWN
        ),
        data_id=str(PAYMENT_ID),
        payload={"action": action, "type": topic, "data": {"id": str(PAYMENT_ID)}},
    )


class TestParseWebhook:
    """The event type comes from the re-read payment, never from the body."""

    def test_the_bare_notification_is_refused(self) -> None:
        """It has no state; `UNKNOWN` would silently never settle."""
        provider = _provider(Recorder(httpx.Response(200, json=_payment())))

        with pytest.raises(TypeError, match="make_mercado_pago_pix_webhook"):
            provider.parse_webhook(_notification())

    def test_anything_else_is_refused(self) -> None:
        """A dict is not a verified delivery."""
        provider = _provider(Recorder(httpx.Response(200, json=_payment())))

        with pytest.raises(TypeError, match="MercadoPagoPixDelivery"):
            provider.parse_webhook({"data": {"id": "1"}})

    async def test_an_approved_payment_is_charge_paid(self) -> None:
        """`payment.updated` says nothing; the re-read says paid."""
        provider = _provider(
            Recorder(httpx.Response(200, json=_payment(status="approved")))
        )

        delivery = await provider.read_delivery(_notification())
        event = provider.parse_webhook(delivery)

        assert event.type is PixEventType.CHARGE_PAID
        assert event.provider_event_name == "payment.updated"
        assert event.charge is not None
        assert event.charge.reference == "order-1042"

    async def test_a_pending_payment_on_created_is_charge_created(self) -> None:
        """The one case where the action decides the type."""
        provider = _provider(Recorder(httpx.Response(200, json=_payment())))

        delivery = await provider.read_delivery(_notification(action="payment.created"))

        assert provider.parse_webhook(delivery).type is PixEventType.CHARGE_CREATED

    async def test_an_expired_payment_is_charge_expired(self) -> None:
        """`cancelled` + `expired` is an expiry, not a cancellation."""
        provider = _provider(
            Recorder(
                httpx.Response(
                    200, json=_payment(status="cancelled", status_detail="expired")
                )
            )
        )

        delivery = await provider.read_delivery(_notification())

        assert provider.parse_webhook(delivery).type is PixEventType.CHARGE_EXPIRED

    async def test_another_topic_is_not_fetched(self) -> None:
        """A merchant order is not a payment; no read, no charge."""
        recorder = Recorder(httpx.Response(200, json=_payment()))
        provider = _provider(recorder)

        delivery = await provider.read_delivery(_notification(topic="merchant_order"))
        event = provider.parse_webhook(delivery)

        assert recorder.requests == []
        assert delivery.charge is None
        assert event.type is PixEventType.UNKNOWN
        assert event.charge is None

    def test_a_delivery_built_by_hand_still_parses(self) -> None:
        """The dataclass is public so a test can build one."""
        provider = _provider(Recorder(httpx.Response(200, json=_payment())))

        event = provider.parse_webhook(
            MercadoPagoPixDelivery(notification=_notification(topic="unknown_topic"))
        )

        assert event.type is PixEventType.UNKNOWN


def _signed_headers(data_id: str, request_id: str = "req-1") -> dict[str, str]:
    """Sign a delivery the way Mercado Pago does.

    Args:
        data_id (str): The ``data.id`` query value.
        request_id (str): The ``x-request-id`` header.

    Returns:
        dict[str, str]: ``x-signature`` and ``x-request-id``.
    """
    ts = str(int(time.time()))
    digest = sign_manifest(
        secret=SECRET, data_id=data_id, request_id=request_id, timestamp=ts
    )
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": request_id}


def _app(provider: MercadoPagoPixProvider) -> FastAPI:
    """Mount the webhook route the recipe builds.

    Args:
        provider (MercadoPagoPixProvider): The adapter.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    delivery_dependency = make_mercado_pago_pix_webhook_dependency(SECRET, provider)

    @app.post("/webhooks/mp")
    async def receive(
        delivery: MercadoPagoPixDelivery = Depends(delivery_dependency),
    ) -> dict[str, Any]:
        """Settle the way the recipe's service does.

        Args:
            delivery (MercadoPagoPixDelivery): The re-read delivery.

        Returns:
            dict[str, Any]: What the event decided.
        """
        event = provider.parse_webhook(delivery)
        if event.type is not PixEventType.CHARGE_PAID or event.charge is None:
            return {"settled": None}
        confirmation = await confirm_pix_payment(
            provider,
            str(PAYMENT_ID),
            reference="order-1042",
            amount_cents=1990,
        )
        return {"settled": confirmation.outcome.value}

    return app


class TestWebhookRoute:
    """The dependency verifies, re-reads, and lets the contract settle."""

    def test_a_signed_paid_delivery_settles(self) -> None:
        """Signature, re-read, `CHARGE_PAID`, confirmation: `paid`."""
        recorder = Recorder(httpx.Response(200, json=_payment(status="approved")))
        app = _app(_provider(recorder))

        with TestClient(app) as client:
            response = client.post(
                f"/webhooks/mp?data.id={PAYMENT_ID}&type=payment",
                headers=_signed_headers(str(PAYMENT_ID)),
                json={"action": "payment.updated", "type": "payment"},
            )

        assert response.status_code == 200
        assert response.json() == {"settled": PixConfirmationOutcome.PAID.value}
        assert [r.url.path for r in recorder.requests] == [
            f"/v1/payments/{PAYMENT_ID}",
            f"/v1/payments/{PAYMENT_ID}",
        ]

    def test_the_unsigned_body_does_not_decide(self) -> None:
        """A body claiming `approved` changes nothing; the API said pending."""
        recorder = Recorder(httpx.Response(200, json=_payment(status="pending")))
        app = _app(_provider(recorder))

        with TestClient(app) as client:
            response = client.post(
                f"/webhooks/mp?data.id={PAYMENT_ID}&type=payment",
                headers=_signed_headers(str(PAYMENT_ID)),
                json={"action": "payment.updated", "status": "approved"},
            )

        assert response.json() == {"settled": None}

    def test_a_bad_signature_is_401_before_any_read(self) -> None:
        """No signature, no re-read."""
        recorder = Recorder(httpx.Response(200, json=_payment(status="approved")))
        app = _app(_provider(recorder))

        with TestClient(app) as client:
            response = client.post(
                f"/webhooks/mp?data.id={PAYMENT_ID}&type=payment",
                headers={"x-signature": "ts=1,v1=00", "x-request-id": "req-1"},
                json={"type": "payment"},
            )

        assert response.status_code == 401
        assert recorder.requests == []

    def test_a_failed_re_read_is_not_a_2xx(self) -> None:
        """The provider retries what the route did not acknowledge."""
        recorder = Recorder(httpx.Response(503, json={"message": "down"}))
        app = _app(_provider(recorder, retry_policy=RetryPolicy(max_attempts=1)))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/webhooks/mp?data.id={PAYMENT_ID}&type=payment",
                headers=_signed_headers(str(PAYMENT_ID)),
                json={"type": "payment"},
            )

        assert response.status_code >= 500


class TestConfirmation:
    """`confirm_pix_payment` works unchanged over this adapter."""

    async def test_a_short_payment_is_amount_mismatch(self) -> None:
        """Paid 19.00 for an order of 19.90 is not released."""
        provider = _provider(
            Recorder(
                httpx.Response(
                    200, json=_payment(status="approved", transaction_amount=19.0)
                )
            )
        )

        confirmation = await confirm_pix_payment(
            provider, str(PAYMENT_ID), reference="order-1042", amount_cents=1990
        )

        assert confirmation.outcome is PixConfirmationOutcome.AMOUNT_MISMATCH
