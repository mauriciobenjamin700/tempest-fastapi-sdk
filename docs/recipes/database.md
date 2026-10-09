# Banco de dados

Esta é a camada que toda service Tempest usa para falar com PostgreSQL
(produção) ou SQLite (desenvolvimento/testes) sobre **SQLAlchemy 2.0
async**. Ela existe para que você nunca reescreva a mesma engine,
a mesma sessão por request, o mesmo CRUD e a mesma paginação em cada
projeto.

!!! info "Instalação"
    O núcleo de banco já vem com `tempest-fastapi-sdk`. Os drivers async
    entram por extra — `uv add "tempest-fastapi-sdk[postgres]"` (PostgreSQL,
    traz `asyncpg`) ou `[sqlite]` (SQLite em dev, traz `aiosqlite`).

São quatro peças, e você vai conhecê-las uma de cada vez:

| Peça | Símbolo | Para quê |
| --- | --- | --- |
| Modelo base | `BaseModel` | As quatro colunas canônicas (`id` / `is_active` / `created_at` / `updated_at`) + helpers de serialização. |
| Conexão | `AsyncDatabaseManager` | Engine, pool, sessão por request, `health_check`. |
| Repository | `BaseRepository[Model]` | CRUD async, filtros por convenção, operações em lote, paginação. |
| Migrações | `AlembicHelper` | Bootstrap do Alembic, autogenerate, gate de drift no CI. |

Mais três opcionais que entram quando o domínio pede: os **mixins**
(`SoftDeleteMixin`, `AuditMixin`, `MFAMixin`), a **paginação por cursor**
e o **`SlowQueryLogger`**.

!!! tip "Como ler esta página"
    Ela é progressiva. Comece pelo modelo, conecte o banco, suba um
    repository, aprenda os filtros, então paginação, migrações e
    observabilidade. Cada bloco de código é um arquivo completo — copie,
    cole, rode. Se você só quer a referência da API, pule para
    [Referência »](../reference.md).

---

## 1. O modelo base

Todo modelo da sua service herda de `BaseModel`. Você ganha quatro
colunas sem escrever nenhuma:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserModel(BaseModel):
    """Users table."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

Isso já cria a tabela `user` com **sete** colunas: as três suas
(`name`, `email`, `password_hash`) mais as quatro herdadas:

| Coluna | Tipo | Padrão | Papel |
| --- | --- | --- | --- |
| `id` | `UUID` (v4) | `uuid4()` | Chave primária, portável entre Postgres/SQLite/MySQL/MSSQL. |
| `is_active` | `bool` | `True` | Flag de soft-delete rápido. |
| `created_at` | `datetime` (tz-aware) | `utcnow()` no flush | Carimbo de criação. |
| `updated_at` | `datetime` (tz-aware) | `utcnow()` no `onupdate` | Carimbo da última escrita. |

!!! info "Por que o nome da tabela é `user` e não `UserModel`?"
    `BaseModel` deriva `__tablename__` da classe automaticamente: tira o
    sufixo `Model` e converte para `snake_case`. `UserModel` → `user`,
    `OrderItemModel` → `order_item`. Você sempre pode fixar
    `__tablename__ = "users"` explicitamente — a declaração explícita
    vence o automático.

!!! tip "Fixar o nome não é só gosto"
    `USER` é palavra reservada no SQL padrão. O SQLAlchemy sempre escreve
    o identificador entre aspas, então a aplicação funciona — mas um
    `SELECT * FROM user` digitado à mão no `psql` devolve o **usuário do
    banco**, não a sua tabela, e sem erro nenhum. O plural (`users`)
    evita a pegadinha e é a convenção que o próprio SDK assume nas
    tabelas de token (`user_tokens`, `user_refresh_tokens`).

### Centralizando os nomes das tabelas

Um nome de tabela quase nunca aparece só uma vez. Ele está no
`__tablename__` **e** volta como string em toda `ForeignKey` que aponta
para ela:

```python hl_lines="9 12"
# src/db/models/user_token.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserTokenModel(BaseModel):
    __tablename__ = "user_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
```

Renomear `users` passa a depender de você lembrar de cada lugar em que a
string aparece. E esquecer uma FK não estoura na hora: o SQLAlchemy só
resolve o alvo quando monta os mapeamentos, então o erro chega na
**subida da aplicação** — ou, pior, numa migração que aponta para uma
tabela que não existe mais.

A saída é um módulo que só guarda nomes:

```python
# src/db/configs/names.py
"""Nomes das tabelas do projeto. Fonte única de verdade."""

USER_TABLE_NAME = "users"
USER_TOKEN_TABLE_NAME = "user_tokens"
USER_REFRESH_TOKEN_TABLE_NAME = "user_refresh_tokens"
ORDER_TABLE_NAME = "orders"
ORDER_ITEM_TABLE_NAME = "order_items"
```

O sufixo `_TABLE_NAME` deixa a constante autoexplicativa no ponto de
uso, onde ela aparece longe deste arquivo: `ForeignKey(f"{USER_TABLE_NAME}.id")`
diz sozinho o que é aquela string. O prefixo acompanha o modelo, no
singular (`UserTokenModel` → `USER_TOKEN_TABLE_NAME`), mesmo quando o
valor é plural.

Cada modelo passa a importar dali, dos dois lados da relação:

```python hl_lines="6 10"
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME


class UserModel(BaseModel):
    __tablename__ = USER_TABLE_NAME

    email: Mapped[str] = mapped_column(unique=True)
```

```python hl_lines="9 13 16"
# src/db/models/user_token.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME, USER_TOKEN_TABLE_NAME


class UserTokenModel(BaseModel):
    __tablename__ = USER_TOKEN_TABLE_NAME

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{USER_TABLE_NAME}.id", ondelete="CASCADE"),
        index=True,
    )
```

O ganho aparece na hora de mexer. Renomear uma tabela vira **uma linha**
em `names.py`. O "find usages" do editor encontra todo mundo que depende
dela, porque virou símbolo em vez de string solta. E nenhuma
`ForeignKey` consegue apontar para uma tabela que não existe mais sem que
o import quebre antes.

!!! tip "Por que `db/configs/` e não `core/constants.py`?"
    Nome de tabela é detalhe do banco, e quem consome é `db/models/`.
    Deixar em `db/` mantém a dependência dentro da própria camada, e o
    módulo fica sem import nenhum do projeto — ele é só string. É isso
    que garante que ele nunca participe de um ciclo: `models` importa
    `configs`, e `configs` não importa ninguém.

!!! check "Vale também para as tabelas do SDK"
    Os modelos abstratos que o SDK entrega (`BaseUserModel`,
    `BaseUserTokenModel`, `BaseUserRefreshTokenModel`,
    `BaseWebPushSubscriptionModel`, `BaseOutboxModel`) deixam de propósito
    o `__tablename__` e a FK para o projeto concreto declarar — justamente
    para que os dois saiam do seu `names.py`, sob a sua convenção.

### `__tablename__` explícito com o Pyright

`BaseModel` declara `__tablename__` como um `@declared_attr.directive`,
que é o mecanismo do SQLAlchemy 2.0 para derivar o nome a partir da
classe. O mypy entende que uma subclasse sobrescreva isso com uma string
e **não reclama** — é o checker que `tempest type` roda, então o gate
padrão passa limpo.

O Pyright é mais estrito: lê o atributo herdado como variável mutável de
tipo invariante e acusa a atribuição.

!!! warning "`reportIncompatibleVariableOverride` no Pyright/Pylance"
    ```text
    Type "Literal['users']" is not assignable to declared type
    "_declared_directive[str]" (reportAssignmentType)
    ```

    Não é defeito no seu modelo: em tempo de execução a atribuição
    funciona, e é a forma usada em toda esta documentação. É o Pyright
    sendo mais rígido que o mypy sobre override de descritor.

Se o seu editor roda Pyright e você quer o arquivo limpo, declare o nome
pelo mesmo mecanismo da classe base:

```python hl_lines="7 8"
from sqlalchemy.orm import declared_attr
from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME


class UserModel(BaseModel):
    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """Fixa o nome da tabela."""
        return USER_TABLE_NAME
```

Mais verboso, e equivalente em runtime. Escolha pelo checker do projeto:
com mypy (ou sem checagem estática no editor), prefira
`__tablename__ = USER_TABLE_NAME`, que é mais direto de ler.

### Convenção de nomes de constraints

`BaseModel.metadata` já vem configurado com `NAMING_CONVENTION`. Isso faz
toda PK/FK/índice/unique/check receber um nome **determinístico** —
`ix_user_email`, `uq_user_email`, `fk_order_user_id_user` — igual em toda
máquina e todo engine.

!!! check "O ganho real está nas migrações"
    Sem nomes determinísticos, o `alembic revision --autogenerate`
    inventa identificadores aleatórios e cada desenvolvedor gera um diff
    diferente para o mesmo schema. Com a convenção, o autogenerate só
    emite **diffs de schema reais** — sem churn de nomes.

### Unique composta — o nome leva todas as colunas

Uma regra de unicidade que envolve mais de uma coluna ("o mesmo título não
pode repetir no mesmo ano") é uma `UniqueConstraint` com várias colunas em
`__table_args__`. Você não precisa dar nome a ela — a convenção dá:

```python
# src/db/models/book.py
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class BookModel(BaseModel):
    """Livros do catálogo."""

    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("isbn"),
        UniqueConstraint("title", "release_year"),
        UniqueConstraint("title", "author"),
        Index(None, "author", "title"),
    )

    isbn: Mapped[str] = mapped_column()
    title: Mapped[str] = mapped_column()
    release_year: Mapped[int] = mapped_column()
    author: Mapped[str] = mapped_column()


names: list[str] = sorted(
    str(item.name)
    for item in [*BookModel.__table__.constraints, *BookModel.__table__.indexes]
)
print(names)
```

Saída:

```text
['ix_books_author_books_title', 'pk_books', 'uq_books_isbn', 'uq_books_title_author', 'uq_books_title_release_year']
```

Cada constraint composta recebe **todas** as colunas no nome, unidas por `_`:
`uq_books_title_release_year` e `uq_books_title_author` são duas regras
diferentes e têm dois nomes diferentes. Os templates usam os tokens
`column_0_N_*` do SQLAlchemy, que para uma coluna só renderizam exatamente o
que os `column_0_*` renderizavam — então `uq_books_isbn`, `ix_user_email` e
`fk_order_user_id_user` continuam iguais.

Nome distinto é o que torna útil o `constraint` que o Postgres devolve em
[`parse_integrity_error`](#qual-constraint-recusou-parse_integrity_error): um
dicionário `nome → código de erro` passa a apontar para uma regra só.

```python
from typing import Any

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk import parse_integrity_error

CONFLICT_CODES: dict[str, str] = {
    "uq_books_isbn": "ISBN_TAKEN",
    "uq_books_title_release_year": "TITLE_TAKEN_THIS_YEAR",
    "uq_books_title_author": "AUTHOR_ALREADY_HAS_TITLE",
}


def to_conflict(error: IntegrityError) -> dict[str, Any]:
    """Traduz a violação no código que o cliente ramifica."""
    failure = parse_integrity_error(error)
    return {
        "code": CONFLICT_CODES.get(failure.constraint or "", "CONFLICT"),
        "fields": list(failure.columns),
    }
```

!!! warning "Antes desta correção, as duas uniques tinham o mesmo nome"
    Até a versão anterior, os templates de `uq`, `ix` e `fk` liam **só a
    primeira coluna**. `UniqueConstraint("title")` e
    `UniqueConstraint("title", "release_year")` viravam ambas
    `uq_books_title`. O PostgreSQL recusa o `CREATE TABLE` com
    `relation "uq_books_title" already exists`; o SQLite aceita as duas, e é
    por isso que o defeito passava no banco de teste. Quando o banco foi
    criado com a convenção antiga, as constraints compostas dele ainda têm o
    nome antigo — o passo a passo para renomear está em
    [Migrar constraints compostas da convenção antiga](#migrar-constraints-compostas-da-convencao-antiga).

!!! note "Nome acima de 63 caracteres"
    O PostgreSQL aceita identificador de até 63 caracteres. Quando o nome da
    convenção passa disso, o SQLAlchemy encurta na hora de emitir o DDL: os
    55 primeiros caracteres, `_` e os 4 últimos dígitos hexadecimais do MD5
    do nome completo — `uq_subscription_billing_events_customer_identifier_bill_f06e`
    para uma unique de 90 caracteres. É o mesmo em toda execução, e o
    autogenerate não acusa diferença depois (medido contra PostgreSQL 16). O
    SQLite não tem limite e guarda o nome inteiro.

??? info "A única ambiguidade que sobra"
    Juntar com `_` deixa um caso que a convenção não separa: colunas
    `("a_b", "c")` e `("a", "b_c")` na mesma tabela geram o mesmo nome. Se o
    seu schema tem esse par, dê `name=` explícito a uma delas — nome
    explícito vence a convenção.

### Helpers que vêm de graça

Toda instância de `BaseModel` ganha:

```python
# Serializar para dict (útil em logs/testes)

from typing import Any

from src.db.models import UserModel
from src.schemas import UserUpdateSchema

payload = UserUpdateSchema(name="Ana Paula")
user = UserModel(name="Ana", email="ana@example.com")


data: dict[str, Any] = user.to_dict(exclude=["password_hash"])

# Atribuir vários campos de uma vez, com whitelist contra mass-assignment
user.update_from_dict(
    payload.model_dump(exclude_unset=True),
    allowed_fields={"name", "email"},   # id/role nunca são escritos
)
```

`__eq__` e `__hash__` comparam por `(tipo, id)`, então a mesma linha
carregada em sessões diferentes é igual — prático em testes e `set`s.
Linhas ainda não persistidas (`id is None`) caem para identidade Python.

!!! warning "Use sempre `allowed_fields` em payloads externos"
    `update_from_dict` sem `allowed_fields` aceita qualquer coluna
    mapeada. Para corpos de PATCH vindos do cliente, passe a whitelist —
    é a defesa contra mass-assignment em colunas sensíveis (`id`, `role`,
    `is_active`).

### O nome da tabela — `get_table_name()`

Chave de dict, lookup no painel, assert de teste: quando você precisa do
nome da tabela, peça ao model, não ao dunder:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite, BaseModel


class OrderItemModel(BaseModel):
    """Item de um pedido."""


print(OrderItemModel.get_table_name())  # order_item

site = AdminSite(title="Painel")
site.register(AdminModel(model=OrderItemModel))
admin = site.get(OrderItemModel)
```

`get_table_name()` lê a tabela que o mapper construiu, não o
`__tablename__`: responde o nome derivado da classe, o `__tablename__`
explícito e também o model declarado com `__table__ = Table(...)`, que
não tem `__tablename__` nenhum. Numa herança de tabela única, a subclasse
responde a tabela do pai, onde as linhas dela moram. O retorno é `str` para
o type-checker, e `AdminSite.get` / `require` / `unregister` aceitam a
própria classe no lugar da string.

**Recap:** herde `BaseModel`, declare só as colunas do seu domínio, e o
SDK entrega id/timestamps/soft-delete, nomes de constraint determinísticos
(unique composta com todas as colunas no nome) e helpers de serialização.

---

## 2. Conectando ao banco

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

### Uma sessão por request

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

### Manager criado sob demanda — `session_dependency_for`

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

### Ciclo de vida no lifespan

Abra e feche a engine junto com a aplicação:

```python
# src/api/app.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.dependencies.resources import db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Open the database on startup, dispose it on shutdown."""
    await db.connect()
    yield
    await db.disconnect()
