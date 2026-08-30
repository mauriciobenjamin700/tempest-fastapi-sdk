# Migrations

Toda receita de banco assume que o schema já existe. Esta é sobre o passo
anterior: **como o schema nasce**, e por que a versão escrita à mão desse passo
é errada de um jeito que fica invisível por semanas.

O SDK entrega o caminho inteiro em um método — `AlembicHelper.sync_schema()` —
e o resto da página explica o que ele decide, para você reconhecer o estado em
que seu banco está.

## O bootstrap completo

```python
# src/db/schema.py
import asyncio

from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcome

from src.core.settings import settings


async def sync_schema() -> SchemaSyncOutcome:
    """Bring the database schema in line with the migration tree."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    return await asyncio.to_thread(helper.sync_schema)
```

Chame isso do lifespan e o serviço sobe com o schema certo a partir de
**qualquer** estado inicial:

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db.schema import sync_schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Sync the schema before the first request is served."""
    outcome = await sync_schema()
    print(f"schema: {outcome.value}")
    yield


app: FastAPI = FastAPI(lifespan=lifespan)
```

O Alembic é síncrono, por isso o `asyncio.to_thread` — chamar direto de código
async bloqueia o event loop durante a migration inteira.

## Os três estados que ele distingue

`sync_schema()` faz uma pergunta que quase todo bootstrap escrito à mão esquece:
**o banco tem tabelas que o Alembic não criou?**

| Estado inicial | O que roda | Retorno |
| --- | --- | --- |
| Banco vazio | `safe_upgrade()` — a revision base cria as tabelas | `SchemaSyncOutcome.SYNCED` |
| Banco que **precede** o Alembic | carimba a **base**, depois `safe_upgrade()` | `SchemaSyncOutcome.ADOPTED` |
| Banco já sob o Alembic | `safe_upgrade()` | `SchemaSyncOutcome.SYNCED` |
| Projeto ainda sem nenhuma revision | nada | `SchemaSyncOutcome.NO_MIGRATIONS` |

O banco vazio é o caso fácil, e é o que faz o resto funcionar: o schema que a
revision base constrói é, por construção, **o mesmo** que as revisions
seguintes assumem estar alterando — porque veio delas.

## Por que `create_tables()` não serve

A receita de [Banco de dados](database.md) diz que `db.create_tables()` é só
para teste e dev local. Vale a pena dizer também **o que acontece se você
usar**, porque a proibição sem a consequência não faz ninguém enxergar o
defeito no próprio código:

!!! danger "`create_all` é `CREATE TABLE IF NOT EXISTS`"
    Contra uma tabela que **já existe**, `create_tables()` não adiciona coluna
    nenhuma. Ele não falha, não avisa, e não retorna nada diferente. É um
    no-op silencioso.

O defeito real, que derrubou um serviço por um dia:

```python
# scripts/broken_bootstrap.py — o defeito, reproduzido; não é receita.
import asyncio

from tempest_fastapi_sdk import AlembicHelper, AsyncDatabaseManager

from src.core.settings import settings

db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)


async def broken_bootstrap() -> None:
    """Reach the worst possible state: old schema, Alembic reporting head."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    if await asyncio.to_thread(helper.current) is None:
        await db.create_tables()
        await asyncio.to_thread(helper.stamp, "head")
        return
    await asyncio.to_thread(helper.safe_upgrade)
```

Cada linha é plausível. Juntas produzem o pior estado possível: **schema velho,
e o Alembic se declarando em dia.**

```console
$ alembic current
a3f9c21e88b4 (head)

$ alembic upgrade head
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
```

Nada a fazer. `alembic history` não acusa nada. Semanas depois, a primeira query
que usa uma coluna nova estoura longe da causa:

```text
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such column: messages.edited_at
```

Do lado do usuário isso aparece como "mandar mensagem parou de funcionar": o
`GET` do histórico dá 500, e nada no boot avisou.

## Adotar um banco que precede o Alembic

Este é o caso que o código acima tentava tratar. A resposta certa não é
carimbar `head` — é carimbar a **revision base**:

```python
# src/db/schema.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings


