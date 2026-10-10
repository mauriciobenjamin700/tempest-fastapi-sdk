"""``MercadoPagoPixProvider`` against the real sandbox.

Every test here is marked ``network`` and stays out of ``make check``. They
need a Mercado Pago **test** credential in the environment:

- ``MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN`` — preferred: the access token of
  a test seller account (``APP_USR-...`` issued to a user whose ``tags``
  include ``test_user``);
- otherwise ``MERCADO_PAGO_TEST_ACCESS_TOKEN`` — an application's ``TEST-``
  token;
- ``MERCADO_PAGO_TEST_BUYER_EMAIL`` — the payer e-mail sent on the charge.

A token that is neither ``TEST-`` nor owned by a ``test_user`` account is
refused before any charge is created: this module creates and cancels
payments, and doing that against a real account is not a test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixPayer,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL

pytestmark = pytest.mark.network

SELLER_TOKEN_ENV: str = "MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN"
APP_TOKEN_ENV: str = "MERCADO_PAGO_TEST_ACCESS_TOKEN"
BUYER_EMAIL_ENV: str = "MERCADO_PAGO_TEST_BUYER_EMAIL"


async def _sandbox_token() -> str:
    """Read a test token, or skip; refuse anything that is not a test account.

    Returns:
        str: The token.
    """
    token = os.environ.get(SELLER_TOKEN_ENV) or os.environ.get(APP_TOKEN_ENV) or ""
    if not token:
        pytest.skip(f"neither {SELLER_TOKEN_ENV} nor {APP_TOKEN_ENV} is set")
    if token.startswith("TEST-"):
        return token
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        response = await http.request("GET", "/users/me")
    response.raise_for_status()
    tags = response.json().get("tags") or []
    if "test_user" not in tags:
        pytest.fail("the token does not belong to a test_user account; refusing")
    return token


@pytest.fixture
async def provider() -> AsyncIterator[MercadoPagoPixProvider]:
    """Build the adapter over the sandbox credential.

    Yields:
        MercadoPagoPixProvider: The adapter.
    """
    token = await _sandbox_token()
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        yield MercadoPagoPixProvider(http)


def _payer() -> PixPayer:
    """The payer sent on every sandbox charge, or skip.

    Returns:
        PixPayer: The test buyer's e-mail. Mercado Pago requires one on a
        Pix payment, so without it there is nothing to test.
    """
    email = os.environ.get(BUYER_EMAIL_ENV, "")
    if "@" not in email:
        pytest.skip(f"{BUYER_EMAIL_ENV} must hold the test buyer's e-mail")
    return PixPayer(email=email)


async def test_create_read_and_cancel_a_pix_charge(
    provider: MercadoPagoPixProvider,
) -> None:
    """The whole lifecycle short of payment: QR out, read back, cancelled."""
    created = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference="tempest-sandbox-lifecycle",
            description="tempest-fastapi-sdk sandbox",
            payer=_payer(),
        )
    )

    assert created.provider == "mercado_pago"
    assert created.status is PaymentStatus.PENDING
    assert created.amount_cents == 1990
    assert created.reference == "tempest-sandbox-lifecycle"
    assert created.br_code
    assert created.qr_code_base64

    read = await provider.get_pix_charge(created.provider_charge_id)

    assert read.provider_charge_id == created.provider_charge_id
    assert read.status is PaymentStatus.PENDING
    assert read.amount_cents == 1990

    cancelled = await provider.cancel_pix_charge(created.provider_charge_id)

    assert cancelled.status is PaymentStatus.CANCELLED
    assert cancelled.provider_charge_id == created.provider_charge_id
