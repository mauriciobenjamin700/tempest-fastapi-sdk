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

## Table + service (recommended)

To store the user's devices and deliver with automatic pruning, the SDK
ships the **base table** `BaseWebPushSubscriptionModel` (one row per
device, unique `endpoint`) and the **base service**
`WebPushSubscriptionService` (saves, removes and sends, pruning dead ones
itself). Like the auth pattern, the SDK provides the abstract row and the
project creates the concrete table with the FK to its `UserModel`:

```python
# src/db/models/web_push_subscription.py
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscriptionModel(BaseWebPushSubscriptionModel):
    """A user device's Web Push subscription."""

    __tablename__ = "web_push_subscriptions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

Build the service from a `BaseRepository` over the table + the VAPID
dispatcher:

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import BaseRepository, WebPushSubscriptionService

from src.core.settings import settings
from src.db.models import WebPushSubscriptionModel


def get_push_service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = BaseRepository(session, model=WebPushSubscriptionModel)
    dispatcher = WebPushDispatcher(**settings.webpush_kwargs())
    return WebPushSubscriptionService(repo, dispatcher)
```

The service exposes:

| Method | What it does |
| --- | --- |
| `subscribe(user_id, subscription, *, user_agent=None)` | Persist the subscription, **idempotent by `endpoint`** — re-subscribe updates, never duplicates. |
| `unsubscribe(endpoint)` | Remove the subscription (no-op when absent). |
| `list_for_user(user_id)` | List the user's devices. |
| `notify_user(user_id, payload)` | Send to every device and **prune the dead ones** (404/410) before returning. Returns how many received it. |

## Aligned with tempest-react-sdk

`tempest-react-sdk`'s [`WebPushClient`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
calls `onSubscribe(subscription)` / `onUnsubscribe(subscription)` with the
raw `PushSubscription.toJSON()`. That JSON *is* the
`WebPushSubscriptionSchema` (it aliases `expiration_time` ↔
`expirationTime`), so the frontend hits these endpoints directly:

```python
# src/api/routers/push.py
from fastapi import APIRouter, Depends, status

from tempest_fastapi_sdk import WebPushSubscriptionSchema, WebPushSubscriptionService

router = APIRouter(prefix="/api/push", tags=["push"])


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    subscription: WebPushSubscriptionSchema,
    user: CurrentUser,                       # your auth dependency
    service: WebPushSubscriptionService = Depends(get_push_service),
) -> dict[str, str]:
    """Receive the WebPushClient onSubscribe and persist the device."""
    await service.subscribe(user.id, subscription)
    return {"status": "subscribed"}


@router.post("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe(
    subscription: WebPushSubscriptionSchema,
    service: WebPushSubscriptionService = Depends(get_push_service),
) -> dict[str, str]:
    """Receive the onUnsubscribe and remove the device."""
    await service.unsubscribe(subscription.endpoint)
    return {"status": "unsubscribed"}
```

### Ready-made router (opt-in)

Don't want to write the two endpoints? `make_web_push_router` wires
`/subscribe` + `/unsubscribe` onto the service for you — `make_auth_router`
style. You only inject how the service and current user are resolved:

```python
# src/api/app.py
from tempest_fastapi_sdk import BaseRepository, WebPushSubscriptionService, make_web_push_router

from src.api.dependencies import get_current_user_id, get_session
from src.core.settings import settings
from src.db.models import WebPushSubscriptionModel


def _service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = BaseRepository(session, model=WebPushSubscriptionModel)
    return WebPushSubscriptionService(repo, WebPushDispatcher(**settings.webpush_kwargs()))


app.include_router(
    make_web_push_router(
        service_factory=_service,
        session_factory=get_session,
        current_user_id=get_current_user_id,   # dependency -> UUID
    )
)
# POST /api/push/subscribe   (201) and  POST /api/push/unsubscribe (200)
```

The request `User-Agent` becomes the device label (`store_user_agent=True`,
the default). Both endpoints require authentication via `current_user_id`.

Notify a user (all devices, automatic pruning built in):

```python
delivered: int = await service.notify_user(
    user.id,
    {"title": "Payment confirmed", "body": "Order approved."},
)
```

## Send a notification (dispatcher directly)

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
- `BaseWebPushSubscriptionModel` table (one row per device, unique `endpoint`) + `WebPushSubscriptionService` (`subscribe`/`unsubscribe`/`notify_user`) — the recommended path, with automatic pruning.
- The `WebPushClient` JSON (tempest-react-sdk) *is* the `WebPushSubscriptionSchema` — `subscribe`/`unsubscribe` map directly.
- `make_web_push_router` mounts ready `/subscribe` + `/unsubscribe` (auth-router style) if you'd rather not write the routes.
- Low-level path: `send()` for one target, `send_many()` for broadcast (returns the dead ones); handle `WebPushGoneError` (404/410) by pruning the store.
