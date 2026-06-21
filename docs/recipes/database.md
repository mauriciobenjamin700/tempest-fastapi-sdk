# Banco de dados

Esta é a camada que toda service Tempest usa para falar com PostgreSQL
(produção) ou SQLite (desenvolvimento/testes) sobre **SQLAlchemy 2.0
async**. Ela existe para que você nunca reescreva a mesma engine,
a mesma sessão por request, o mesmo CRUD e a mesma paginação em cada
projeto.

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

### Helpers que vêm de graça

Toda instância de `BaseModel` ganha:

```python
# Serializar para dict (útil em logs/testes)
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

**Recap:** herde `BaseModel`, declare só as colunas do seu domínio, e o
SDK entrega id/timestamps/soft-delete, nomes de constraint determinísticos
e helpers de serialização.

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

### Ciclo de vida no lifespan

Abra e feche a engine junto com a aplicação:

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.dependencies.resources import db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database on startup, dispose it on shutdown."""
    await db.connect()
    yield
    await db.disconnect()
```

### Health check

`health_check()` roda um `SELECT 1` e engole qualquer exceção, devolvendo
só `True`/`False` — perfeito para `/health`:

```python
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
    - `db.get_session_context()` — context manager que faz **commit** no
      sucesso e rollback no erro. Use em scripts e tasks de background.
    - `db.get_session()` — sessão crua; você fecha.
    - `db.create_tables()` / `db.drop_tables()` — só para testes e dev
      local; em produção o schema é do Alembic.

!!! danger "Nunca logue `db_url`, sempre `db_url_safe`"
    A URL crua carrega usuário e senha. `db_url_safe` renderiza
    `postgresql+asyncpg://***@host/db`. A URL crua fica num atributo
    privado justamente para não vazar em `repr()` ou log acidental.

**Recap:** um `AsyncDatabaseManager` por app, em `resources.py`;
`session_dependency` injeta a sessão por request; `connect`/`disconnect`
no lifespan; `health_check` + `db_url_safe` no `/health`.

---

## 3. O repository

`BaseRepository[Model]` é o coração da camada. Ele encapsula o CRUD async,
os filtros, as operações em lote e a paginação. Há dois jeitos de usá-lo.

### Modo direto — CRUD puro

Quando você não tem query custom, instancie direto:

```python
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

repository = BaseRepository(session, model=UserModel)
user = await repository.get_by_id(user_id)
```

### Modo subclasse — quando há queries próprias

Subclassifique para adicionar consultas do domínio e os três mappers que
traduzem ORM ↔ DTO. **O construtor é o contrato** — você repassa `model`
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
    eles, o SDK gera mensagens a partir de `Model.__name__` (`"User not
    found"`, `"Conflict creating User"`). Passe `not_found_exception=`
    para subir uma exceção de domínio mais rica que o `NotFoundException`
    padrão.

### O CRUD que você ganha

Lembrando da convenção de coleções do projeto: lookups de **registro
único** levantam 404; lookups de **coleção** devolvem `[]`.

```python
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
taken = await repository.exists_excluding(
    {"email": "a@b.com"}, exclude_id=user.id
)

# id-ou-instância → instância (sem if isinstance espalhado nas services)
user = await repository.resolve(user_or_id)

# Escrita
created = await repository.add(
    UserModel(name="Ana", email="ana@x.com", password_hash="...")
)
updated = await repository.update(user)         # commita mutações numa instância anexada

# Remoção
await repository.delete(user_id)                # hard delete (404 se não existe)
await repository.delete_many({"is_active": False})  # retorna contagem
await repository.delete_batch([id1, id2, id3])      # por PK, retorna contagem

# Soft-delete via flag is_active (não precisa do SoftDeleteMixin)
await repository.soft_delete(user_id)           # is_active = False
await repository.restore(user_id)               # is_active = True
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

---

## 4. Filtros por convenção

Todos os métodos que recebem `filters: dict[str, Any]` passam pelo mesmo
motor. Um valor `None` **sempre pula** a condição (filtro ausente ≠
`WHERE col IS NULL`). As convenções:

| Chave / valor | SQL gerado | Exemplo |
| --- | --- | --- |
| `name` (str) | `ILIKE %value%` case-insensitive | `{"name": "ana"}` |
| `bool` | `col.is_(value)` | `{"is_active": True}` |
| `list` | `col.in_(values)` | `{"id": [id1, id2]}` |
| `date` | `func.date(col) == value` (dia inteiro) | `{"created_at": hoje}` |
| `start_in` / `end_in` (date) | range no `date`/`created_at` | `{"start_in": d1, "end_in": d2}` |
| `<col>__<op>` | comparação `gt`/`gte`/`lt`/`lte`/`ne` | `{"updated_at__gt": marca}` |
| qualquer outra coluna | `col == value` | `{"email": "a@b.com"}` |

```python
# "ativos atualizados depois da marca d'água" — precisão de timestamp
changed = await repository.list({
    "is_active": True,
    "updated_at__gt": watermark,
})

