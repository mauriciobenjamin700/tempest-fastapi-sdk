# Integrated example — a complete shop admin

This example wires **every** admin-panel feature into one app — a small
shop — so you can see how they combine. Each feature has its own recipe
in [Admin panel](recipes/admin.md); this is the whole picture.

Exercised at once: **audit history** (`audit_model=`), **autocomplete
FK** (`autocomplete_fields=`), **inlines** (`inlines=`), **business
cards** (`dashboard_cards=`), **CSV import** (`can_import=`), **granular
RBAC** (`access_policy=`), **lenses** (`lenses=`) and the **JSON widget**
(automatic on `JSON` columns).

!!! info "What you need"
    SDK core + the `[admin]` extra. The **operational notifications**
    section (§5) adds the `[webpush]` extra; the SSE channel is core and
    needs nothing.

## 1. Models

A shop: categories, products (with JSON specs and an FK to category),
orders and order items. Plus an audit table and a user with a `role` for
RBAC.

```python
# src/db/models.py
import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseAuditLogModel, BaseModel, BaseUserModel
from tempest_fastapi_sdk.core import BaseStrEnum


class OrderStatus(BaseStrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class User(BaseUserModel):
    __tablename__ = "users"
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="staff")


class AuditLog(BaseAuditLogModel):
    __tablename__ = "audit_log"


class Category(BaseModel):
    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Product(BaseModel):
    __tablename__ = "products"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    specs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Order(BaseModel):
    __tablename__ = "orders"
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        String(16), nullable=False, default=OrderStatus.PENDING
    )
    placed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class OrderItem(BaseModel):
    __tablename__ = "order_items"
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(nullable=False, default=1)
```

## 2. Dashboard business cards

Each card takes the session and returns a `MetricValue`, `MetricTrend`
or `MetricPartition`.

```python
# src/admin/cards.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BaseRepository,
    MetricPartition,
    MetricTrend,
    MetricValue,
)

from src.db.models import Order, OrderStatus, Product


async def total_products(session: AsyncSession) -> MetricValue:
    count = await BaseRepository(session, model=Product).count()
    return MetricValue(count, unit="products")


async def paid_vs_pending(session: AsyncSession) -> MetricTrend:
    repo = BaseRepository(session, model=Order)
    paid = await repo.count({"status": OrderStatus.PAID.value})
    pending = await repo.count({"status": OrderStatus.PENDING.value})
    return MetricTrend(value=float(paid), previous=float(pending), unit="orders")


async def orders_by_status(session: AsyncSession) -> MetricPartition:
    repo = BaseRepository(session, model=Order)
    segments = [
        (status.value, float(await repo.count({"status": status.value})))
        for status in OrderStatus
    ]
    return MetricPartition(segments=segments)
```

## 3. The admin configuration

This is where it all clicks together. Note the features annotated on
each `AdminModel`.

```python
# src/admin/site.py
from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermission,
    AdminSite,
    Inline,
    Lens,
    MetricCard,
)

from src.admin.cards import orders_by_status, paid_vs_pending, total_products
from src.db.models import AuditLog, Category, Order, OrderItem, Product, User


def access_policy(user: User, admin: AdminModel, action: AdminPermission) -> bool:
    """superadmin does everything; staff read-only; no one else gets in."""
    if user.role == "superadmin":
        return True
    if user.role == "staff":
        return action is AdminPermission.VIEW
    return False


site = AdminSite(
    title="Shop",
    dashboard_cards=[
        MetricCard("Products", total_products, help_text="active catalog"),
        MetricCard("Paid vs pending", paid_vs_pending),
        MetricCard("Orders by status", orders_by_status),
    ],
)

site.register(AdminModel(model=Category, search_fields=[Category.name]))

site.register(
    AdminModel(
        model=Product,
        search_fields=[Product.name],
        # FK as an HTMX search (a category can have thousands of rows):
        autocomplete_fields=[Product.category_id],
        # the JSON `specs` column becomes a JSON editor automatically;
        # import the catalog from CSV:
        can_import=True,
        # per-product audit trail on the detail view:
        audit_model=AuditLog,
    )
)

site.register(
    AdminModel(
        model=Order,
        search_fields=[Order.customer_email],
        # order items listed on the order's detail view:
        inlines=[Inline(OrderItem, OrderItem.order_id)],
        # work-queue tabs:
        lenses=[
            Lens("Pending", filters={"status": "pending"}),
            Lens("Paid", filters={"status": "paid"}, order_by="-placed_at"),
        ],
        audit_model=AuditLog,
    )
)

# OrderItem needs a registered admin for the inline's links to work:
site.register(AdminModel(model=OrderItem, autocomplete_fields=[OrderItem.product_id]))
```

