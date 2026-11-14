"""The whole Mercado Pago flow, offline: real adapter, simulated provider.

A service mounts :func:`make_mercado_pago_webhook_delivery_dependency` with
the **real** :class:`MercadoPagoPixProvider` / :class:`MercadoPagoCardProvider`,
both over :class:`FakeMercadoPagoOrdersAPI`. The test moves the order the way
only the provider can and sends the signed delivery each move returns; the
route has to see the state the move produced, re-read from the API.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.exceptions import PixRefundExceedsBalanceException
from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PaymentStatusEnum,
    PixChargeRequest,
    PixEventTypeEnum,
    PixPayer,
)
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoCardProvider,
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.testing.fakes import (
    FakeMercadoPagoOrdersAPI,
    MercadoPagoDelivery,
)
from tempest_fastapi_sdk.utils.retry import RetryPolicy

SECRET: str = "segredo-do-servico"
"""The secret the service and the simulator share."""

PAYER: PixPayer = PixPayer(email="comprador@example.com")
"""A payer the Orders API accepts."""


def _service(provider: MercadoPagoPixProvider) -> FastAPI:
    """Build the service's webhook route, as a service would.

    Args:
        provider (MercadoPagoPixProvider): The adapter, over the simulator.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    dependency = make_mercado_pago_webhook_delivery_dependency(SECRET, provider)

    @app.post("/webhooks/mercado-pago")
    async def webhook(
        delivery: MercadoPagoOrderDelivery = Depends(dependency),
    ) -> dict[str, Any]:
        """Report what the re-read decided.

        Args:
            delivery (MercadoPagoOrderDelivery): The verified, re-read
                delivery.

        Returns:
            dict[str, Any]: The fields a test asserts on.
        """
        event = provider.parse_webhook(delivery)
        return {
            "order_status": delivery.order["status"] if delivery.order else None,
            "type": event.type.value,
            "status": event.charge.status.value if event.charge else None,
        }

    return app


