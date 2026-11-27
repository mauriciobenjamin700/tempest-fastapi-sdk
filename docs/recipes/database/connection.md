# 2. Conectar ao banco

Use para abrir engine e sessão com `AsyncDatabaseManager`: sessão por request, lifespan, health check, uso fora de request e SQLite (WAL, chave estrangeira).

Parte da receita [Banco de dados](../database.md).

`AsyncDatabaseManager` é instanciado **uma vez** por aplicação e cuida da
engine, do pool e da fábrica de sessões. Coloque-o nas dependências de
infraestrutura, não dentro do `app.py`:

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import AsyncDatabaseManager

from src.core.settings import settings

db = AsyncDatabaseManager(
    settings.DATABASE_URL,
    echo=settings.DEBUG,        # ecoa SQL no stdout em dev
    pool_size=10,               # ignorado para SQLite
    max_overflow=20,
    pool_recycle=3600,
)
```

Ele detecta o backend pela URL (`make_url`), então SQLite ganha
`check_same_thread=False` automaticamente e os parâmetros de pool são
ignorados — não há truque de substring.

## Uma sessão por request

Use `session_dependency` como dependência do FastAPI. Ela entrega uma
sessão por request e **não** faz commit no sucesso — o commit é
responsabilidade da camada de repository/service:

```python
# src/api/dependencies/resources.py (continuação)

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.resources import db


SessionDep = Annotated[AsyncSession, Depends(db.session_dependency)]
```

```python
# src/api/routers/user.py
from uuid import UUID

from fastapi import APIRouter

from src.api.dependencies.resources import SessionDep
from src.db.repositories import UserRepository
from src.schemas import UserResponse

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, session: SessionDep) -> UserResponse:
    """Fetch a single user by id."""
    repository = UserRepository(session)
    return repository.map_to_response(await repository.get_by_id(user_id))
```

## Manager criado sob demanda — `session_dependency_for`

`Depends(db.session_dependency)` exige que `db` já exista quando o módulo é
importado. Tem serviço que prefere construir o manager só na primeira
chamada: para ler as settings **depois** do guard de boot, e para os testes
trocarem a `DATABASE_URL` antes de qualquer engine nascer. Nesse caso,
entregue a **fábrica** para `session_dependency_for`:

```python
# src/api/dependencies/resources.py
import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import AsyncDatabaseManager, session_dependency_for


@lru_cache
def get_db() -> AsyncDatabaseManager:
    """Build the manager on first use, never at import."""
    return AsyncDatabaseManager(os.environ["DATABASE_URL"])


get_session = session_dependency_for(get_db)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
```

A dependency devolvida tem a mesma semântica de `session_dependency`: uma
sessão por request, **sem** commit no sucesso, e a sessão fechada no fim do
request — também quando o endpoint levanta. `get_db` é chamado a cada
request, nunca no import; com o `@lru_cache`, todo request recebe o mesmo
manager.

!!! tip "Crie a dependency uma vez, no nível do módulo"
    O FastAPI faz cache de dependency por request pela **identidade** do
    callable. Duas chamadas a `session_dependency_for(get_db)` devolvem duas
    funções diferentes — e, usadas no mesmo request, duas sessões. Guarde o
    resultado num nome (`get_session`) e reuse.

## Ciclo de vida no lifespan

Abra e feche a engine junto com a aplicação, e traga o schema para a head
das migrations **antes** de atender o primeiro request:

```python
# src/api/app.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.configs import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Migrate the schema, open the database, dispose it on shutdown."""
    await run_migrations(settings.DATABASE_URL)
    await db.connect()
    yield
    await db.disconnect()
```

O `run_migrations` é o mesmo que o `tempest new` gera em
`src/db/configs/migrations.py`: uma chamada a
`AlembicHelper.sync_schema_async()` com o caminho absoluto do `alembic.ini`.

```python
# src/db/configs/migrations.py
from pathlib import Path

from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcomeEnum

ALEMBIC_INI_PATH: Path = Path(__file__).resolve().parents[3] / "alembic.ini"


