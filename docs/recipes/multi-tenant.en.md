# Multi-tenant (TenantScopedRepository)

In a **shared-schema** multi-tenant database, every tenant's rows live in
the same table, told apart by a `tenant_id` column. The danger is obvious:
forget **one** `WHERE tenant_id = ?` and tenant A reads (or deletes)
tenant B's data. `TenantScopedRepository` takes that risk off the table —
you bind the `tenant_id` at construction and it injects the filter into
**every** read and stamps it onto **every** write. Call sites can't get it
wrong.

!!! info "Where this fits"
    It's a `BaseRepository` that behaves identically — same API
    ([Database](database.md)). The difference is invisible to the caller:
    tenant scoping is automatic.

## 1. The model needs a tenant column

```python
from uuid import UUID

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class OrderModel(BaseModel):
    """Order — isolated per tenant."""

    __tablename__ = "order"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False, index=True)
    total: Mapped[int] = mapped_column(nullable=False)
```

## 2. Build the repository bound to the tenant

The `tenant_id` usually comes from the request's JWT / session / header:

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import TenantScopedRepository

from src.db.models import OrderModel


def get_order_repo(
    session: AsyncSession, tenant_id: UUID
) -> TenantScopedRepository[OrderModel]:
    """Order repository locked to the request's tenant."""
    return TenantScopedRepository(session, model=OrderModel, tenant_id=tenant_id)
```

If the model has no `tenant_id` column, the constructor raises
`AttributeError` immediately — you catch the mistake at boot, not in
production. Different column name? Pass `tenant_field="org_id"`.

## 3. Use it like any repository

Every read comes filtered; every write comes stamped:

```python
from uuid import UUID

from tempest_fastapi_sdk import TenantScopedRepository

from src.db.models import OrderModel


async def list_orders(repo: TenantScopedRepository[OrderModel]) -> list[OrderModel]:
    """Only THIS tenant's orders — no manual WHERE."""
    return await repo.list()  # WHERE tenant_id = <bound tenant>


async def create_order(
    repo: TenantScopedRepository[OrderModel], total: int
) -> OrderModel:
    """tenant_id is stamped automatically on insert."""
    return await repo.add(OrderModel(total=total))
```

### Cross-tenant access is impossible, even by id

`get_by_id`, `delete` and `delete_batch` are scoped too. An id from another
tenant simply **doesn't match** — indistinguishable from a row that never
existed:

```python
from uuid import UUID

from tempest_fastapi_sdk import TenantScopedRepository

from src.db.models import OrderModel


async def fetch(repo: TenantScopedRepository[OrderModel], order_id: UUID) -> OrderModel:
    """Raises NotFound if the order belongs to ANOTHER tenant — no existence leak."""
    return await repo.get_by_id(order_id)
```

`delete_many({})` deletes only **this** tenant's rows, never the whole
table — the tenant predicate is always added.

!!! warning "Raw queries are your responsibility"
    A subclass that builds its own `select(...)` without going through
    `_apply_filters` — or a pre-built `query=` passed to `paginate` — is
    **not** auto-scoped. In those cases, add
    `.where(self.tenant_column == self.tenant_id)` yourself.

## Recap

- The model declares `tenant_id` (or another column, via `tenant_field=`).
- `TenantScopedRepository(session, model=..., tenant_id=...)` injects the
  filter into every read and stamps every write.
- `get_by_id` / `delete` / `delete_batch` / `delete_many` are scoped —
  cross-tenant access is impossible through the repository methods.
- The constructor validates the tenant column exists at boot.
