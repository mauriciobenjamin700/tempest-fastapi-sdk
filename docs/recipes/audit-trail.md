# Audit trail

`AuditMixin` guarda **quem** mexeu por último (`created_by` / `updated_by`) e o `BaseModel` guarda **quando** (`created_at` / `updated_at`). Nenhum dos dois guarda o **histórico** das mudanças. O audit trail adiciona um log append-only: uma linha por create / update / delete, com o ator, a ação e um diff antes/depois das colunas alteradas.

A linha de auditoria é gravada na **mesma transação** da mudança (reusa a maquinaria do outbox), então uma entrada de auditoria nunca referencia uma mudança que foi revertida.

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
product = await repo.add_audited(ProductModel(name="Widget"), actor=str(user.id))
# grava o produto + uma entrada CREATE com {"after": {...}}
```

### Update — tire um snapshot antes de mutar

`update_audited` precisa do estado **anterior** para calcular o diff. Tire o snapshot com `repo.snapshot(...)` antes de alterar a instância:

```python
async def rename_product(repo: ProductRepository, product_id: UUID, name: str) -> None:
    """Renomeia um produto registrando o diff na auditoria.

    Args:
        repo (ProductRepository): O repository de produtos.
        product_id (UUID): O id do produto.
        name (str): O novo nome.

    Raises:
        NotFoundException: Se o produto não existe.
    """
    product = await repo.get_by_id(product_id)
    before = repo.snapshot(product)                  # ← antes de mutar
    product.name = name
    await repo.update_audited(product, before, actor=str(user.id))
    # grava uma entrada UPDATE com {"name": {"before": "...", "after": "..."}}
```

### Delete

```python
await repo.delete_audited(product, actor=str(user.id))
# apaga a linha + grava uma entrada DELETE com {"before": {...}}
```

!!! warning "Mesma transação"
    As três variantes commitam a linha de negócio e a de auditoria **juntas**. Se a auditoria falhar, a mudança é revertida — nunca fica meia gravada. Repositories sem `audit_model` levantam `RuntimeError` ao chamar os métodos auditados.

## Helpers avulsos

Fora do repository, `snapshot_model(instance)` e `diff_snapshots(before, after)` ficam disponíveis, e `BaseAuditLogModel.for_create / for_update / for_delete` constroem a entrada (sem adicionar à sessão) quando você quer controlar a gravação manualmente.

## Recapitulando

- `BaseAuditLogModel` (subclasse com `__tablename__`) + `AuditAction`.
- `repo = Repository(session, model=..., audit_model=AuditLogModel)`.
- `add_audited` / `update_audited(model, before)` / `delete_audited` — negócio + auditoria na mesma tx.
- `repo.snapshot(model)` antes de mutar; `snapshot_model` / `diff_snapshots` para uso manual.
