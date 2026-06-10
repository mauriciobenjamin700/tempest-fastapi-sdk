# Multi-tenant (TenantScopedRepository)

Num banco multi-tenant de **schema compartilhado**, as linhas de todos os
tenants moram na mesma tabela, separadas por uma coluna `tenant_id`. O
perigo é claro: esquecer **um** `WHERE tenant_id = ?` e o tenant A lê (ou
apaga) os dados do tenant B. `TenantScopedRepository` tira esse risco da
mesa — você amarra o `tenant_id` na construção e ele injeta o filtro em
**toda** leitura e carimba em **toda** escrita. Os call sites não têm como
errar.

!!! info "Onde isso encaixa"
    É um `BaseRepository` que se comporta igualzinho — mesma API
    ([Banco de dados](database.md)). A diferença é invisível pro chamador:
    o escopo de tenant é automático.

## 1. O modelo precisa da coluna de tenant

```python
from uuid import UUID

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class OrderModel(BaseModel):
    """Pedido — isolado por tenant."""

    __tablename__ = "order"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False, index=True)
    total: Mapped[int] = mapped_column(nullable=False)
```

## 2. Construa o repositório amarrado ao tenant

O `tenant_id` normalmente vem do JWT / sessão / header do request:

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import TenantScopedRepository

from src.db.models import OrderModel


def get_order_repo(
    session: AsyncSession, tenant_id: UUID
) -> TenantScopedRepository[OrderModel]:
    """Repositório de pedidos travado no tenant do request."""
    return TenantScopedRepository(session, model=OrderModel, tenant_id=tenant_id)
```

Se o modelo não tiver a coluna `tenant_id`, o construtor levanta
`AttributeError` na hora — você descobre o erro no boot, não em produção.
Coluna com outro nome? Passe `tenant_field="org_id"`.

## 3. Use como qualquer repositório

Toda leitura já vem filtrada; toda escrita já vem carimbada:

```python
from uuid import UUID

from tempest_fastapi_sdk import TenantScopedRepository

from src.db.models import OrderModel


async def list_orders(repo: TenantScopedRepository[OrderModel]) -> list[OrderModel]:
    """Só os pedidos DESTE tenant — sem WHERE manual."""
    return await repo.list()  # WHERE tenant_id = <tenant amarrado>


async def create_order(
    repo: TenantScopedRepository[OrderModel], total: int
) -> OrderModel:
    """tenant_id é carimbado automaticamente no insert."""
    return await repo.add(OrderModel(total=total))
```

### Acesso cruzado é impossível, mesmo por id

`get_by_id`, `delete` e `delete_batch` também são escopados. Um id de outro
tenant simplesmente **não casa** — é indistinguível de uma linha que nunca
existiu:

```python
from uuid import UUID

from tempest_fastapi_sdk import TenantScopedRepository

from src.db.models import OrderModel


async def fetch(repo: TenantScopedRepository[OrderModel], order_id: UUID) -> OrderModel:
    """Levanta NotFound se o pedido for de OUTRO tenant — sem vazar existência."""
    return await repo.get_by_id(order_id)
```

`delete_many({})` apaga só as linhas **deste** tenant, nunca a tabela
inteira — o predicado de tenant é sempre adicionado.

!!! warning "Queries cruas são responsabilidade sua"
    Uma subclasse que monta o próprio `select(...)` sem passar por
    `_apply_filters` — ou um `query=` pré-montado passado pro `paginate` —
    **não** é escopado automaticamente. Nesses casos, adicione
    `.where(self.tenant_column == self.tenant_id)` você mesmo.

## Recap

- O modelo declara `tenant_id` (ou outra coluna, via `tenant_field=`).
- `TenantScopedRepository(session, model=..., tenant_id=...)` injeta o
  filtro em toda leitura e carimba toda escrita.
- `get_by_id` / `delete` / `delete_batch` / `delete_many` são escopados —
  acesso cruzado entre tenants é impossível pelos métodos do repositório.
- Construtor valida a existência da coluna de tenant no boot.