## 4. Mounting the router

The `access_policy` plugs in here; the audit trail is written by the
repository (`add_audited`/`update_audited`) — the admin already does this
for the writes it performs when `audit_model=` is set.

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin.site import access_policy, site
from src.core.settings import settings
from src.db.connection import db  # your AsyncDatabaseManager


def create_app() -> FastAPI:
    app = FastAPI(title="Shop")
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(User, mfa_issuer="Shop"),
            secret_key=settings.SECRET_KEY,
            access_policy=access_policy,
        )
    )
    return app
```

## 5. Operational notifications (SSE + Web Push)

The audit trail (§1, §4) is the **durable record** of what happened — one
row per write, there to query later. But it's passive: nobody sits staring
at `audit_log`. When a domain event matters to the team **right now** — a
new order landed, a signup came in, a moderation flag went up — you want a
**live signal**, not a row to audit afterwards. That's the other half of
the pair: audit = durable, passive; notification = ephemeral, live.

The same event travels on **two channels, with the same payload**:

- **SSE** for the **open** panel — a shared `"staff"` channel every logged-in
  admin subscribes to; it lands as a live toast/badge.
- **Web Push** for the **closed** panel — the browser delivers it in the
  background via a Service Worker, even with no tab open.

A single `NotificationService.notify_staff(...)` fans out to both.

!!! info "Install"
    SSE is core — it ships with the SDK. Web Push needs the extra:
    `uv add "tempest-fastapi-sdk[webpush]"` (brings `pywebpush` +
    `cryptography`). Primitives in [SSE](recipes/sse.md) and
    [Web Push](recipes/webpush.md).

### The Web Push subscription table

One device per row, unique `endpoint`, an FK to the user — the concrete row
over the SDK base (same pattern as auth):

```python
# src/db/models.py  (alongside the §1 models)
from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscription(BaseWebPushSubscriptionModel):
    __tablename__ = "web_push_subscriptions"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
```

### The notification service

One piece, orchestrating both channels. The broker is the same
process-wide singleton from the [SSE recipe](recipes/sse.md#broadcast-pra-varios-clientes-ssebroker);
the `WebPushSubscriptionService` comes from the [Web Push recipe](recipes/webpush.md#tabela-servico-recomendado).

```python
# src/services/notification.py
from tempest_fastapi_sdk import (
    BaseRepository,
    SSEBroker,
    WebPushPayloadSchema,
    WebPushSubscriptionService,
)

from src.db.models import User

STAFF_CHANNEL = "staff"


