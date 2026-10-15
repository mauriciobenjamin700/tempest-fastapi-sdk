"""``RevenueCatClient``, ``RevenueCatSettings`` and the fake, over a mock transport.

The response body is the documentation's own sample for
``GET /subscribers/{app_user_id}``
(https://www.revenuecat.com/docs/api-v1/customers). No call reaches
RevenueCat: there is no credential in this repository.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    REVENUECAT_API_BASE_URL,
    RevenueCatClient,
    revenuecat_http_client,
)
from tempest_fastapi_sdk.settings import RevenueCatSettings
from tempest_fastapi_sdk.testing.fakes import FakeRevenueCatClient

DOCUMENTED_RESPONSE: dict[str, Any] = {
    "request_date": "2019-07-26T17:40:10Z",
    "request_date_ms": 1564162810884,
    "subscriber": {
        "entitlements": {
            "pro_cat": {
                "expires_date": None,
                "grace_period_expires_date": None,
                "product_identifier": "onetime",
                "purchase_date": "2019-04-05T21:52:45Z",
            }
        },
        "first_seen": "2019-02-21T00:08:41Z",
        "management_url": "https://apps.apple.com/account/subscriptions",
        "non_subscriptions": {
            "onetime": [
                {
                    "id": "cadba0c81b",
                    "is_sandbox": True,
                    "purchase_date": "2019-04-05T21:52:45Z",
                    "store": "app_store",
                }
            ]
        },
        "original_app_user_id": "XXX-XXXXX-XXXXX-XX",
        "original_application_version": "1.0",
        "original_purchase_date": "2019-01-30T23:54:10Z",
        "other_purchases": {},
        "subscriptions": {
            "onemonth": {
                "auto_resume_date": None,
                "billing_issues_detected_at": None,
                "expires_date": "2019-06-17T22:47:55Z",
                "grace_period_expires_date": None,
                "is_sandbox": True,
                "original_purchase_date": "2019-02-21T00:42:05Z",
                "ownership_type": "PURCHASED",
                "period_type": "normal",
                "purchase_date": "2019-06-17T22:42:55Z",
                "refunded_at": None,
                "store": "app_store",
                "store_transaction_id": 1000000652379790,
                "unsubscribe_detected_at": "2019-06-17T22:48:38Z",
            }
        },
        "subscriber_attributes": {"$email": {"value": "person@example.com"}},
    },
}
"""The response sample of the v1 reference, plus attributes."""


def _client(seen: list[httpx.Request], status: int = 200) -> RevenueCatClient:
    """A client whose transport records requests and answers the sample."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=DOCUMENTED_RESPONSE)

    return RevenueCatClient(
        revenuecat_http_client("sk_test", transport=httpx.MockTransport(handler))
    )


class TestGetSubscriber:
    """The one call the reconciliation needs."""

    async def test_it_reads_the_documented_shape(self) -> None:
        seen: list[httpx.Request] = []

        info = await _client(seen).get_subscriber("user-1456")

        assert info.subscriber.original_app_user_id == "XXX-XXXXX-XXXXX-XX"
        assert info.subscriber.entitlements["pro_cat"].expires_date is None
        assert info.subscriber.subscriptions["onemonth"].store_transaction_id == (
            1000000652379790
        )
        assert info.subscriber.subscriptions["onemonth"].is_sandbox is True

    async def test_it_sends_the_key_and_no_platform_header(self) -> None:
        seen: list[httpx.Request] = []

        await _client(seen).get_subscriber("user-1456")

        (request,) = seen
        assert request.method == "GET"
        assert str(request.url) == f"{REVENUECAT_API_BASE_URL}/subscribers/user-1456"
        assert request.headers["Authorization"] == "Bearer sk_test"
        assert "X-Platform" not in request.headers

    async def test_it_url_encodes_an_anonymous_id(self) -> None:
        seen: list[httpx.Request] = []

        await _client(seen).get_subscriber("$RCAnonymousID:87c6049c/x")

        assert seen[0].url.raw_path == (
            b"/v1/subscribers/%24RCAnonymousID%3A87c6049c%2Fx"
        )

    async def test_attributes_are_readable_but_not_dumped(self) -> None:
        info = await _client([]).get_subscriber("user-1456")

        assert info.subscriber.subscriber_attributes["$email"]["value"]
        assert "subscriber_attributes" not in info.model_dump()["subscriber"]

    async def test_a_non_2xx_raises(self) -> None:
        with pytest.raises(httpx.HTTPStatusError):
            await _client([], status=401).get_subscriber("user-1456")

    async def test_an_empty_id_is_refused(self) -> None:
        with pytest.raises(ValueError):
            await _client([]).get_subscriber("")


class TestSettings:
    """``RevenueCatSettings``: the kwargs mapper and the production guard."""

    def test_kwargs_build_a_working_client(self) -> None:
        settings = RevenueCatSettings(REVENUECAT_API_KEY="sk_live")

        kwargs = settings.revenuecat_kwargs()
        http = HTTPClient(**kwargs)

        assert kwargs["base_url"] == REVENUECAT_API_BASE_URL
        assert kwargs["default_headers"]["Authorization"] == "Bearer sk_live"
        assert isinstance(RevenueCatClient(http), RevenueCatClient)

    def test_an_empty_webhook_secret_is_a_production_violation(self) -> None:
        violations = RevenueCatSettings().production_violations()

        assert any(v.startswith("REVENUECAT_WEBHOOK_SECRET") for v in violations)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"REVENUECAT_WEBHOOK_SECRET": "x"},
            {"REVENUECAT_WEBHOOK_SIGNING_SECRET": "y"},
        ],
    )
    def test_either_webhook_secret_satisfies_the_guard(
        self, kwargs: dict[str, str]
    ) -> None:
        assert RevenueCatSettings(**kwargs).production_violations() == []


class TestFake:
    """``FakeRevenueCatClient`` answers what was seeded and fails on demand."""

    async def test_it_returns_the_seeded_subscriber(self) -> None:
        fake = FakeRevenueCatClient()
        fake.set_subscriber("user-1", DOCUMENTED_RESPONSE["subscriber"])

        info = await fake.get_subscriber("user-1")

        assert info.subscriber.original_app_user_id == "XXX-XXXXX-XXXXX-XX"
        assert fake.requested == ["user-1"]

    async def test_an_unseeded_id_raises_the_real_error_type(self) -> None:
        with pytest.raises(httpx.HTTPStatusError):
            await FakeRevenueCatClient().get_subscriber("nobody")

    async def test_fail_next_raises_once(self) -> None:
        fake = FakeRevenueCatClient()
        fake.set_subscriber("user-1", {})
        fake.fail_next(httpx.ConnectError("down"))

        with pytest.raises(httpx.ConnectError):
            await fake.get_subscriber("user-1")
        assert (await fake.get_subscriber("user-1")).subscriber.entitlements == {}
