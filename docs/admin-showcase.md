# Exemplo integrado — admin de loja completo

Este exemplo junta **todos** os recursos do painel admin num app só — uma
loja pequena — pra você ver como eles se combinam. Cada recurso tem sua
própria receita em [Painel admin](recipes/admin.md); aqui é a visão de
conjunto.

Exercitados de uma vez: **audit history** (`audit_model=`),
**autocomplete FK** (`autocomplete_fields=`), **inlines** (`inlines=`),
**cards de negócio** (`dashboard_cards=`), **import CSV** (`can_import=`),
**RBAC granular** (`access_policy=`), **lenses** (`lenses=`) e o **widget
JSON** (automático em colunas `JSON`).

!!! info "O que você precisa"
    Núcleo do SDK + o extra `[admin]`. Nada além.

## 1. Modelos

Uma loja: categorias, produtos (com specs em JSON e FK pra categoria),
pedidos e itens de pedido. Mais uma tabela de auditoria e um usuário com
papel (`role`) pro RBAC.

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

## 2. Cards de negócio pro dashboard

Cada card recebe a sessão e devolve um `MetricValue`, `MetricTrend` ou
`MetricPartition`.

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

## 3. Configuração do admin

Aqui tudo se encaixa. Note os recursos anotados em cada `AdminModel`.

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
    """superadmin faz tudo; staff só lê; ninguém mais entra."""
    if user.role == "superadmin":
        return True
    if user.role == "staff":
        return action is AdminPermission.VIEW
    return False


site = AdminSite(
    title="Loja",
    dashboard_cards=[
        MetricCard("Produtos", total_products, help_text="catálogo ativo"),
        MetricCard("Pagos vs pendentes", paid_vs_pending),
        MetricCard("Pedidos por status", orders_by_status),
    ],
)

site.register(AdminModel(model=Category, search_fields=[Category.name]))

site.register(
    AdminModel(
        model=Product,
        search_fields=[Product.name],
        # FK como busca HTMX (categoria pode ter milhares de linhas):
        autocomplete_fields=[Product.category_id],
        # coluna JSON `specs` vira editor JSON automaticamente;
        # importa catálogo por CSV:
        can_import=True,
        # trilha de auditoria por produto no detail:
        audit_model=AuditLog,
    )
)

site.register(
    AdminModel(
        model=Order,
        search_fields=[Order.customer_email],
        # itens do pedido listados no detail do pedido:
        inlines=[Inline(OrderItem, OrderItem.order_id)],
        # abas de fila de trabalho:
        lenses=[
            Lens("Pendentes", filters={"status": "pending"}),
            Lens("Pagos", filters={"status": "paid"}, order_by="-placed_at"),
        ],
        audit_model=AuditLog,
    )
)

# OrderItem precisa de admin registrado pros links do inline funcionarem:
site.register(AdminModel(model=OrderItem, autocomplete_fields=[OrderItem.product_id]))
```

## 4. Montando o router

O `access_policy` entra aqui; a trilha de auditoria é gravada pelo
repository (`add_audited`/`update_audited`) — o admin já faz isso quando
`audit_model=` está setado nos writes que ele mesmo executa.

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin.site import access_policy, site
from src.core.settings import settings
from src.db.connection import db  # seu AsyncDatabaseManager


def create_app() -> FastAPI:
    app = FastAPI(title="Loja")
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(User, mfa_issuer="Loja"),
            secret_key=settings.SECRET_KEY,
            access_policy=access_policy,
        )
    )
    return app
```

## 5. O que você vê

- **Dashboard** — três cards de negócio (número, tendência, partição) no
  topo, ao lado do painel de sistema (CPU/RAM). Só os modelos que o
  `access_policy` libera pra `VIEW` aparecem (staff não vê o que não pode).
- **Produtos** — busca de categoria por autocomplete no form; `specs`
  como editor JSON (pretty-print + validação); botão **Import CSV**;
  timeline de auditoria no detail de cada produto.
- **Pedidos** — abas **All / Pendentes / Pagos** (lenses); os itens do
  pedido numa tabela no detail, com "Add" já ligado ao pedido.
- **RBAC** — um `staff` navega e lê tudo o que pode, mas todo botão de
  criar/editar/apagar some e as rotas respondem `403`.

!!! check "Recap"
    Um `AdminSite` + alguns `AdminModel` bem anotados entregam um admin de
    produção: auditoria, autocomplete, inlines, métricas, import, RBAC e
    lenses — cada um um argumento, todos tipados, sem metaclasse. Detalhe
    de cada um nas receitas do [Painel admin](recipes/admin.md).
