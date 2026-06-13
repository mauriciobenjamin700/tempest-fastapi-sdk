# Sync offline-first (delta)

Apps móveis e PWAs trabalham offline: capturam dados sem rede e sincronizam
quando a conexão volta. O backend precisa responder uma pergunta só — *"o que
mudou desde a última vez que falei com você?"* — e isso inclui **registros
deletados**, senão eles ficam órfãos para sempre no aparelho.

Este recipe monta esse fluxo bidirecional (push + pull) com
`BaseRepository.changes_since`, sem reescrever lógica de cursor por projeto.

## O problema

O cliente guarda os dados localmente (IndexedDB, SQLite) e mantém um
**watermark**: o instante do último sync bem-sucedido. No próximo sync ele quer:

1. **Push** — enviar o que criou/editou offline. Como pode reenviar (retry,
   rede instável), a escrita precisa ser **idempotente**.
2. **Pull** — receber tudo que mudou no servidor desde o watermark, incluindo
   exclusões, para espelhar localmente.

## O modelo

Use o `id` gerado pelo cliente como chave primária (idempotência de graça) e
misture `SoftDeleteMixin` para que exclusões virem **tombstones** em vez de
sumirem da query.

```python
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel, SoftDeleteMixin


class AnalysisModel(BaseModel, SoftDeleteMixin):
    """Uma análise sincronizável, com id vindo do dispositivo."""

    __tablename__ = "analyses"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    animal_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    notes: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
```

`BaseModel` já entrega `id`, `created_at`, `updated_at` e `is_active`;
`SoftDeleteMixin` adiciona `deleted_at` + `is_deleted` + `mark_deleted()`.

## O repositório

`changes_since` é o único método novo de que você precisa. Crie um repositório
fino para mapear linha → schema:

```python
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import BaseRepository


class AnalysisRepository(BaseRepository[AnalysisModel]):
    """Acesso a dados das análises sincronizáveis."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=AnalysisModel)
```

## Push idempotente

O `id` é do cliente, então "upsert por id" não duplica em retry:

```python
from uuid import UUID


async def upsert_analysis(
    repo: AnalysisRepository,
    *,
    user_id: UUID,
    analysis_id: UUID,
    animal_id: str,
    notes: str,
) -> AnalysisModel:
    """Cria ou atualiza uma análise, idempotente por id do cliente.

    Args:
        repo (AnalysisRepository): O repositório de análises.
        user_id (UUID): O dono do registro.
        analysis_id (UUID): O id gerado no dispositivo (chave primária).
        animal_id (str): Identificador do animal (brinco / herd id).
        notes (str): Observações livres.

    Returns:
        AnalysisModel: A linha persistida.
    """
    existing = await repo.get_or_none({"id": analysis_id, "user_id": user_id})
    if existing is not None:
        existing.animal_id = animal_id
        existing.notes = notes
        return await repo.update(existing)
    return await repo.add(
        AnalysisModel(
            id=analysis_id,
            user_id=user_id,
            animal_id=animal_id,
            notes=notes,
        )
    )
```

## Pull (o delta)

`changes_since(since)` devolve só o que mudou após o watermark, em ordem
crescente de `updated_at`, paginado por cursor, **com os tombstones**:

```python
from datetime import datetime
from uuid import UUID


async def pull_changes(
    repo: AnalysisRepository,
    *,
    user_id: UUID,
    since: datetime | None,
    cursor: str | None = None,
) -> dict[str, object]:
    """Retorna as análises que mudaram desde o watermark do cliente.

    Args:
        repo (AnalysisRepository): O repositório de análises.
        user_id (UUID): Escopo do dono — nunca sincronize sem ele.
        since (datetime | None): Watermark do último sync. None faz o
            sync completo (primeira vez).
        cursor (str | None): Cursor da página anterior; None pega a
            primeira página.

    Returns:
        dict[str, object]: O envelope de cursor + `server_time`.
    """
    return await repo.changes_since(
        since,
        filters={"user_id": user_id},
        cursor=cursor,
        limit=100,
    )
```