```

### Health check

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
    [Migrations »](migrations.md).

!!! danger "Nunca logue a URL crua, sempre `db_url_safe`"
    A URL crua carrega usuário e senha. `db_url_safe` troca **só a senha**:
    `postgresql+asyncpg://user:pass@host:5432/db` sai como
    `postgresql+asyncpg://user:***@host:5432/db` — usuário, host, porta,
    banco e query string continuam visíveis, então trate o resultado como
    diagnóstico interno se o próprio usuário do banco for sensível. A URL
    crua fica num atributo privado justamente para não vazar em `repr()` ou
    log acidental.

### Sua própria `DeclarativeBase`

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

### Fora de um request

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
    [Agentes de IA (banco de dados) »](agents-db.md) mostra por que a sessão é
    aberta **dentro** de cada ferramenta, o que o commit automático significa
    para uma ferramenta que escreve, e por que dois `AsyncDatabaseManager` no
    mesmo processo são dois pools.

### SQLite com um worker: WAL e busy timeout

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

### SQLite com chave estrangeira: `PRAGMA foreign_keys`

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
[Testes](testing.md#o-engine-de-teste-confere-chave-estrangeira).

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
    ela ligada — veja [Migrações](migrations.md#sqlite-migration-roda-com-chave-estrangeira-desligada).

**Recap:** um `AsyncDatabaseManager` por app, em `resources.py`;
`session_dependency` injeta a sessão por request (`session_dependency_for`
quando o manager nasce sob demanda), e `get_session_context()`/`transaction()`,
que commitam, ficam fora do request; `create_tables(metadata)` aceita a sua
`DeclarativeBase`; `connect`/`disconnect`
no lifespan; `health_check` + `db_url_safe` no `/health`; em SQLite, WAL
e `busy_timeout` já vêm ligados para web e worker conviverem, e a chave
estrangeira é conferida como no PostgreSQL.

---

## 3. O repository

`BaseRepository[Model]` é o coração da camada. Ele encapsula o CRUD async,
os filtros, as operações em lote e a paginação. Há dois jeitos de usá-lo.

### Modo direto — CRUD puro

Quando você não tem query custom, instancie direto:

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


repository = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    user = await repository.get_by_id(user_id)


asyncio.run(main())
```

### Modo subclasse — quando há queries próprias

Subclassifique para adicionar consultas do domínio e os mappers que
traduzem ORM ↔ DTO. São três: `map_to_model` (padrão `self.model(**data)`),
`map_to_response` e `map_to_schema` — os dois últimos levantam
`NotImplementedError` até você sobrescrever, então implemente os que o seu
código chama (o exemplo implementa `map_to_response` e `map_to_model`).
**O construtor é o contrato** — você repassa `model`
para `super().__init__`, não há atributos de classe mágicos:

```python
# src/db/repositories/user.py
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel
from src.schemas import UserResponse


class UserRepository(BaseRepository[UserModel]):
    """Data access for the user domain."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session and the user model.

        Args:
            session (AsyncSession): The async database session.
        """
        super().__init__(
            session,
            model=UserModel,
            not_found_message="Usuário não encontrado",
            create_conflict_message="E-mail já cadastrado",
        )

    def map_to_response(self, instance: UserModel) -> UserResponse:
        """Map an ORM row to its API response schema.

        Args:
            instance (UserModel): The persisted user row.

        Returns:
            UserResponse: The serializable response DTO.
        """
        return UserResponse.model_validate(instance)

    def map_to_model(self, data: dict[str, Any]) -> UserModel:
        """Build an ORM instance from a plain payload.

        Args:
            data (dict[str, Any]): Column-value pairs.

        Returns:
            UserModel: The unpersisted instance.
        """
        return UserModel(**data)
```

!!! tip "Mensagens de erro por repository"
    Os kwargs `not_found_message`, `create_conflict_message`,
    `update_conflict_message`, `bulk_create_conflict_message` e
    `bulk_update_conflict_message` customizam o texto das exceções. Sem
    eles, o SDK gera mensagens a partir de `Model.__name__` — o nome da
    classe inteiro, sufixo incluído: `"UserModel not found"`,
    `"Conflict creating UserModel"`.

!!! tip "Classes de exceção por repository"
    Cada `*_message` tem um `*_exception` correspondente. A mensagem sozinha
    não dá para o cliente ramificar: o `ConflictException` padrão responde
    `code = "CONFLICT"`, então uma chave duplicada de coin pack fica
    indistinguível de qualquer outro 409 — e `error_responses()` não consegue
    documentá-la. Passando uma subclasse de domínio (que **declara seu próprio
    `code` no corpo da classe**), o 409 fica identificável sem o repository
    saber nada do domínio:

    ```python
    from tempest_fastapi_sdk import BaseRepository, ConflictException

    from src.core.exceptions import CoinPackNotFoundException
    from src.db.models import CoinPackModel


    class CoinPackAlreadyExistsException(ConflictException):
        """Raised when a coin pack name is already taken."""

        code: str = "COIN_PACK_ALREADY_EXISTS"


    class CoinPackRepository(BaseRepository[CoinPackModel]):
        """Data access for coin packs."""

        def __init__(self, session: AsyncSession) -> None:
            """Initialize the repository.

            Args:
                session (AsyncSession): The async database session.
            """
            super().__init__(
                session,
                model=CoinPackModel,
                not_found_exception=CoinPackNotFoundException,
                create_conflict_exception=CoinPackAlreadyExistsException,
            )
    ```

    A resolução é do mais específico para o mais genérico —
    `create_conflict_exception` se informado, senão `conflict_exception`, senão
    `ConflictException` — então um kwarg só (`conflict_exception=`) cobre todas
    as escritas, ou cada escrita usa a sua:

    | Kwarg | Cobre |
    | --- | --- |
    | `create_conflict_exception` | `add`, `save_with_outbox`, `add_audited` |
    | `update_conflict_exception` | `update`, `update_audited` |
    | `bulk_create_conflict_exception` | `add_all`, `bulk_create_values`, `bulk_upsert` |
    | `bulk_update_conflict_exception` | `update_many`, `bulk_update`, `update_returning` |
    | `conflict_exception` | fallback de todas as quatro |

    A classe é instanciada como `cls(message=...)`, o mesmo contrato que
    `not_found_exception` já tem — então ela precisa aceitar o keyword
    `message`. Declarar `code` no corpo da classe e receber `message` opcional
    atende aos dois. Todos os kwargs são opcionais: omitindo-os, o
    comportamento é o de antes (`ConflictException` genérico). Disponível a
    partir de 0.169.0.

### Qual constraint recusou — `parse_integrity_error`

Trocar `ConflictException` por uma classe sua responde *houve conflito*. Para
responder *em qual campo*, é preciso ler o que o servidor disse — e o que ele
diz tem a forma do driver, não da sua aplicação.

```python
from typing import Any

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk import parse_integrity_error


def to_response(error: IntegrityError) -> dict[str, Any]:
    """Traduz um erro de integridade no envelope que o cliente parseia."""
    failure = parse_integrity_error(error)
    return {
        "code": failure.kind.value,
        "field": failure.column,
        "constraint": failure.constraint,
    }
```

`IntegrityViolation` classifica em `unique`, `foreign_key`, `not_null`,
`check` e `unknown`. `IntegrityFailure` carrega o que o servidor informou:
`constraint`, `table`, `columns` (na ordem listada) e `column`, que é o atalho
para o caso de uma coluna só.

!!! warning "Campo vazio significa 'este servidor não disse'"
    Nunca 'não existe'. Os dois dialetos falam de jeitos diferentes, e as
    diferenças não são cosméticas:

    | | Postgres | SQLite |
    | --- | --- | --- |
    | unique | nomeia constraint, tabela e colunas | nomeia `tabela.coluna`; sem constraint |
    | not null | nomeia coluna e tabela | nomeia `tabela.coluna` |
    | check | nomeia a constraint e a tabela | devolve a **expressão** quando a constraint não tem nome |
    | foreign key | nomeia constraint, tabela e coluna | diz só `FOREIGN KEY constraint failed` |

    Uma ausência medida que vale repetir: FK no SQLite não traz nada além do
    tipo.

!!! info "No Postgres, os campos do driver vêm antes do texto"
    O `asyncpg` expõe o diagnóstico do servidor como atributos da exceção —
    `sqlstate`, `detail`, `constraint_name`, `table_name`, `column_name` —, e
    o `parse_integrity_error` lê esses primeiro, caindo para a frase só no que
    eles deixam vazio. O texto não é um portador estável. Medido contra
    Postgres 16 com `asyncpg` 0.31.0:

    | SQLAlchemy | `str(error.orig)` | onde fica a exceção do driver |
    | --- | --- | --- |
    | 2.0.52 (o piso do SDK) | primeira frase **mais** a linha `DETAIL:` | `error.orig.__cause__` |
    | 2.1.1 | só a primeira frase | `error.orig.orig` (e `__cause__`) |

    Lendo só o texto, uma unique no 2.1.1 voltava com `columns=()`. Os campos
    são lidos pelo nome do atributo: o `asyncpg` não é importado, e uma
    instalação sem `[postgres]` não precisa dele. A `table` de uma unique vem
    do `table_name`; uma mensagem solta, sem a exceção do driver atrás, ainda
    deixa `None`, porque a frase não diz e dividir o nome da constraint numa
    convenção seria chute. O `sqlstate` (`23505`, `23503`, `23502`, `23514`)
    também classifica uma violação cuja frase o servidor traduziu: com
    `lc_messages=de_DE.utf8`, nas duas versões do SQLAlchemy, uma unique
    voltou `unique` com constraint e tabela, e com `columns=()`, porque o
    `DETAIL` também vem traduzido.

!!! info "Lê `error.orig`, não `str(error)`"
    `str()` numa `IntegrityError` do SQLAlchemy anexa `[SQL: <statement>]`, e
    um statement contém dado do usuário. Um valor que contivesse o texto
    `UNIQUE constraint failed: x.y` seria parseado como nome de coluna. A
    função lê a exceção do DBAPI, cuja mensagem é só a frase do servidor.

!!! check "Nunca levanta"
    Mensagem desconhecida volta como `IntegrityViolation.UNKNOWN` com o texto
    em `failure.message`. Um parser de prosa de erro que levanta transforma um
    409 tratado num 500 não tratado, que é pior do que o conflito genérico que
    ele veio melhorar.

### O CRUD que você ganha

Lembrando da convenção de coleções do projeto: lookups de **registro
único** levantam 404; lookups de **coleção** devolvem `[]`.

```python
import asyncio
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

id1, id2, id3 = uuid4(), uuid4(), uuid4()
repository = BaseRepository(session, model=UserModel)
user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")
user_or_id = user_id


async def main() -> None:
    """Run this example."""
    # Leitura — registro único (404 quando não acha)
    user = await repository.get_by_id(user_id)
    user = await repository.get({"email": "a@b.com"})

    # Leitura — pode não existir (None, sem 404)
    user = await repository.get_or_none({"email": "a@b.com"})
    first = await repository.first({"is_active": True})

    # Leitura — coleção (sempre [], nunca 404)
    users = await repository.list({"is_active": True})

    # Existência / contagem
    exists = await repository.exists({"email": "a@b.com"})
    total = await repository.count({"is_active": True})

    # "Esse valor já é de OUTRO registro?" — validação de unicidade no update
    taken = await repository.exists_excluding({"email": "a@b.com"}, exclude_id=user.id)

    # id-ou-instância → instância (sem if isinstance espalhado nas services)
    user = await repository.resolve(user_or_id)

    # Escrita
    created = await repository.add(
        UserModel(name="Ana", email="ana@x.com", password_hash="...")
    )
    updated = await repository.update(user)  # commita mutações numa instância anexada

    # Remoção
    await repository.delete(user_id)  # hard delete (404 se não existe)
    await repository.delete_many({"is_active": False})  # retorna contagem
    await repository.delete_batch([id1, id2, id3])  # por PK, retorna contagem

    # Soft-delete via flag is_active (não precisa do SoftDeleteMixin)
    await repository.soft_delete(user_id)  # is_active = False
    await repository.restore(user_id)  # is_active = True


asyncio.run(main())
```

!!! note "`update` espera uma instância anexada"
    O fluxo típico é: `get_by_id` → mutar com `update_from_dict` →
    `repository.update(instance)`. Não construa um modelo solto e mande
    para o `update` — ele persiste mutações de algo já carregado na
    sessão.

!!! tip "`resolve` e `exists_excluding` — dois ajudantes que você vai usar sempre"
    **`resolve(id_ou_instância)`** resolve o velho dilema: seu método
    recebe `UUID | UserModel` e você não quer escrever
    `if isinstance(x, UUID): ... else: ...` em toda service. O
    `resolve` faz isso por você — passa um `UUID`, ele busca (404 se não
    existir); passa uma instância, ele devolve a mesma. Uma linha:

    ```python
    user_model = await self.repository.resolve(user)  # user é UUID OU UserModel
    ```

    **`exists_excluding(filtros, exclude_id=...)`** responde a pergunta
    "esse e-mail/telefone/username já é de **outra** pessoa?" — exatamente
    o que você precisa ao **atualizar** um campo único. O `exists` normal
    diria `True` até para o próprio registro; o `exists_excluding` ignora
    o id que você passar:

    ```python
    if await self.repository.exists_excluding(
        {"phone": new_phone}, exclude_id=user.id
    ):
        raise UserWithPhoneExistsException(phone=new_phone)
    ```

    Passe `exclude_id=None` no cadastro (quando ainda não há registro a
    excluir) — aí ele se comporta igual ao `exists`.

**Recap:** instancie direto para CRUD puro, subclassifique para queries +
mappers. 404 só em lookup único; coleção devolve `[]`. `soft_delete`
mexe na flag `is_active`; o `SoftDeleteMixin` (seção 6) adiciona um
carimbo `deleted_at` quando você precisa de auditoria temporal.

### Eager-loading de relacionamentos com `with_`

Acessar um relacionamento (`user.orders`) **depois** que a sessão async
fechou levanta o temido `MissingGreenlet` — o SQLAlchemy tentaria uma
query lazy num contexto que não pode mais aguardar I/O. A solução é
carregar o relacionamento junto, na mesma query. Todo método de leitura
(`get`, `get_or_none`, `get_by_id`, `first`, `list`) aceita `with_=`:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # Carrega o user e seus pedidos numa só ida ao banco
        user = await repository.get_by_id(user_id, with_=["orders"])
        for order in user.orders:      # sem lazy load, sem MissingGreenlet
            print(order.total)

        # Vários relacionamentos + aninhado (pontilhado)
        user = await repository.get_by_id(
            user_id,
            with_=["profile", "orders.items"],   # orders → e os items de cada order
        )

        # Também funciona em coleções
        users = await repository.list({"is_active": True}, with_=["orders"])


asyncio.run(main())
```

Cada caminho usa `selectinload`: N relacionados custam **uma** query
extra por nível (um `SELECT ... IN (...)`), não N — nada de row
multiplication de `JOIN`, e funciona tanto para coleções quanto para
escalares.

!!! warning "Nome errado falha alto"
    Um segmento de `with_` que não seja um relacionamento do modelo
    naquele ponto levanta `ValueError` na hora — não um erro silencioso
    em runtime. `with_=["orders.ghost"]` → `ValueError: Order has no
    relationship 'ghost'`.

### Signals de ciclo de vida

Quando você quer reagir a uma escrita — invalidar cache, enfileirar um
evento, sincronizar um índice de busca, disparar um domain event — sem
espalhar callbacks por toda service, registre um **signal**. O
repository emite quatro momentos no caminho unit-of-work:

```python
from tempest_fastapi_sdk import RepositorySignal, on_signal
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.db import connect, disconnect

from src.core.settings import settings
from src.db.models import UserModel
from src.services.search import SearchIndex

cache = AsyncRedisManager(settings.REDIS_URL, decode_responses=True)
search_index = SearchIndex()


# Forma decorator
@on_signal(UserModel, RepositorySignal.POST_SAVE)
async def index_user(user: UserModel) -> None:
    """Reindexa o user na busca depois que a linha commitou."""
    await search_index.upsert(user.id, user.name)


# Forma imperativa (mesma coisa)
async def bust_cache(user: UserModel) -> None:
    """Derruba a entrada de cache do user depois que a linha commitou."""
    await cache.client.delete(f"user:{user.id}")

connect(UserModel, RepositorySignal.POST_SAVE, bust_cache)
disconnect(UserModel, RepositorySignal.POST_SAVE, bust_cache)  # remove
```

!!! note "`search_index` é ilustrativo; o `cache` não"
    `search_index.upsert(...)` é um placeholder do seu projeto (um cliente
    de busca) — não faz parte do SDK. Troque pelo objeto real do seu domínio.

    O `cache` é um `AsyncRedisManager` de verdade, e é por isso que a chamada
    passa por `cache.client`: o manager cuida do ciclo de vida, e os comandos
    Redis vivem no client. Construindo o handler antes do lifespan, use
    `cache.client_proxy` — `cache.client` levanta `RuntimeError` até o
    `connect()` rodar.

Os quatro momentos:

| Signal | Quando dispara | Uso típico |
|--------|----------------|------------|
| `PRE_SAVE` | antes do `INSERT`/`UPDATE` commitar | validação transversal; **levantar aqui veta a escrita** (rollback + re-raise) |
| `POST_SAVE` | depois de commitar + refresh | reindex, cache-bust, evento de domínio |
| `PRE_DELETE` | antes do delete de linha única | limpar dependências externas |
| `POST_DELETE` | depois de o delete commitar | notificar que o registro sumiu |

Handlers podem ser sync **ou** `async` — um retorno awaitable é
aguardado. Registrar num modelo base vale para as subclasses (resolvido
pela MRO da instância).

!!! danger "Signals cobrem só o caminho unit-of-work"
    `add` / `add_all` / `update` / `update_many` / `soft_delete` /
    `restore` / `delete` disparam signals. Os métodos bulk set-based
    (`bulk_update`, `bulk_create_values`, `bulk_upsert`, `delete_many`,
    `delete_batch`) emitem **uma** instrução SQL e **fazem bypass** dos
    signals por design — eles nunca materializam as linhas afetadas.
    `soft_delete`/`restore` disparam `PRE_SAVE`/`POST_SAVE` (são um
    `UPDATE`), não os signals de delete.

!!! tip "Isolamento em testes"
    O registro é global ao processo. Em testes, chame
    `clear_signals()` (de `tempest_fastapi_sdk.db.signals`) no teardown
    de uma fixture para um handler de um teste não vazar para o
    seguinte.

### Expressões `F` e `Q`

Para quem vem do Django: `F` referencia uma coluna dentro da query e `Q`
compõe condições com `OR`/`NOT`. Os dois plugam direto no repository.

**`F` — atualização atômica no banco.** Decrementar estoque com
read-modify-write tem race: dois requests leem `10`, ambos gravam `9`.
`F("stock") - 1` calcula no banco, numa instrução — sem lost update:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel

from tempest_fastapi_sdk import F


async def main() -> None:
    """Run this example."""
    product_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
    pid = product_id
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # stock = stock - 1, no banco
        await repository.bulk_update({"id": product_id}, {"stock": F("stock") - 1})

        # aritmética dos dois lados e entre colunas
        await repository.bulk_update({"id": pid}, {"stock": 100 - F("stock")})
        await repository.bulk_update({"id": pid}, {"total": F("price") * F("qty")})


asyncio.run(main())
```

**`update_returning` — a decisão e o valor novo na mesma instrução.**
`bulk_update` devolve só a contagem. Quando a escrita é condicional e você
precisa saber o resultado — debitar só se houver saldo, e com o saldo novo
em mãos —, `update_returning` roda o mesmo `UPDATE ... WHERE` com
`RETURNING`. Linha nenhuma devolvida significa que a condição não casou;
não existe leitura antes, então não existe janela para outra requisição:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository, F

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    user_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        rows = await repository.update_returning(
            {"id": user_id, "wallet_cents__gte": 500},
            {"wallet_cents": F("wallet_cents") - 500},
            returning=("wallet_cents",),
        )
        if rows:
            print("novo saldo:", rows[0]["wallet_cents"])
        else:
            print("recusado: saldo abaixo de 500")


asyncio.run(main())
```

O `where=` aceita um `Q` ou uma cláusula SQLAlchemy pronta, para o que o
dict não expressa — a [carteira](wallet.md) desconta o saldo retido com
uma subquery correlata ali. `RETURNING` exige PostgreSQL, ou SQLite 3.35+.

**`Q` — `OR` / `NOT` que o dict de filtros não expressa.** O dict ANDeia
tudo; `Q` combina com `&` / `|` / `~` e entra via `where=`:

```python
import asyncio

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel

from tempest_fastapi_sdk import Q


async def main() -> None:
    """Run this example."""
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # status open OU pending
        abertos = await repository.list(where=Q(status="open") | Q(status="pending"))

        # ativos que NÃO são guest
        ativos = await repository.list(where=Q(is_active=True) & ~Q(role="guest"))

        # combina com o dict (AND): estoque >= 5 E (open OU closed)
        rows = await repository.list(
            {"stock__gte": 5}, where=Q(status="open") | Q(status="closed")
        )


asyncio.run(main())
```

!!! warning "`~Q(...)` não devolve a linha com `NULL`"
    A negação vira `role != 'guest'` em SQL, e `NULL != 'guest'` é
    `NULL`, não verdadeiro: a linha com `role` nulo **fica de fora** junto
    com a `guest`. Com três usuários (`guest`, `admin` e `role=None`),
    `~Q(role="guest")` devolve só o `admin`. Se a linha nula deve entrar,
    peça explicitamente: `~Q(role="guest") | Q(role=None)` devolve `admin` e
    o nulo (`None` numa coluna simples é `IS NULL`). O `role__ne="guest"`
    tem a mesma semântica do `~Q` aqui — também exclui o nulo.

`Q` usa as mesmas convenções do dict de filtros (`name` ILIKE,
`campo__gte`, iterável → `IN`, …), então `Q(priority__gte=5, name="ana")` é
o `AND` dessas condições. `where=` funciona em `list` / `first` / `get` /
`get_or_none` / `count` / `exists` / `paginate` / `delete_many`.

Operadores de sufixo `campo__op` disponíveis (no `Q` **e** no dict):

| Sufixo | SQL | Exemplo |
|--------|-----|---------|
| `gt` `gte` `lt` `lte` `ne` | comparação | `Q(priority__gte=5)` |
| `in` `notin` `not_in` | `IN` / `NOT IN` (valor = qualquer iterável não-string: `list`/`set`/`tuple`/gerador; `not_in` é alias de `notin`) | `Q(status__in={"open", "paid"})` |
| `between` | `col BETWEEN lo AND hi` (valor = par ordenado `(lo, hi)` em `list`/`tuple`) | `Q(price__between=(10, 20))` |
| `iexact` | igualdade case-insensitive (`lower(col) == lower(v)`) | `Q(email__iexact="Ana@X.com")` |
| `like` `ilike` | `LIKE` / `ILIKE` cru, com os wildcards do chamador (`%`/`_`) **sem escape** | `Q(sku__ilike="ab_-%")` |
| `isnull` | `IS NULL` (True) / `IS NOT NULL` (False) | `Q(closed_at__isnull=True)` |
| `contains` `icontains` | `ILIKE %v%` (valor escapado) | `Q(name__contains="ana")` |
| `startswith` `endswith` | `ILIKE v%` / `%v` (valor escapado) | `Q(sku__startswith="SKU-")` |

!!! warning "Case-sensitivity do `like` depende do backend"
    `ilike` é sempre case-insensitive. Já o `like` puro segue a semântica de
    `LIKE` do banco: o SQLite ignora maiúsculas/minúsculas para ASCII, o
    PostgreSQL não. Para comparação de caixa **portável**, use `ilike` ou
    `iexact`.

!!! note "SQLAlchemy puro continua ali"
    `F`/`Q` são açúcar tipado sobre expressões que o SQLAlchemy já tem.
    Precisou de algo que eles não cobrem? Use `select(...)` direto — o
    repository não fica no caminho.

---

## 4. Filtros por convenção

Todos os métodos que recebem `filters: dict[str, Any]` passam pelo mesmo
motor. Um valor `None` significa **`IS NULL`**, como em qualquer ORM:
`{"deleted_at": None}` casa as linhas que nunca foram apagadas. As
convenções:

| Chave / valor | SQL gerado | Exemplo |
| --- | --- | --- |
| `name` (str) | `ILIKE %value%` case-insensitive | `{"name": "ana"}` |
| `bool` | `col.is_(value)` | `{"is_active": True}` |
| iterável não-string (`list`/`set`/`tuple`/`frozenset`/`range`/gerador/`dict` view) | `col.in_(values)` — o iterável é materializado uma vez, então passar um `set` não precisa de conversão manual pra `list` | `{"id": {id1, id2}}` |
| `date` | `func.date(col) == value` (dia inteiro) | `{"created_at": hoje}` |
| `start_in` / `end_in` (date) | range no `date`/`created_at` | `{"start_in": d1, "end_in": d2}` |
| `<col>__<op>` | comparação `gt`/`gte`/`lt`/`lte`/`ne` | `{"updated_at__gt": marca}` |
| qualquer outra coluna | `col == value` | `{"email": "a@b.com"}` |

```python
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    fim = datetime(2026, 1, 31, tzinfo=timezone.utc)
    inicio = datetime(2026, 1, 1, tzinfo=timezone.utc)
    selected_ids = [uuid4(), uuid4()]
    watermark = datetime.now(timezone.utc) - timedelta(hours=1)
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # "ativos atualizados depois da marca d'água" — precisão de timestamp
        changed = await repository.list({
            "is_active": True,
            "updated_at__gt": watermark,
        })

        # "criados entre duas datas" — dia inteiro
        report = await repository.list({"start_in": inicio, "end_in": fim})

        # busca textual + pertinência a um conjunto
        hits = await repository.list({"name": "silva", "id": selected_ids})


asyncio.run(main())
```

!!! info "`start_in`/`end_in` vs `__gt`/`__lt`"
    `start_in`/`end_in` casam por **dia inteiro** (`func.date`) contra a
    coluna `date` do modelo (ou `created_at` se não houver). Os sufixos
    `__op` são **precisos no timestamp** — é o que queries de delta-sync
    usam. Escolha por precisão.

!!! tip "Filtros vêm de um schema, não de strings soltas"
    Na prática você não monta esse dict à mão. `BasePaginationFilterSchema`
    (e suas subclasses) expõem `.get_conditions()`, que devolve o dict já
    limpo de `None`. O router recebe o filtro via `Depends()`.

### Chave desconhecida é recusada

Uma chave que não é coluna do model (`{"usr_id": 1}`, erro de digitação) ou
um sufixo que não é operador (`{"user_id__bogus": 1}`) levanta
`UnknownFilterKeyException` **antes** de qualquer statement rodar — em todo
método que recebe `filters` (`list`, `count`, `exists`, `first`, `get`,
`get_or_none`, `paginate`, `cursor_paginate`, `bulk_update`,
`update_returning`, `delete_many`), no `Q` e nas camadas que só repassam
(`BaseService`, `BaseController`, `TenantScopedRepository`):

```python
import asyncio

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel, BaseRepository, UnknownFilterKeyException


class TicketModel(BaseModel):
    __tablename__ = "tickets_unknown_key_demo"

    user_id: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))


async def main() -> None:
    """Run this example."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TicketModel.metadata.create_all)
    async with AsyncSession(engine) as session:
        repo: BaseRepository[TicketModel] = BaseRepository(session, model=TicketModel)
        for user_id in (1, 2, 3):
            await repo.add(TicketModel(user_id=user_id, status="open"))
        try:
            await repo.delete_many({"usr_id": 1})
        except UnknownFilterKeyException as exc:
            print(exc.status_code, exc.code, exc.details)
        try:
            await repo.list({"user_id__bogus": 1})
        except UnknownFilterKeyException as exc:
            print(exc.details)
        print(await repo.count())
    await engine.dispose()


asyncio.run(main())
```

Saída:

```text
422 UNKNOWN_FILTER_KEY {'filter': 'usr_id'}
{'filter': 'user_id__bogus'}
3
```

As três linhas continuam lá: o `delete_many` não apagou nada.

- `details` traz só a chave recusada, nunca a lista de colunas do model —
  quando o dict vem de uma querystring, essa lista é o mapa do que sondar.
- Atributo Python que não tem leitura SQL (método, `@property`, o próprio
  `metadata`) conta como desconhecido. Coluna, `column_property`
  e a expressão de classe de um `hybrid_property` valem.
- `start_in` / `end_in` continuam sendo açúcar do repository, não coluna.
- No admin, `list_filter`, `search_fields` e os `filters` de cada `Lens` são
  conferidos na construção do `AdminModel`: um typo vira `ValueError` no
  boot, nomeando a opção, em vez de 422 no primeiro clique.

!!! warning "Mudança de comportamento (#465)"
    Até a 0.308.0 a chave desconhecida era **ignorada em silêncio**: com só
    `{"usr_id": 1}` no dict, `list`/`count` devolviam todas as linhas e
    `bulk_update`/`delete_many` alteravam a tabela inteira. Quem dependia
    disso — por exemplo, um schema de filtro com um campo que não é coluna
    (`search`, `include_archived`) repassado inteiro por `get_conditions()` —
    passa a receber 422. Tire o campo do dict antes de repassar
    (`conditions = f.get_conditions()` e depois
    `search = conditions.pop("search", None)`) e trate-o no service.

### Toda paginação herda os operadores

Como `get_conditions()` só remove as chaves de paginação (`page`,
`page_size`, `order_by`, `ascending`) e repassa **todo o resto** ao mesmo
motor, qualquer subclasse de `BasePaginationFilterSchema` ganha os
operadores de graça: basta declarar um campo com o nome `<coluna>__<op>`.
Sem herança extra, sem mixin — o nome do campo é o operador.

```python
from tempest_fastapi_sdk import BasePaginationFilterSchema
from pydantic import Field


class ProductFilter(BasePaginationFilterSchema):
    """Filtro de listagem de produtos — cada campo vira uma condição."""

    name: str | None = Field(default=None)                 # ILIKE %name%
    category_id__in: set[int] | None = Field(default=None)  # IN (set!)
    price__between: tuple[float, float] | None = Field(default=None)  # BETWEEN
    sku__ilike: str | None = Field(default=None)            # ILIKE cru
    created_at__gte: str | None = Field(default=None)       # >=
```

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel
from src.schemas import ProductFilterSchema

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

f = ProductFilterSchema(name="silva", page=1, page_size=20)
repo = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    # No service/repo, o schema inteiro vira filtros + paginação:
    data = await repo.paginate(
        filters=f.get_conditions(),  # name/category_id__in/price__between/…
        **f.get_pagination_conditions(),  # page/page_size/order_by/ascending
    )


asyncio.run(main())
```

O front chama `?category_id__in=1&category_id__in=2&price__between=10&price__between=20`
e o FastAPI monta o schema via `Depends()`. O `get_conditions()` do schema
remove os campos não preenchidos **antes** de montar o dict
(`to_dict(exclude_none=True)`), então o cliente manda só os campos que quer
e um `None` do schema nunca chega ao repositório como `IS NULL`.

!!! warning "`None` mudou de significado na v0.292.0"
    Antes, um `None` no dict **descartava a chave** — o filtro sumia e a
    query devolvia *tudo*. Era o modo de falha mais caro possível: falha
    silenciosa, na direção de **mais** linhas. Medido num consumidor,
    `{"left_at": None}` (a grafia óbvia de "ainda é membro") casava também
    quem tinha saído, e o fan-out de WebSocket construído a partir disso
    continuava entregando as mensagens do grupo a essas pessoas.

    Quem montava o dict à mão com chaves opcionais e contava com o
    descarte precisa passar a omitir a chave — ou usar
    `get_conditions()`, que já faz isso. Onde `None` não tem leitura
    nenhuma (`__gt`, `__between`, `__in`), a condição continua sendo
    descartada, agora com um `DroppedFilterWarning` nomeando a chave.

**Recap:** um dict, convenções previsíveis, `None` é `IS NULL`. Strings em
`name` viram busca ILIKE; sufixos `__op` dão comparações precisas.
Toda paginação herda esses operadores só declarando o campo.

---

### O nome do tamanho de página no fio

Por default o envelope publica `page_size`, tanto na resposta quanto no query
param. Para um serviço novo isso não custa nada — mas um serviço que **já**
publica `{"total", "items", "page", "size", "pages"}` não pode renomear sem
quebrar todo endpoint paginado de uma vez, e um app já em loja não atualiza em
lockstep com o backend.

Desde a v0.261.0 existe a variante pronta:

```python
from tempest_fastapi_sdk import (
    CompactPaginationFilterSchema,
    CompactPaginationSchema,
)

# GET /users?size=50
filters = CompactPaginationFilterSchema.model_validate({"size": 50})
filters.page_size   # 50 — o nome Python não muda

page = CompactPaginationSchema[int](
    items=[1], total=1, page=1, page_size=20, pages=1
)
page.model_dump(by_alias=True)["size"]   # 20
```

O FastAPI serializa `response_model` com `by_alias=True`, então declarar
`CompactPaginationSchema[Item]` como `response_model` já basta.

!!! important "O repositório não muda"
    `BaseRepository.paginate` continua devolvendo
    `{"items", "total", "page", "page_size", "pages"}` seja qual for o
    envelope. A renomeação vive **no schema e em nenhum outro lugar** —
    não há o que renomear na camada de dados, e trocar um serviço de um
    envelope para o outro toca uma linha.

Se o seu nome no fio não é nem `page_size` nem `size`, as duas classes base
ligam `populate_by_name`, então dá para sobrescrever só o campo:

```python
from pydantic import Field

from tempest_fastapi_sdk import BasePaginationSchema


class MyPage(BasePaginationSchema[int]):
    """Envelope próprio, renomeando só o tamanho de página."""

    page_size: int = Field(
        validation_alias="perPage",
        serialization_alias="perPage",
    )
```

O nome do fio é escrito **duas vezes**, `validation_alias` para ler e
`serialization_alias` para escrever. `Field(alias=...)` sozinho parece
funcionar e é aceito pelo mypy, mas pyright e basedpyright acusam erro no
consumidor — o SDK não usa essa forma em lugar nenhum.


## 5. Operações em lote

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

**Recap:** `add_all`/`update_many` quando você quer as instâncias de
volta; `bulk_*` quando quer throughput. Filtro vazio em `bulk_update` é
erro proposital.

---

## 6. Soft-delete e auditoria (mixins)

Os mixins são **opt-in**: você os mistura ao lado de `BaseModel` só quando
o domínio pede. `SoftDeleteMixin` adiciona `deleted_at` (+
`mark_deleted()` / `mark_restored()` / `is_deleted`). `AuditMixin`
adiciona `created_by` / `updated_by` (+ `stamp_created_by` /
`stamp_updated_by`).

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AuditMixin, BaseModel, SoftDeleteMixin


class UserModel(BaseModel, SoftDeleteMixin, AuditMixin):
    """Users — soft-deletable and audited."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

A filtragem é responsabilidade de quem chama — o mixin **não** instala um
filtro global. Esconda linhas soft-deleted passando `deleted_at=None`, ou
filtrando na subclasse. Carimbar auditoria pertence ao service, onde o
usuário atual está em escopo:

```python
# src/services/user.py
from uuid import UUID

from tempest_fastapi_sdk import BaseService

from src.db.repositories import UserRepository
from src.schemas import UserResponse, UserUpdateSchema


class UserService(BaseService[UserRepository, UserResponse]):
    """Business logic for the user domain."""

    async def list_alive(self) -> list[UserResponse]:
        """Return only rows where ``deleted_at IS NULL``.

        Returns:
            list[UserResponse]: The alive users.
        """
        instances = await self.repository.list(filters={"deleted_at": None})
        return [self.repository.map_to_response(i) for i in instances]

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
        *,
        actor_id: UUID,
    ) -> UserResponse:
        """Apply a partial update and stamp ``updated_by`` with the actor.

        Args:
            user_id (UUID): Primary key of the row to update.
            data (UserUpdateSchema): The partial payload.
            actor_id (UUID): The acting user, written to ``updated_by``.

        Returns:
            UserResponse: The updated user.
        """
        instance = await self.repository.get_by_id(user_id)
        instance.update_from_dict(data.model_dump(exclude_unset=True))
        instance.stamp_updated_by(actor_id)
        updated = await self.repository.update(instance)
        return self.repository.map_to_response(updated)
```

!!! tip "Dois carimbos de delete, propósitos diferentes"
    Use `repository.soft_delete(id)` (flag `is_active`) quando o booleano
    já basta. Use os helpers do `SoftDeleteMixin` (`mark_deleted` →
    `deleted_at`) quando precisa **saber quando** o delete aconteceu —
    auditoria, políticas de retenção.

!!! info "MFA é outro mixin opt-in"
    `MFAMixin` adiciona `totp_secret` / `totp_enabled_at` / `totp_last_step` ao modelo de
    usuário quando o projeto liga o fluxo MFA bundled. Detalhes em
    [MFA (TOTP / 2FA) »](mfa.md).

### Locale — idioma preferido do usuário

`LocaleColumnMixin` adiciona uma coluna `locale` (BCP-47, ex. `"pt-BR"`,
`"en-US"`, nullable) pra o modelo carregar o idioma em que as notificações
e textos localizados dele devem sair — sem cada projeto redeclarar a mesma
coluna. Misture-o como qualquer outro mixin:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, LocaleColumnMixin


class UserModel(BaseModel, LocaleColumnMixin):
    """Users — carregam o locale de notificação."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
```

Pra escrever o valor, use o enum `Locale` (lista curada de tags BCP-47) em
vez de digitar a string na mão — cada membro **é** a própria tag, então
compara e grava como ela:

```python
from tempest_fastapi_sdk import Locale

from src.db.models import UserModel

user = UserModel(name="Ana", email="ana@example.com")


user.locale = Locale.PT_BR          # grava "pt-BR"
user.locale = "en-US"               # a string crua também vale
assert Locale.PT_BR == "pt-BR"      # membro é str
```

`locale` `NULL` significa "sem preferência": resolva pro default da sua app
na hora de renderizar (tipicamente via
[`MessageCatalog`](../reference.md)), **não** trate como erro. Esse é
exatamente o par que o [recipe de Web Push »](webpush.md) usa pra localizar
o `title`/`body` de cada notificação pelo `locale` do destinatário.

!!! note "`Locale` é curado, não exaustivo"
    O enum cobre os locales mais usados (pt/en/es/fr/de/… + variantes de
    região). Precisa de uma tag fora da lista? A coluna é `str`, então
    guarde a string crua e proponha o novo membro upstream quando virar
    comum.

**Recap:** mixins entram só quando o domínio precisa; a filtragem de
soft-delete é sua (`deleted_at IS NULL` via query crua); o carimbo de
auditoria mora no service; o `locale` do usuário vem do `LocaleColumnMixin`
+ enum `Locale`.

---

## 7. Paginação

O SDK pagina de duas formas, **ambas embutidas no repository**. Você
quase nunca escreve a query de paginação à mão.

### Offset — quando o cliente quer "página 3 de 12"

```python
# src/db/repositories/user.py — método de conveniência

from typing import Any

from tempest_fastapi_sdk import BasePaginationSchema, BaseRepository

from src.db.models import UserModel
from src.schemas import UserResponse


UserPage = BasePaginationSchema[UserResponse]


class UserRepository(BaseRepository[UserModel]):
    # ... __init__ + mappers ...

    async def list_page(
        self,
        *,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> UserPage:
        """Return one offset-paginated page of users.

        Args:
            filters (dict[str, Any] | None): Filter conditions.
            page (int): 1-indexed page number.
            page_size (int): Items per page.

        Returns:
            UserPage: Items + total + page metadata.
        """
        result = await self.paginate(
            filters=filters,
            page=page,
            page_size=page_size,
        )
        return UserPage(
            items=[self.map_to_response(i) for i in result["items"]],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
            pages=result["pages"],
        )
```

`BaseRepository.paginate` devolve um `dict` com `items` / `total` /
`page` / `page_size` / `pages`. O total é computado da **mesma** query
filtrada, então joins custom ainda reportam total correto. Quando
`order_by` é `None`, ordena por `created_at desc`.

!!! warning "`order_by` é validado contra as colunas do model"
    Ele chega direto de um query param (`BasePaginationFilterSchema` declara um
    `str`), então é entrada não confiável. `paginate` e `cursor_paginate`
    resolvem o nome pelo mapper e levantam `OrderByNotAllowedException`
    (**422**, subclasse de `ValidationException`) quando não é coluna mapeada —
    inclusive pra atributo que existe na classe mas não é coluna, como
    `metadata`. A recusa **nunca** lista as colunas do model: desde a 0.298.0
    `details["allowed"]` só aparece quando você declarou `orderable_columns`,
    e aí contém só esse conjunto. Ver a seção seguinte.

!!! tip "Encaminhe o schema sem desempacotar à mão"
    O par `get_conditions()` / `get_pagination_conditions()` cobre os dois
    lados do filtro: o primeiro devolve só os filtros de domínio, o segundo
    só as chaves de paginação (`page`, `page_size`, `order_by`,
    `ascending`). Assim o service repassa o filtro direto, sem `**f` — que
    vazaria filtros de domínio (`is_active`, etc.) como kwargs que o
    repository não aceita:

    ```python
    data = await repo.paginate(
        filters=f.get_conditions(),
        **f.get_pagination_conditions(),
    )
    ```

    `CursorPaginationFilterSchema` tem o mesmo par (com `cursor` / `limit`
    no lugar de `page` / `page_size`).

### Quais colunas ordenam, e quantas linhas cabem numa página

Validar que `order_by` é uma coluna não basta numa listagem pública.
**Ordenar é um oráculo**: `?order_by=wallet` ranqueia todo mundo por saldo, e
quem controla a própria linha (grava o próprio `email`, o próprio CPF)
descobre o valor da linha vizinha por busca binária sobre a posição em que a
sua cai. E sem teto em `page_size`, um request lê a tabela inteira.

Declare as duas coisas no filtro da listagem:

```python
from typing import Annotated

from fastapi import FastAPI, Query
from tempest_fastapi_sdk import BasePaginationFilterSchema, register_exception_handlers


class ProducerFilterSchema(BasePaginationFilterSchema):
    """Filtro da listagem pública de produtores."""

    orderable_columns = frozenset({"created_at", "name"})
    max_page_size = 50


app = FastAPI()
register_exception_handlers(app)


@app.get("/producers")
def list_producers(
    filters: Annotated[ProducerFilterSchema, Query()],
) -> dict[str, int | str | None]:
    """Echo the validated pagination keys."""
    return {"page_size": filters.page_size, "order_by": filters.order_by}
```

Pedaço por pedaço:

- **`orderable_columns`** é um `ClassVar[frozenset[str] | None]`. Com o
  conjunto declarado, qualquer outro valor vira `OrderByNotAllowedException`
  já na validação do schema, antes de chegar ao repository:

    ```json
    {
      "detail": "Cannot order by this field",
      "code": "ORDER_BY_NOT_ALLOWED",
      "details": {"order_by": "hashed_password", "allowed": ["created_at", "name"]},
      "field": "order_by"
    }
    ```

    `details["allowed"]` é **só** o conjunto declarado, ordenado. O default é
    `None`, que aceita qualquer valor e deixa a checagem para o repository —
    o comportamento de antes. `order_by` vazio (`?order_by=`) significa
    ausente e vira `None`, nunca 422.

- **`max_page_size`** é o teto inclusivo de `page_size`. O default da base é
  `DEFAULT_MAX_PAGE_SIZE` (**100**); declarar na subclasse reescreve o `le=`
  do campo, então a recusa é o `less_than_equal` do próprio pydantic e o
  OpenAPI publica `maximum: 50` no parâmetro — com `Depends()` e com
  `Annotated[..., Query()]`. `None` remove o teto.

!!! warning "O teto de 100 é novo na 0.298.0"
    Até a 0.297.x `page_size` só tinha `ge=1`. Cliente que pedia
    `page_size=500` passa a levar 422 — declare `max_page_size` com o valor
    que a listagem realmente precisa (ou `None`) em vez de redeclarar o
    campo.

A segunda linha é o repository, para quem chega lá sem passar pelo schema
(script, painel, outra rota). Os mesmos nomes existem em `BaseRepository`,
como atributo de classe **ou** argumento do construtor (o construtor vence):

```python
import asyncio
from typing import ClassVar

from sqlalchemy import Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    OrderByNotAllowedException,
    PageSizeTooLargeException,
)


class ProducerModel(BaseModel):
    __tablename__ = "producers_order_demo"

    name: Mapped[str] = mapped_column(String(80))
    wallet: Mapped[int] = mapped_column(Integer, default=0)


class ProducerRepository(BaseRepository[ProducerModel]):
    orderable_columns: ClassVar[frozenset[str] | None] = frozenset(
        {"created_at", "name"},
    )
    max_page_size: ClassVar[int | None] = 50

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=ProducerModel)


async def main() -> None:
    """Run this example."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(ProducerModel.metadata.create_all)
    async with AsyncSession(engine) as session:
        repo = ProducerRepository(session)
        await repo.paginate(order_by="name", page_size=50)
        try:
            await repo.paginate(order_by="wallet")
        except OrderByNotAllowedException as exc:
            print(exc.details)
        try:
            await repo.paginate(page_size=51)
        except PageSizeTooLargeException as exc:
            print(exc.code, exc.details)
    await engine.dispose()


asyncio.run(main())
```

Saída:

```text
{'order_by': 'wallet', 'allowed': ['created_at', 'name']}
PAGE_SIZE_TOO_LARGE {'page_size': 51, 'max_page_size': 50}
```

- Sem `orderable_columns` no repository, qualquer coluna mapeada ainda ordena
  — mas a recusa de um nome que não é coluna sai com `details` só com
  `{"order_by": ...}`, sem a chave `allowed`.
- Nome declarado que não é coluna do model levanta `ValueError` na
  construção do repository: um typo na declaração recusaria, em todo
  request, exatamente a coluna que você queria liberar.
- `max_page_size` do repository tem default `None` (sem teto), porque
  chamadores internos como o export do painel `/admin` paginam aos milhares.
  Declarado, vale para `paginate`, `cursor_paginate` (sobre `limit`) e
  `GeoRepositoryMixin.paginate_nearby`.

!!! tip "O conjunto do repository vale para todo caminho"
    `orderable_columns` no repository governa `paginate`, `cursor_paginate` e
    `changes_since` — inclua a coluna de watermark (`updated_at`) se usar
    sync. E o painel `/admin` ordena pelo mesmo repository: se ele usa esta
    classe via `repository_class`, a ordenação por coluna fora do conjunto
    passa a ser recusada lá também.

`CursorPaginationFilterSchema` tem o mesmo `orderable_columns`, e
`max_limit` (default `DEFAULT_MAX_CURSOR_LIMIT`, **500** — o `le=500` que o
campo sempre teve) no papel de `max_page_size`.

**Recap:** declare `orderable_columns` e `max_page_size` no filtro de toda
listagem pública; o 422 lista só o que você declarou. No repository, os
mesmos nomes são a segunda linha.

### Cursor — quando a tabela é grande

A paginação por cursor escala melhor que offset em tabelas grandes (sem
`COUNT(*)`, estável sob inserts concorrentes) ao custo de perder acesso
aleatório. **Já está pronta** em `cursor_paginate` — ordena por
`(order_by, id)` e codifica o cursor opaco automaticamente:

```python
# src/db/repositories/user.py

from typing import Any

from tempest_fastapi_sdk import BaseRepository, CursorPaginationSchema

from src.db.models import UserModel
from src.schemas import UserResponse


UserCursorPage = CursorPaginationSchema[UserResponse]


class UserRepository(BaseRepository[UserModel]):
    # ... __init__ + mappers ...

    async def cursor_page(
        self,
        *,
        cursor: str | None = None,
        limit: int = 20,
        ascending: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> UserCursorPage:
        """Return one cursor-paginated page of users.

        Args:
            cursor (str | None): Opaque cursor from the previous page.
            limit (int): Max items in the page.
            ascending (bool): Sort direction.
            filters (dict[str, Any] | None): Filter conditions.

        Returns:
            UserCursorPage: Items + next_cursor + has_more.
        """
        result = await self.cursor_paginate(
            filters=filters,
            cursor=cursor,
            limit=limit,
            order_by="created_at",
            ascending=ascending,
        )
        return UserCursorPage(
            items=[self.map_to_response(i) for i in result["items"]],
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
            limit=result["limit"],
        )
```

Router, com o filtro vindo de um schema via `Depends()`:

```python
# src/api/routers/user.py
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import CursorPaginationFilterSchema

from src.api.dependencies.resources import SessionDep
from src.db.repositories import UserCursorPage, UserRepository

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCursorFilter(CursorPaginationFilterSchema):
    """Cursor filter for the user listing."""

    name: str | None = None   # ILIKE %value% pela convenção do repository


@router.get("/", response_model=UserCursorPage)
async def list_users(
    session: SessionDep,
    f: UserCursorFilter = Depends(),
) -> UserCursorPage:
    """List users, cursor-paginated."""
    repository = UserRepository(session)
    return await repository.cursor_page(
        cursor=f.cursor,
        limit=f.limit,
        ascending=f.ascending,
        filters=f.get_conditions(),
    )
```

!!! info "O cursor é opaco"
    `next_cursor` é JSON em base64 url-safe. O cliente nunca o inspeciona;
    ele devolve o valor literalmente até `next_cursor` virar `null`. Por
    baixo, `cursor_paginate` usa `encode_cursor`/`decode_cursor` e uma
    comparação de tupla `(order_by, id)` estável no Postgres.

!!! info "O valor do cursor volta com o tipo da coluna"
    O cursor é JSON, então um `datetime` sai da página 1 como string ISO.
    Antes de montar o `WHERE`, `cursor_paginate` reidrata o valor no tipo
    Python da coluna (`datetime`, `date`, `time`, `UUID`, `Decimal`, enum).
    Sem isso, o PostgreSQL recusava a comparação inteira na **segunda**
    página:

    ```text
    operator does not exist: timestamp with time zone < character varying
    ```

    E o SQLite não reclamava — comparava as duas strings e devolvia uma
    página **errada**: medido, o passeio de 7 linhas em páginas de 3
    repetia `r02` e perdia `r05`. Coluna de texto continua intocada; um
    `TypeDecorator` que não declara `python_type` também.

!!! tip "Para sincronização offline-first, há um terceiro modo"
    `changes_since` + `SyncPaginationSchema` fazem paginação de delta
    (rows alteradas desde uma marca d'água). Veja
    [Offline sync »](offline-sync.md).

**Recap:** `paginate` (offset) para navegação por página; `cursor_paginate`
para feeds/tabelas grandes. Ambos prontos — você só mapeia o resultado
para o schema de resposta.

---

## 8. Migrações Alembic

`AlembicHelper` embrulha o Alembic com uma config curada (timezone UTC,
arquivos com prefixo de data, `target_metadata` já ligado, modo batch).
Fluxo completo: bootstrap → revisão → aplicar → gate de CI.

### Bootstrap, uma vez por projeto

```python
# scripts/alembic_init.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper(config_path="alembic.ini", db_url=settings.DATABASE_URL)
helper.init(
    directory="alembic",
    metadata_module="src.db.models",   # expõe BaseModel
    metadata_attr="BaseModel",
    db_url=settings.DATABASE_URL,
)
```

```bash
uv run python scripts/alembic_init.py
```

Cria:

```text
alembic.ini                 # config curada pelo SDK (UTC, prefixo de data, post-write hooks)
alembic/
├── env.py                  # template do SDK (target_metadata, compare_type, batch)
├── script.py.mako
└── versions/
```

### Gerar revisões

```python
# scripts/make_migration.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
helper.revision(message=sys.argv[1], autogenerate=True)
```

```bash
uv run python scripts/make_migration.py "add users table"
```

O arquivo cai em
`alembic/versions/2026_05_16_1432-ae12cd34_add_users_table.py` — o prefixo
de data ordena cronologicamente e torna conflitos de merge óbvios.

!!! check "Migrações já saem lint-clean"
    O `alembic.ini` que o `init()` escreve inclui `[post_write_hooks]` que
    roda `ruff format` e **depois** `ruff check --fix` em cada revisão.
    Sem isso, os arquivos do Alembic falham no `tempest lint` (`W291` no
    `Revises:` vazio, `E501` em `sa.Column(...)` longas) — e é por isso
    que o formatter vem primeiro: ele é quem quebra a linha longa e tira
    o espaço à direita, então rodar o linter antes reportaria erros que
    o hook seguinte ia consertar. Os hooks usam a
    config de `ruff` do **seu** projeto. Requer `ruff` no `PATH` — já é
    dependência de dev em todo scaffold `tempest new`.

### Aplicar no startup

```python
# src/api/app.py — dentro do lifespan

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import AlembicHelper

from src.api.dependencies.resources import db
from src.core.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run pending migrations, then serve."""
    helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
    await helper.upgrade_async()
    await db.connect()
    yield
    await db.disconnect()
```

`upgrade_async()` roda a migration numa thread de trabalho: o `env.py` do SDK
chama `asyncio.run`, que não aninha dentro do loop do lifespan, então um
`helper.upgrade()` simples ali levanta `RuntimeError` nomeando o
`upgrade_async`. Todo comando que roda o `env.py` tem o mesmo par `_async` —
veja [Migrations »](migrations.md#de-codigo-async-use-o-metodo-_async).

!!! warning "Migrações destrutivas: use `safe_upgrade`"
    `helper.pending_destructive_ops()` lista DROPs de coluna/tabela
    pendentes; `helper.safe_upgrade()` levanta `DestructiveMigrationError`
    em vez de apagar dados silenciosamente. O guia completo de deploy
    (migração + shutdown gracioso) está em
    [Deploy seguro »](deploy-safety.md).

### Gate de CI — o schema deve casar com os modelos

```python
# scripts/check_migrations.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
if not helper.check():
    print("Schema drift detected — run make_migration.py and commit.")
    sys.exit(1)
print("Schema is in sync.")
```

```yaml
# .github/workflows/ci.yml
- name: Check migrations are in sync
  run: uv run python scripts/check_migrations.py
```

!!! info "Colunas base sempre primeiro"
    O `env.py` do SDK instala o hook `reorder_base_columns_first`, então
    toda migração gerada lista `id` / `is_active` / `created_at` /
    `updated_at` antes das suas colunas — diffs consistentes entre
    pessoas.

!!! check "Coluna `NOT NULL` nova não explode mais (v0.67.0)"
    Adicionar uma coluna `NOT NULL` numa tabela que **já tem linhas**
    estoura no Postgres com `NotNullViolationError: column "x" contains
    null values` — porque um `default=` Python só roda no insert do ORM,
    nunca como DDL. O SDK agora instala um segundo hook,
    `backfill_non_nullable_defaults`: toda coluna adicionada que seja
    `nullable=False`, **sem** `server_default`, mas **com** um `default`
    escalar no model, recebe um `server_default` derivado desse default —
    então a migração gerada backfilla as linhas existentes na mesma
    instrução.

    ```python
    # No model — só o default Python:
    is_professional: Mapped[bool] = mapped_column(default=False)
    ```

    ```python
    # A migração gerada agora sai assim (note o server_default):
    op.add_column(
        "users",
        sa.Column(
            "is_professional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    ```

    Cobre `bool` / `int` / `float` / `str` / `Enum` (usa `.value`). **Não**
    age quando o default é callable (`uuid4`, `func.now()`) ou inexistente
    — esses precisam de uma migração de dados escrita à mão, porque o SDK
    não tem como inferir um valor de backfill seguro.

    Já tem um `env.py` antigo? Atualize o import + wiring para os dois
    hooks compostos:

    ```python
    # alembic/env.py
    from tempest_fastapi_sdk.db.alembic_hooks import (
        backfill_non_nullable_defaults,
        compose_hooks,
        reorder_base_columns_first,
    )

    _process_revision_directives = compose_hooks(
        reorder_base_columns_first,
        backfill_non_nullable_defaults,
    )

    # ...e passe-o em context.configure(process_revision_directives=...)
    ```

    Para uma migração **já gerada** que estourou, adicione o
    `server_default=sa.text("...")` na mão no `op.add_column` (ou
    backfille + `alter_column` para remover o default depois).

### Migrar constraints compostas da convenção antiga

Se o seu banco foi criado antes da correção de nomes compostos, cada
unique, índice e foreign key **com mais de uma coluna** ainda tem o nome da
primeira coluna só. Os de uma coluna não mudaram. Ao atualizar o SDK, o gate
de drift acusa diferença — e o que o autogenerate propõe não é o que você
quer aplicar.

Num banco com `UniqueConstraint("title", "release_year")`,
`Index(None, "author", "title")` e uma FK composta, o
`alembic revision --autogenerate` gera (medido contra PostgreSQL 16 e Alembic
1.19.1):

```python
from alembic import op


def upgrade() -> None:
    """O que o autogenerate gera sobre o banco antigo."""
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_books_author"))
        batch_op.drop_constraint(batch_op.f("uq_books_title"), type_="unique")
        batch_op.create_index(
            batch_op.f("ix_books_author_books_title"),
            ["author", "title"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_books_title_release_year"),
            ["title", "release_year"],
        )
```

!!! danger "Não aplique esse drop + create no PostgreSQL"
    Dois problemas medidos, e um custo:

    - **Quebra quando uma FK depende da unique.** Com uma FK composta
      apontando para a unique que ele derruba, o PostgreSQL recusa:
      `cannot drop constraint uq_authors_tenant_id on table authors because
      other objects depend on it`.
    - **Não vê as foreign keys.** Uma FK composta cujo nome mudou não aparece
      no diff; ela fica com o nome antigo no banco, e é esse nome que o
      `parse_integrity_error` devolve.
    - **Reconstrói o índice.** Criar a unique de novo constrói o índice
      dela lendo a tabela inteira. O `RENAME` só troca o nome — e no
      PostgreSQL renomear a constraint renomeia junto o índice por trás dela
      (medido em `pg_indexes`).

O `legacy_constraint_renames` compara a `metadata` com a convenção antiga e
lista toda constraint composta cujo nome mudou — foreign keys incluídas —,
com o `RENAME` pronto. Rode uma vez, com todos os models importados:

```python
# scripts/constraint_renames.py
from sqlalchemy.dialects import postgresql

import src.db.models
from tempest_fastapi_sdk import BaseModel, legacy_constraint_renames

dialect = postgresql.dialect()
renames = legacy_constraint_renames(BaseModel.metadata)

print("def upgrade() -> None:")
for rename in renames:
    print(f"    op.execute({rename.statement(dialect)!r})")

print("def downgrade() -> None:")
for rename in renames:
    print(f"    op.execute({rename.inverse().statement(dialect)!r})")
```

Crie uma revisão **vazia** (`helper.revision(message="rename composite
constraints", autogenerate=False)`) e cole a saída nela:

```python
"""rename composite constraints to the per-column convention."""

from alembic import op

revision: str = "b7e1c2d3a4f5"
down_revision: str | None = "ae12cd34"


def upgrade() -> None:
    """Renomeia as constraints compostas para a convenção por coluna."""
    op.execute("ALTER INDEX ix_books_author RENAME TO ix_books_author_books_title")
    op.execute(
        "ALTER TABLE books RENAME CONSTRAINT uq_books_title "
        "TO uq_books_title_release_year"
    )


def downgrade() -> None:
    """Volta aos nomes da convenção antiga."""
    op.execute("ALTER INDEX ix_books_author_books_title RENAME TO ix_books_author")
    op.execute(
        "ALTER TABLE books RENAME CONSTRAINT uq_books_title_release_year "
        "TO uq_books_title"
    )
```

Depois do `upgrade`, o autogenerate não vê mais diferença nenhuma, e as
constraints de uma coluna ficaram intocadas (medido: `compare_metadata`
devolve `[]` sobre o banco renomeado, em `tests/db/test_naming_live.py`).

!!! tip "Por que colar, e não chamar a função dentro da migração"
    A migração tem que fazer amanhã o mesmo que faz hoje. Chamada no
    `upgrade()`, a função calcularia os renames a partir dos models do dia
    em que a migração roda — uma constraint composta criada depois entraria
    num arquivo antigo. As revisões anteriores não precisam de nada: o
    autogenerate gravou o nome em cada uma com `op.f(...)`.

!!! info "Detalhes que valem saber"
    - **O SDK também tem uma.** O `make_user_oauth_account_model` declara
      `UniqueConstraint("provider", "subject")` e
      `UniqueConstraint("user_id", "provider")`: `uq_<tabela>_provider` vira
      `uq_<tabela>_provider_subject`, e `uq_<tabela>_user_id` vira
      `uq_<tabela>_user_id_provider`. Com o model importado, a função os
      lista junto dos seus.
    - **Duas entradas com o mesmo `old_name`** são a colisão antiga: o
      PostgreSQL recusou o segundo `CREATE`, então o banco tem só uma delas.
      Fique com a entrada cujas colunas são as da constraint que existe.
    - **SQLite não renomeia constraint** — o nome mora no texto do
      `CREATE TABLE` —, e `statement()` levanta `ValueError` fora do
      PostgreSQL. No SQLite de desenvolvimento, aplique a migração em batch
      que o autogenerate gera (ela reconstrói a tabela e funciona, medido) ou
      recrie o banco.
    - **Quer adiar?** Dê à constraint o nome que ela já tem no banco:
      `UniqueConstraint("title", "release_year", name="uq_books_title")`.
      Nome explícito vence a convenção, e a função passa a ignorá-la.

**Recap:** `init` uma vez, `revision --autogenerate` por mudança, `upgrade`
no startup, `check` no CI, `safe_upgrade` para proteger dados. Banco criado
antes da correção de nomes compostos: `legacy_constraint_renames` escreve os
`RENAME`, e você cola numa revisão vazia.

---

## 9. Detectando queries lentas

`SlowQueryLogger` registra um listener na engine e emite uma linha de log
para toda instrução acima de um limiar. Anexe uma vez no boot:

```python
# src/api/app.py — depois de db.connect()

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk.db import SlowQueryLogger

from src.api.dependencies.resources import db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Connect, instrument slow queries, then serve."""
    await db.connect()
    slow = SlowQueryLogger(db.engine, threshold_ms=200.0)
    slow.attach()
    yield
    await db.disconnect()
```

| Parâmetro | Padrão | Para quê |
| --- | --- | --- |
| `threshold_ms` | `500.0` | Instruções neste tempo ou acima são logadas. |
| `level` | `logging.WARNING` | Nível das linhas de slow-query. |
| `log_parameters` | `False` | Inclui os bind params na linha. **Só em dev** — podem carregar PII. |
| `explain` | `False` | Roda `EXPLAIN` e anexa o plano. Custa um round-trip por query lenta. |

!!! danger "`log_parameters=True` só em desenvolvimento"
    Os bind parameters podem conter segredos e PII. Mantenha `False` em
    produção — o padrão já é seguro.

**Recap:** `SlowQueryLogger(db.engine, threshold_ms=...).attach()` no
lifespan transforma queries lentas em linhas de log acionáveis, com
`EXPLAIN` opcional para investigar planos.

---

## Recap

- `BaseModel` traz `id`, `is_active`, `created_at` e `updated_at`; você declara
  só as colunas do seu domínio, e o hook do Alembic mantém essa ordem nas
  migrações geradas.
- Constraint composta ganha nome com todas as colunas
  (`uq_books_title_release_year`); banco criado antes dessa correção se
  migra com os `RENAME` que `legacy_constraint_renames` escreve.
- Um `AsyncDatabaseManager` por aplicação, em `resources.py` — não um por
  request.
- `BaseRepository` serve instanciado para CRUD puro e subclassificado quando
  aparece query própria; filtro é um dict com convenção previsível, e `None`
  numa coluna simples é `IS NULL`.
- Operação em lote tem duas famílias: a que devolve instância (`add_all`,
  `update_many`) e a que não devolve, mas é uma ida ao banco.
- Mixin entra quando o domínio pede: soft-delete e auditoria custam coluna,
  e o filtro é seu — o `SoftDeleteMixin` não instala filtro global, então
  esconder linha apagada é passar `{"deleted_at": None}`.
- Paginação tem duas formas com propósitos diferentes: `paginate` para navegar
  por página, `cursor_paginate` para lista que cresce enquanto o usuário lê.
- Migração é `init` uma vez, `revision --autogenerate` por mudança, `upgrade`
  no deploy — e `SlowQueryLogger` no `engine` mostra a query lenta com
  `EXPLAIN` antes do usuário reclamar.

## Próximos passos

Esta página cobriu o núcleo. Os recursos avançados de banco têm receitas
dedicadas:

- [Multi-tenant »](multi-tenant.md) — `TenantScopedRepository` para
  isolamento por tenant.
- [Audit trail »](audit-trail.md) — `BaseAuditLogModel`, `add_audited` /
  `update_audited` / `delete_audited` (quem mudou o quê, na mesma tx).
- [Outbox transacional »](outbox.md) — `BaseOutboxModel` + `OutboxRelay`,
  `save_with_outbox` para publicar eventos atomicamente com a escrita.
- [Offline sync »](offline-sync.md) — `changes_since` + paginação de
  delta para clientes offline-first.
- [Deploy seguro »](deploy-safety.md) — migrações destrutivas + shutdown
  gracioso.
- [Testes »](testing.md) — SQLite em memória, fixtures, `create_tables`.
