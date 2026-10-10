"""The Mercado Pago adapters, driven over the wire on bodies the sandbox sent.

Every response here is one Mercado Pago returned to the Orders API on
2026-10-09, redacted into ``fixtures/mercado_pago_orders/`` — ids, tokens,
the QR, URLs and account ids replaced by stable fakes, every key, type and
state kept (``vendor/mercadopago-evidence.md`` section 9). The transport is
an ``httpx.MockTransport``, so what is asserted is the request the adapter
put on the wire and how it read what came back.

The webhook half mounts a route: the defect this design avoids — a
notification that carries no state, settled on as if it did — only shows
through the dependency a consumer mounts.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import HTTPClient, register_exception_handlers
from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PaymentStatus,
    PixChargeRequest,
    PixConfirmationOutcome,
    PixEventType,
    PixPayer,
    confirm_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoCardProvider,
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    MercadoPagoEvent,
    MercadoPagoWebhookEvent,
    sign_manifest,
)
from tempest_fastapi_sdk.utils.retry import RetryPolicy

FIXTURES: Path = Path(__file__).parent / "fixtures" / "mercado_pago_orders"
SECRET: str = "webhook-secret"
BUYER: PixPayer = PixPayer(email="buyer@example.com")


def fixture(name: str) -> tuple[int, Any]:
    """Load one redacted sandbox response.

    Args:
        name (str): The fixture's stem.

    Returns:
        tuple[int, Any]: The HTTP status and the body.
    """
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return int(data["status"]), data["body"]


def response(name: str) -> httpx.Response:
    """Build the response the sandbox gave.

    Args:
        name (str): The fixture's stem.

    Returns:
        httpx.Response: Same status, same body.
    """
    status, body = fixture(name)
    return httpx.Response(status, json=body)


class Recorder:
    """A scripted transport that keeps every request.

    Attributes:
        requests (list[httpx.Request]): Requests in arrival order.
        responses (list[httpx.Response]): Answers in order; the last repeats.
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
            request (httpx.Request): The request.

        Returns:
            httpx.Response: The next scripted answer.
        """
        self.requests.append(request)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def _http(recorder: Recorder, retry_policy: RetryPolicy | None = None) -> HTTPClient:
    """Build a client over the scripted transport.

    Args:
        recorder (Recorder): The transport.
        retry_policy (RetryPolicy | None): Retry policy.

    Returns:
        HTTPClient: The client.
    """
    return HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer APP_USR-fake"},
        transport=httpx.MockTransport(recorder),
        retry_policy=retry_policy,
    )


def _body(request: httpx.Request) -> Any:
    """Decode a request body.

    Args:
        request (httpx.Request): The request.

    Returns:
        Any: The JSON body.
    """
    return json.loads(request.content)


def _card(**overrides: Any) -> CardChargeRequest:
    """Build a card request.

    Args:
        **overrides (Any): Fields to replace.

    Returns:
        CardChargeRequest: A R$ 100,00 Visa charge.
    """
    fields: dict[str, Any] = {
        "amount_cents": 10000,
        "reference": "r",
        "card_token": "tok_123",
        "payment_method_id": "visa",
        "payer": BUYER,
    }
    fields.update(overrides)
    return CardChargeRequest(**fields)


class TestPix:
    """Pix through `/v1/orders`."""

    async def test_the_request_is_an_order_with_one_pix_payment(self) -> None:
        """Decimal strings, `pix`/`bank_transfer`, expiry in seconds."""
        recorder = Recorder(response("pix_create"))
        provider = MercadoPagoPixProvider(_http(recorder))

        await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990,
                reference="tempest-orders-pix",
                description="Pedido 1",
                expires_in=timedelta(minutes=30),
                payer=PixPayer(
                    email="buyer@example.com",
                    name="Maria da Silva",
                    tax_id="123.456.789-09",
                ),
            )
        )

        request = recorder.requests[0]
        body = _body(request)
        assert request.method == "POST"
        assert request.url.path == "/v1/orders"
        assert request.headers["X-Idempotency-Key"]
        assert body["total_amount"] == "19.90"
        assert body["external_reference"] == "tempest-orders-pix"
        assert body["description"] == "Pedido 1"
        assert body["payer"] == {
            "email": "buyer@example.com",
            "first_name": "Maria da Silva",
            "identification": {"number": "12345678909", "type": "CPF"},
        }
        assert body["transactions"]["payments"][0] == {
            "amount": "19.90",
            "expiration_time": "PT1800S",
            "payment_method": {"id": "pix", "type": "bank_transfer"},
        }

    async def test_the_created_order_maps_into_a_pending_charge(self) -> None:
        """QR from the payment method, order id as the charge id."""
        provider = MercadoPagoPixProvider(_http(Recorder(response("pix_create"))))

        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=1990, reference="x", payer=BUYER)
        )

        _, body = fixture("pix_create")
        assert charge.provider == "mercado_pago"
        assert charge.provider_charge_id == body["id"]
        assert charge.reference == "tempest-orders-pix"
        assert charge.amount_cents == 1990
        assert charge.status is PaymentStatus.PENDING
        assert charge.provider_status == "action_required"
        assert charge.br_code is not None
        assert charge.br_code.startswith("000201")
        assert charge.qr_code_base64
        assert charge.qr_code_image_url is None
        assert charge.expires_at is not None
        assert charge.raw["transactions"]["payments"][0]["id"].startswith("PAY")

    async def test_read_and_cancel(self) -> None:
        """`GET` and `POST …/cancel` on the order id."""
        recorder = Recorder(response("pix_get"), response("pix_cancel"))
        provider = MercadoPagoPixProvider(_http(recorder))
        _, created = fixture("pix_create")

        read = await provider.get_pix_charge(created["id"])
        cancelled = await provider.cancel_pix_charge(created["id"])

        assert read.status is PaymentStatus.PENDING
        assert cancelled.status is PaymentStatus.CANCELLED
        assert recorder.requests[0].method == "GET"
        assert recorder.requests[0].url.path == f"/v1/orders/{created['id']}"
        assert recorder.requests[1].method == "POST"
        assert recorder.requests[1].url.path == f"/v1/orders/{created['id']}/cancel"
        assert recorder.requests[1].headers["X-Idempotency-Key"]

    async def test_a_transport_retry_reuses_the_idempotency_key(self) -> None:
        """A create retried after a 500 carries the same key."""
        recorder = Recorder(
            httpx.Response(500, json={"message": "boom"}), response("pix_create")
        )
        provider = MercadoPagoPixProvider(
            _http(recorder, RetryPolicy(max_attempts=2, backoff_initial_seconds=0.0))
        )

        await provider.create_pix_charge(
            PixChargeRequest(amount_cents=1990, reference="x", payer=BUYER)
        )

        keys = {r.headers["X-Idempotency-Key"] for r in recorder.requests}
        assert len(recorder.requests) == 2
        assert len(keys) == 1

    async def test_two_creates_get_two_keys_unless_told_otherwise(self) -> None:
        """Default: one key per call. Opt-in: the key follows the reference."""
        recorder = Recorder(response("pix_create"))
        default = MercadoPagoPixProvider(_http(recorder))
        by_reference = MercadoPagoPixProvider(
            _http(recorder), idempotency_key=lambda reference: reference
        )
        request = PixChargeRequest(amount_cents=1990, reference="order-9", payer=BUYER)

        await default.create_pix_charge(request)
        await default.create_pix_charge(request)
        await by_reference.create_pix_charge(request)

        keys = [r.headers["X-Idempotency-Key"] for r in recorder.requests]
        assert keys[0] != keys[1]
        assert keys[2] == "order-9"

    @pytest.mark.parametrize(
        "patch",
        [
            {"transactions": {"payments": [{"date_of_expiration": "garbage"}]}},
            {"transactions": "not-an-object"},
            {"currency": 42},
        ],
        ids=["expiry", "transactions", "currency"],
    )
    async def test_a_malformed_field_it_does_not_need_is_not_fatal(
        self, patch: dict[str, Any]
    ) -> None:
        """The order exists once POST answered; raising would invite a retry."""
        _, body = fixture("pix_create")
        provider = MercadoPagoPixProvider(
            _http(Recorder(httpx.Response(201, json={**body, **patch})))
        )

        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=1990, reference="x", payer=BUYER)
        )

        assert charge.amount_cents == 1990

    async def test_an_unreadable_amount_is_refused(self) -> None:
        """Never a charge of zero."""
        _, body = fixture("pix_create")
        provider = MercadoPagoPixProvider(
            _http(Recorder(httpx.Response(201, json={**body, "total_amount": None})))
        )

        with pytest.raises(ValueError, match="total_amount"):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=1990, reference="x", payer=BUYER)
            )

    async def test_an_order_without_payer_surfaces_the_provider_400(self) -> None:
        """Measured: `'$.payer' - minimum 1 properties allowed`."""
        recorder = Recorder(
            httpx.Response(
                400,
                json={"errors": [{"code": "required_properties"}]},
            )
        )
        provider = MercadoPagoPixProvider(_http(recorder))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=1990, reference="x")
            )

        assert _body(recorder.requests[0])["payer"] == {}


class TestCard:
    """Cards through `/v1/orders`."""

    async def test_the_request_carries_the_token_not_the_card(self) -> None:
        """Brand, token, installments; `capture_mode` only when deferring."""
        recorder = Recorder(response("card_approved"))
        provider = MercadoPagoCardProvider(_http(recorder))

        await provider.create_card_charge(_card())

        body = _body(recorder.requests[0])
        assert body["total_amount"] == "100.00"
        assert "capture_mode" not in body
        assert body["transactions"]["payments"][0]["payment_method"] == {
            "id": "visa",
            "type": "credit_card",
            "token": "tok_123",
            "installments": 1,
        }

    async def test_an_approved_card_is_paid(self) -> None:
        """`processed` / `accredited`."""
        provider = MercadoPagoCardProvider(_http(Recorder(response("card_approved"))))

        charge = await provider.create_card_charge(_card())

        assert charge.status is PaymentStatus.PAID
        assert charge.status_detail == "accredited"
        assert charge.amount_cents == 10000
        assert charge.refunded_cents == 0
        assert charge.payment_method_id == "visa"
        assert charge.installments == 1

    async def test_a_decline_is_a_402_returned_not_raised(self) -> None:
        """The order is in `data`; the reason is the payment's detail."""
        provider = MercadoPagoCardProvider(_http(Recorder(response("card_rejected"))))

        charge = await provider.create_card_charge(_card())

        assert fixture("card_rejected")[0] == 402
        assert charge.status is PaymentStatus.FAILED
        assert charge.provider_status == "failed"
        assert charge.status_detail == "rejected_by_issuer"
        assert charge.reference == "tempest-orders-card-rejected"

    async def test_authorize_then_capture(self) -> None:
        """`capture=False` is `manual`; the order waits, then settles."""
        _, manual = fixture("card_manual")
        _, capture = fixture("card_capture")
        read_back = {
            **manual,
            "status": capture["status"],
            "status_detail": capture["status_detail"],
            "transactions": capture["transactions"],
        }
        recorder = Recorder(
            response("card_manual"),
            response("card_capture"),
            httpx.Response(200, json=read_back),
        )
        provider = MercadoPagoCardProvider(_http(recorder))

        authorized = await provider.create_card_charge(_card(capture=False))
        captured = await provider.capture_card_charge(authorized.provider_charge_id)

        assert "total_amount" not in capture
        assert _body(recorder.requests[0])["capture_mode"] == "manual"
        assert authorized.status is PaymentStatus.AUTHORIZED
        assert authorized.status_detail == "waiting_capture"
        assert recorder.requests[1].url.path.endswith("/capture")
        assert recorder.requests[2].method == "GET"
        assert captured.status is PaymentStatus.PAID
        assert captured.amount_cents == 10000

    async def test_cancel_an_authorization(self) -> None:
        """`POST …/cancel` releases the hold."""
        provider = MercadoPagoCardProvider(
            _http(Recorder(response("card_cancel_auth")))
        )

        charge = await provider.cancel_card_charge("ORD1")

        assert charge.status is PaymentStatus.CANCELLED

    async def test_a_transient_409_is_retried_with_the_same_key(self) -> None:
        """Measured 3/10 on an immediate cancel; the retry succeeded."""
        transient = httpx.Response(
            409,
            json={
                "errors": [
                    {
                        "code": "processor_communication_error",
                        "message": "The operation could not be completed. "
                        "Try again shortly.",
                    }
                ]
            },
        )
        recorder = Recorder(transient, response("card_cancel_auth"))
        provider = MercadoPagoCardProvider(
            _http(recorder), action_retry_delays=(0.0, 0.0)
        )

        charge = await provider.cancel_card_charge("ORD1")

        keys = {r.headers["X-Idempotency-Key"] for r in recorder.requests}
        assert charge.status is PaymentStatus.CANCELLED
        assert len(recorder.requests) == 2
        assert len(keys) == 1

    async def test_another_409_is_a_real_conflict(self) -> None:
        """Only the processor code is retried."""
        recorder = Recorder(
            httpx.Response(409, json={"errors": [{"code": "invalid_status"}]}),
            response("card_cancel_auth"),
        )
        provider = MercadoPagoCardProvider(
            _http(recorder), action_retry_delays=(0.0, 0.0)
        )

        with pytest.raises(httpx.HTTPStatusError):
            await provider.cancel_card_charge("ORD1")

        assert len(recorder.requests) == 1

    async def test_the_retry_gives_up_after_the_last_delay(self) -> None:
        """Bounded: delays plus the first attempt, then the 409 surfaces."""
        recorder = Recorder(
            httpx.Response(
                409, json={"errors": [{"code": "processor_communication_error"}]}
            )
        )
        provider = MercadoPagoCardProvider(
            _http(recorder), action_retry_delays=(0.0, 0.0)
        )

        with pytest.raises(httpx.HTTPStatusError):
            await provider.capture_card_charge("ORD1")

        assert len(recorder.requests) == 3

    async def test_an_immediate_refund_422_is_retried(self) -> None:
        """Measured 7/10 right after approval; all refunded within ~5 s."""
        recorder = Recorder(
            httpx.Response(422, json={"errors": [{"code": "unprocessable_entity"}]}),
            response("card_refund_rest"),
            response("card_get_after_refund"),
        )
        provider = MercadoPagoCardProvider(
            _http(recorder), action_retry_delays=(0.0, 0.0)
        )

        charge = await provider.refund_card_charge("ORD1")

        assert charge.status is PaymentStatus.REFUNDED
        assert [r.method for r in recorder.requests] == ["POST", "POST", "GET"]

    async def test_a_422_on_cancel_is_not_retried(self) -> None:
        """The refund-only allowance does not leak into other actions."""
        recorder = Recorder(
            httpx.Response(422, json={"errors": [{"code": "unprocessable_entity"}]}),
            response("card_cancel_auth"),
        )
        provider = MercadoPagoCardProvider(
            _http(recorder), action_retry_delays=(0.0, 0.0)
        )

        with pytest.raises(httpx.HTTPStatusError):
            await provider.cancel_card_charge("ORD1")

        assert len(recorder.requests) == 1

    async def test_partial_refund_addresses_the_payment(self) -> None:
        """Read, refund the `PAY…` id with an amount, read back."""
        _, approved = fixture("card_approved")
        _, refunded = fixture("card_get_after_refund")
        partial_read = {
            **refunded,
            "status": "processed",
            "status_detail": "partially_refunded",
            "transactions": {
                "payments": approved["transactions"]["payments"],
                "refunds": refunded["transactions"]["refunds"][:1],
            },
        }
        recorder = Recorder(
            httpx.Response(200, json=approved),
            response("card_refund_partial"),
            httpx.Response(200, json=partial_read),
        )
        provider = MercadoPagoCardProvider(_http(recorder))

        charge = await provider.refund_card_charge(approved["id"], amount_cents=3000)

        payment_id = approved["transactions"]["payments"][0]["id"]
        refund = recorder.requests[1]
        assert refund.url.path == f"/v1/orders/{approved['id']}/refund"
        assert _body(refund) == {
            "transactions": [{"id": payment_id, "amount": "30.00"}]
        }
        assert charge.status is PaymentStatus.PAID
        assert charge.refunded_cents == 3000

    async def test_full_refund_posts_no_body(self) -> None:
        """No amount: refund what is left, then read back."""
        recorder = Recorder(
            response("card_refund_rest"), response("card_get_after_refund")
        )
        provider = MercadoPagoCardProvider(_http(recorder))

        charge = await provider.refund_card_charge("ORD1")

        assert recorder.requests[0].content in (b"", b"null")
        assert charge.status is PaymentStatus.REFUNDED
        assert charge.refunded_cents == 10000


