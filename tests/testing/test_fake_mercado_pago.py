"""The Mercado Pago simulator: signed deliveries and the stateful Orders API.

The delivery is checked against the SDK's **real** verifier, never against a
second copy of the manifest rules: a delivery the dependency accepts with the
same secret, and refuses with another, is the whole promise.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    MercadoPagoEventEnum,
    MercadoPagoWebhookEvent,
    make_mercado_pago_webhook_dependency,
)
from tempest_fastapi_sdk.testing.fakes import (
    FakeMercadoPagoOrdersAPI,
    build_mercado_pago_delivery,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
    FAKE_CONTROL_PREFIX,
    FAKE_WEBHOOK_SECRET,
    make_fake_orders_app,
    order_action,
)

SECRET: str = "segredo-de-teste"
"""The secret the route under test verifies with."""

ORDER: dict[str, Any] = {
    "id": "ORD01M7B5HWREYRJCAPJ61QTF1SRW",
    "external_reference": "pedido-1",
    "status": "canceled",
    "status_detail": "canceled",
    "total_amount": "10.00",
    "total_paid_amount": "0.00",
    "type": "online",
    "transactions": {
        "payments": [
            {
                "id": "PAY01",
                "amount": "10.00",
                "status": "canceled",
                "payment_method": {"id": "pix", "type": "bank_transfer"},
            }
        ]
    },
}
"""The order of the production delivery of 2026-11-13, redacted."""


def _route(secret: str) -> FastAPI:
    """Mount the SDK's verifying dependency on a route.

    Args:
        secret (str): The secret the dependency verifies with.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    verified = make_mercado_pago_webhook_dependency(secret)

    @app.post("/webhook")
    async def webhook(
        event: MercadoPagoWebhookEvent = Depends(verified),
    ) -> dict[str, Any]:
        """Echo what the dependency verified.

        Args:
            event (MercadoPagoWebhookEvent): The verified notification.

        Returns:
            dict[str, Any]: The fields a test asserts on.
        """
        return {
            "data_id": event.data_id,
            "event": event.event.value if event.event else None,
            "action": event.payload.get("action"),
        }

    return app


async def _post(app: FastAPI, delivery: Any) -> httpx.Response:
    """Send a delivery to an app in-process.

    Args:
        app (FastAPI): The receiving app.
        delivery (Any): The :class:`MercadoPagoDelivery`.

    Returns:
        httpx.Response: The answer.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        response: httpx.Response = await delivery.send(client, "/webhook")
        return response


class TestDelivery:
    """The delivery against the SDK's own verifier."""

    async def test_it_verifies_with_the_same_secret(self) -> None:
        """Same secret, ``200``; the id arrives as delivered, uppercase."""
        delivery = build_mercado_pago_delivery(ORDER, secret=SECRET)

        response = await _post(_route(SECRET), delivery)

        assert response.status_code == 200
        assert response.json() == {
            "data_id": ORDER["id"],
            "event": MercadoPagoEventEnum.ORDER.value,
            "action": "order.canceled",
        }

    async def test_it_is_refused_with_another_secret(self) -> None:
        """Another secret, ``401`` before the route runs."""
        delivery = build_mercado_pago_delivery(ORDER, secret=SECRET)

        response = await _post(_route("outro-segredo"), delivery)

        assert response.status_code == 401

    async def test_an_old_timestamp_falls_outside_the_tolerance(self) -> None:
        """``ts`` an hour old is refused under the default five minutes."""
        delivery = build_mercado_pago_delivery(
            ORDER, secret=SECRET, ts=int(time.time()) - 3600
        )

        response = await _post(_route(SECRET), delivery)

        assert response.status_code == 401

    def test_it_has_the_observed_shape(self) -> None:
        """Query, headers and body keys of the production delivery."""
        delivery = build_mercado_pago_delivery(
            ORDER, secret=SECRET, request_id="req-1", ts=1700000000
        )

        assert delivery.query == {
            "data.external_reference": "pedido-1",
            "data.id": ORDER["id"],
            "type": "order",
        }
        assert delivery.headers["x-request-id"] == "req-1"
        assert delivery.headers["x-signature"].startswith("ts=1700000000,v1=")
        assert set(delivery.payload) == {
            "action",
            "api_version",
            "application_id",
            "data",
            "live_mode",
            "type",
            "user_id",
        }
        assert delivery.payload["live_mode"] is False
        assert delivery.payload["data"]["transactions"]["payments"][0][
            "payment_method"
        ] == {"id": "pix", "type": "bank_transfer"}

    def test_an_explicit_action_wins(self) -> None:
        """``action=`` overrides the status-derived name."""
        delivery = build_mercado_pago_delivery(
            ORDER, secret=SECRET, action="order.updated"
        )

        assert delivery.payload["action"] == "order.updated"

    def test_order_action_follows_the_status(self) -> None:
        """``order.<status>``, and ``order.updated`` without a status."""
        assert order_action({"status": "processed"}) == "order.processed"
        assert order_action({}) == "order.updated"

    @pytest.mark.parametrize(
        "order,secret",
        [({"id": ""}, SECRET), ({}, SECRET), (ORDER, "")],
        ids=["blank-id", "no-id", "empty-secret"],
    )
    def test_it_refuses_what_cannot_verify(
        self, order: dict[str, Any], secret: str
    ) -> None:
        """No id or no secret raises instead of building a sure ``401``.

        Args:
            order (dict[str, Any]): The order.
            secret (str): The secret.
        """
        with pytest.raises(ValueError):
            build_mercado_pago_delivery(order, secret=secret)

    def test_url_joins_an_existing_query(self) -> None:
        """An endpoint that already has a query gets ``&``, not ``?``."""
        delivery = build_mercado_pago_delivery({"id": "ORD1"}, secret=SECRET)

        assert delivery.url("/w?x=1") == "/w?x=1&data.id=ORD1&type=order"


