# Web Push

VAPID-signed Web Push notifications to browsers via `WebPushDispatcher`.
It wraps the synchronous `pywebpush` library in `asyncio.to_thread` and
surfaces the two errors the application actually handles:
`WebPushGoneError` (HTTP 404/410 — delete the subscription) and
`WebPushError` (any other failure). Requires the `[webpush]` extra
(`pywebpush` + `cryptography`).

!!! info "What this guide follows"
    The SDK ships the pieces (base table, dispatcher, service, schema); the
    project assembles them in the layered
    **router → controller → service → repository** architecture. Every block
    below carries the **file path** at the top and the explanation right
    after, so you can paste it straight into the right place.

## VAPID configuration

`WebPushSettings` ships `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and
`VAPID_SUBJECT`. The **public** key goes to the frontend (in
`pushManager.subscribe`); the **private** key signs each push on the
backend. The `sub` must be a `mailto:` or `https:` URI.

!!! tip "Generating a VAPID keypair"
    You generate the pair once and reuse it across every environment.
    With `pywebpush` (the `[webpush]` extra) installed:

    ```bash
    vapid --gen
    ```

    This writes `private_key.pem` + `public_key.pem` and prints the
    public key as url-safe base64 (the frontend's `applicationServerKey`).
    Without Python around, Node's `web-push` does the same:

    ```bash
    npx web-push generate-vapid-keys
    ```

    The output shows **Public Key** and **Private Key**: map
    `Public Key` → `VAPID_PUBLIC_KEY` and `Private Key` →
    `VAPID_PRIVATE_KEY`. Set `VAPID_SUBJECT` to a `mailto:` or `https:`
    URI of yours.

The dispatcher is an **infrastructure singleton**: built once and reached
everywhere through `Depends`, alongside the other resources (database,
storage, cache). Build it **lazily** (only on first use), like the SSE
broker — so an app without valid VAPID keys (a test, or a service that
doesn't use push) never fails at import time.

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import WebPushDispatcher

from src.core.settings import settings

_webpush_dispatcher: WebPushDispatcher | None = None


def get_webpush_dispatcher() -> WebPushDispatcher:
    """Return the shared VAPID dispatcher, built once on first use.

    ``settings.webpush_kwargs()`` yields ``vapid_private_key``,
    ``vapid_subject`` and ``ttl_seconds``.
    """
    global _webpush_dispatcher
    if _webpush_dispatcher is None:
        _webpush_dispatcher = WebPushDispatcher(**settings.webpush_kwargs())
    return _webpush_dispatcher
```

## Table, repository, service and controller (recommended)

To store the user's devices and deliver with automatic pruning, the SDK
ships the **base table** `BaseWebPushSubscriptionModel` (one row per
device, unique `endpoint`) and the **base service**
`WebPushSubscriptionService` (saves, removes and sends, pruning dead ones
itself). We assemble the four layers in the order a request crosses them.

### 1. Model — the concrete table

Like the auth pattern, the SDK provides the abstract row and the project
creates the concrete table with the FK to its `UserModel`:

```python
# src/db/models/webpush.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscriptionModel(BaseWebPushSubscriptionModel):
    """A user device's Web Push subscription (one row per device)."""

    __tablename__ = "web_push_subscriptions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

`BaseWebPushSubscriptionModel` already brings `endpoint` (unique +
indexed), `p256dh`, `auth`, `expiration_time` and `user_agent`; you only
add the `user_id` FK. Remember to generate a migration for the new table.

### 2. Repository — a typed `BaseRepository` subclass

Following the project's repository pattern, create a concrete subclass
(instead of instantiating a bare `BaseRepository`) to get a named type
the DI and the tests reference:

```python
# src/db/repositories/webpush.py
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import BaseRepository

from src.db.models import WebPushSubscriptionModel


