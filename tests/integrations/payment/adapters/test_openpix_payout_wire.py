"""``OpenPixPayoutProvider`` driven over the wire.

The transport is an ``httpx.MockTransport``, so every assertion is on bytes:
what the adapter put on the wire, and how it read what came back.

Response bodies come from ``vendor/openpix-openapi.json``, the examples of
``POST /api/v1/payment`` (``pixKey`` and ``autoApproved``). The
``CONFIRMED``, ``DENIED`` and ``FAILED`` bodies are the ``pixKey`` example
with only ``status`` replaced by another value the document's
``PaymentStatus`` declares: there is no published example for them, and
saying so is better than presenting them as captured.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk import (
    HTTPClient,
    PayoutRejectedException,
    PixKeyType,
    RetryPolicy,
)
from tempest_fastapi_sdk.integrations.payment import PayoutRequest, PayoutStatus
from tempest_fastapi_sdk.integrations.payment.adapters import OpenPixPayoutProvider
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironment

SPEC: Path = Path(__file__).resolve().parents[4] / "vendor" / "openpix-openapi.json"
BASE_URL: str = OpenPixEnvironment.SANDBOX.base_url
APP_ID: str = "Q2xpZW50X0lkX3Rlc3Q6Q2xpZW50X1NlY3JldF90ZXN0"
REQUEST: PayoutRequest = PayoutRequest(
    amount_cents=100,
    pix_key="c4249323-b4ca-43f2-8139-8232aab09b93",
    pix_key_type=PixKeyType.RANDOM,
    correlation_id="payment1",
    comment="payment comment",
)


def _example(name: str) -> dict[str, Any]:
    """Return a ``POST /api/v1/payment`` 200 example from the vendored spec.

    Args:
        name (str): The example key.

    Returns:
        dict[str, Any]: A fresh copy of the example body.
    """
    document = json.loads(SPEC.read_text(encoding="utf-8"))
    responses = document["paths"]["/api/v1/payment"]["post"]["responses"]
    examples = responses["200"]["content"]["application/json"]["examples"]
    return copy.deepcopy(examples[name]["value"])


def _with_status(status: str) -> dict[str, Any]:
    """The ``pixKey`` example with ``payment.status`` replaced.

    Args:
        status (str): A value the document's ``PaymentStatus`` declares.

    Returns:
        dict[str, Any]: The derived body.
    """
    body = _example("pixKey")
    body["payment"]["status"] = status
    return body


class _Wire:
    """A transport that answers one fixed response and keeps the request."""

    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        """Remember what to answer.

        Args:
            status_code (int): HTTP status to answer with.
            body (dict[str, Any]): JSON body to answer with.
        """
        self.status_code: int = status_code
        self.body: dict[str, Any] = body
        self.sent: httpx.Request | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer.

        Args:
            request (httpx.Request): What the adapter sent.

        Returns:
            httpx.Response: The fixed answer.
        """
        self.sent = request
        return httpx.Response(self.status_code, json=self.body)


def _http(handler: Any, **kwargs: Any) -> HTTPClient:
    """An HTTPClient over a mock transport, by default with no retries.

    Args:
        handler (Any): The ``httpx.MockTransport`` handler.
        **kwargs (Any): Forwarded to ``HTTPClient``.

    Returns:
        HTTPClient: The transport.
    """
    options: dict[str, Any] = {"retry_policy": RetryPolicy(max_attempts=1), **kwargs}
    return HTTPClient(
        base_url=BASE_URL,
        default_headers={"Authorization": APP_ID},
        transport=httpx.MockTransport(handler),
        **options,
    )


def _provider(wire: _Wire) -> OpenPixPayoutProvider:
    return OpenPixPayoutProvider(_http(wire))


async def test_the_body_is_a_pix_key_payment_with_auto_approve() -> None:
    wire = _Wire(200, _example("autoApproved"))

    await _provider(wire).transfer_to_pix_key(REQUEST)

    assert wire.sent is not None
    assert wire.sent.url.path == "/api/v1/payment"
    sent: dict[str, Any] = json.loads(wire.sent.content)
    assert sent == {
        "type": "PIX_KEY",
        "value": 100,
        "destinationAlias": "c4249323-b4ca-43f2-8139-8232aab09b93",
        "destinationAliasType": "RANDOM",
        "correlationID": "payment1",
        "comment": "payment comment",
        "autoApprove": True,
    }


async def test_the_documented_auto_approved_answer_is_pending() -> None:
    """``APPROVED`` is money in flight, not settled: settlement is a webhook."""
    result = await _provider(_Wire(200, _example("autoApproved"))).transfer_to_pix_key(
        REQUEST
    )

    assert result.status is PayoutStatus.PENDING
    assert result.provider_status == "APPROVED"
    assert result.provider == "openpix"


async def test_confirmed_is_confirmed() -> None:
    result = await _provider(_Wire(200, _with_status("CONFIRMED"))).transfer_to_pix_key(
        REQUEST
    )

    assert result.status is PayoutStatus.CONFIRMED


@pytest.mark.parametrize("status", ["DENIED", "FAILED"])
async def test_a_refused_payment_is_rejected(status: str) -> None:
    with pytest.raises(PayoutRejectedException, match=status):
        await _provider(_Wire(200, _with_status(status))).transfer_to_pix_key(REQUEST)


async def test_a_400_is_rejected() -> None:
    wire = _Wire(400, {"error": "Saldo insuficiente"})

    with pytest.raises(PayoutRejectedException, match="400"):
        await _provider(wire).transfer_to_pix_key(REQUEST)


async def test_a_500_is_not_a_rejection() -> None:
    """The payment may exist: the wallet must keep the debit."""
    with pytest.raises(httpx.HTTPStatusError):
        await _provider(_Wire(500, {"error": "boom"})).transfer_to_pix_key(REQUEST)


async def test_a_timeout_is_not_a_rejection() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    with pytest.raises(httpx.ReadTimeout):
        await OpenPixPayoutProvider(_http(timeout)).transfer_to_pix_key(REQUEST)


def test_a_retrying_transport_is_refused() -> None:
    """The default policy re-sends a POST answered 500 three times."""
    with pytest.raises(ValueError, match="max_attempts=1"):
        OpenPixPayoutProvider(_http(_Wire(200, {}), retry_policy=RetryPolicy()))


async def test_a_500_goes_out_once() -> None:
    sent: list[str] = []

    def count(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        await OpenPixPayoutProvider(_http(count)).transfer_to_pix_key(REQUEST)

    assert sent == ["POST"]
