"""A minimal RevenueCat REST v1 client, for reconciliation.

The webhooks page recommends "calling the ``GET /subscribers`` REST API
endpoint after receiving any webhook", so the customer's state always comes
in one format (https://www.revenuecat.com/docs/integrations/webhooks). That
is the one call this client makes.

From the v1 reference (https://www.revenuecat.com/docs/api-v1):

* base URL ``https://api.revenuecat.com/v1``;
* ``Authorization: Bearer <api key>``; ``subscriber_attributes`` comes back
  "only ... with a secret API key" — and a secret key belongs on the server,
  never in the app;
* URL parameters such as ``app_user_id`` must be URL-encoded. App User IDs
  carry characters like ``$`` and ``:`` (``$RCAnonymousID:...``), so this
  client encodes every character outside the unreserved set.

!!! warning "The endpoint is *Get or Create*"
    The Customers reference names it "Get or Create Customer": an
    ``app_user_id`` RevenueCat has never seen is **created** and answered
    with ``201``. Reconcile only ids that came from a webhook or from your own
    users table; a typo here makes a customer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatCustomerInfo,
)
from tempest_fastapi_sdk.utils.http_client import HTTPClient

if TYPE_CHECKING:
    import httpx

    from tempest_fastapi_sdk.utils.retry import RetryPolicy

REVENUECAT_API_BASE_URL: str = "https://api.revenuecat.com/v1"
"""Base URL of the REST API v1, from https://www.revenuecat.com/docs/api-v1."""


def revenuecat_http_client(
    api_key: str,
    *,
    base_url: str = REVENUECAT_API_BASE_URL,
    timeout: float = 10.0,
    retry_policy: RetryPolicy | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HTTPClient:
    """Build the :class:`~tempest_fastapi_sdk.utils.HTTPClient` RevenueCat expects.

    Args:
        api_key (str): A **secret** API key (``sk_...``) from the dashboard.
        base_url (str): API host; override only for a proxy or a recorded
            fixture server.
        timeout (float): Per-request timeout, seconds.
        retry_policy (RetryPolicy | None): Retry behaviour; ``None`` keeps
            the SDK default. The only call is a read, so retrying is safe.
        transport (httpx.AsyncBaseTransport | None): Transport override —
            ``httpx.MockTransport`` in tests.

    Returns:
        HTTPClient: A client with ``Authorization: Bearer <key>`` and JSON
        accept headers set.
    """
    return HTTPClient(
        base_url=base_url,
        timeout=timeout,
        retry_policy=retry_policy,
        default_headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        transport=transport,
    )


class RevenueCatClient:
    """The RevenueCat calls a subscription backend needs from the server.

    Example:
        >>> client = RevenueCatClient(revenuecat_http_client("sk_..."))
        >>> info = await client.get_subscriber("user-1456")
        >>> info.subscriber.entitlements.keys()
    """

    def __init__(self, http: HTTPClient) -> None:
        """Wrap an HTTP client already pointed at the API.

        Args:
            http (HTTPClient): From :func:`revenuecat_http_client`, or
                ``HTTPClient(**settings.revenuecat_kwargs())``.
        """
        self._http: HTTPClient = http

    async def get_subscriber(self, app_user_id: str) -> RevenueCatCustomerInfo:
        """Read a customer's current info (``GET /subscribers/{app_user_id}``).

        Does not send ``X-Platform``: the reference says not to set it "if
        you are calling the getter for informational purposes or using a
        secret API key", because it updates the customer's ``last_seen``.

        Args:
            app_user_id (str): The App User ID (any of the subscriber's
                aliases works).

        Returns:
            RevenueCatCustomerInfo: The customer info. A ``201`` (customer
            just created — see the module warning) parses the same way.

        Raises:
            ValueError: If ``app_user_id`` is empty — the path would collapse
                to ``/subscribers/``.
            httpx.HTTPStatusError: For any non-2xx answer (``401`` for a bad
                key, ``400`` for a malformed id).
        """
        if not app_user_id:
            raise ValueError("app_user_id must not be empty")
        response = await self._http.get(f"/subscribers/{quote(app_user_id, safe='')}")
        response.raise_for_status()
        return RevenueCatCustomerInfo.model_validate(response.json())


__all__: list[str] = [
    "REVENUECAT_API_BASE_URL",
    "RevenueCatClient",
    "revenuecat_http_client",
]
