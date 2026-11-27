# 4. Filtros por convenção

Use para filtrar por `campo__operador` e saber quando uma chave ou valor é recusado e como a paginação herda os operadores.

Parte da receita [Banco de dados](../database.md).

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

## Chave desconhecida é recusada

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

## Valor que não cabe no operador é recusado

Um valor com a **forma errada** para o operador — um `between` que não é o par
`(lo, hi)` — levanta `InvalidFilterValueException` antes de qualquer statement.
Era o restante do defeito do #465: a condição sumia do `WHERE` e, em
`bulk_update`, `delete_many` e `update_returning`, a escrita atingia justamente
as linhas que o filtro **não** nomeava.

O `in` **não** entra nessa lista: um escalar solto é embrulhado numa lista de um
item (`{"status__in": "open"}` → `IN ('open')`), como sempre foi, porque a
condição não sumia do `WHERE`.

```python
import asyncio

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    InvalidFilterValueException,
)


class TicketModel(BaseModel):
    __tablename__ = "tickets_bad_value_demo"

    status: Mapped[str] = mapped_column(String(16))


async def main() -> None:
    """Run this example."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TicketModel.metadata.create_all)
    async with AsyncSession(engine) as session:
        repo: BaseRepository[TicketModel] = BaseRepository(
            session, model=TicketModel
        )
        for status in ("open", "paid"):
            await repo.add(TicketModel(status=status))
        try:
            await repo.delete_many({"status__between": ["open"]})
        except InvalidFilterValueException as exc:
            print(exc.status_code, exc.code, exc.details)
        print(await repo.count())
    await engine.dispose()


asyncio.run(main())
```

Saída:

```text
422 INVALID_FILTER_VALUE {'filter': 'status__between', 'value': "['open']"}
2
```

`details["value"]` ecoa um `repr` do valor, **truncado** em 64 caracteres: o
filtro errado costuma carregar valor grande (um payload, uma lista de ids), e
o erro não é lugar de repetir tudo.

!!! warning "Mudança de comportamento (#484)"
    Até a 0.312.0 um `between` malformado era **ignorado** — a condição
    desaparecia do `WHERE`. Quem mandava um par errado e dependia desse
    silêncio passa a receber 422; corrija a forma do par `(lo, hi)`. Um escalar
    em `in` segue embrulhado numa lista de um item, como sempre foi.

## Toda paginação herda os operadores

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

## O nome do tamanho de página no fio

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