class NotificationService:
    """Fan one staff-relevant domain event out to SSE and Web Push.

    The audit log is the durable record of what happened; this is the live
    signal that pokes whoever is on shift. The same payload goes on two
    channels: a shared SSE channel every open admin panel subscribes to,
    and Web Push for the panels that are closed.
    """

    def __init__(
        self,
        broker: SSEBroker,
        push: WebPushSubscriptionService,
        users: BaseRepository,
    ) -> None:
        """Wire the SSE broker, the Web Push service and the user repository.

        Args:
            broker (SSEBroker): Fan-out broker for the shared staff channel.
            push (WebPushSubscriptionService): Per-device Web Push delivery.
            users (BaseRepository): Repository used to resolve staff members.
        """
        self.broker = broker
        self.push = push
        self.users = users

    async def notify_staff(
        self, event: str, title: str, body: str, data: dict
    ) -> None:
        """Broadcast a domain event to the whole staff, on both channels.

        Publishes once to the shared SSE channel (every open panel), then
        pushes each staff member's registered devices for the closed ones.

        Args:
            event (str): SSE event name and Web Push tag (e.g. "new_order").
            title (str): Web Push notification title.
            body (str): Web Push notification body.
            data (dict): Payload carried identically on both channels.
        """
        await self.broker.publish(STAFF_CHANNEL, data, event=event)
        payload = WebPushPayloadSchema(title=title, body=body, tag=event, data=data)
        admins = await self.users.list()
        for user in admins:
            if user.role in {"staff", "superadmin"}:
                await self.push.notify_user(user.id, payload)
```

`notify_staff` is the **fan-out**: one domain event goes out on two channels at
the same instant — SSE for the **open** panel, Web Push for the **closed** one —
carrying the **same `data`**. Step by step through the method body:

1. **SSE broadcast** — `await self.broker.publish(STAFF_CHANNEL, data, event=event)`
   publishes **once** to the shared `"staff"` channel. The broker walks every
   stream subscribed to that channel (one per open admin panel) and delivers the
   same event to each: one `publish` → N panels. Whoever has the panel closed has
   no stream here — that's what step 3 covers.
2. **Build the system payload** — `WebPushPayloadSchema(title=..., body=...,
   tag=event, data=data)` wraps the **same `data`** in the Web Push format,
   adding what only the system notification shows (`title`/`body`) and the `tag`
   that collapses duplicates in the tray.
3. **Web Push fan-out** — `self.users.list()` resolves the team and, for each
   `staff`/`superadmin`, `push.notify_user(user.id, payload)` pushes to **all of
   that user's registered devices** (the table from the previous subsection). This
   is the channel that lands with the panel **closed**.

The two key calls, argument by argument:

- `broker.publish(STAFF_CHANNEL, data, event=event)` — 1st argument is the
  **channel** (`"staff"`, the same string the subscribe endpoint uses); 2nd is
  the **payload** (`data`, which becomes JSON in the SSE frame); `event=` is the
  **name** the frontend listens for with `addEventListener`.
- `push.notify_user(user.id, payload)` — 1st argument is **who** receives it (the
  user id; the service resolves their devices under the hood); 2nd is the
  `WebPushPayloadSchema` delivered to each device.

!!! note "Why both channels, not one"
    The SSE `publish` only reaches whoever is **connected right now**; Web Push
    depends on browser permission. Neither is a reliable record — the thing that
    durably keeps *"what happened"* is the audit trail (§1, §4). Here the goal is
    the opposite: **alert in the moment**. Auditing records, the notification
    pokes — `notify_staff` only handles that second half of the pair.

### Firing on the domain event

Orders come in from the storefront (not the admin). The controller that
creates the order delegates to both services after persisting — audit and
notification are distinct layers: one records, the other alerts. No DB
access in the route or the controller: resolving staff is the
`NotificationService`'s job.

```python
# src/controllers/order.py  (storefront side — where the order is placed)
from src.schemas import OrderCreateSchema, OrderResponseSchema
from src.services import NotificationService, OrderService


class OrderController:
    """Create an order and alert staff in real time."""

    def __init__(
        self, orders: OrderService, notifications: NotificationService
    ) -> None:
        """Wire the order service and the notification fan-out.

        Args:
            orders (OrderService): Order business logic.
            notifications (NotificationService): Live staff notifier.
        """
        self.orders = orders
        self.notifications = notifications

    async def place_order(self, data: OrderCreateSchema) -> OrderResponseSchema:
        """Persist an order and push a live alert to every staff member.

        Args:
            data (OrderCreateSchema): The order creation payload.

        Returns:
            The created order.
        """
        order = await self.orders.create(data)
        await self.notifications.notify_staff(
            event="new_order",
            title="New order",
            body=f"{order.customer_email} — R$ {order.total}",
            data={"order_id": str(order.id), "url": f"/admin/orders/{order.id}"},
        )
        return order
