"""The Mercado Pago adapters against the real sandbox, over the Orders API.

Marked ``network``: out of ``make check``. They need, in the environment:

- ``MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN`` — the production access token of
  a **test seller** account's *Checkout Transparente / API de Orders*
  application (``docs/recipes/mercado-pago-sandbox.md`` walks through
  getting it);
- ``MERCADO_PAGO_TEST_BUYER_EMAIL`` — optional: the payer e-mail. Measured,
  the Orders API accepts any valid e-mail with the right seller token, so it
  defaults to ``buyer@example.com``.

A Pix is paid in the sandbox by sending ``APRO`` as the payer's first name
(``PixPayer.name`` becomes ``first_name``).

Before anything is created, the token's account is read from
``/users/me`` and must carry the ``test_user`` tag; any other account is
refused. Cards are tokenized here with the public test Visa — the only use
of a card number on a server this package condones, because it is not a
card.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PaymentStatus,
    PixChargeRequest,
    PixPayer,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoCardProvider,
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL

pytestmark = pytest.mark.network

SELLER_TOKEN_ENV: str = "MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN"
BUYER_EMAIL_ENV: str = "MERCADO_PAGO_TEST_BUYER_EMAIL"
TEST_VISA: dict[str, object] = {
    "card_number": "4235647728025682",
    "expiration_month": 11,
    "expiration_year": 2030,
    "security_code": "123",
}


@pytest.fixture
async def http() -> AsyncIterator[HTTPClient]:
    """A client on the test seller's token, after checking it is a test account.

    Yields:
        HTTPClient: The client.
    """
    token = os.environ.get(SELLER_TOKEN_ENV, "")
    if not token:
        pytest.skip(f"{SELLER_TOKEN_ENV} is not set")
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as client:
        me = (await client.request("GET", "/users/me")).json()
        if "test_user" not in (me.get("tags") or []):
            pytest.fail("the token does not belong to a test_user account; refusing")
        yield client


def _payer() -> PixPayer:
    """The payer.

    Returns:
        PixPayer: ``MERCADO_PAGO_TEST_BUYER_EMAIL`` when set, else
        ``buyer@example.com``.
    """
    return PixPayer(email=os.environ.get(BUYER_EMAIL_ENV) or "buyer@example.com")


async def _token(http: HTTPClient, holder: str) -> str:
    """Tokenize the public test Visa.

    Args:
        http (HTTPClient): The client.
        holder (str): Cardholder name — ``APRO`` approves, ``OTHE`` declines.

    Returns:
        str: The card token.
    """
    response = await http.request(
        "POST",
        "/v1/card_tokens",
        json={
            **TEST_VISA,
            "cardholder": {
                "name": holder,
                "identification": {"type": "CPF", "number": "12345678909"},
            },
        },
    )
    response.raise_for_status()
    token: str = response.json()["id"]
    return token


async def test_pix_create_read_and_cancel(http: HTTPClient) -> None:
    """QR out, read back, cancelled."""
    provider = MercadoPagoPixProvider(http)

    created = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference="tempest-sandbox-pix",
            expires_in=timedelta(minutes=30),
            payer=_payer(),
        )
    )
    read = await provider.get_pix_charge(created.provider_charge_id)
    cancelled = await provider.cancel_pix_charge(created.provider_charge_id)

    assert created.status is PaymentStatus.PENDING
    assert created.amount_cents == 1990
    assert created.br_code
    assert created.qr_code_base64
    assert read.provider_charge_id == created.provider_charge_id
    assert cancelled.status is PaymentStatus.CANCELLED


async def test_pix_paid_then_refunded_in_two_steps(http: HTTPClient) -> None:
    """``first_name`` ``APRO`` pays the Pix; refund part, then the rest."""
    provider = MercadoPagoPixProvider(http)
    payer = PixPayer(email=_payer().email, name="APRO")

    created = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990, reference="tempest-sandbox-pix-paid", payer=payer
        )
    )
    paid = created
    for _ in range(10):
        await asyncio.sleep(2)
        paid = await provider.get_pix_charge(created.provider_charge_id)
        if paid.status is not PaymentStatus.PENDING:
            break
    partial = await provider.refund_pix_charge(
        created.provider_charge_id, amount_cents=500
    )
    full = await provider.refund_pix_charge(created.provider_charge_id)

    assert paid.status is PaymentStatus.PAID
    assert paid.end_to_end_id
    assert partial.status is PaymentStatus.PAID
    assert partial.raw["status_detail"] == "partially_refunded"
    assert full.status is PaymentStatus.REFUNDED


async def test_card_approved_declined_and_refunded(http: HTTPClient) -> None:
    """Approve, refund in two steps; a decline comes back, not raised."""
    provider = MercadoPagoCardProvider(http)

    approved = await provider.create_card_charge(
        CardChargeRequest(
            amount_cents=10000,
            reference="tempest-sandbox-card",
            card_token=await _token(http, "APRO"),
            payment_method_id="visa",
            payer=_payer(),
        )
    )
    declined = await provider.create_card_charge(
        CardChargeRequest(
            amount_cents=10000,
            reference="tempest-sandbox-card-declined",
            card_token=await _token(http, "OTHE"),
            payment_method_id="visa",
            payer=_payer(),
        )
    )
    partial = await provider.refund_card_charge(
        approved.provider_charge_id, amount_cents=3000
    )
    full = await provider.refund_card_charge(approved.provider_charge_id)

    assert approved.status is PaymentStatus.PAID
    assert declined.status is PaymentStatus.FAILED
    assert declined.status_detail == "rejected_by_issuer"
    assert partial.refunded_cents == 3000
    assert full.status is PaymentStatus.REFUNDED
    assert full.refunded_cents == 10000


async def test_card_authorize_capture_and_release(http: HTTPClient) -> None:
    """Authorize, capture one; authorize, cancel another."""
    provider = MercadoPagoCardProvider(http)

    def request(reference: str, token: str) -> CardChargeRequest:
        return CardChargeRequest(
            amount_cents=10000,
            reference=reference,
            card_token=token,
            payment_method_id="visa",
            capture=False,
            payer=_payer(),
        )

    held = await provider.create_card_charge(
        request("tempest-sandbox-hold", await _token(http, "APRO"))
    )
    captured = await provider.capture_card_charge(held.provider_charge_id)
    released_hold = await provider.create_card_charge(
        request("tempest-sandbox-release", await _token(http, "APRO"))
    )
    released = await provider.cancel_card_charge(released_hold.provider_charge_id)

    assert held.status is PaymentStatus.AUTHORIZED
    assert captured.status is PaymentStatus.PAID
    assert captured.amount_cents == 10000
    assert released.status is PaymentStatus.CANCELLED
