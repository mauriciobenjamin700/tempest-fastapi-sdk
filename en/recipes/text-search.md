# Text search (LIKE + full-text)

Two layers, because they answer different questions. The portable one
behaves **identically** on PostgreSQL and SQLite and needs no index, no
extension and no migration. The full-text one uses PostgreSQL's engine —
stemming, per-field weighting, a relevance score — and degrades to the
first one where that engine does not exist.

## Portable layer: `search()`

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def find(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Search the term across two columns.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.

    Returns:
        list[ArticleModel]: The matching articles.
    """
    return await articles.search("nota fiscal", fields=["title", "body"])
```

Each word of the term is matched case-insensitively against **every**
listed column, and the words combine with `AND`. So typing more words
narrows the result, which is what a search box should do. `nota` may be
in the title and `rodape` in the body — the row still matches.

!!! check "The user's `%` is literal"
    `search("100%", fields=["title"])` searches for the `%` character.
    Without escaping, that term would match every row — the quietest way
    for a search to be wrong.

A blank term applies no filter at all, so an empty search box **lists**
rather than hides.

### Typed columns

`fields` accepts either the name or the mapped attribute itself, so the
type-checker can catch a renamed column:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def by_author(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Search by the mapped attribute rather than a string name.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.

    Returns:
        list[ArticleModel]: The matching articles.
    """
    return await articles.search("joao", fields=[ArticleModel.author])
```

### Combining with everything else

`search()` takes `filters`, `where`, `order_by`, `with_` and `limit`:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def find_by_author(
    articles: BaseRepository[ArticleModel],
    author: str,
) -> list[ArticleModel]:
    """Combine a text search with an ordinary filter.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.
        author (str): The author to filter by.

    Returns:
        list[ArticleModel]: The matching articles.
    """
    return await articles.search(
        "nota",
        fields=["title", "body"],
        filters={"author": author},
        limit=20,
    )
```

### Fields chosen by the request

Searching is an oracle on the searched column: which rows come back tells,
row by row, whether the value contains the term. That is why `search()`,
`search_condition()`, `full_text_search()` and `full_text_condition()`
**always** refuse `hashed_password` and the columns the model declares in
`__secret_columns__`, with a `422`. When `fields` comes from a query
parameter, also declare which columns a search may name — the same shape as
`orderable_columns`:

```python
from typing import ClassVar

from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


class ArticleRepository(BaseRepository[ArticleModel]):
    """Article repository whose search is limited to three columns."""

    searchable_columns: ClassVar[frozenset[str] | None] = frozenset(
        {"title", "body", "author"}
    )


async def find(
    articles: ArticleRepository,
    term: str,
    fields: list[str],
) -> list[ArticleModel]:
    """Search the columns the client asked for, inside the declared set.

    Args:
        articles (ArticleRepository): Article repository.
        term (str): The typed term.
        fields (list[str]): The columns from the query string.

    Returns:
        list[ArticleModel]: The matching articles.
    """
    return await articles.search(term, fields=fields)
```

A column outside the set becomes a `ValidationException` (`422`) with
`details={"field": <name>, "allowed": [...]}`, and `allowed` lists only the
declared set. Without a declared set, `details` carries only the `field`:
the refusal never publishes the model's column list. The constructor also
takes `searchable_columns=`, and declaring a column the model does not map,
or a secret column, fails at construction with `ValueError`.


#### The same set on the request schema

The repository refusal is the boundary; `BaseSearchSchema` moves it up to
request validation, the way `BasePaginationFilterSchema.orderable_columns`
does for `order_by`. Its fields carry the names of `search()`'s arguments
(`q` is the term, 1 to 256 characters; `fields`, the columns), and the
declared set shows in the OpenAPI schema as an `enum`:

```python
from typing import ClassVar

from fastapi import APIRouter, FastAPI

from src.db.models import ArticleModel
from tempest_fastapi_sdk import (
    BaseRepository,
    BaseSearchSchema,
    register_exception_handlers,
)


class ArticleRepository(BaseRepository[ArticleModel]):
    """Article repository with search narrowed to three columns."""

    searchable_columns: ClassVar[frozenset[str] | None] = frozenset(
        {"title", "body", "author"}
    )


class ArticleSearchSchema(BaseSearchSchema):
    """Article search: the repository's three columns."""

    searchable_columns: ClassVar[frozenset[str] | None] = (
        ArticleRepository.searchable_columns
    )


router = APIRouter()


@router.post("/articles/search")
async def search_articles(body: ArticleSearchSchema) -> list[str]:
    """Validate the columns before any query.

    Args:
        body (ArticleSearchSchema): Term and columns.

    Returns:
        list[str]: The accepted columns.
    """
    return body.fields


app = FastAPI()
register_exception_handlers(app)
app.include_router(router)
```

The refusal is a `ValidationException`, an `HTTPException`: without
`register_exception_handlers(app)` FastAPI still answers `422`, but with only
`detail` (measured: `{"detail": "'hashed_password' is not a searchable column"}`),
no `code` and no `details`. Measured with the handlers:
`{"q": "x", "fields": ["hashed_password"]}` answered `422` with
`code="VALIDATION_ERROR"` and
`details={"field": "hashed_password", "allowed": ["author", "body", "title"]}` (the
repository's envelope) before the handler ran.

## Paginating a search

`search()` returns a list. To paginate and count, ask for the
**condition** and pass it as `where=` — the search then behaves like any
other filter:

```python
from typing import Any

from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def search_page(
    articles: BaseRepository[ArticleModel],
    term: str,
) -> dict[str, Any]:
    """Paginate and count a search like any other filter.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.
        term (str): The term the user typed.

    Returns:
        dict[str, Any]: The page, with its total and items.
    """
    return await articles.paginate(
        where=articles.search_condition(term, fields=["title", "body"]),
        page=2,
        page_size=20,
    )
```

This works because `where=` now accepts either a `Q` or a ready-made
SQLAlchemy clause. `count()` and `list()` accept the same.

`cursor_paginate()` has **no** `where=` — passing one raises
`TypeError: ... got an unexpected keyword argument 'where'`. It takes a
ready-made `Select` in `query=`, and the condition goes there:

```python
from typing import Any

from sqlalchemy import select
from tempest_fastapi_sdk import BaseRepository

from src.db.models import ArticleModel


async def search_feed(
    articles: BaseRepository[ArticleModel],
    term: str,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Cursor-paginate a search.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.
        term (str): The term the user typed.
        cursor (str | None): The previous page's `next_cursor`.

    Returns:
        dict[str, Any]: `items`, `next_cursor`, `has_more` and `limit`.
    """
    query = select(ArticleModel)
    condition = articles.search_condition(term, fields=["title", "body"])
    if condition is not None:
        query = query.where(condition)
    return await articles.cursor_paginate(query=query, cursor=cursor, limit=20)
```

The `if` is not decoration: a blank term makes `search_condition` return
`None` — through `where=` that already means "no filter", but a hand-built
`Select` has to skip the `.where(...)` itself.

## Full-text layer: `full_text_search()`

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository, TextSearchLanguageEnum


async def ranked(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Search with stemming, ordered by relevance.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.

    Returns:
        list[ArticleModel]: The articles, strongest match first.
    """
    return await articles.full_text_search(
        "nota fiscal",
        fields=["title", "body"],
        language=TextSearchLanguageEnum.PORTUGUESE,
    )
```

On PostgreSQL this compiles to
`to_tsvector(...) @@ websearch_to_tsquery('portuguese', term)` and the
results come back ordered by `ts_rank`, strongest first.

What this layer does and the portable one does not:

- **Stemming.** `comprou` finds `comprar`.
- **Stop words.** `de`, `a`, `para` do not needlessly narrow the search.
- **Search-engine syntax.** `websearch_to_tsquery` accepts what users
  already type — `"exact phrase"` in quotes, `-excluded` with a hyphen —
  and never raises a syntax error on stray punctuation, unlike
  `to_tsquery`.
- **A relevance score**, which is what makes ordering possible.

### Per-field weight

A term in the title should outrank the same term in the body:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository, TextSearchWeightEnum


async def title_first(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Make the title outweigh the body in the relevance score.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.

    Returns:
        list[ArticleModel]: The articles, ordered by relevance.
    """
    return await articles.full_text_search(
        "nota fiscal",
        fields=["title", "body"],
        weights={"title": TextSearchWeightEnum.A},
    )
```

PostgreSQL ranks `A` highest and `D` lowest; columns you leave out get
`D`.

## On SQLite

`full_text_search()` still returns **the right rows** — it falls back to
the portable layer. What is missing is stemming and ranking. Ask before
promising a relevance bar in the UI:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


def can_rank(articles: BaseRepository[ArticleModel]) -> bool:
    """Report whether the backend can order by relevance.

    Args:
        articles (BaseRepository[ArticleModel]): The article repository.

    Returns:
        bool: ``True`` when ``ts_rank`` is available.
    """
    return articles.supports_full_text
```

!!! warning "Do not mistake this for an index"
    Full-text search here is computed on the fly, with no materialized
    `tsvector` column and no GIN index. That makes it correct and
    migration-free, but it reads the table. For a large table on a hot
    query, the next step is a generated column with a GIN index — which
    the SDK does not package yet.

## Recap

- `search()` — identical on both backends, escapes the input, `AND`
  across words, `OR` across columns.
- `search_condition()` / `full_text_condition()` return the clause, which
  is why a search paginates and counts like any other filter.
- `full_text_search()` — stems, accepts the user's syntax, orders by
  relevance on PostgreSQL; degrades to the portable layer elsewhere.
- `supports_full_text` tells you which of the two you got.
- `hashed_password` and `__secret_columns__` are never searchable;
  `searchable_columns` narrows `fields` when it comes from the request.
- `TextSearchLanguageEnum`, `TextSearchWeightEnum` and `TokenMatchEnum` are enums: no
  magic strings at the call site.
