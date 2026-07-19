# Integrated example — Pix checkout

The [Tour](tour.md) shows each piece in isolation. Here they work
**together** in a real flow: an authenticated customer pays an order via
Pix, and the system prices it with a cache, writes the order + event in the
**same transaction** (outbox), fires a background email, and notifies the
customer in real time over **SSE + Web Push** — all with SDK blocks.

Components exercised at once: **settings + db**, **auth (JWT)**,
**validated fields + PixKeyField**, **cache (`@cached`)**, **repository +
service**, **transactional outbox**, **MessageBroker**, **TaskQueue**, **SSE**
and **Web Push**.

## 1. Resources (one place)

```python
# src/core/resources.py
from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.sse import SSEBroker
from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

db = AsyncDatabaseManager(settings.DATABASE_URL)
cache = AsyncRedisManager(settings.REDIS_URL)
mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)      # cross-service events
tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)     # work off the request
events = SSEBroker(redis=cache.client)                  # real-time status
```

They all start/stop in the lifespan (`connect`/`disconnect`) — see the
[Tutorial](tutorial.md) and the [Safe deploy](recipes/deploy-safety.md) recipe.

## 2. Checkout schema (self-validating fields)

```python
# src/schemas/checkout.py
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import PixKeyField, PositiveIntField


class CheckoutSchema(BaseSchema):
    product_id: str
    quantity: PositiveIntField        # > 0, else 422
    pix_key: PixKeyField              # validates CPF/CNPJ/email/phone/random
```

## 3. Price with a cache

The product rarely changes; cache the read and invalidate on write.

```python
# src/services/catalog.py
from tempest_fastapi_sdk.cache import CacheInvalidator, cached

from src.core.resources import cache


@cached(cache, ttl=300, key_prefix="catalog:", namespace="products")
async def get_product_cents(product_id: str) -> int:
    """Unit price in cents; 5-minute cache."""
    return await load_price_from_db(product_id)


async def invalidate_product(product_id: str) -> None:
    await CacheInvalidator(cache, key_prefix="catalog:").invalidate_namespace("products")
```

## 4. Service: write order + event in one transaction (outbox)

Writing the order and publishing "order paid" as two separate operations is
unsafe. Write the order row **and** the outbox row together —
`save_with_outbox` does it in a single transaction.

```python
# src/services/orders.py
from src.core.resources import db
from src.db.models import OrderModel, OutboxModel
from src.services.catalog import get_product_cents


class OrderService:
    async def checkout(self, *, user_id: str, data: CheckoutSchema) -> OrderModel:
        unit_cents = await get_product_cents(data.product_id)     # cache
        total = unit_cents * data.quantity

        async with db.get_session_context() as session:
            repo = OrderRepository(session)
            order = OrderModel(
                user_id=user_id,
                product_id=data.product_id,
                total_cents=total,
                pix_key=data.pix_key,
                status="paid",
            )
            # order + outbox event commit together (or neither):
            await repo.save_with_outbox(
                order,
                OutboxModel.new_event(
                    "orders.paid",
                    {"order_id": str(order.id), "user_id": user_id, "total": total},
                ),
            )
        return order
```

## 5. Authenticated endpoint

The user comes from the JWT; an invalid payload never reaches here (422
automatically).

```python
# src/api/routers/checkout.py
from fastapi import APIRouter, Depends

from src.api.dependencies import current_user, get_order_service

router = APIRouter(prefix="/api/checkout")


@router.post("")
async def checkout(
    data: CheckoutSchema,
    user: UserModel = Depends(current_user),        # JWT (header/cookie/query)
    service: OrderService = Depends(get_order_service),
) -> dict[str, str]:
    order = await service.checkout(user_id=str(user.id), data=data)
    return {"order_id": str(order.id), "status": order.status}
```

`current_user` comes from `make_jwt_user_dependency` / `UserAuthService` —
see [Auth flow](recipes/auth-flow.md).

## 6. Outbox relay → publishes to the broker

One process drains the outbox and publishes to the `MessageBroker` (with
backoff and locking). The relay's `publish` plugs straight in:

```python
# src/tasks/relay.py
from tempest_fastapi_sdk import OutboxRelay

from src.core.resources import db, mq
from src.db.models import OutboxModel

relay = OutboxRelay(db, model=OutboxModel,
                    publish=lambda e: mq.publish(e.topic, e.payload))
# asyncio.create_task(relay.run()) in the lifespan (or a dedicated process)
```

## 7. Consumer reacts: background email + SSE push

Whoever listens to "orders.paid" fires the email (TaskQueue, off the
request) and pushes the status to the user's SSE channel.

```python
# src/queue/consumers.py
from src.core.resources import events, mq, tq
from src.schemas.events import OrderPaid


@tq.task
async def send_receipt(to: str, order_id: str) -> None:
    await email.send(to, "Receipt", f"Order {order_id} paid.")


@mq.on("orders.paid")
async def on_order_paid(event: OrderPaid) -> None:
    await send_receipt.enqueue(to=event.user_email, order_id=event.order_id)   # background
    await events.publish(event.user_id, {"order_id": event.order_id, "status": "paid"},
                         event="order_update")                                 # SSE
```

## 8. Live payment notification (SSE + Web Push)

Section 7 pushed the status with a raw `events.publish` — perfect with the tab
**open**. But the Pix confirmation is a domain event that deserves **both**
channels: SSE for whoever is online and **Web Push (VAPID)** for whoever closed
the app.

**What "fan-out" means here:** the same business event goes out over two
different transports in the same call. The **same payload** (`data`) goes to SSE
(arrives instantly if the app is open) **and** to Web Push (arrives even with the
app closed, via a Service Worker). A `NotificationService` wraps that fan-out in
a single `notify` function — the caller never has to know there are two channels.

