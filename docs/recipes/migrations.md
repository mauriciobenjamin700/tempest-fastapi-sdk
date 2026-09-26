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
from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcome

from src.core.settings import settings


async def sync_schema() -> SchemaSyncOutcome:
    """Bring the database schema in line with the migration tree."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    return await helper.sync_schema_async()
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

## De código async, use o método `_async`

O lifespan roda dentro do event loop do uvicorn. Todo método do `AlembicHelper`
que executa o `alembic/env.py` tem um par `_async` — `upgrade_async`,
`safe_upgrade_async`, `downgrade_async`, `stamp_async`, `revision_async`,
`check_async`, `current_async`, `has_existing_schema_async`, `adopt_async`,
`sync_schema_async`, `squash_async`, `pending_destructive_ops_async` — com a
mesma assinatura, e é ele que você chama de código async.

O motivo é o `env.py` que o SDK gera: ele sobe o engine async com
`asyncio.run(...)`, e `asyncio.run` não pode ser chamado com um loop já
rodando. Chamar o método síncrono do lifespan agora falha **na hora**, com a
instrução certa:

```text
RuntimeError: AlembicHelper.upgrade() was called from a running event loop. It runs alembic/env.py, which drives migrations with asyncio.run() and cannot nest inside the loop; use `await helper.upgrade_async(...)` instead.
```

Antes, o mesmo erro saía de dentro do Alembic como `asyncio.run() cannot be
called from a running event loop`, acompanhado de um
`RuntimeWarning: coroutine 'run_async_migrations' was never awaited` — e o
`check()` nem levantava: engolia o erro e respondia `False`, como se o schema
tivesse driftado.

O método `_async` roda o síncrono numa thread de trabalho, que não tem loop
próprio, então o `asyncio.run` do `env.py` funciona lá e o loop do serviço
continua atendendo enquanto a migration roda. Como a thread não depende de
nada novo no `env.py`, **o `env.py` que você já tem no repositório continua
funcionando** — não precisa regenerar.

!!! tip "`current()` síncrono continua funcionando no loop"
    Ler a revision não passa pelo `env.py`: com um driver síncrono instalado
    (o `sqlite3` da stdlib, ou `psycopg2` no PostgreSQL), `helper.current()`
    funciona de código async como antes. Só a instalação só-async (`asyncpg`
    sem driver síncrono) cai no caminho de `asyncio.run` — e aí ele também
    levanta pedindo `current_async()`.

??? info "Detalhes técnicos: compartilhar a conexão em vez de usar thread"
    O Alembic tem uma receita oficial para rodar a migration **no próprio
    loop**: abrir uma `AsyncConnection` e entregar a conexão síncrona que o
    `run_sync` fornece em `config.attributes["connection"]`. O `env.py` que o
    SDK gera a partir desta versão aceita isso — quando recebe a conexão,
    migra nela, sem criar engine nem chamar `asyncio.run`:

    ```python
    # src/db/shared_connection.py
    from alembic import command
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
    from tempest_fastapi_sdk import AlembicHelper

    from src.core.settings import settings


    async def upgrade_on_shared_connection() -> None:
        """Run every pending migration on a connection this code owns."""
        helper: AlembicHelper = AlembicHelper(
            "alembic.ini",
            db_url=settings.DATABASE_URL,
        )
        config = helper.config

        def _upgrade(connection: Connection) -> None:
            """Hand the sync connection to env.py and upgrade on it."""
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        engine: AsyncEngine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(_upgrade)
        await engine.dispose()
    ```

    Os métodos `_async` **não** usam esse caminho, e o motivo é
    compatibilidade: o `env.py` gerado antes desta versão ignora
    `config.attributes` e chama `asyncio.run` de qualquer jeito — dentro do
    `run_sync` o loop está rodando, e ele falha com o mesmo erro de antes. A
    thread funciona com qualquer `env.py` já gerado. Use o caminho acima
    quando precisar que a migration rode na **sua** conexão ou transação, e
    regenere o `env.py` para isso: rode `tempest db init` num diretório
    vazio e copie o `alembic/env.py` gerado por cima do seu, conferindo o
    import de metadata (o default é `from src.db.models import BaseModel`).

    O `env.py` novo também recusa, com mensagem própria, ser executado de
    dentro de um loop **sem** conexão entregue — o caso de quem chama
    `command.upgrade` direto de código async.

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
from tempest_fastapi_sdk import AlembicHelper, AsyncDatabaseManager

from src.core.settings import settings

db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)


async def broken_bootstrap() -> None:
    """Reach the worst possible state: old schema, Alembic reporting head."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    if await helper.current_async() is None:
        await db.create_tables()
        await helper.stamp_async("head")
        return
    await helper.safe_upgrade_async()
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
    - De código async (lifespan, endpoint), chame o par `_async`
      (`await helper.sync_schema_async()`); o método síncrono levanta com um
      loop rodando, nomeando o `_async` a usar.
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
