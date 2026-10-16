"""Stand-in for the RevenueCat REST client.

:class:`FakeRevenueCatClient` answers ``get_subscriber`` from customers you
seed, so a reconciliation flow runs without a RevenueCat project, a secret
key or the network. Asking for an id nobody seeded raises the same
:class:`httpx.HTTPStatusError` type the real client raises for a non-2xx
answer — not a ``201`` with a fresh customer, which is what the real
"Get or Create" endpoint would do: a test that reconciles an unknown id is
usually a test with a bug, and the fake says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.client import (
    REVENUECAT_API_BASE_URL,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatCustomerInfo,
    RevenueCatSubscriber,
)
from tempest_fastapi_sdk.testing.fakes._control import _Steerable


class FakeRevenueCatClient(_Steerable):
    """In-memory stand-in for ``RevenueCatClient``.

    Example:
        >>> fake = FakeRevenueCatClient()
        >>> fake.set_subscriber("user-1", {"entitlements": {}})
        >>> info = await fake.get_subscriber("user-1")

    Attributes:
        calls (list[str]): Methods that ran, in order.
        requested (list[str]): ``app_user_id`` of every ``get_subscriber``.
    """

    def __init__(self) -> None:
        """Start with no customers."""
        super().__init__()
        self._subscribers: dict[str, RevenueCatSubscriber] = {}
        self.requested: list[str] = []

    def set_subscriber(
        self,
        app_user_id: str,
        subscriber: RevenueCatSubscriber | Mapping[str, Any],
    ) -> None:
        """Seed (or replace) the customer ``get_subscriber`` returns.

        Args:
            app_user_id (str): The App User ID the call will ask for.
            subscriber (RevenueCatSubscriber | Mapping[str, Any]): The
                ``subscriber`` object, typed or as the JSON RevenueCat sends.
        """
        self._subscribers[app_user_id] = (
            subscriber
            if isinstance(subscriber, RevenueCatSubscriber)
            else RevenueCatSubscriber.model_validate(dict(subscriber))
        )

    async def get_subscriber(self, app_user_id: str) -> RevenueCatCustomerInfo:
        """Return the seeded customer.

        Args:
            app_user_id (str): The App User ID.

        Returns:
            RevenueCatCustomerInfo: The seeded customer, stamped with the
            current time as ``request_date``.

        Raises:
            ValueError: If ``app_user_id`` is empty, as the real client does.
            httpx.HTTPStatusError: A ``404`` for an id nobody seeded.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("get_subscriber")
        if not app_user_id:
            raise ValueError("app_user_id must not be empty")
        self.requested.append(app_user_id)
        subscriber = self._subscribers.get(app_user_id)
        if subscriber is None:
            request = httpx.Request(
                "GET", f"{REVENUECAT_API_BASE_URL}/subscribers/{app_user_id}"
            )
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(
                f"FakeRevenueCatClient has no subscriber {app_user_id!r}; "
                "seed it with set_subscriber()",
                request=request,
                response=response,
            )
        now = datetime.now(tz=UTC)
        return RevenueCatCustomerInfo(
            request_date=now,
            request_date_ms=int(now.timestamp() * 1000),
            subscriber=subscriber,
        )


__all__: list[str] = ["FakeRevenueCatClient"]