def _notification(
    data_id: str, *, topic: str = "order", action: str = "order.updated"
) -> MercadoPagoWebhookEvent:
    """Build a verified notification by hand.

    Args:
        data_id (str): The signed resource id.
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
        data_id=data_id,
        payload={"action": action, "type": topic, "data": {"id": data_id}},
    )


class TestDelivery:
    """The re-read decides; the body never does."""

    async def test_a_cancelled_pix_is_charge_cancelled(self) -> None:
        """Type from the re-read order."""
        _, order = fixture("pix_get_after_cancel")
        provider = MercadoPagoPixProvider(
            _http(Recorder(response("pix_get_after_cancel")))
        )

        event = provider.parse_webhook(
            await provider.read_delivery(_notification(order["id"]))
        )

        assert event.type is PixEventType.CHARGE_CANCELLED
        assert event.charge is not None
        assert event.charge.status is PaymentStatus.CANCELLED
        assert event.provider_event_name == "order.updated"

    async def test_a_pending_pix_is_charge_created(self) -> None:
        """Waiting for transfer."""
        _, order = fixture("pix_get")
        provider = MercadoPagoPixProvider(_http(Recorder(response("pix_get"))))

        event = provider.parse_webhook(
            await provider.read_delivery(_notification(order["id"]))
        )

        assert event.type is PixEventType.CHARGE_CREATED

    async def test_an_action_alone_identifies_an_order_delivery(self) -> None:
        """Topic absent, `order.*` action present: still re-read."""
        provider = MercadoPagoPixProvider(_http(Recorder(response("pix_get"))))

        delivery = await provider.read_delivery(_notification("ORD1", topic="other"))

        assert delivery.order is not None

    async def test_another_resource_is_not_fetched(self) -> None:
        """A payment notification is not an order."""
        recorder = Recorder(response("pix_get"))
        provider = MercadoPagoPixProvider(_http(recorder))

        delivery = await provider.read_delivery(
            _notification("123", topic="payment", action="payment.updated")
        )

        assert recorder.requests == []
        assert provider.parse_webhook(delivery).type is PixEventType.UNKNOWN

    async def test_an_id_that_is_not_an_order_id_is_not_fetched(self) -> None:
        """Nothing but `[A-Za-z0-9]+` reaches the path."""
        recorder = Recorder(response("pix_get"))
        provider = MercadoPagoPixProvider(_http(recorder))

        delivery = await provider.read_delivery(_notification("../payments/1"))

        assert recorder.requests == []
        assert delivery.order is None

    async def test_a_404_is_an_answer_not_a_failure(self) -> None:
        """The dashboard's simulation signs a made-up id."""
        provider = MercadoPagoPixProvider(
            _http(Recorder(httpx.Response(404, json={"errors": []})))
        )

        delivery = await provider.read_delivery(_notification("ORD1"))

        assert delivery.order is None

    async def test_a_card_order_is_not_a_pix_event(self) -> None:
        """The Pix adapter ignores a card order; the card adapter reads it."""
        provider = MercadoPagoPixProvider(_http(Recorder(response("card_approved"))))

        delivery = await provider.read_delivery(_notification("ORD1"))
        card = MercadoPagoCardProvider.charge_from_delivery(delivery)

        assert provider.parse_webhook(delivery).charge is None
        assert card is not None
        assert card.status is PaymentStatus.PAID

    def test_the_bare_notification_is_refused(self) -> None:
        """It carries no state."""
        provider = MercadoPagoPixProvider(_http(Recorder(response("pix_get"))))

        with pytest.raises(TypeError, match="make_mercado_pago_webhook_delivery"):
            provider.parse_webhook(_notification("ORD1"))

    def test_a_hand_built_delivery_parses(self) -> None:
        """The dataclass is public for tests."""
        provider = MercadoPagoPixProvider(_http(Recorder(response("pix_get"))))

        event = provider.parse_webhook(
            MercadoPagoOrderDelivery(notification=_notification("ORD1"))
        )

        assert event.type is PixEventType.UNKNOWN


