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
    SDK core + the `[admin]` extra. Nothing else.

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

## 5. What you get

- **Dashboard** — three business cards (number, trend, partition) at the
  top, next to the system panel (CPU/RAM). Only the models the
  `access_policy` allows for `VIEW` appear (staff don't see what they
  can't touch).
- **Products** — category autocomplete on the form; `specs` as a JSON
  editor (pretty-print + validation); an **Import CSV** button; an audit
  timeline on each product's detail.
- **Orders** — **All / Pending / Paid** tabs (lenses); the order items in
  a table on the detail, with an "Add" pre-linked to the order.
- **RBAC** — a `staff` user browses and reads everything they may, but
  every create/edit/delete button disappears and the routes answer `403`.

!!! check "Recap"
    One `AdminSite` plus a few well-annotated `AdminModel`s deliver a
    production admin: auditing, autocomplete, inlines, metrics, import,
    RBAC and lenses — each a single argument, all typed, no metaclass.
    Details for each in the [Admin panel](recipes/admin.md) recipes.