async def _send(app: FastAPI, delivery: MercadoPagoDelivery) -> httpx.Response:
    """Deliver to the service in-process.

    Args:
        app (FastAPI): The service.
        delivery (MercadoPagoDelivery): The signed delivery.

    Returns:
        httpx.Response: The route's answer.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://service"
    ) as client:
        return await delivery.send(client, "/webhooks/mercado-pago")


@pytest.fixture
def api() -> FakeMercadoPagoOrdersAPI:
    """Provide a simulator signing with the service's secret.

    Returns:
        FakeMercadoPagoOrdersAPI: The simulator.
    """
    return FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)


@pytest.fixture
def pix(api: FakeMercadoPagoOrdersAPI) -> MercadoPagoPixProvider:
    """Provide the real Pix adapter over the simulator.

    Args:
        api (FakeMercadoPagoOrdersAPI): The simulator.

    Returns:
        MercadoPagoPixProvider: The adapter.
    """
    return MercadoPagoPixProvider(api.http_client(), action_retry_delays=())


async def _charge(pix: MercadoPagoPixProvider, reference: str = "pedido-1") -> str:
    """Create a Pix charge of R$ 19,90.

    Args:
        pix (MercadoPagoPixProvider): The adapter.
        reference (str): The charge's reference.

    Returns:
        str: The order id.
    """
    charge = await pix.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference=reference, payer=PAYER)
    )
    assert charge.status is PaymentStatusEnum.PENDING
    assert charge.br_code
    return charge.provider_charge_id


class TestPixFlow:
    """Create, move, deliver, re-read."""

    async def test_paid(
        self, api: FakeMercadoPagoOrdersAPI, pix: MercadoPagoPixProvider
    ) -> None:
        """``pay`` → the route re-reads ``processed`` → ``charge_paid``."""
        order_id = await _charge(pix)

        response = await _send(_service(pix), api.pay(order_id))

        assert response.status_code == 200
        assert response.json() == {
            "order_status": "processed",
            "type": PixEventTypeEnum.CHARGE_PAID.value,
            "status": PaymentStatusEnum.PAID.value,
        }

    async def test_refunded_in_two_steps(
        self, api: FakeMercadoPagoOrdersAPI, pix: MercadoPagoPixProvider
    ) -> None:
        """A partial refund stays paid; the rest makes it refunded."""
        order_id = await _charge(pix)
        api.pay(order_id)
        app = _service(pix)

        partial = await pix.refund_pix_charge(
            order_id, 500, refund_reference="estorno-1"
        )
        after_partial = await _send(app, api.delivery(order_id))
        rest = await pix.refund_pix_charge(order_id, refund_reference="estorno-2")
        after_rest = await _send(app, api.delivery(order_id))

        assert (partial.amount_cents, partial.refunded_cents) == (500, 500)
        assert (rest.amount_cents, rest.refunded_cents) == (1490, 1990)
        assert after_partial.json()["status"] == PaymentStatusEnum.PAID.value
        assert after_rest.json() == {
            "order_status": "refunded",
            "type": PixEventTypeEnum.CHARGE_REFUNDED.value,
            "status": PaymentStatusEnum.REFUNDED.value,
        }
        assert await pix.get_refunded_cents(order_id) == 1990

    async def test_refund_replay_and_balance(
        self, api: FakeMercadoPagoOrdersAPI, pix: MercadoPagoPixProvider
    ) -> None:
        """The same reference replays; asking past the balance is refused."""
        order_id = await _charge(pix)
        api.pay(order_id)

        first = await pix.refund_pix_charge(order_id, 500, refund_reference="r1")
        again = await pix.refund_pix_charge(order_id, 500, refund_reference="r1")

        assert first.provider_refund_id == again.provider_refund_id
        assert await pix.get_refunded_cents(order_id) == 500
        with pytest.raises(PixRefundExceedsBalanceException):
            await pix.refund_pix_charge(order_id, 5000, refund_reference="r2")

    async def test_cancelled(
        self, api: FakeMercadoPagoOrdersAPI, pix: MercadoPagoPixProvider
    ) -> None:
        """Cancelled through the adapter, announced with ``order.canceled``."""
        order_id = await _charge(pix)

        charge = await pix.cancel_pix_charge(order_id)
        delivery = api.delivery(order_id)
        response = await _send(_service(pix), delivery)

        assert charge.status is PaymentStatusEnum.CANCELLED
        assert delivery.payload["action"] == "order.canceled"
        assert response.json()["type"] == PixEventTypeEnum.CHARGE_CANCELLED.value

    async def test_expired(
        self, api: FakeMercadoPagoOrdersAPI, pix: MercadoPagoPixProvider
    ) -> None:
        """``expire`` → ``canceled`` / ``expired`` → ``charge_expired``."""
        order_id = await _charge(pix)

        response = await _send(_service(pix), api.expire(order_id))

        assert response.json() == {
            "order_status": "canceled",
            "type": PixEventTypeEnum.CHARGE_EXPIRED.value,
            "status": PaymentStatusEnum.EXPIRED.value,
        }

    async def test_a_made_up_id_reads_as_no_order(
        self, pix: MercadoPagoPixProvider
    ) -> None:
        """A signed delivery for an unknown id: ``200`` and no order.

        What the dashboard's "simulate notification" produces, reproduced
        offline: the re-read answers ``404``.
        """
        other = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        await _charge(MercadoPagoPixProvider(other.http_client()))
        delivery = other.delivery(other.order_ids[0])

        response = await _send(_service(pix), delivery)

        assert response.status_code == 200
        assert response.json() == {
            "order_status": None,
            "type": PixEventTypeEnum.UNKNOWN.value,
            "status": None,
        }

    async def test_a_delivery_signed_with_another_secret_is_401(
        self, pix: MercadoPagoPixProvider
    ) -> None:
        """The simulator's default secret is not the service's."""
        stranger = FakeMercadoPagoOrdersAPI()
        await _charge(MercadoPagoPixProvider(stranger.http_client()))

        response = await _send(_service(pix), stranger.delivery(stranger.order_ids[0]))

        assert response.status_code == 401

    async def test_the_same_reference_is_the_same_order(
        self, api: FakeMercadoPagoOrdersAPI, pix: MercadoPagoPixProvider
    ) -> None:
        """The adapter's reference-derived key meets the simulator's rule."""
        first = await _charge(pix, "pedido-9")
        again = await _charge(pix, "pedido-9")

        assert first == again
        assert api.order_ids == [first]
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await pix.create_pix_charge(
                PixChargeRequest(amount_cents=2000, reference="pedido-9", payer=PAYER)
            )
        assert caught.value.response.status_code == 409

    async def test_a_lost_answer_is_replayed_by_the_retry(
        self, api: FakeMercadoPagoOrdersAPI
    ) -> None:
        """The create lands, the answer is lost, the client's retry replays."""
        provider = MercadoPagoPixProvider(
            api.http_client(
                retry_policy=RetryPolicy(max_attempts=2, backoff_initial_seconds=0)
            )
        )
        api.lose_next_answer()

        order_id = await _charge(provider)

        assert api.order_ids == [order_id]
        assert [call[0] for call in api.calls] == ["POST", "POST"]