async def run_migrations(database_url: str) -> SchemaSyncOutcomeEnum:
    """Bring the database schema to the Alembic head."""
    return await AlembicHelper(
        str(ALEMBIC_INI_PATH),
        db_url=database_url,
    ).sync_schema_async()
```

O scaffold ainda envolve a chamada em
`if settings.DATABASE_MIGRATE_ON_STARTUP:` — campo do `Settings` gerado, não
de um mixin do SDK — para desligá-la quando várias réplicas sobem juntas e um
job roda `tempest db upgrade`.

O `alembic.ini` e o `alembic/env.py` vêm do `AlembicHelper.init(...)` (ou
`tempest db init`), não escritos à mão — veja
[Bootstrap, uma vez por projeto](migrations.md#bootstrap-uma-vez-por-projeto). E o
`sync_schema_async()` só cria tabela a partir de revision: sem nenhuma
revision em `alembic/versions/`, ele devolve
`SchemaSyncOutcomeEnum.NO_MIGRATIONS` e não toca no banco. Gere a revision
inicial (`tempest db revision -m "initial"`) antes da primeira subida.

!!! danger "Nada de `create_tables()` no lifespan"
    É a forma que parece equivalente, e não é:

    ```python
    # src/api/app.py — errado: o schema para de evoluir na segunda subida.
    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from src.api.dependencies.resources import db


    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Create the tables on every boot."""
        await db.connect()
        await db.create_tables()
        yield
        await db.disconnect()
    ```

    Na primeira subida ela cria tudo. Na seguinte, com uma coluna nova no
    model, `create_tables()` não acrescenta a coluna, não falha e não avisa
    — medido no SQLite: a tabela `notes` continua com
    `['title', 'id', 'is_active', 'created_at', 'updated_at']` depois de o
    model ganhar `body`. O erro só aparece na primeira query que lê a coluna.
    `create_tables()` fica em fixture de teste com SQLite em memória, onde o
    banco nasce e morre no processo — veja
    [Migrations »](../migrations.md#onde-create_tables-e-legitimo).

## Health check

`health_check()` roda um `SELECT 1` e engole qualquer exceção, devolvendo
só `True`/`False` — perfeito para `/health`:

```python
from fastapi import APIRouter

from src.api.dependencies.resources import db

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    """Liveness + database probe."""
    return {
        "status": "ok",
        "database": await db.health_check(),
        "url": db.db_url_safe,   # credenciais mascaradas
    }
```

!!! info "Outras formas de obter sessão"
    - `db.get_session_context()` / `db.transaction()` — o mesmo context
      manager com dois nomes: faz **commit** na saída e rollback no erro.
      Use em scripts e tasks de background.
    - `db.get_session()` — sessão crua; você fecha.
    - `db.create_tables()` / `db.drop_tables()` — só para testes e dev
      local; em produção o schema é do Alembic. Sem argumento, só os models
      do `BaseModel` do SDK — veja
      [Sua própria `DeclarativeBase`](#sua-propria-declarativebase).

!!! warning "`get_session_context()` commita — nunca numa dependency de request"
    O código depois do `yield` de uma dependency roda **depois** que a
    resposta saiu. Medido no FastAPI 0.141.1, com um middleware ASGI
    registrando as mensagens: `http.response.start` e `http.response.body`
    são enviadas antes do `COMMIT`. Uma dependency que embrulha
    `get_session_context()` responde `204` para um `DELETE` cujo commit
    ainda não aconteceu — o `GET` seguinte do cliente pode ver a linha — e,
    se esse commit falhar, o status que o cliente já recebeu não muda mais.
    Para request use `session_dependency` (ou `session_dependency_for`) e
    commite na camada de service/repository.

!!! danger "`create_tables()` é no-op silencioso em tabela que já existe"
    `create_all` é `CREATE TABLE IF NOT EXISTS`: contra uma tabela existente
    ele **não adiciona coluna nenhuma**, não falha e não avisa. Usá-lo no boot
    e carimbar `head` em seguida deixa o schema velho com o Alembic se
    declarando em dia — a falha aparece semanas depois, longe da causa. O
    bootstrap correto, para os três estados possíveis do banco, está em
    [Migrations »](../migrations.md).

!!! danger "Nunca logue a URL crua, sempre `db_url_safe`"
    A URL crua carrega usuário e senha. `db_url_safe` troca **só a senha**:
    `postgresql+asyncpg://user:pass@host:5432/db` sai como
    `postgresql+asyncpg://user:***@host:5432/db` — usuário, host, porta,
    banco e query string continuam visíveis, então trate o resultado como
    diagnóstico interno se o próprio usuário do banco for sensível. A URL
    crua fica num atributo privado justamente para não vazar em `repr()` ou
    log acidental.

## Sua própria `DeclarativeBase`

`create_tables()` sem argumento cria só o que herda o `BaseModel` do SDK. Um
serviço com a própria `DeclarativeBase` — porque endereça por chave natural,
por exemplo, e o `BaseModel` impõe UUID e timestamps — não ganha tabela
nenhuma: a primeira query falha com `no such table` (SQLite). Passe a
`metadata` da sua base:

```python
import asyncio

from sqlalchemy import Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from tempest_fastapi_sdk import AsyncDatabaseManager


class Base(DeclarativeBase):
    """A base of your own: natural key, no BaseModel UUID or timestamps."""


class ObjectModel(Base):
    __tablename__ = "objects"

    bucket: Mapped[str] = mapped_column(String(63), primary_key=True)
    key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables(Base.metadata)
    async with db.transaction() as session:
        session.add(ObjectModel(bucket="photos", key="cat.jpg", size=2048))
    async with db.transaction() as session:
        rows = (await session.scalars(select(ObjectModel))).all()
        print([(row.bucket, row.key, row.size) for row in rows])
    await db.disconnect()


asyncio.run(main())
```

Saída: `[('photos', 'cat.jpg', 2048)]`. `drop_tables(Base.metadata)` faz o
caminho inverso. Os dois continuam sendo ferramenta de teste e dev local.

## Fora de um request

Nem todo consumidor tem uma requisição onde pendurar o `Depends`. Uma
ferramenta de agente, uma task TaskIQ, um consumer FastStream e um script de
manutenção rodam fora do ciclo HTTP — e todos usam `get_session_context()`,
que abre a sessão, **confirma** ao sair e faz rollback no erro:

```python
# src/tasks/cleanup.py
from src.api.dependencies.resources import db
from src.db.repositories import UserRepository


async def count_inactive_users() -> int:
    """Count the users that were deactivated."""
    async with db.get_session_context() as session:
        repository = UserRepository(session)
        return len(await repository.list(filters={"is_active": False}))
```

A regra é abrir o mais tarde possível e fechar o quanto antes: um processo que
segura a sessão enquanto espera outra coisa — um modelo gerando tokens, uma API
externa respondendo — ocupa uma conexão do pool sem usá-la.

!!! tip "Ferramenta de agente é o caso mais delicado"
    Uma execução de agente atravessa vários passos e pode levar minutos.
    [Agentes de IA (banco de dados) »](../agents-db.md) mostra por que a sessão é
    aberta **dentro** de cada ferramenta, o que o commit automático significa
    para uma ferramenta que escreve, e por que dois `AsyncDatabaseManager` no
    mesmo processo são dois pools.

## SQLite com um worker: WAL e busy timeout

No dia em que a aplicação ganha um worker, o SQLite de desenvolvimento
passa a ter **dois processos** escrevendo no mesmo arquivo. No journal
padrão (`delete`) um leitor e um escritor se excluem, então o segundo
morre:

```text
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
[SQL: INSERT INTO budget_drafts (...) VALUES (?, ?, ...)]
```

Medido entre dois processos — um segurando uma transação de leitura
aberta enquanto o outro insere:

| `journal_mode` | O que acontece com o escritor |
| --- | --- |
| `delete` | espera o `busy_timeout` inteiro e falha com `database is locked` |
| `wal` | commita na hora |

Por isso o `AsyncDatabaseManager` abre todo arquivo SQLite em **WAL**,
com `busy_timeout` de 30 segundos. Não é preciso pedir:

```python
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
# journal_mode = wal, busy_timeout = 30000 (ms)
```

Os dois são reguláveis, e ignorados em qualquer outro backend:

```python
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager(
    "sqlite+aiosqlite:///./app.db",
    sqlite_wal=False,           # sistemas de arquivos sem shared memory
    sqlite_busy_timeout=5.0,    # segundos
)
```

Pelo ambiente, via `DatabaseSettings`: `DATABASE_SQLITE_WAL` e
`DATABASE_SQLITE_BUSY_TIMEOUT`.

!!! info "WAL é propriedade do arquivo"
    Basta ligar uma vez: o modo sobrevive ao processo e todo conexão
    posterior, de qualquer processo, já abre o arquivo em WAL. Em banco
    `:memory:` o pragma é inócuo — o SQLite responde `memory` e segue.

!!! info "`:memory:` recebe conexão de verdade por sessão (v0.252.0)"
    `sqlite+aiosqlite:///:memory:` faz o SQLAlchemy escolher `StaticPool`:
    **uma** conexão DBAPI compartilhada por todas as sessões. Junto com o
    `BEGIN` explícito que o manager emite desde a v0.200.0 — necessário para
    o `RELEASE SAVEPOINT` parar de comitar no SQLite — isso quebrava qualquer
    par de sessões sobrepostas com `cannot start a transaction within a
    transaction`. Pega o padrão de teste que este SDK recomenda, e pega um
    endpoint que responde e termina o trabalho num `BackgroundTasks`.

    O manager passa a reescrever a URL para um banco in-memory de **cache
    compartilhado** (`file:<nome>?mode=memory&cache=shared&uri=true`), com
    pool normal, e mantém uma conexão viva enquanto o manager existe — um
    banco in-memory de cache compartilhado é destruído quando a última
    conexão fecha. Cada manager recebe um nome próprio, então dois managers
    continuam isolados.

    Medido nas duas propriedades: sessão sobreposta funciona **e** bloco
    aninhado que sai limpo continua não durável depois de um rollback
    externo. Tirar o `BEGIN` — a saída mais óbvia — dá a primeira e perde a
    segunda.

    Precisa da topologia antiga? Passe `poolclass=StaticPool`
    explicitamente: pool informado pelo caller nunca é sobrescrito.

!!! warning "O que esperar não conserta"
    WAL admite **um escritor por vez**; os outros aguardam o
    `busy_timeout`. O que timeout nenhum resolve é uma transação que
    **lê primeiro e escreve depois**: promover o lock falha na hora se
    outra conexão escreveu no meio, e o `busy_timeout` não se aplica
    porque não há o que aguardar. Em trabalho longo: reivindique a
    linha, faça o trabalho **sem sessão aberta**, e só então persista.

!!! info "`COMMIT` recusado não suja mais a conexão do pool"
    Sem WAL, um leitor aberto em outra conexão faz o `COMMIT` falhar com
    `database is locked`. A transação continua aberta no driver, e o
    SQLAlchemy não a fecha: a conexão voltava ao pool dentro do `BEGIN`, e o
    próximo uso dela falhava com `cannot start a transaction within a
    transaction`. O `enable_sqlite_savepoints` — que o manager e o
    `create_test_engine` aplicam — dá `ROLLBACK` na devolução ao pool sempre
    que o driver ainda está em transação. O `COMMIT` recusado continua
    levantando `OperationalError`; o que muda é que a conexão seguinte sai
    limpa, e a escrita recusada não fica durável. Engine montado à mão
    precisa chamar `enable_sqlite_savepoints` para ganhar o mesmo reset.

## SQLite com chave estrangeira: `PRAGMA foreign_keys`

O SQLite lê a cláusula `REFERENCES`, mas **só confere** a chave estrangeira
numa conexão que rodou `PRAGMA foreign_keys=ON` — desligado é o default dele,
e o pragma vale por conexão, não pelo arquivo. Desligado, três coisas passam
no SQLite e falham (ou fazem outra coisa) no PostgreSQL:

| Operação | FK desligada | FK ligada |
| --- | --- | --- |
| inserir filho apontando para pai inexistente | aceita | `IntegrityError` |
| apagar o pai com `ON DELETE CASCADE` | filho fica órfão | filho apagado |
| `add_all([filho, pai])` sem `relationship()` ordenando o flush | aceita | `IntegrityError` |

Medido no SQLAlchemy 2.0.52 e 2.1.3, aiosqlite 0.22.1, SQLite 3.47.1. Por
isso o `AsyncDatabaseManager` liga o pragma em todo engine SQLite — arquivo e
`:memory:` —, igual ao WAL:

```python
import asyncio

from sqlalchemy import ForeignKey, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager


class Base(DeclarativeBase):
    """Base declarativa só deste exemplo."""


class Org(Base):
    """Organização: a linha pai."""

    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(primary_key=True)


class Member(Base):
    """Membro: apagado junto com a organização."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))


