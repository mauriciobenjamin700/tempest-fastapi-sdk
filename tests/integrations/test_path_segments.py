"""An identifier passed to a provider client addresses exactly one resource.

Percent-encoding escapes ``/``, ``?`` and ``#``, but leaves ``.`` alone, and
``httpx`` resolves dot segments before sending. So ``get_charge("..")`` went
out as ``GET /api/v1`` and ``delete_charge("..")`` as ``DELETE /api/v1``,
with the merchant's credentials attached. Every client below runs over a
real :class:`HTTPClient` and an ``httpx.MockTransport`` that records what
would have reached the provider.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.integrations.messaging.zap import ZapClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient
from tempest_fastapi_sdk.integrations.payment.openpix.schemas import (
    WithdrawFromAccountBody,
)
from tempest_fastapi_sdk.integrations.payment.stripe import StripeClient
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    RevenueCatClient,
)
from tempest_fastapi_sdk.utils.http_client import HTTPClient

DOT_SEGMENTS: list[str] = ["", ".", ".."]
"""Identifiers that would collapse or climb the path."""

RESERVED: list[str] = ["../x", "a/b", "a?b=c", "a#b"]
"""Identifiers whose reserved characters must stay inside one segment."""


class _Recorder:
    """Transport double that records every request and answers ``{}``.

    Attributes:
        requests (list[httpx.Request]): Every request that reached it.
    """

    def __init__(self) -> None:
        """Initialize with no recorded requests."""
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer an empty JSON object.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: ``200`` with ``{}``.
        """
        self.requests.append(request)
        return httpx.Response(200, json={})


def _http(recorder: _Recorder, base_url: str) -> HTTPClient:
    """Build an authenticated transport over the recorder.

    Args:
        recorder (_Recorder): The transport double.
        base_url (str): The provider base URL.

    Returns:
        HTTPClient: The client every provider wrapper takes.
    """
    return HTTPClient(
        base_url=base_url,
        default_headers={"Authorization": "secret"},
        transport=httpx.MockTransport(recorder.handler),
    )


Call = Callable[[_Recorder, str], Awaitable[Any]]
"""Issue one provider call with the given identifier."""


def _openpix_get(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``OpenPixClient.get_charge``."""
    client = OpenPixClient(_http(recorder, "https://api.openpix.com.br"))
    return client.get_charge(value)


def _openpix_delete(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``OpenPixClient.delete_charge``."""
    client = OpenPixClient(_http(recorder, "https://api.openpix.com.br"))
    return client.delete_charge(value)


def _openpix_withdraw(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``OpenPixClient.withdraw_from_account``."""
    client = OpenPixClient(_http(recorder, "https://api.openpix.com.br"))
    return client.withdraw_from_account(
        value, body=WithdrawFromAccountBody.model_validate({"value": 1})
    )


def _mercado_pago_get(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``MercadoPagoClient.get_preference``."""
    client = MercadoPagoClient(_http(recorder, "https://api.mercadopago.com"))
    return client.get_preference(value)


def _zap_check(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``ZapClient.check_number``."""
    client = ZapClient(_http(recorder, "http://127.0.0.1:3000"))
    return client.check_number(value)


def _stripe_retrieve(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``StripeClient.customers.retrieve``."""
    return StripeClient(_http(recorder, "https://api.stripe.com")).customers.retrieve(
        value
    )


def _stripe_update(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``StripeClient.payment_intents.update``."""
    client = StripeClient(_http(recorder, "https://api.stripe.com"))
    return client.payment_intents.update(value, {"description": "x"})


def _stripe_delete(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``StripeClient.customers.delete``."""
    return StripeClient(_http(recorder, "https://api.stripe.com")).customers.delete(
        value
    )


def _revenuecat_get(recorder: _Recorder, value: str) -> Awaitable[Any]:
    """Call ``RevenueCatClient.get_subscriber``."""
    client = RevenueCatClient(_http(recorder, "https://api.revenuecat.com/v1"))
    return client.get_subscriber(value)


CALLS: dict[str, tuple[Call, str]] = {
    "openpix.get_charge": (_openpix_get, "/api/v1/charge/"),
    "openpix.delete_charge": (_openpix_delete, "/api/v1/charge/"),
    "openpix.withdraw_from_account": (_openpix_withdraw, "/api/v1/account/"),
    "mercado_pago.get_preference": (_mercado_pago_get, "/checkout/preferences/"),
    "zap.check_number": (_zap_check, "/message/check-number/"),
    "stripe.customers.retrieve": (_stripe_retrieve, "/v1/customers/"),
    "stripe.payment_intents.update": (_stripe_update, "/v1/payment_intents/"),
    "stripe.customers.delete": (_stripe_delete, "/v1/customers/"),
    "revenuecat.get_subscriber": (_revenuecat_get, "/v1/subscribers/"),
}
"""Every call under test, with the path prefix its identifier lives under."""


@pytest.mark.parametrize("name", sorted(CALLS))
@pytest.mark.parametrize("value", DOT_SEGMENTS)
async def test_dot_segments_are_refused_before_sending(name: str, value: str) -> None:
    """``""``, ``.`` and ``..`` raise and nothing reaches the provider."""
    call, _ = CALLS[name]
    recorder = _Recorder()
    with pytest.raises(ValueError):
        await call(recorder, value)
    assert recorder.requests == []


@pytest.mark.parametrize("name", sorted(CALLS))
@pytest.mark.parametrize("value", RESERVED)
async def test_reserved_characters_stay_in_one_segment(name: str, value: str) -> None:
    """The request path is the prefix plus exactly one encoded segment."""
    call, prefix = CALLS[name]
    recorder = _Recorder()
    with contextlib.suppress(Exception):
        await call(recorder, value)
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    raw_path = request.url.raw_path.decode()
    assert request.url.query == b""
    assert request.url.fragment == ""
    assert raw_path.startswith(prefix), raw_path
    segment = raw_path[len(prefix) :].split("/", 1)[0]
    assert "/" not in segment
    assert "?" not in segment
    assert "#" not in segment
    assert ".." not in raw_path.split("/")


async def test_an_ordinary_id_still_goes_through() -> None:
    """The control: a plain id reaches the expected path."""
    recorder = _Recorder()
    await _openpix_get(recorder, "abc")
    assert recorder.requests[0].url.raw_path == b"/api/v1/charge/abc"