```

### The SSE subscribe endpoint

The open panel subscribes to the `"staff"` channel. Since the native
`EventSource` can't send a header, the route reuses **the same session
cookie** `make_admin_router` issued at login — the `SignedCookieSessionStore`
with the same `secret_key` and the `UserModelAuthBackend` from §4:

```python
# src/api/dependencies/admin_stream.py
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.admin import SignedCookieSessionStore, UserModelAuthBackend

from src.core.settings import settings
from src.db.connection import db
from src.db.models import User

_store = SignedCookieSessionStore(secret_key=settings.SECRET_KEY)
_backend = UserModelAuthBackend(User, mfa_issuer="Shop")


async def require_admin(
    request: Request,
    session: AsyncSession = Depends(db.session_dependency),
) -> User:
    """Resolve the logged-in admin from the panel's own session cookie.

    EventSource cannot send an Authorization header, so the stream leans on
    the signed session cookie make_admin_router already issued at login —
    no second auth mechanism to keep in sync.

    Args:
        request (Request): The inbound SSE request.
        session (AsyncSession): A live DB session.

    Returns:
        The authenticated admin user.

    Raises:
        HTTPException: 401 when there is no valid, fully-authenticated admin
            session (missing, tampered, or still MFA-pending).
    """
    admin_session = _store.load(request)
    if admin_session is None or admin_session.mfa_pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await _backend.load_principal(session, admin_session.principal_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return user
```

Step by step, what `require_admin` does on every stream request:

1. `_store.load(request)` — reads the session cookie `make_admin_router` issued
   at login and **verifies its signature**. A missing or tampered cookie comes
   back as `None`.
2. `admin_session is None or admin_session.mfa_pending` — rejects both the
   missing/invalid session and the one that passed the password but has **not**
   completed MFA yet. Either case → `401`.
3. `_backend.load_principal(session, admin_session.principal_id)` — loads the
   real `User` from the DB by the id stored in the session. If the user is gone
   (deleted after login) → `401`.
4. Returns the authenticated `User`. The endpoint doesn't use the object beyond
   requiring it to exist — which is why the route parameter is `_`.

```python
# src/api/routers/admin_stream.py
from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import SSEBroker

from src.api.dependencies.admin_stream import require_admin
from src.api.dependencies.resources import get_broker
from src.db.models import User

router = APIRouter(prefix="/admin/notifications")


@router.get("/stream")
async def stream(
    _: User = Depends(require_admin),
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Subscribe the open admin panel to the shared "staff" channel."""
    return broker.response(STAFF_CHANNEL)
```

The endpoint itself is one line. Step by step, on every
`GET /admin/notifications/stream`:

1. `Depends(require_admin)` runs the guard above. Without a valid admin session
   the request dies with `401` and **never** reaches the broker — the stream is
   private to staff.
2. `Depends(get_broker)` injects the shared broker — the same process-wide
   singleton from the [SSE recipe](recipes/sse.md#broadcast-pra-varios-clientes-ssebroker),
   held on `app.state`.
3. `broker.response(STAFF_CHANNEL)` does **three things in one call**:
     - **register** — creates a fresh `EventStream` and subscribes it to the
       `"staff"` channel;
     - **stream** — returns the `StreamingResponse` with the SSE headers already
       set (the panel starts receiving right away);
     - **unregister** — wires an `on_disconnect` that removes this stream from
       the channel when the panel closes. It runs in the response generator's
       `finally` — the one spot that fires on disconnect — so there's no
       `try/finally` for you to forget.

!!! info "Shared channel — every admin on the same `\"staff\"`"
    In the [SSE recipe](recipes/sse.md#broadcast-pra-varios-clientes-ssebroker)
    the channel was **each user's id**: everyone on their own, isolated from the
    rest. Here it's the opposite — **every** logged-in admin subscribes to the
    **same** `"staff"` channel. That's why a single `broker.publish("staff", ...)`
    (step 1 of `notify_staff`) reaches **all** open panels at once, without the
    service having to know who's connected. Both sides only have to agree on the
    same constant string `STAFF_CHANNEL` — subscription on one side, publish on
    the other.

### Mounting: device subscription + stream

Web Push needs each device to subscribe; `make_web_push_router` ships
`/subscribe` + `/unsubscribe` ready-made (like `make_auth_router`). Wire
them into the §4 `create_app`, next to the admin router and the stream:

```python
# src/api/app.py  (adding to §4)
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BaseRepository,
    SSEBroker,
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
)

from src.api.dependencies import get_current_user_id, get_session
from src.api.routers.admin_stream import router as admin_stream_router
from src.db.models import WebPushSubscription

broker = SSEBroker()   # the same singleton get_broker resolves


def _push_service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = BaseRepository(session, model=WebPushSubscription)
    return WebPushSubscriptionService(repo, WebPushDispatcher(**settings.webpush_kwargs()))


app.state.broker = broker
app.include_router(admin_stream_router)
app.include_router(
    make_web_push_router(
        service_factory=_push_service,
        session_factory=get_session,
        current_user_id=get_current_user_id,
    )
)
# GET  /admin/notifications/stream         (SSE, admin cookie)
# POST /api/push/subscribe | /unsubscribe  (device registration)
```

In the panel's frontend, `EventSource` subscribes to the channel and each
`new_order` becomes a toast (the admin cookie rides along with
`withCredentials`):

```javascript
const es = new EventSource("/admin/notifications/stream", { withCredentials: true });
es.addEventListener("new_order", (e) => toast(JSON.parse(e.data)));
```

When an order lands, every open panel gets the frame instantly:

```text
event: new_order
data: {"order_id": "9f3a...", "url": "/admin/orders/9f3a..."}
```

And anyone with the panel closed gets the **same** event as a system Web
Push notification (`title`/`body`), the Service Worker opening `data.url`
on click.

!!! check "Durable vs live — both, not either/or"
    Auditing (`audit_model=`) answers *"what happened and who did it"* weeks
    later; the notification answers *"what do I need to see now"*. SSE only
    reaches whoever is **connected at that moment** and push depends on
    browser permission — neither is a reliable record. Persist the fact
    (audit/DB) **and** fire the signal (notification); never swap one for
    the other.

## 6. What you get

- **Dashboard** — three business cards (number, trend, partition) at the
  top, next to the system panel (CPU/RAM). Only the models the
  `access_policy` allows for `VIEW` appear (staff don't see what they
  can't touch).
- **Products** — category autocomplete on the form; `specs` as a JSON
  editor (pretty-print + validation); an **Import CSV** button; an audit
  timeline on each product's detail.
- **Orders** — **All / Pending / Paid** tabs (lenses); the order items in
  a table on the detail, with an "Add" pre-linked to the order.
- **Notifications** — with the panel open, each new order pings a live
  toast (SSE on the `"staff"` channel); with it closed, it arrives as a
  system Web Push — the same event the audit log records in parallel.
- **RBAC** — a `staff` user browses and reads everything they may, but
  every create/edit/delete button disappears and the routes answer `403`.

!!! check "Recap"
    One `AdminSite` plus a few well-annotated `AdminModel`s deliver a
    production admin: auditing, autocomplete, inlines, metrics, import,
    RBAC and lenses — each a single argument, all typed, no metaclass. On
    top, a `NotificationService` reuses the `SSEBroker` (the `"staff"`
    channel) and the `WebPushSubscriptionService` to turn domain events into
    live signals — the audit log's counterpart: one records durably, the
    other alerts in the moment. Details for each in the
    [Admin panel](recipes/admin.md), [SSE](recipes/sse.md) and
    [Web Push](recipes/webpush.md) recipes.
