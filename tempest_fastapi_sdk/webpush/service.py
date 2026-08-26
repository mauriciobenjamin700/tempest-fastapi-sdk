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
from typing import Any, Final, Generic, TypeVar
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

_DEFAULT_BROADCAST_PAGE_SIZE: Final[int] = 500
"""Rows :meth:`WebPushSubscriptionService.notify_all` loads per batch."""

_DEFAULT_BROADCAST_CONCURRENCY: Final[int] = 32
"""Dispatches :meth:`WebPushSubscriptionService.notify_all` keeps in flight."""

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

    async def list_all(self) -> list[SubscriptionModelT]:
        """Return every stored subscription, across all users.

        The whole table in one list. For delivery prefer
        :meth:`notify_all`, which walks the table in batches instead of
        holding it in memory; this method exists for the caller that
        needs the rows themselves — an export, a count by host, a
        migration.

        Returns:
            list[SubscriptionModelT]: Every subscription (``[]`` when the
            table is empty).
        """
        return await self.repository.list()

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

    async def notify_all(
        self,
        payload: WebPushPayloadSchema | dict[str, Any] | str | bytes,
        *,
        ttl_seconds: int | None = None,
        exclude_endpoints: list[str] | None = None,
        page_size: int = _DEFAULT_BROADCAST_PAGE_SIZE,
        max_concurrency: int | None = _DEFAULT_BROADCAST_CONCURRENCY,
    ) -> int:
        """Send ``payload`` to every stored device, pruning the dead ones.

        The global counterpart of :meth:`notify_user`: same delivery, same
        automatic pruning, no ``user_id`` filter. Use it for the announcement
        that is not about one account — maintenance window, release notice,
        campaign.

        Two things differ from :meth:`notify_user`, and both are about size.
        The table is walked in batches of ``page_size`` rather than loaded
        whole, so memory does not scale with the subscriber base. And the
        dispatch fan-out is bounded by ``max_concurrency``, because every
        dispatch is a request to a push service and thousands at once earn a
        rate limit.

        The walk is cursor-based, not offset-based, precisely because this
        method deletes as it goes: an offset would skip a row for every row
        pruned on an earlier page. Rows created **while** the broadcast runs
        are not visited — the cursor moves from newest to oldest, and a new
        subscription is newer than the page already passed. That is what
        makes the walk terminate.

        Args:
            payload (WebPushPayloadSchema | dict | str | bytes): The
                notification body (same shapes as
                :meth:`WebPushDispatcher.send`).
            ttl_seconds (int | None): Optional TTL override.
            exclude_endpoints (list[str] | None): Push endpoints to skip.
                Excluded devices are never contacted and never pruned.
            page_size (int): Rows loaded per batch. Defaults to ``500``.
            max_concurrency (int | None): Dispatches in flight at once,
                forwarded to :meth:`WebPushDispatcher.send_many`. ``None``
                sends the whole batch at once. Defaults to ``32``.

        Returns:
            int: How many devices the payload was delivered to (targeted
            devices minus the pruned, gone ones).

        Raises:
            ValueError: When ``page_size`` is below ``1``, which would
                either loop forever or read nothing.
        """
        if page_size < 1:
            raise ValueError(f"page_size must be >= 1, got {page_size}.")
        excluded: set[str] = set(exclude_endpoints or ())
        delivered: int = 0
        cursor: str | None = None
        while True:
            page: dict[str, Any] = await self.repository.cursor_paginate(
                cursor=cursor,
                limit=page_size,
            )
            rows: list[SubscriptionModelT] = [
                row for row in page["items"] if row.endpoint not in excluded
            ]
            if rows:
                gone = await self.dispatcher.send_many(
                    [self._to_schema(row) for row in rows],
                    payload,
                    ttl_seconds=ttl_seconds,
                    max_concurrency=max_concurrency,
                )
                if gone:
                    await self.prune(gone)
                delivered += len(rows) - len(gone)
            next_cursor: str | None = page.get("next_cursor")
            if not page.get("has_more") or next_cursor is None:
                return delivered
            cursor = next_cursor

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