### The notification service

```python
# src/services/notification.py
from uuid import UUID

from tempest_fastapi_sdk import (
    SSEBroker,
    WebPushPayloadSchema,
    WebPushSubscriptionService,
)


class NotificationService:
    """Fan one domain event out to SSE (foreground) and Web Push (background)."""

    def __init__(self, broker: SSEBroker, push: WebPushSubscriptionService) -> None:
        self.broker = broker
        self.push = push

    async def notify(
        self, user_id: UUID, event: str, title: str, body: str, data: dict
    ) -> None:
        """Deliver the same event over SSE (live) and Web Push (closed app)."""
        await self.broker.publish(str(user_id), data, event=event)
        await self.push.notify_user(
            user_id,
            WebPushPayloadSchema(title=title, body=body, tag=event, data=data),
        )
```

The body of `notify` is **two lines**, one per channel:

- **`self.broker.publish(str(user_id), data, event=event)`** — the **live**
  channel (SSE). The 1st argument is the **channel** (the user id as a string);
  the 2nd is the **payload** (`data`, serialized to JSON); `event=` is the
  **name** of the event the front listens for with `addEventListener`. Reaches
  only whoever is **connected right now**.
- **`self.push.notify_user(user_id, WebPushPayloadSchema(...))`** — the
  **background** channel (Web Push). `notify_user` looks up that user's VAPID
  subscriptions and sends the push to each device; `WebPushPayloadSchema` carries
  the `title`/`body` the Service Worker shows as a system notification, plus the
  same `data` (and `tag=event` to group/replace notifications of the same kind).

Note both get the **same `data`** — that's what keeps the channels in sync. Wire
`notifications` with the global `events` (section 1) and a
`WebPushSubscriptionService(BaseRepository(session, PushSubscriptionModel), dispatcher)`
— the `dispatcher` comes from `WebPushDispatcher(**settings.webpush_kwargs())`.

### Triggering from the handler

On confirmation, section 7's handler swaps the raw `events.publish` for a single
`notify`:

```python
# src/queue/consumers.py
@mq.on("orders.paid")
async def on_order_paid(event: OrderPaid) -> None:
    await send_receipt.enqueue(to=event.user_email, order_id=event.order_id)   # background
    await notifications.notify(                                                # SSE + Web Push
        event.user_id,
        event="payment_confirmed",
        title="Payment approved",
        body=f"Order {event.order_id} confirmed.",
        data={"order_id": event.order_id},
    )
```

The handler **just calls `notify`** — it never touches `EventStream`, an HTTP
response, or a Web Push subscription. The whole "which channels this goes out on"
decision lives inside `NotificationService`; the handler only describes **what**
to notify (title, body, `data`), not **how** to deliver it.

### The subscribe endpoint (SSE)

Whoever has the app open subscribes to the channel over SSE. One line does it
all: `broker.response(channel)`.

```python
# src/api/routers/notifications.py
@router.get("/notifications/stream")
async def stream(user: UserModel = Depends(current_user)) -> StreamingResponse:
    return notifications.broker.response(str(user.id))
```

Step by step, what happens on each `GET /notifications/stream`:

1. `Depends(current_user)` resolves **who** the client is from the JWT. Their id
   becomes the channel name — the **same** id `notify` uses in
   `broker.publish(str(user_id), ...)`, so both sides agree on the same string.
2. `broker.response(str(user.id))` does **three things in one call**:
     - **register** — creates a fresh `EventStream` and subscribes it to the
       user's channel;
     - **stream** — returns a `StreamingResponse` with the SSE headers already
       set (the client starts receiving);
     - **unregister** — wires an `on_disconnect` that removes this stream from the
       channel when the client drops, with no hand-written `try/finally`.

When `notify` runs `broker.publish` on that channel, the frame lands on the open
stream:

```text
event: payment_confirmed
id: 42
data: {"order_id": "ord_123"}
```

### The Web Push subscriptions

The register/unregister of Web Push (VAPID) subscriptions — enrolling each device
to receive push with the app closed — is mounted via `make_web_push_router(...)`.
The concrete model (`PushSubscriptionModel`) and the router live in the
[Web Push](recipes/webpush.md) recipe; that's where `notify_user` reads the
subscriptions from at fan-out time.

SSE is **core** (no extra); Web Push needs the extra:
`uv add "tempest-fastapi-sdk[webpush]"`. Each channel's primitives live in
[SSE](recipes/sse.md) and [Web Push](recipes/webpush.md) — here we only compose them.

## 9. Frontend gets the status in real time

```python
# src/api/routers/feed.py
from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from src.core.resources import events

router = APIRouter()


@router.get("/api/feed")
async def feed(user: UserModel = Depends(current_user)) -> StreamingResponse:
    return events.response(str(user.id))    # register + stream + unregister
```

## The flow, end to end

1. `POST /api/checkout` — JWT authenticates, the schema validates (quantity > 0, Pix ok).
2. The service prices with the **cache**, writes **order + outbox event** in one transaction.
3. The **relay** publishes `orders.paid` to the **broker** once the commit landed.
4. The consumer enqueues the email (**TaskQueue**) and fans the confirmation out
   with the **NotificationService**: **SSE** for the open tab, **Web Push** for the closed one.
5. The browser gets the update immediately on `GET /api/feed` / `GET /notifications/stream`;
   whoever is away receives the **Web Push**.

Each capability has its own recipe (see the [Tour](tour.md)); the point
here is how they compose with no manual glue: exceptions become the right
HTTP status, tokens gate the route, fields reject junk, the outbox
guarantees the event, and real time closes the loop.