class TestCardFlow:
    """The card adapter over the same simulator."""

    @staticmethod
    def _request(capture: bool = True) -> CardChargeRequest:
        """Build a R$ 100,00 card charge.

        Args:
            capture (bool): ``False`` only authorizes.

        Returns:
            CardChargeRequest: The request.
        """
        return CardChargeRequest(
            amount_cents=10000,
            reference="cartao-1",
            card_token="tok",
            payment_method_id="visa",
            payer=PAYER,
            capture=capture,
        )

    async def test_approved_then_refunded(self, api: FakeMercadoPagoOrdersAPI) -> None:
        """Approved at once; refunded in part, then the rest."""
        card = MercadoPagoCardProvider(api.http_client(), action_retry_delays=())

        charge = await card.create_card_charge(self._request())
        partial = await card.refund_card_charge(charge.provider_charge_id, 3000)
        rest = await card.refund_card_charge(charge.provider_charge_id)

        assert charge.status is PaymentStatusEnum.PAID
        assert (partial.status, partial.status_detail) == (
            PaymentStatusEnum.PAID,
            "partially_refunded",
        )
        assert (rest.status, rest.refunded_cents) == (PaymentStatusEnum.REFUNDED, 10000)

    async def test_authorized_then_captured(
        self, api: FakeMercadoPagoOrdersAPI
    ) -> None:
        """``capture=False`` authorizes; capture pays."""
        card = MercadoPagoCardProvider(api.http_client(), action_retry_delays=())

        charge = await card.create_card_charge(self._request(capture=False))
        captured = await card.capture_card_charge(charge.provider_charge_id)

        assert charge.status is PaymentStatusEnum.AUTHORIZED
        assert captured.status is PaymentStatusEnum.PAID

    async def test_declined_is_a_failed_charge(
        self, api: FakeMercadoPagoOrdersAPI
    ) -> None:
        """A queued decline answers ``402``; the adapter returns ``FAILED``."""
        card = MercadoPagoCardProvider(api.http_client(), action_retry_delays=())
        api.decline_next_card()

        charge = await card.create_card_charge(self._request())

        assert charge.status is PaymentStatusEnum.FAILED
        assert charge.status_detail == "rejected_by_issuer"

    async def test_the_card_charge_reads_from_a_delivery(
        self, api: FakeMercadoPagoOrdersAPI
    ) -> None:
        """``charge_from_delivery`` over a captured order's re-read."""
        card = MercadoPagoCardProvider(api.http_client(), action_retry_delays=())
        charge = await card.create_card_charge(self._request(capture=False))
        app = FastAPI()
        dependency = make_mercado_pago_webhook_delivery_dependency(SECRET, card)

        @app.post("/webhooks/mercado-pago")
        async def webhook(
            delivery: MercadoPagoOrderDelivery = Depends(dependency),
        ) -> dict[str, Any]:
            """Report the card charge the delivery carries.

            Args:
                delivery (MercadoPagoOrderDelivery): The re-read delivery.

            Returns:
                dict[str, Any]: The charge status.
            """
            found = card.charge_from_delivery(delivery)
            return {"status": found.status.value if found else None}

        response = await _send(app, api.capture(charge.provider_charge_id))

        assert response.json() == {"status": PaymentStatusEnum.PAID.value}
