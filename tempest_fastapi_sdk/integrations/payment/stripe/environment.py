"""Where Stripe lives, and which version of it you are talking to.

Two constants and one factory. The interesting one is the version: the
Stripe API is versioned **per account**, and an account upgraded in the
dashboard starts answering with different response shapes to code that
never changed. Sending ``Stripe-Version`` on every request pins the
behaviour to what this SDK was written against, so an upgrade over there
is a decision here rather than an incident.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

if TYPE_CHECKING:
    import httpx

STRIPE_BASE_URL: str = "https://api.stripe.com"
"""``servers[0].url`` from Stripe's OpenAPI specification, without the slash.

There is no separate sandbox host: test mode is selected by the API key
(``sk_test_...``), not by the URL.
"""

STRIPE_API_VERSION: str = "2026-07-29.dahlia"
"""The API version this integration is written against.

Taken from ``info.version`` of the vendored specification facts
(``vendor/stripe-api-facts.yaml``), and pinned into the ``Stripe-Version``
header of every request. ``tests/integrations/payment/stripe`` asserts the
two agree, so refreshing the facts without revisiting the code fails a
test instead of silently changing response shapes in production.
"""

STRIPE_VERSION_HEADER: str = "Stripe-Version"
"""Header carrying the pinned API version."""

STRIPE_IDEMPOTENCY_HEADER: str = "Idempotency-Key"
"""Header Stripe reads to deduplicate a retried write."""


def stripe_http_client(
    api_key: str,
    *,
    base_url: str = STRIPE_BASE_URL,
    api_version: str = STRIPE_API_VERSION,
    timeout: float = 30.0,
    retry_policy: RetryPolicy | None = None,
    extra_headers: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HTTPClient:
    """Build an :class:`HTTPClient` configured for Stripe.

    Args:
        api_key (str): The secret key (``sk_live_...`` / ``sk_test_...``).
            Sent as a bearer token, which is one of the two schemes the
            specification declares.
        base_url (str): API host. Override only to point at a proxy or a
            recorded fixture server.
        api_version (str): Value for the ``Stripe-Version`` header. Change
            it deliberately, after checking what the newer version alters.
        timeout (float): Total per-request timeout in seconds. Defaults to
            ``30.0`` rather than the SDK's usual ``10.0``: card
            authorization legitimately takes longer than a database read,
            and a client-side timeout on a charge leaves the caller not
            knowing whether money moved.
        retry_policy (RetryPolicy | None): Retry behaviour. ``None`` keeps
            the SDK default. Retries are safe on Stripe writes **only**
            with an idempotency key, which
            :class:`~tempest_fastapi_sdk.integrations.payment.stripe.StripeClient`
            attaches.
        extra_headers (Mapping[str, str] | None): Additional default
            headers — ``Stripe-Account`` for a Connect platform acting on
            behalf of a connected account, for instance.
        transport (httpx.AsyncBaseTransport | None): Transport override.
            Pass an ``httpx.MockTransport`` to exercise the whole client —
            headers, encoding and all — without a network call.

    Returns:
        HTTPClient: A client with authentication, the pinned version and
        JSON accept headers already set.
    """
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        STRIPE_VERSION_HEADER: api_version,
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return HTTPClient(
        base_url=base_url,
        timeout=timeout,
        retry_policy=retry_policy,
        default_headers=headers,
        transport=transport,
    )


__all__: list[str] = [
    "STRIPE_API_VERSION",
    "STRIPE_BASE_URL",
    "STRIPE_IDEMPOTENCY_HEADER",
    "STRIPE_VERSION_HEADER",
    "stripe_http_client",
]
