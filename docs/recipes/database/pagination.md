# 7. Paginação

Use para paginar por offset ("página 3 de 12") ou por cursor (tabela grande) e limitar colunas de ordenação e tamanho de página.

Parte da receita [Banco de dados](../database.md).

O SDK pagina de duas formas, **ambas embutidas no repository**. Você
quase nunca escreve a query de paginação à mão.

## Offset — quando o cliente quer "página 3 de 12"

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

## Quais colunas ordenam, e quantas linhas cabem numa página

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

## Cursor — quando a tabela é grande

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
    [Offline sync »](../offline-sync.md).

**Recap:** `paginate` (offset) para navegação por página; `cursor_paginate`
para feeds/tabelas grandes. Ambos prontos — você só mapeia o resultado
para o schema de resposta.
