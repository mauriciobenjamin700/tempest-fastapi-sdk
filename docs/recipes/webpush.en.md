# Web Push

VAPID-signed Web Push notifications to browsers via `WebPushDispatcher`.
It wraps the synchronous `pywebpush` library in `asyncio.to_thread` and
surfaces the two errors the application actually handles:
`WebPushGoneError` (HTTP 404/410 — delete the subscription) and
`WebPushError` (any other failure). Requires the `[webpush]` extra
(`pywebpush` + `cryptography`).

## VAPID configuration

`WebPushSettings` ships `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and
`VAPID_SUBJECT`. The **public** key goes to the frontend (in
`pushManager.subscribe`); the **private** key signs each push on the
backend. The `sub` must be a `mailto:` or `https:` URI.

```python
# src/services/notifications.py
from tempest_fastapi_sdk import WebPushDispatcher

from src.core.settings import settings


# settings.webpush_kwargs() -> vapid_private_key + vapid_subject + ttl_seconds
dispatcher = WebPushDispatcher(**settings.webpush_kwargs())
```

## Store the subscription

`WebPushSubscriptionSchema` round-trips the exact JSON the browser's
`PushSubscription.toJSON()` emits (it aliases `expiration_time` ↔
`expirationTime`), so you store inbound subscriptions verbatim and replay
them on dispatch.

```python
# src/api/routers/push.py
from fastapi import APIRouter

from tempest_fastapi_sdk import WebPushSubscriptionSchema

router = APIRouter(prefix="/push", tags=["push"])


@router.post("/subscribe", status_code=201)
async def subscribe(subscription: WebPushSubscriptionSchema) -> dict[str, str]:
    """Persist the subscription sent by the browser's Service Worker."""
    await subscriptions_repo.upsert_by_endpoint(subscription)
    return {"status": "subscribed"}
```

## Send a notification

The `payload` accepts `WebPushPayloadSchema`, `dict`, `str`, or `bytes`
(models and dicts are JSON-encoded). Handle `WebPushGoneError` to prune
the dead subscription from your store.

```python
from tempest_fastapi_sdk import (
    WebPushGoneError,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)


async def notify_order_paid(
    subscription: WebPushSubscriptionSchema,
    order_id: str,
) -> None:
    payload = WebPushPayloadSchema(
        title="Payment confirmed",
        body=f"Order {order_id} approved.",
        icon="/static/icons/order.png",
        data={"orderId": order_id, "url": f"/orders/{order_id}"},
    )
    try:
        await dispatcher.send(subscription, payload)
    except WebPushGoneError:
        await subscriptions_repo.delete_by_endpoint(subscription.endpoint)
```

## Broadcast with automatic pruning

`send_many()` fans out the same payload concurrently (`asyncio.gather`)
and **returns the dead endpoints** (404/410) for you to remove — other
failures are logged, not raised.

```python
async def broadcast(
    subs: list[WebPushSubscriptionSchema],
    payload: WebPushPayloadSchema,
) -> None:
    gone: list[str] = await dispatcher.send_many(subs, payload)
    if gone:
        await subscriptions_repo.delete_by_endpoints(gone)
```

!!! warning "Always prune dead subscriptions"
    Subscriptions expire when the user changes device or revokes the
    permission. Ignoring `WebPushGoneError` / the `send_many` return value
    piles up zombie endpoints and wastes dispatch. Delete them as soon as
    the push service answers 404/410.

## Recap

- Install `[webpush]` and configure `WebPushSettings` (VAPID keys).
- Public key → frontend; private key → signs the pushes on the backend.
- Store `WebPushSubscriptionSchema` verbatim; reuse it on dispatch.
- `send()` for one target, `send_many()` for broadcast (returns the dead ones).
- Handle `WebPushGoneError` (404/410) by pruning the subscription from the store.