!!! danger "Sempre passe o escopo do dono"
    `changes_since` **não** filtra por usuário sozinho. Passe sempre
    `filters={"user_id": user_id}` (ou o escopo do tenant), senão um cliente
    puxa o delta do mundo inteiro.

## O endpoint

`SyncFilterSchema` e `SyncPaginationSchema` casam exatamente com os argumentos
e o retorno de `changes_since`:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from tempest_fastapi_sdk import SyncFilterSchema, SyncPaginationSchema

router = APIRouter(prefix="/api/analyses", tags=["sync"])


@router.get("/changes")
async def get_changes(
    filters: Annotated[SyncFilterSchema, Query()],
    repo: AnalysisRepository = Depends(get_analysis_repository),
    user_id: UUID = Depends(get_current_user_id),
) -> SyncPaginationSchema[AnalysisResponseSchema]:
    """Endpoint de pull: o delta desde o watermark do cliente."""
    page = await repo.changes_since(
        filters.since,
        filters={"user_id": user_id},
        cursor=filters.cursor,
        limit=filters.limit,
        include_deleted=filters.include_deleted,
    )
    return SyncPaginationSchema[AnalysisResponseSchema](
        items=[AnalysisResponseSchema.model_validate(r) for r in page["items"]],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        limit=page["limit"],
        server_time=page["server_time"],
    )
```

## O protocolo de watermark

Esta é a parte que costuma dar bug. Siga à risca:

1. **Primeiro sync:** chame com `since=None`. Drene todas as páginas via
   `next_cursor` até `has_more` ser `False`.
2. **Guarde o `server_time` da resposta** como próximo `since` — **não** use o
   maior `updated_at` dos itens, nem o relógio do dispositivo.
3. **Próximo sync:** mande aquele `server_time` como `since`. O filtro é
   `updated_at > since` (estrito).

!!! tip "Por que `server_time` e não o relógio do cliente"
    O `server_time` é capturado no servidor **antes** da query rodar. Como ele é
    um marco do próprio relógio do banco, qualquer linha escrita depois tem
    `updated_at` maior e aparece no pull seguinte — imune ao clock skew do
    aparelho.

!!! warning "Tombstones não são opcionais"
    Deixe `include_deleted=True` (padrão). Um pull que esconde os deletados
    deixa linhas excluídas presas no dispositivo para sempre, porque o cliente
    nunca fica sabendo que elas saíram.

## Filtros de comparação

`changes_since` é açúcar em cima de um recurso mais geral: o sufixo
`<coluna>__<op>` em qualquer `filters`. Disponível em `list`, `paginate`,
`cursor_paginate`, `count` etc.

```python
# updated_at > watermark (precisão de timestamp)
await repo.list(filters={"updated_at__gt": watermark})

# faixa: 1 <= value <= 10
await repo.list(filters={"value__gte": 1, "value__lte": 10})

# diferente de
await repo.list(filters={"status__ne": "archived"})
```

Operadores: `gt`, `gte`, `lt`, `lte`, `ne`. Um valor `None` ignora a condição,
igual a qualquer outro filtro.

!!! note "Diferente de `start_in` / `end_in`"
    `start_in` / `end_in` filtram por **dia inteiro** sobre `created_at`. Os
    operadores `__gt` etc. são por **timestamp**, em qualquer coluna — é o que o
    watermark de sync precisa.

## Recap

- Use o **id do cliente como PK** → push vira upsert idempotente.
- Misture **`SoftDeleteMixin`** → exclusões viram tombstones que o pull entrega.
- **`changes_since(since, filters={"user_id": ...})`** é o pull inteiro: delta
  por `updated_at`, ordem estável, cursor e tombstones.
- Persista o **`server_time`** da resposta como próximo `since` — não o relógio
  do dispositivo.
- Por baixo, os operadores **`__gt/gte/lt/lte/ne`** funcionam em qualquer
  `filters`.
