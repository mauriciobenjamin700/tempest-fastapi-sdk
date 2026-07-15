"""Persistence + delivery service for Web Push subscriptions.

Bridges the three pieces the SDK already ships — the
:class:`~tempest_fastapi_sdk.db.BaseWebPushSubscriptionModel` table, the
:class:`~tempest_fastapi_sdk.webpush.WebPushDispatcher`, and the
:class:`~tempest_fastapi_sdk.webpush.WebPushSubscriptionSchema` wire
shape — into the two operations every app needs:

* **subscribe** — persist (idempotently, keyed by endpoint) what the
  browser produced and ``tempest-react-sdk``'s ``WebPushClient`` POSTed.
* **notify** — fan a payload out to all of a user's devices and
  **automatically prune** the ones the push service reports as gone
  (HTTP 404/410), so dead devices never pile up.
"""

from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar
from uuid import UUID

from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.db.webpush_subscription_model import (
    BaseWebPushSubscriptionModel,
)
from tempest_fastapi_sdk.webpush.dispatcher import WebPushDispatcher
from tempest_fastapi_sdk.webpush.schemas import (
    WebPushKeysSchema,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)

logger = logging.getLogger(__name__)

SubscriptionModelT = TypeVar(
    "SubscriptionModelT",
    bound=BaseWebPushSubscriptionModel,
)


class WebPushSubscriptionService(Generic[SubscriptionModelT]):
    """Store, prune and deliver Web Push subscriptions.

    Generic over the concrete subscription model so it returns the
    project's own rows. Pair a :class:`BaseRepository` bound to that
    model with a configured :class:`WebPushDispatcher`.

    Generic parameters:
        SubscriptionModelT: The concrete
            :class:`BaseWebPushSubscriptionModel` subclass.

    Attributes:
        repository (BaseRepository[SubscriptionModelT]): Data access for
            the subscription table.
        dispatcher (WebPushDispatcher): VAPID-signed sender.
    """

    def __init__(
        self,
        repository: BaseRepository[SubscriptionModelT],
        dispatcher: WebPushDispatcher,
    ) -> None:
        """Initialize the service.

        Args:
            repository (BaseRepository[SubscriptionModelT]): Repository
                bound to the concrete subscription model.
            dispatcher (WebPushDispatcher): Configured dispatcher.
        """
        self.repository: BaseRepository[SubscriptionModelT] = repository
        self.dispatcher: WebPushDispatcher = dispatcher

    async def subscribe(
        self,
        user_id: UUID,
        subscription: WebPushSubscriptionSchema,
        *,
        user_agent: str | None = None,
    ) -> SubscriptionModelT:
        """Persist a subscription, idempotently keyed by ``endpoint``.

        A device that re-subscribes (or a subscription that moves to a
        new user) updates the existing row instead of creating a
        duplicate — the unique ``endpoint`` is the device identity.

        Args:
            user_id (UUID): The user that owns the device.
            subscription (WebPushSubscriptionSchema): The browser
                ``PushSubscription.toJSON()`` payload.
            user_agent (str | None): Optional device label to store.

        Returns:
            SubscriptionModelT: The persisted (created or updated) row.
        """
        existing = await self.repository.get_or_none(
            {"endpoint": subscription.endpoint},
        )
        if existing is not None:
            existing.user_id = user_id
            existing.p256dh = subscription.keys.p256dh
            existing.auth = subscription.keys.auth
            existing.expiration_time = subscription.expiration_time
            if user_agent is not None:
                existing.user_agent = user_agent
            return await self.repository.update(existing)

        row = self.repository.model(
            user_id=user_id,
            endpoint=subscription.endpoint,
            p256dh=subscription.keys.p256dh,
            auth=subscription.keys.auth,
            expiration_time=subscription.expiration_time,
            user_agent=user_agent,
        )
        return await self.repository.add(row)

    async def unsubscribe(self, endpoint: str) -> bool:
        """Remove the subscription with ``endpoint``, if present.

        Idempotent: removing an endpoint that is not stored is a no-op.

        Args:
            endpoint (str): The push endpoint to drop.

        Returns:
            bool: ``True`` when a row was deleted, ``False`` when none
            matched.
        """
        existing = await self.repository.get_or_none({"endpoint": endpoint})
        if existing is None:
            return False
        await self.repository.delete(existing.id)
        return True

    async def list_for_user(self, user_id: UUID) -> list[SubscriptionModelT]:
        """Return every stored subscription for a user.

        Args:
            user_id (UUID): The user whose devices to list.

        Returns:
            list[SubscriptionModelT]: The user's subscriptions (``[]``
            when the user has none).
        """
        return await self.repository.list(filters={"user_id": user_id})

    async def prune(self, endpoints: list[str]) -> int:
        """Delete the subscriptions matching ``endpoints``.

        Args:
            endpoints (list[str]): Push endpoints to remove (typically
                the gone list returned by :meth:`notify_user`).

        Returns:
            int: The number of rows actually deleted.
        """
        deleted = 0
        for endpoint in endpoints:
            if await self.unsubscribe(endpoint):
                deleted += 1
        return deleted

    async def notify_user(
        self,
        user_id: UUID,
        payload: WebPushPayloadSchema | dict[str, Any] | str | bytes,
        *,
        ttl_seconds: int | None = None,
        exclude_endpoints: list[str] | None = None,
    ) -> int:
        """Send ``payload`` to every device a user subscribed, pruning dead ones.

        Subscriptions the push service reports as gone (HTTP 404/410) are
        deleted from the store before returning, so stale devices never
        accumulate.

        Pass ``exclude_endpoints`` to skip specific devices — the common
        case being a multi-device sync notification where the device that
        made the change must not notify itself. Excluded devices are never
        contacted and never pruned.

        Args:
            user_id (UUID): The recipient user.
            payload (WebPushPayloadSchema | dict | str | bytes): The
                notification body (same shapes as
                :meth:`WebPushDispatcher.send`).
            ttl_seconds (int | None): Optional TTL override.
            exclude_endpoints (list[str] | None): Push endpoints to skip
                (e.g. the originating device). ``None`` sends to all.

        Returns:
            int: How many devices the payload was delivered to (targeted
            devices minus the pruned, gone ones).
        """
        rows = await self.list_for_user(user_id)
        if exclude_endpoints:
            excluded = set(exclude_endpoints)
            rows = [row for row in rows if row.endpoint not in excluded]
        if not rows:
            return 0
        subscriptions = [self._to_schema(row) for row in rows]
        gone = await self.dispatcher.send_many(
            subscriptions,
            payload,
            ttl_seconds=ttl_seconds,
        )
        if gone:
            await self.prune(gone)
        return len(rows) - len(gone)

    @staticmethod
    def _to_schema(row: BaseWebPushSubscriptionModel) -> WebPushSubscriptionSchema:
        """Map a stored row to the dispatcher's wire schema.

        Args:
            row (BaseWebPushSubscriptionModel): The persisted row.

        Returns:
            WebPushSubscriptionSchema: The equivalent subscription
            schema the dispatcher accepts.
        """
        return WebPushSubscriptionSchema(
            endpoint=row.endpoint,
            keys=WebPushKeysSchema(p256dh=row.p256dh, auth=row.auth),
            expiration_time=row.expiration_time,
        )


__all__: list[str] = [
    "WebPushSubscriptionService",
]
