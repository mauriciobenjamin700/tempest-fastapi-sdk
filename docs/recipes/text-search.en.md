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
SQLAlchemy clause. `count()`, `list()` and `cursor_paginate()` accept the
same.

## Full-text layer: `full_text_search()`

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository, TextSearchLanguage


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
        language=TextSearchLanguage.PORTUGUESE,
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
from tempest_fastapi_sdk import BaseRepository, TextSearchWeight


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
        weights={"title": TextSearchWeight.A},
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
- `TextSearchLanguage`, `TextSearchWeight` and `TokenMatch` are enums: no
  magic strings at the call site.
