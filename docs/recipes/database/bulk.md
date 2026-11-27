---
description: "Use para gravar muitas linhas de uma vez no repository, mantendo ou contornando a unit-of-work, inclusive inserção idempotente com ON CONFLICT DO NOTHING."
---

# 5. Operações em lote

Use para gravar muitas linhas de uma vez, mantendo ou contornando a unit-of-work, inclusive inserção idempotente (ON CONFLICT DO NOTHING).

Parte da receita [Banco de dados](../database.md).

Para volume, o ORM linha-a-linha é caro. O repository oferece duas
famílias: as que **mantêm** a unit-of-work (instâncias atualizadas de
volta) e as que a **contornam** (uma única instrução, sem refresh).

```python
import asyncio

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    m1, m2, m3 = (UserModel(name=n, email=f"{n}@x.com") for n in "abc")
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # Mantém a UoW — instâncias anexadas e atualizadas
        created = await repository.add_all([m1, m2, m3])      # vários INSERTs, 1 tx
        u1, u2 = created[0], created[1]
        updated = await repository.update_many([u1, u2])      # vários UPDATEs, 1 tx

        # Contorna a UoW — uma instrução, escala melhor (>= 50 linhas)
        n = await repository.bulk_create_values([
            {"name": "A", "email": "a@x.com", "password_hash": "..."},
            {"name": "B", "email": "b@x.com", "password_hash": "..."},
        ])  # INSERT ... VALUES (...), (...) — devolve nº de linhas

        n = await repository.bulk_update(
            filters={"is_active": False},
            values={"is_active": True},
        )  # UPDATE ... WHERE — devolve nº de linhas afetadas

        n = await repository.bulk_upsert(
            rows=[{"sku": "ABC", "price": 10}, {"sku": "DEF", "price": 20}],
            conflict_columns=["sku"],          # precisa de índice UNIQUE
            update_columns=["price"],          # None = atualiza tudo menos PK + conflito
        )  # INSERT ... ON CONFLICT DO UPDATE — Postgres e SQLite


asyncio.run(main())
```

!!! warning "`bulk_update` recusa filtro vazio"
    Passar `filters={}` levanta `ValueError` — é a trava contra um UPDATE
    acidental na tabela inteira. Para realmente atualizar todas as linhas,
    passe uma condição explícita sempre verdadeira.

!!! danger "`bulk_*` não atualiza a sessão"
    `bulk_create_values`, `bulk_update` e `bulk_upsert` emitem uma
    instrução crua e **não** refrescam nem anexam instâncias à sessão.
    Use quando você não precisa dos objetos ORM de volta. Se precisar das
    instâncias, use `add_all` / `update_many`.

!!! note "`bulk_upsert` é específico de dialeto"
    Postgres e SQLite têm upsert nativo. Outros dialetos levantam
    `NotImplementedError` — caia para um loop `SELECT FOR UPDATE` +
    `UPDATE`.

## Inserção idempotente (ON CONFLICT DO NOTHING)

Às vezes o conflito **é o caso esperado**. Pense numa rotina que
materializa as ocorrências de uma tarefa (`unique(task, occurs_on)`),
chamada pela rota e por uma varredura, às vezes ao mesmo tempo: cada
rodada tenta inserir as mesmas linhas, e o que você quer é criar só as
que faltam — sem duplicar, sem falhar e sem desfazer a transação de quem
chamou.

`on_conflict="nothing"` gera `INSERT ... ON CONFLICT (...) DO NOTHING`: a
linha que já existe fica intacta e a nova é descartada pelo próprio banco.