def _pix_body(
    amount: str = "19.90", payer: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a Pix create body like the adapter sends.

    Args:
        amount (str): The amount in reais.
        payer (dict[str, Any] | None): The payer block.

    Returns:
        dict[str, Any]: The body.
    """
    return {
        "type": "online",
        "processing_mode": "automatic",
        "total_amount": amount,
        "external_reference": "pedido-1",
        "payer": {"email": "comprador@example.com"} if payer is None else payer,
        "transactions": {
            "payments": [
                {
                    "amount": amount,
                    "payment_method": {"id": "pix", "type": "bank_transfer"},
                }
            ]
        },
    }


class TestOrdersAPI:
    """The HTTP surface, without the adapter in between."""

    async def test_same_key_and_body_is_the_same_order(self) -> None:
        """Replay with the same key answers the first order."""
        api = FakeMercadoPagoOrdersAPI()
        async with httpx.AsyncClient(
            transport=api.transport(), base_url="https://api"
        ) as client:
            headers = {"X-Idempotency-Key": "k1"}
            first = await client.post("/v1/orders", json=_pix_body(), headers=headers)
            again = await client.post("/v1/orders", json=_pix_body(), headers=headers)
            other = await client.post(
                "/v1/orders", json=_pix_body("20.00"), headers=headers
            )

        assert first.status_code == again.status_code == 201
        assert first.json()["id"] == again.json()["id"]
        assert other.status_code == 409
        assert other.json()["errors"][0]["code"] == "idempotency_key_already_used"
        assert api.order_ids == [first.json()["id"]]

    async def test_a_create_without_payer_is_400(self) -> None:
        """The measured answer to a missing payer."""
        api = FakeMercadoPagoOrdersAPI()
        async with httpx.AsyncClient(
            transport=api.transport(), base_url="https://api"
        ) as client:
            response = await client.post("/v1/orders", json=_pix_body(payer={}))

        assert response.status_code == 400
        assert response.json()["errors"][0]["code"] == "required_properties"

    async def test_an_unknown_order_is_404(self) -> None:
        """``GET`` of an id never created."""
        api = FakeMercadoPagoOrdersAPI()
        async with httpx.AsyncClient(
            transport=api.transport(), base_url="https://api"
        ) as client:
            response = await client.get("/v1/orders/ORD00000000000000000000000000")

        assert response.status_code == 404

    async def test_a_pix_order_starts_waiting_on_the_transfer(self) -> None:
        """``action_required`` / ``waiting_transfer``, with the QR."""
        api = FakeMercadoPagoOrdersAPI()
        async with httpx.AsyncClient(
            transport=api.transport(), base_url="https://api"
        ) as client:
            created = (await client.post("/v1/orders", json=_pix_body())).json()

        payment = created["transactions"]["payments"][0]
        assert (created["status"], created["status_detail"]) == (
            "action_required",
            "waiting_transfer",
        )
        assert payment["payment_method"]["qr_code"]
        assert payment["date_of_expiration"]

    async def test_an_unpaid_order_cannot_be_refunded(self) -> None:
        """``409 cannot_refund_order``, as measured."""
        api = FakeMercadoPagoOrdersAPI()
        async with httpx.AsyncClient(
            transport=api.transport(), base_url="https://api"
        ) as client:
            created = (await client.post("/v1/orders", json=_pix_body())).json()
            response = await client.post(f"/v1/orders/{created['id']}/refund")

        assert response.status_code == 409
        assert response.json()["errors"][0]["code"] == "cannot_refund_order"

    def test_a_transition_that_does_not_apply_raises(self) -> None:
        """Refunding nothing, or paying twice, is a test bug: ``ValueError``."""
        api = FakeMercadoPagoOrdersAPI()
        with httpx.Client(transport=httpx.MockTransport(api)) as client:
            order_id = client.post("https://api/v1/orders", json=_pix_body()).json()[
                "id"
            ]
        api.pay(order_id)

        with pytest.raises(ValueError, match="cannot_pay_order"):
            api.pay(order_id)
        with pytest.raises(ValueError, match="Unknown transition"):
            api.apply(order_id, "teleport")
        with pytest.raises(KeyError):
            api.pay("ORD404")

    def test_transitions_sign_with_the_configured_secret(self) -> None:
        """The default secret is the public constant; a custom one is used."""
        assert FakeMercadoPagoOrdersAPI().webhook_secret == FAKE_WEBHOOK_SECRET
        assert FakeMercadoPagoOrdersAPI(webhook_secret="x").webhook_secret == "x"


class TestServer:
    """The app ``tempest mercado-pago fake-api`` serves."""

    async def test_control_route_moves_the_order(self) -> None:
        """Create through ``/v1/orders``, pay through the control route."""
        api = FakeMercadoPagoOrdersAPI()
        app = make_fake_orders_app(api)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fake"
        ) as client:
            created = (
                await client.post(
                    "/v1/orders",
                    json=_pix_body(),
                    headers={"X-Idempotency-Key": "k"},
                )
            ).json()
            paid = await client.post(
                f"{FAKE_CONTROL_PREFIX}/orders/{created['id']}/pay"
            )
            read = await client.get(f"/v1/orders/{created['id']}")
            listed = await client.get(f"{FAKE_CONTROL_PREFIX}/orders")
            missing = await client.post(f"{FAKE_CONTROL_PREFIX}/orders/ORD1/pay")
            twice = await client.post(
                f"{FAKE_CONTROL_PREFIX}/orders/{created['id']}/pay"
            )

        assert paid.status_code == 200
        assert paid.json()["action"] == "order.processed"
        assert read.json()["status"] == "processed"
        assert listed.json() == {"orders": [created["id"]]}
        assert missing.status_code == 404
        assert twice.status_code == 409

    async def test_control_routes_find_the_order_by_external_reference(
        self,
    ) -> None:
        """``pedido-1`` reads and moves the most recent order created with it."""
        api = FakeMercadoPagoOrdersAPI()
        app = make_fake_orders_app(api)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fake"
        ) as client:
            first = (await client.post("/v1/orders", json=_pix_body())).json()
            second = (await client.post("/v1/orders", json=_pix_body())).json()
            found = await client.get(f"{FAKE_CONTROL_PREFIX}/orders/pedido-1")
            by_id = await client.get(f"{FAKE_CONTROL_PREFIX}/orders/{first['id']}")
            paid = await client.post(f"{FAKE_CONTROL_PREFIX}/orders/pedido-1/pay")
            missing = await client.get(f"{FAKE_CONTROL_PREFIX}/orders/pedido-2")

        assert found.json()["id"] == second["id"]
        assert by_id.json()["id"] == first["id"]
        assert paid.json()["order"]["id"] == second["id"]
        assert api.order(first["id"])["status"] == "action_required"
        assert missing.status_code == 404


class TestFindOrderId:
    """:meth:`FakeMercadoPagoOrdersAPI.find_order_id`."""

    def test_an_id_wins_then_the_newest_reference(self) -> None:
        """The id itself, else the newest order with that reference."""
        api = FakeMercadoPagoOrdersAPI()
        with httpx.Client(transport=api.transport()) as client:
            first = client.post("https://api/v1/orders", json=_pix_body()).json()
            second = client.post("https://api/v1/orders", json=_pix_body()).json()

        assert api.find_order_id(first["id"]) == first["id"]
        assert api.find_order_id("pedido-1") == second["id"]
        with pytest.raises(KeyError):
            api.find_order_id("pedido-404")