class WebPushSubscriptionRepository(BaseRepository[WebPushSubscriptionModel]):
    """CRUD repository for the WebPushSubscriptionModel."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session=session, model=WebPushSubscriptionModel)
```

### 3. Service — the SDK's, no wrapper

The SDK's `WebPushSubscriptionService` already implements
`subscribe`/`unsubscribe`/`list_for_user`/`notify_user` over a
`BaseRepository` + `WebPushDispatcher`. Since there is no extra logic to
add, use it **directly** — no pass-through subclass. Re-export it from the
services package to keep a single import point:

```python
# src/services/__init__.py
from tempest_fastapi_sdk import WebPushSubscriptionService

# ... the project's other services ...

__all__: list[str] = [
    # ...
    "WebPushSubscriptionService",
]
```

The service exposes:

| Method | What it does |
| --- | --- |
| `subscribe(user_id, subscription, *, user_agent=None)` | Persist the subscription, **idempotent by `endpoint`** — re-subscribe updates, never duplicates. |
| `unsubscribe(endpoint)` | Remove the subscription (no-op when absent). |
| `list_for_user(user_id)` | List the user's devices. |
| `notify_user(user_id, payload)` | Send to every device and **prune the dead ones** (404/410) before returning. Returns how many received it. |

### 4. Controller — the thin policy layer

The controller keeps the `router → controller → service` graph uniform and
is where application policy lives — here, the active-user gate
(`require_active`) before delegating:

```python
# src/controllers/webpush.py
from tempest_fastapi_sdk import WebPushSubscriptionSchema, require_active

from src.db.models import UserModel, WebPushSubscriptionModel
from src.services import WebPushSubscriptionService


class WebPushController:
    """Web Push subscription controller (auth gate + delegation)."""

    def __init__(
        self, service: WebPushSubscriptionService[WebPushSubscriptionModel]
    ) -> None:
        self.service = service

    async def subscribe(
        self,
        user: UserModel,
        subscription: WebPushSubscriptionSchema,
        *,
        user_agent: str | None = None,
    ) -> None:
        """Persist the authenticated user's device (idempotent by endpoint)."""
        require_active(user)
        await self.service.subscribe(user.id, subscription, user_agent=user_agent)

    async def unsubscribe(self, subscription: WebPushSubscriptionSchema) -> None:
        """Remove the device by endpoint (no-op when absent)."""
        await self.service.unsubscribe(subscription.endpoint)
```

### 5. DI providers — one layer per file

Each layer gets its own `Depends` provider, in the matching file. The
`session` **always** comes via `Depends(get_session)` (otherwise FastAPI
tries to resolve it as a request parameter):

```python
# src/api/dependencies/repositories.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import WebPushSubscriptionRepository

from .resources import get_session


def get_webpush_subscription_repository(
    session: AsyncSession = Depends(get_session),
) -> WebPushSubscriptionRepository:
    """Subscription repository bound to the request session."""
    return WebPushSubscriptionRepository(session=session)
```

```python
# src/api/dependencies/services.py
from fastapi import Depends
from tempest_fastapi_sdk import WebPushDispatcher

from src.db.models import WebPushSubscriptionModel
from src.db.repositories import WebPushSubscriptionRepository
from src.services import WebPushSubscriptionService

from .repositories import get_webpush_subscription_repository
from .resources import get_webpush_dispatcher


def get_webpush_service(
    webpush_repository: WebPushSubscriptionRepository = Depends(
        get_webpush_subscription_repository
    ),
    dispatcher: WebPushDispatcher = Depends(get_webpush_dispatcher),
) -> WebPushSubscriptionService[WebPushSubscriptionModel]:
    """Pair the per-request repository with the shared dispatcher."""
    return WebPushSubscriptionService(webpush_repository, dispatcher)
```

```python
# src/api/dependencies/controllers.py
from fastapi import Depends

from src.controllers import WebPushController
from src.db.models import WebPushSubscriptionModel
from src.services import WebPushSubscriptionService

from .services import get_webpush_service


def get_webpush_controller(
    webpush_service: WebPushSubscriptionService[WebPushSubscriptionModel] = Depends(
        get_webpush_service
    ),
) -> WebPushController:
    """Build the controller with the request's service."""
    return WebPushController(service=webpush_service)