```python
import asyncio
from datetime import date
from uuid import UUID

from sqlalchemy import Date, String, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import StaticPool
from tempest_fastapi_sdk import BaseModel, BaseRepository


class OccurrenceModel(BaseModel):
    """One occurrence of a task on a given day."""

    __tablename__ = "occurrences"
    __table_args__ = (UniqueConstraint("task", "occurs_on"),)

    task: Mapped[str] = mapped_column(String(32))
    occurs_on: Mapped[date] = mapped_column(Date)


def occurrences(*days: int) -> list[dict[str, object]]:
    """Build one row per day of October 2026 for the task ``math``."""
    return [{"task": "math", "occurs_on": date(2026, 10, day)} for day in days]


async def main() -> None:
    """Materialize the same occurrences twice and see what each call created."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)

    async with AsyncSession(engine) as session:
        repository = BaseRepository(session, model=OccurrenceModel)

        created: int = await repository.bulk_upsert(
            occurrences(1, 2, 3),
            conflict_columns=["task", "occurs_on"],
            on_conflict="nothing",
        )
        print("first round:", created)

        again: int = await repository.bulk_upsert(
            occurrences(1, 2, 3),
            conflict_columns=["task", "occurs_on"],
            on_conflict="nothing",
        )
        print("same rows again:", again)

        new_ids: list[UUID] = await repository.bulk_upsert(
            occurrences(3, 4, 5),
            conflict_columns=["task", "occurs_on"],
            on_conflict="nothing",
            returning_ids=True,
        )
        print("ids created:", len(new_ids))
        print("rows in the table:", await repository.count())

    await engine.dispose()


asyncio.run(main())
```

Rodando, a saída é:

```text
first round: 3
same rows again: 0
ids created: 2
rows in the table: 5
```

Pedaço por pedaço:

* **O retorno é o número de linhas realmente inseridas.** Ele vem do
  `RETURNING` da chave primária, não do `rowcount` do driver, então a
  segunda rodada devolve `0` de verdade.
* **`returning_ids=True` devolve os `id`s criados** (`list[UUID]`) em vez
  da contagem. É o que você usa para publicar um evento por linha nova: só
  as linhas desta chamada aparecem, nunca as que já existiam. A ordem é a
  do banco, não a de `rows`.
* **Nenhum rollback.** O conflito é resolvido pelo banco, sem exceção, então
  um `repository.transaction()` aberto em volta continua inteiro.
* **Concorrência.** Duas sessões inserindo as mesmas chaves não duplicam nem
  falham: a segunda espera o `COMMIT` da primeira e pula as chaves que ela
  criou. Medido no PostgreSQL 16 e no SQLite 3.47
  (`tests/db/test_bulk_upsert_conflict.py`).

!!! warning "Só o conflito em `conflict_columns` é ignorado"
    Violação de **outra** constraint (outro índice único, chave
    estrangeira, `NOT NULL`) continua levantando a exceção de conflito do
    repository, como em `bulk_create_values`.

!!! note "`update_columns` não combina com `on_conflict='nothing'`"
    Passar `update_columns` não vazio com `on_conflict="nothing"` levanta
    `ValueError`: a lista seria ignorada, e quase sempre é chamada errada.
    O inverso também: `on_conflict="update"` sem coluna para atualizar
    (`update_columns=[]`) levanta `ValueError` apontando para `"nothing"`.

??? info "O que o número significa em cada modo"
    | Chamada | Retorno |
    | --- | --- |
    | `on_conflict="update"` (padrão) | `rowcount` do driver: inseridas **+** atualizadas (no PostgreSQL e no SQLite, uma linha em conflito conta uma vez) |
    | `on_conflict="nothing"` | só as inseridas, contadas pelo `RETURNING` (`0` quando todas já existiam) |
    | `returning_ids=True` com `"nothing"` | os `id`s criados |
    | `returning_ids=True` com `"update"` | os `id`s inseridos **ou** atualizados — nenhum dos dois dialetos separa os dois de forma portátil |

    `bulk_create_values` devolve o `rowcount` do driver. Um `0` real chega
    como `0`; só um driver que responde `-1` (contagem desconhecida) cai
    para `len(rows)`.

**Recap:** `add_all`/`update_many` quando você quer as instâncias de
volta; `bulk_*` quando quer throughput. Filtro vazio em `bulk_update` é
erro proposital. Conflito esperado é `bulk_upsert(on_conflict="nothing")`,
com `returning_ids=True` quando você precisa saber quais linhas nasceram.