async def main() -> None:
    """Mostra o órfão recusado e o cascade aplicado."""
    db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables(Base.metadata)

    async with await db.get_session() as session:
        session.add(Member(id=1, org_id=999))
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            print("órfão:", exc.orig)

        session.add(Org(id=1))
        await session.flush()
        session.add(Member(id=2, org_id=1))
        await session.commit()

        org: Org | None = await session.get(Org, 1)
        await session.delete(org)
        await session.commit()
        left: int = (
            await session.execute(select(func.count()).select_from(Member))
        ).scalar_one()
        print("membros depois de apagar a organização:", left)

    await db.disconnect()


asyncio.run(main())
```

```text
órfão: FOREIGN KEY constraint failed
membros depois de apagar a organização: 0
```

!!! tip "INSERT fora de ordem"
    Sem `relationship()` entre os dois models, o unit of work do SQLAlchemy
    não sabe que o membro depende da organização e manda os INSERTs na ordem
    do `add_all`. Declare o `relationship()`, ou dê `flush()` depois do pai,
    como o exemplo faz.

Para desligar — banco legado com linha órfã que você ainda não pode limpar:

```python
from tempest_fastapi_sdk import AsyncDatabaseManager

db: AsyncDatabaseManager = AsyncDatabaseManager(
    "sqlite+aiosqlite:///./legado.db",
    sqlite_foreign_keys=False,
)
```

Pelo ambiente, via `DatabaseSettings`: `DATABASE_SQLITE_FOREIGN_KEYS=false`.
Em teste, `create_test_engine(foreign_keys=False)` — veja
[Testes](../testing.md#o-engine-de-teste-confere-chave-estrangeira).

!!! info "Por que o pragma roda no `connect`"
    O SQLite ignora `PRAGMA foreign_keys` dentro de uma transação, sem erro.
    Como o manager emite `BEGIN` explícito (é o que faz o `RELEASE SAVEPOINT`
    parar de comitar), um pragma emitido pela sessão nunca teria efeito:
    medido, ele continua respondendo `0`. O SDK registra um listener no
    evento `connect` do engine, que roda o pragma pelo cursor do driver antes
    de qualquer transação. Engine montado à mão ganha o mesmo comportamento
    com `enable_sqlite_foreign_keys(engine)`.

!!! warning "Migration não roda com FK ligada"
    O batch mode do Alembic recria a tabela no SQLite, e com FK ligada o
    `DROP TABLE` da cópia antiga dispara o `ON DELETE CASCADE`: medido, os
    filhos vão de 3 linhas para 0, sem erro. Os engines de migration do SDK
    deixam a FK desligada, e o `env.py` gerado recusa uma conexão entregue com
    ela ligada — veja [Migrações](../migrations.md#sqlite-migration-roda-com-chave-estrangeira-desligada).

**Recap:** um `AsyncDatabaseManager` por app, em `resources.py`;
`session_dependency` injeta a sessão por request (`session_dependency_for`
quando o manager nasce sob demanda), e `get_session_context()`/`transaction()`,
que commitam, ficam fora do request; `create_tables(metadata)` aceita a sua
`DeclarativeBase`; `connect`/`disconnect`
no lifespan; `health_check` + `db_url_safe` no `/health`; em SQLite, WAL
e `busy_timeout` já vêm ligados para web e worker conviverem, e a chave
estrangeira é conferida como no PostgreSQL.