def _signed(data_id: str) -> dict[str, str]:
    """Sign a delivery the way Mercado Pago does.

    Args:
        data_id (str): The ``data.id`` value.

    Returns:
        dict[str, str]: ``x-signature`` and ``x-request-id``.
    """
    ts = str(int(time.time()))
    digest = sign_manifest(
        secret=SECRET, data_id=data_id, request_id="req-1", timestamp=ts
    )
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": "req-1"}


def _app(provider: MercadoPagoPixProvider, order_id: str, amount_cents: int) -> FastAPI:
    """Mount the webhook route a service builds.

    Args:
        provider (MercadoPagoPixProvider): The adapter.
        order_id (str): The order the service stored.
        amount_cents (int): What the order costs.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    dependency = make_mercado_pago_webhook_delivery_dependency(SECRET, provider)

    @app.post("/webhooks/mp")
    async def receive(
        delivery: MercadoPagoOrderDelivery = Depends(dependency),
    ) -> dict[str, Any]:
        """Settle like the recipe's service.

        Args:
            delivery (MercadoPagoOrderDelivery): The re-read delivery.

        Returns:
            dict[str, Any]: What happened.
        """
        event = provider.parse_webhook(delivery)
        if event.type is not PixEventType.CHARGE_PAID or event.charge is None:
            return {"settled": None, "type": event.type.value}
        confirmation = await confirm_pix_payment(
            provider,
            order_id,
            reference=event.charge.reference,
            amount_cents=amount_cents,
        )
        return {"settled": confirmation.outcome.value}

    return app


class TestWebhookRoute:
    """Signature, re-read, settlement."""

    def test_a_signed_delivery_is_re_read(self) -> None:
        """A cancelled Pix: re-read, not settled."""
        _, order = fixture("pix_get_after_cancel")
        recorder = Recorder(response("pix_get_after_cancel"))
        app = _app(MercadoPagoPixProvider(_http(recorder)), order["id"], 1990)

        with TestClient(app) as client:
            answer = client.post(
                f"/webhooks/mp?data.id={order['id']}&type=order",
                headers=_signed(order["id"]),
                json={"action": "order.updated", "type": "order"},
            )

        assert answer.status_code == 200
        assert answer.json() == {"settled": None, "type": "charge_cancelled"}
        assert recorder.requests[0].url.path == f"/v1/orders/{order['id']}"

    def test_a_paid_order_settles(self) -> None:
        """`processed` re-read, then confirmed: `paid`."""
        _, order = fixture("pix_get")
        paid = {**order, "status": "processed", "status_detail": "accredited"}
        app = _app(
            MercadoPagoPixProvider(_http(Recorder(httpx.Response(200, json=paid)))),
            order["id"],
            1990,
        )

        with TestClient(app) as client:
            answer = client.post(
                f"/webhooks/mp?data.id={order['id']}&type=order",
                headers=_signed(order["id"]),
                json={"action": "order.updated", "type": "order"},
            )

        assert answer.json() == {"settled": PixConfirmationOutcome.PAID.value}

    def test_a_bad_signature_is_401_before_any_read(self) -> None:
        """No signature, no re-read."""
        recorder = Recorder(response("pix_get"))
        app = _app(MercadoPagoPixProvider(_http(recorder)), "ORD1", 1990)

        with TestClient(app) as client:
            answer = client.post(
                "/webhooks/mp?data.id=ORD1&type=order",
                headers={"x-signature": "ts=1,v1=00", "x-request-id": "req-1"},
                json={"type": "order"},
            )

        assert answer.status_code == 401
        assert recorder.requests == []

    def test_a_failed_re_read_is_not_acknowledged(self) -> None:
        """5xx on the re-read: the route fails, the provider retries."""
        app = _app(
            MercadoPagoPixProvider(
                _http(
                    Recorder(httpx.Response(503, json={})), RetryPolicy(max_attempts=1)
                )
            ),
            "ORD1",
            1990,
        )

        with TestClient(app, raise_server_exceptions=False) as client:
            answer = client.post(
                "/webhooks/mp?data.id=ORD1&type=order",
                headers=_signed("ORD1"),
                json={"type": "order"},
            )

        assert answer.status_code == 500

    def test_a_simulated_notification_is_acknowledged(self) -> None:
        """404 on the re-read: 200, nothing settled, no retry loop."""
        app = _app(
            MercadoPagoPixProvider(_http(Recorder(httpx.Response(404, json={})))),
            "ORD1",
            1990,
        )

        with TestClient(app) as client:
            answer = client.post(
                "/webhooks/mp?data.id=ORD9&type=order",
                headers=_signed("ORD9"),
                json={"action": "order.updated", "type": "order"},
            )

        assert answer.status_code == 200
        assert answer.json()["settled"] is None