```

### 6. Router — aligned with tempest-react-sdk

`tempest-react-sdk`'s [`WebPushClient`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
calls `onSubscribe(subscription)` / `onUnsubscribe(subscription)` with the
raw `PushSubscription.toJSON()`. That JSON *is* the
`WebPushSubscriptionSchema` (it aliases `expiration_time` ↔
`expirationTime`), so the frontend hits these endpoints directly. The
router receives the **controller** via `Depends`, the user via your auth
dependency, and uses a **bare prefix** (`/webpush`) — `/api` is applied at
the aggregate mount:

```python
# src/api/routers/webpush.py
from fastapi import APIRouter, Depends, Request, status
from tempest_fastapi_sdk import WebPushSubscriptionSchema

from src.api.dependencies import get_current_user, get_webpush_controller
from src.controllers import WebPushController
from src.db.models import UserModel

router = APIRouter(prefix="/webpush", tags=["webpush"])


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    subscription: WebPushSubscriptionSchema,
    request: Request,
    user: UserModel = Depends(get_current_user),
    controller: WebPushController = Depends(get_webpush_controller),
) -> dict[str, str]:
    """Receive the onSubscribe and persist the device, labelling by User-Agent."""
    await controller.subscribe(
        user, subscription, user_agent=request.headers.get("user-agent")
    )
    return {"status": "subscribed"}


@router.post("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe(
    subscription: WebPushSubscriptionSchema,
    controller: WebPushController = Depends(get_webpush_controller),
) -> dict[str, str]:
    """Receive the onUnsubscribe and remove the device."""
    await controller.unsubscribe(subscription)
    return {"status": "unsubscribed"}
```

### 7. Registration — under `/api`

Include the router in the business aggregate (which `app.py` mounts with
`prefix="/api"`), just like the other domains:

```python
# src/api/routers/__init__.py
from fastapi import APIRouter

from .webpush import router as webpush_router

router = APIRouter()

# ... the other routers ...
router.include_router(webpush_router)
# effective: POST /api/webpush/subscribe (201) and POST /api/webpush/unsubscribe (200)
```

!!! note "Final route"
    Since the router prefix is bare (`/webpush`) and the aggregate is
    mounted under `/api`, hardcoding `/api/webpush` in
    `APIRouter(prefix=...)` would double the segment (`/api/api/webpush`).
    Keep `/api` only at the mount.

Notify a user (all devices, automatic pruning built in) — from any
service/controller that holds the `WebPushSubscriptionService`:

```python
delivered: int = await service.notify_user(
    user.id,
    {"title": "Payment confirmed", "body": "Order approved."},
)
```

### Ready-made router (opt-in, bypasses the layers)

For a quick prototype, `make_web_push_router` mounts `/subscribe` +
`/unsubscribe` already wired to the service — `make_auth_router` style. It
**skips the controller** and wires the router straight to the service, so
prefer the layers above in production apps; use this only as a shortcut:

```python
# src/api/app.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
)

from src.api.dependencies import get_current_user_id, get_session
from src.core.settings import settings
from src.db.models import WebPushSubscriptionModel
from src.db.repositories import WebPushSubscriptionRepository


def _service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = WebPushSubscriptionRepository(session)
    return WebPushSubscriptionService(
        repo, WebPushDispatcher(**settings.webpush_kwargs())
    )


app.include_router(
    make_web_push_router(
        service_factory=_service,
        session_factory=get_session,
        current_user_id=get_current_user_id,   # dependency -> UUID
    )
)
# POST /api/push/subscribe (201) and POST /api/push/unsubscribe (200)
```

The request `User-Agent` becomes the device label (`store_user_agent=True`,
the default). Both endpoints require authentication via `current_user_id`.

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
- The dispatcher is an infra singleton in `resources.py`, built lazily via `get_webpush_dispatcher`.
- Assemble the layers: **model** (FK to your user) → **repository** (`BaseRepository` subclass) → **service** (the SDK's, no wrapper) → **controller** (`require_active` gate) → **providers** (one per file, `session` via `Depends`) → **router** (bare prefix, controller via `Depends`) → registration under `/api`.
- The `WebPushClient` JSON (tempest-react-sdk) *is* the `WebPushSubscriptionSchema` — `subscribe`/`unsubscribe` map directly.
- `make_web_push_router` is an opt-in shortcut that **skips the controller** — good for a prototype, not for a layered app.
- Low-level path: `send()` for one target, `send_many()` for broadcast (returns the dead ones); handle `WebPushGoneError` (404/410) by pruning the store.