# "criados entre duas datas" — dia inteiro
report = await repository.list({"start_in": inicio, "end_in": fim})

# busca textual + pertinência a um conjunto
hits = await repository.list({"name": "silva", "id": selected_ids})
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

**Recap:** um dict, convenções previsíveis, `None` pula. Strings em `name`
viram busca ILIKE; sufixos `__op` dão comparações precisas; `None` nunca
vira `IS NULL`.

---

## 5. Operações em lote

Para volume, o ORM linha-a-linha é caro. O repository oferece duas
famílias: as que **mantêm** a unit-of-work (instâncias atualizadas de
volta) e as que a **contornam** (uma única instrução, sem refresh).

```python
# Mantém a UoW — instâncias anexadas e atualizadas
created = await repository.add_all([m1, m2, m3])      # vários INSERTs, 1 tx
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

from sqlalchemy import select

from tempest_fastapi_sdk import BaseService

from src.db.models import UserModel
from src.db.repositories import UserRepository
from src.schemas import UserResponse, UserUpdateSchema


class UserService(BaseService[UserRepository, UserResponse]):
    """Business logic for the user domain."""

    async def list_alive(self) -> list[UserResponse]:
        """Return only rows where ``deleted_at IS NULL``.

        ``_apply_filters`` skips ``None`` by design (filtro ausente !=
        ``IS NULL``), so the ``IS NULL`` clause must be issued as a raw
        SQLAlchemy query bound to the same session.

        Returns:
            list[UserResponse]: The alive users.
        """
        result = await self.repository.session.execute(
            select(UserModel).where(UserModel.deleted_at.is_(None))
        )
        instances = result.scalars().all()
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
    `MFAMixin` adiciona `totp_secret` / `totp_enabled_at` ao modelo de
    usuário quando o projeto liga o fluxo MFA bundled. Detalhes em
    [MFA (TOTP / 2FA) »](mfa.md).

**Recap:** mixins entram só quando o domínio precisa; a filtragem de
soft-delete é sua (`deleted_at IS NULL` via query crua); o carimbo de
auditoria mora no service.

---

## 7. Paginação

O SDK pagina de duas formas, **ambas embutidas no repository**. Você
quase nunca escreve a query de paginação à mão.

### Offset — quando o cliente quer "página 3 de 12"

```python
# src/db/repositories/user.py — método de conveniência
from typing import Any

from tempest_fastapi_sdk import BasePaginationSchema

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

### Cursor — quando a tabela é grande

A paginação por cursor escala melhor que offset em tabelas grandes (sem
`COUNT(*)`, estável sob inserts concorrentes) ao custo de perder acesso
aleatório. **Já está pronta** em `cursor_paginate` — ordena por
`(order_by, id)` e codifica o cursor opaco automaticamente:

```python
# src/db/repositories/user.py
from typing import Any

from tempest_fastapi_sdk import CursorPaginationSchema

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
    roda `ruff check --fix` e depois `ruff format` em cada revisão. Sem
    isso, os arquivos do Alembic falham no `tempest lint` (`W291` no
    `Revises:` vazio, `E501` em `sa.Column(...)` longas). Os hooks usam a
    config de `ruff` do **seu** projeto. Requer `ruff` no `PATH` — já é
    dependência de dev em todo scaffold `tempest new`.

### Aplicar no startup

```python
# src/api/app.py — dentro do lifespan
import asyncio

from tempest_fastapi_sdk import AlembicHelper

from src.api.dependencies.resources import db
from src.core.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run pending migrations, then serve."""
    helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
    await asyncio.to_thread(helper.upgrade)
    await db.connect()
    yield
    await db.disconnect()
```

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

**Recap:** `init` uma vez, `revision --autogenerate` por mudança, `upgrade`
no startup, `check` no CI, `safe_upgrade` para proteger dados.

---

## 9. Detectando queries lentas

`SlowQueryLogger` registra um listener na engine e emite uma linha de log
para toda instrução acima de um limiar. Anexe uma vez no boot:

```python
# src/api/app.py — depois de db.connect()
from tempest_fastapi_sdk.db import SlowQueryLogger

from src.api.dependencies.resources import db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
