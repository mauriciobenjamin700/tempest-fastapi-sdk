# Audit trail

`AuditMixin` guarda **quem** mexeu por último (`created_by` / `updated_by`) e o `BaseModel` guarda **quando** (`created_at` / `updated_at`). Nenhum dos dois guarda o **histórico** das mudanças. O audit trail adiciona um log append-only: uma linha por create / update / delete, com o ator, a ação e um diff antes/depois das colunas alteradas.

A linha de auditoria é gravada na **mesma transação** da mudança — `add_audited` / `update_audited` adicionam a linha de auditoria e a linha de negócio e commitam as duas juntas, o mesmo padrão que o outbox usa —, então uma entrada de auditoria nunca referencia uma mudança que foi revertida.

## A tabela de auditoria

Subclasse `BaseAuditLogModel` e escolha um `__tablename__` (`audit_log` por convenção), igual ao `BaseOutboxModel`:

```python
from tempest_fastapi_sdk import BaseAuditLogModel


class AuditLogModel(BaseAuditLogModel):
    """Log append-only de mutações por entidade."""

    __tablename__ = "audit_log"
```

Herda os quatro campos canônicos (`id`, `is_active`, `created_at`, `updated_at`) mais: `entity` (nome do model), `entity_id` (id da linha, como texto), `action` (`AuditAction`), `actor` (quem fez, ou `None`), `changes` (o diff em JSON) e `context` (metadados opcionais — request id, ip, motivo).

## Ligando no repository

Passe `audit_model=` no repository e use as variantes auditadas. Elas gravam a linha de negócio **e** a de auditoria juntas:

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.db.models import AuditLogModel, ProductModel


class ProductRepository(BaseRepository[ProductModel]):
    """Repository de produtos com trilha de auditoria."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa o repository.

        Args:
            session (AsyncSession): A sessão async do banco.
        """
        super().__init__(session, model=ProductModel, audit_model=AuditLogModel)
```

### Create

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.db.models import ProductModel
from src.db.repositories import ProductRepository

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = ProductRepository(session)

# O ator é o id de quem já está autenticado (ex.: `current_user.id`).
actor_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


async def main() -> None:
    """Run this example."""
    product = await repo.add_audited(ProductModel(name="Widget"), actor=str(actor_id))
    # grava o produto + uma entrada CREATE com {"after": {...}}


asyncio.run(main())
```

### Update — tire um snapshot antes de mutar

`update_audited` precisa do estado **anterior** para calcular o diff. Tire o snapshot com `repo.snapshot(...)` antes de alterar a instância:

```python
from uuid import UUID

from src.db.repositories import ProductRepository


async def rename_product(
    repo: ProductRepository, product_id: UUID, name: str, actor: str
) -> None:
    """Renomeia um produto registrando o diff na auditoria.

    Args:
        repo (ProductRepository): O repository de produtos.
        product_id (UUID): O id do produto.
        name (str): O novo nome.
        actor (str): Quem realizou a alteração.

    Raises:
        NotFoundException: Se o produto não existe.
    """
    product = await repo.get_by_id(product_id)
    before = repo.snapshot(product)                  # ← antes de mutar
    product.name = name
    await repo.update_audited(product, before, actor=actor)
    # grava uma entrada UPDATE com {"name": {"before": "...", "after": "..."}}
```

### Delete

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.db.repositories import ProductRepository

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = ProductRepository(session)

product_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
actor_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


async def main() -> None:
    """Run this example."""
    product = await repo.get_by_id(product_id)
    await repo.delete_audited(product, actor=str(actor_id))
    # apaga a linha + grava uma entrada DELETE com {"before": {...}}


asyncio.run(main())
```

!!! warning "Mesma transação"
    As três variantes gravam a linha de negócio e a de auditoria **juntas**. Chamadas soltas, elas commitam as duas no fim; dentro de um bloco `repo.transaction()` (ou num repository com `autocommit=False`) elas só fazem `flush`, e o commit é do bloco — se o bloco aborta, as duas linhas somem juntas. Nos dois casos, se a auditoria falhar a mudança é revertida — nunca fica meia gravada. Veja [Transações](transactions.md). Repositories sem `audit_model` levantam `RuntimeError` ao chamar os métodos auditados.

## Helpers avulsos

Fora do repository, `snapshot_model(instance)` e `diff_snapshots(before, after)` ficam disponíveis, e `BaseAuditLogModel.for_create / for_update / for_delete` constroem a entrada (sem adicionar à sessão) quando você quer controlar a gravação manualmente.

## Recapitulando

- `BaseAuditLogModel` (subclasse com `__tablename__`) + `AuditAction`.
- `repo = Repository(session, model=..., audit_model=AuditLogModel)`.
- `add_audited` / `update_audited(model, before)` / `delete_audited` — negócio + auditoria na mesma tx.
- `repo.snapshot(model)` antes de mutar; `snapshot_model` / `diff_snapshots` para uso manual.