def adopt_existing_database() -> bool:
    """Bring a pre-Alembic schema under Alembic, without upgrading it."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    return helper.adopt()
```

Carimbar a base diz *"a baseline já está aplicada"* — o que é verdade, porque as
tabelas existem — e deixa **toda revision depois dela pendente**, o que também é
verdade. O `safe_upgrade()` seguinte roda exatamente essas.

Carimbar `head` diz *"tudo já está aplicado"*, o que é falso para todas menos a
primeira.

`adopt()` não faz nada quando não é o caso: banco já carimbado não tem o que
adotar, e banco vazio precisa que a baseline **rode**, não que seja pulada. É
por isso que `sync_schema()` pode chamá-lo sempre.

!!! tip "Quem responde a pergunta é `has_existing_schema()`"
    Ele lista as tabelas e desconta `alembic_version`, que o próprio Alembic
    escreve — a presença dela não diz nada sobre o schema da aplicação.

## Reparar um banco já carimbado errado

Se você já está no estado ruim, o conserto tem dois passos. O primeiro limpa o
ponteiro; o segundo re-adota corretamente:

```python
# scripts/repair_schema.py
from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcome

from src.core.settings import settings


def repair() -> SchemaSyncOutcome:
    """Clear a wrong stamp and re-adopt the schema from the base."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    helper.stamp("base")
    return helper.sync_schema()
```

`stamp("base")` é diferente de `stamp(helper.base_revision())`: `"base"` é a
palavra do Alembic para *nenhuma revision aplicada*, e apaga a linha de
`alembic_version`. Depois disso o banco volta a parecer o que é — schema
existente, sem ponteiro — e o `sync_schema()` toma o caminho de adoção.

!!! warning "Confira o que está pendente antes de subir"
    Depois do `stamp("base")`, as revisions entre a base e o head vão rodar
    contra um schema que talvez já tenha parte delas. Rode
    `helper.pending_destructive_ops()` e leia o `helper.history()` antes — e
    tenha backup. `safe_upgrade` recusa migration destrutiva sem `force=True`,
    o que ajuda, mas não substitui olhar.

## Onde `create_tables()` é legítimo

Em teste e em dev local descartável — onde o banco nasce e morre no mesmo
processo, e não existe migration para driftar:

```python
# tests/conftest.py
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import AsyncDatabaseManager


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory schema."""
    db: AsyncDatabaseManager = AsyncDatabaseManager(
        "sqlite+aiosqlite:///:memory:"
    )
    await db.create_tables()
    async with db.get_session_context() as opened:
        yield opened
    await db.drop_tables()
```

A razão técnica de funcionar aqui e não em produção é a mesma nos dois casos:
`create_all` só sabe criar o que falta. Num banco que acabou de nascer, "o que
falta" é tudo — então ele acerta. Num banco que já rodou, "o que falta" é
nenhuma tabela, mas possivelmente várias colunas — e coluna ele não olha.

!!! check "Recap"
    - `sync_schema()` é o bootstrap inteiro: distingue banco vazio, banco que
      precede o Alembic e banco já migrado, e devolve qual caminho tomou.
    - `create_tables()` é `CREATE TABLE IF NOT EXISTS` — **no-op silencioso**
      numa tabela existente. Nunca é o passo que faz um schema evoluir.
    - Ao adotar um schema existente, carimbe a **revision base**
      (`helper.base_revision()`, ou simplesmente `helper.adopt()`), nunca
      `head`.
    - Para reparar um `stamp("head")` errado: `stamp("base")` e depois
      `sync_schema()`.
    - `create_tables()` fica legítimo onde não há migration para driftar:
      teste e SQLite in-memory.

## Veja também

- [Banco de dados »](database.md) — sessão, repository, `AsyncDatabaseManager`.
- [CLI »](cli.md) — `tempest db upgrade`, `revision`, `stamp`, `check`.
- [Deploy seguro »](deploy-safety.md) — `safe_upgrade` e o gate de drift na CI.
