"""Installments through the Mercado Pago card adapter, on a body the sandbox sent.

``card_installments.json`` / ``card_installments_get.json`` are a 3x Amex
order of R$ 1.000,00 the test seller created on 2026-10-30, redacted like the
other fixtures in ``fixtures/mercado_pago_orders/``
(``vendor/mercadopago-evidence.md`` section 10). What they pin: the request
carries the price, the provider adds the interest, and the charge reports
what the payer owes apart from the price.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PaymentStatusEnum,
    PixPayer,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoCardProvider,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL

FIXTURES: Path = Path(__file__).parent / "fixtures" / "mercado_pago_orders"


def fixture(name: str) -> tuple[int, Any]:
    """Load one redacted sandbox response.

    Args:
        name (str): The fixture's stem.

    Returns:
        tuple[int, Any]: The HTTP status and the body.
    """
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return int(data["status"]), data["body"]


def provider_answering(
    status: int, body: Any, requests: list[httpx.Request]
) -> MercadoPagoCardProvider:
    """Build a card provider whose transport always gives one answer.

    Args:
        status (int): The HTTP status to answer.
        body (Any): The JSON body to answer.
        requests (list[httpx.Request]): Where each request is recorded.

    Returns:
        MercadoPagoCardProvider: The provider over a mock transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the request and answer.

        Args:
            request (httpx.Request): The request.

        Returns:
            httpx.Response: The scripted answer.
        """
        requests.append(request)
        return httpx.Response(status, json=body)

    http = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer APP_USR-fake"},
        transport=httpx.MockTransport(handler),
    )
    return MercadoPagoCardProvider(http)


def installment_request() -> CardChargeRequest:
    """Build the request the fixture answers: R$ 1.000,00 in 3x on Amex.

    Returns:
        CardChargeRequest: The charge request.
    """
    return CardChargeRequest(
        amount_cents=100000,
        reference="tempest-orders-card-installments",
        card_token="tok_123",
        payment_method_id="amex",
        installments=3,
        payer=PixPayer(email="buyer@example.com"),
    )


class TestInstallments:
    """A card charge in installments."""

    async def test_the_request_carries_the_price_not_the_quoted_total(self) -> None:
        """The interest is the provider's to add; the body has the price."""
        requests: list[httpx.Request] = []
        provider = provider_answering(*fixture("card_installments"), requests)

        await provider.create_card_charge(installment_request())

        body = json.loads(requests[0].content)
        payment = body["transactions"]["payments"][0]
        assert body["total_amount"] == "1000.00"
        assert payment["amount"] == "1000.00"
        assert payment["payment_method"]["installments"] == 3

    async def test_the_charge_reports_the_price_and_what_the_payer_owes(self) -> None:
        """`amount_cents` is the price; the interest shows apart."""
        provider = provider_answering(*fixture("card_installments"), [])

        charge = await provider.create_card_charge(installment_request())

        assert charge.status is PaymentStatusEnum.PAID
        assert charge.amount_cents == 100000
        assert charge.installments == 3
        assert charge.installment_amount_cents == 37077
        assert charge.payer_total_cents == 111231

    async def test_the_payer_total_is_the_payments_not_the_orders(self) -> None:
        """The order's `total_paid_amount` rounds the quote one cent away."""
        _, body = fixture("card_installments")
        provider = provider_answering(200, body, [])

        charge = await provider.get_card_charge(body["id"])

        assert body["total_paid_amount"] == "1112.30"
        assert charge.payer_total_cents == 111231
        assert charge.payer_total_cents == 3 * (charge.installment_amount_cents or 0)

    async def test_reading_the_order_back_reports_the_same(self) -> None:
        """`GET /v1/orders/{id}` keeps the installment fields."""
        status, body = fixture("card_installments_get")
        provider = provider_answering(status, body, [])

        charge = await provider.get_card_charge(body["id"])

        assert charge.installments == 3
        assert charge.installment_amount_cents == 37077
        assert charge.payer_total_cents == 111231

    async def test_a_single_installment_owes_the_price(self) -> None:
        """No interest: the payer total equals the price."""
        provider = provider_answering(*fixture("card_approved"), [])

        charge = await provider.get_card_charge("ORD")

        assert charge.installments == 1
        assert charge.installment_amount_cents == 10000
        assert charge.payer_total_cents == charge.amount_cents == 10000

    async def test_unreported_or_unreadable_amounts_are_none(self) -> None:
        """Informational amounts never refuse an order that exists."""
        _, body = fixture("card_installments")
        body = copy.deepcopy(body)
        payment = body["transactions"]["payments"][0]
        del payment["paid_amount"]
        payment["payment_method"]["installment_amount"] = "not-a-number"
        provider = provider_answering(200, body, [])

        charge = await provider.get_card_charge(body["id"])

        assert charge.payer_total_cents is None
        assert charge.installment_amount_cents is None
        assert charge.amount_cents == 100000
