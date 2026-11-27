# 4. Convention-based filters

Use to filter with `field__operator` and know when a key or value is refused and how pagination inherits the operators.

Part of the [Database](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/database.md) recipe.

Every method that takes `filters: dict[str, Any]` goes through the same
engine. A `None` value means **`IS NULL`**, as in any ORM:
`{"deleted_at": None}` matches the rows that were never deleted. The
conventions:

| Key / value | Generated SQL | Example |
| --- | --- | --- |
| `name` (str) | case-insensitive `ILIKE %value%` | `{"name": "ana"}` |
| `bool` | `col.is_(value)` | `{"is_active": True}` |
| non-string iterable (`list`/`set`/`tuple`/`frozenset`/`range`/generator/`dict` view) | `col.in_(values)` — the iterable is materialized once, so passing a `set` needs no manual conversion to a `list` | `{"id": {id1, id2}}` |
| `date` | `func.date(col) == value` (whole day) | `{"created_at": today}` |
| `start_in` / `end_in` (date) | range on `date`/`created_at` | `{"start_in": d1, "end_in": d2}` |
| `<col>__<op>` | comparison `gt`/`gte`/`lt`/`lte`/`ne` | `{"updated_at__gt": mark}` |
| any other column | `col == value` | `{"email": "a@b.com"}` |

```python
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    selected_ids = [uuid4(), uuid4()]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    watermark = datetime.now(timezone.utc) - timedelta(hours=1)
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # "active rows updated after the watermark" — timestamp precision
        changed = await repository.list({
            "is_active": True,
            "updated_at__gt": watermark,
        })

        # "created between two dates" — whole day
        report = await repository.list({"start_in": start, "end_in": end})

        # text search + membership in a set
        hits = await repository.list({"name": "silva", "id": selected_ids})


asyncio.run(main())
```

!!! info "`start_in`/`end_in` vs `__gt`/`__lt`"
    `start_in`/`end_in` match by **whole day** (`func.date`) against the
    model's `date` column (or `created_at` if absent). The `__op` suffixes
    are **timestamp-precise** — that's what delta-sync queries use. Choose
    by precision.

!!! tip "Filters come from a schema, not loose strings"
    In practice you don't assemble this dict by hand.
    `BasePaginationFilterSchema` (and its subclasses) expose
    `.get_conditions()`, which returns the dict already stripped of
    `None`. The router receives the filter via `Depends()`.

## An unknown key is refused

A key that is not a column of the model (`{"usr_id": 1}`, a typo) or a
suffix that is not an operator (`{"user_id__bogus": 1}`) raises
`UnknownFilterKeyException` **before** any statement runs — in every
method that takes `filters` (`list`, `count`, `exists`, `first`, `get`,
`get_or_none`, `paginate`, `cursor_paginate`, `bulk_update`,
`update_returning`, `delete_many`), in `Q`, and in the layers that only
pass it along (`BaseService`, `BaseController`, `TenantScopedRepository`):

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

Output:

```text
422 UNKNOWN_FILTER_KEY {'filter': 'usr_id'}
{'filter': 'user_id__bogus'}
3
```

All three rows are still there: `delete_many` deleted nothing.

- `details` carries only the rejected key, never the model's column list —
  when the dict comes from a query string, that list is a map of what to
  probe.
- A Python attribute with no SQL reading (a method, a `@property`, the
  `metadata` itself) counts as unknown. A column, a `column_property` and
  the class-level expression of a `hybrid_property` all resolve.
- `start_in` / `end_in` remain repository sugar, not columns.
- In the admin, `list_filter`, `search_fields` and every `Lens`' `filters`
  are checked when the `AdminModel` is built: a typo becomes a `ValueError`
  at boot, naming the option, instead of a 422 on the first click.

!!! warning "Behavior change (#465)"
    Up to 0.308.0 an unknown key was **silently ignored**: with only
    `{"usr_id": 1}` in the dict, `list`/`count` returned every row and
    `bulk_update`/`delete_many` changed the whole table. Anyone relying on
    that — for instance a filter schema with a non-column field (`search`,
    `include_archived`) forwarded whole through `get_conditions()` — now gets
    a 422. Take the field out of the dict before forwarding
    (`conditions = f.get_conditions()` then
    `search = conditions.pop("search", None)`) and handle it in the service.

## A value that does not fit the operator is refused

A value with the **wrong shape** for its operator — a `between` that is not
the `(lo, hi)` pair — raises `InvalidFilterValueException` before any statement
runs. It was the rest of the #465 defect: the condition vanished from the
`WHERE` and, on `bulk_update`, `delete_many` and `update_returning`, the write
hit exactly the rows the filter did **not** name.

`in` is **not** on that list: a bare scalar is wrapped in a one-item list
(`{"status__in": "open"}` → `IN ('open')`), as it always was, because the
condition never vanished from the `WHERE`.

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

Output:

```text
422 INVALID_FILTER_VALUE {'filter': 'status__between', 'value': "['open']"}
2
```

`details["value"]` echoes a `repr` of the value, **truncated** at 64
characters: a mistyped filter usually carries a large value (a payload, a list
of ids), and the error is no place to repeat all of it.

!!! warning "Behavior change (#484)"
    Up to 0.312.0 a malformed `between` was **ignored** — the condition
    disappeared from the `WHERE`. Anyone sending a wrong pair and relying on
    that silence now gets a 422; fix the shape of the `(lo, hi)` pair. A scalar
    in `in` is still wrapped in a one-item list, as it always was.

## Every paginated listing inherits the operators

Because `get_conditions()` only strips the pagination keys (`page`,
`page_size`, `order_by`, `ascending`) and forwards **everything else** to
the same engine, any `BasePaginationFilterSchema` subclass gets the
operators for free: just declare a field named `<column>__<op>`. No extra
inheritance, no mixin — the field name *is* the operator.

```python
from tempest_fastapi_sdk import BasePaginationFilterSchema
from pydantic import Field


class ProductFilter(BasePaginationFilterSchema):
    """Product listing filter — each field becomes one condition."""

    name: str | None = Field(default=None)                 # ILIKE %name%
    category_id__in: set[int] | None = Field(default=None)  # IN (a set!)
    price__between: tuple[float, float] | None = Field(default=None)  # BETWEEN
    sku__ilike: str | None = Field(default=None)            # raw ILIKE
    created_at__gte: str | None = Field(default=None)       # >=
```

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel
from src.schemas import ProductFilterSchema

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

f = ProductFilterSchema(name="silva", page=1, page_size=20)
repo = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    # In the service/repo the whole schema becomes filters + pagination:
    data = await repo.paginate(
        filters=f.get_conditions(),  # name/category_id__in/price__between/…
        **f.get_pagination_conditions(),  # page/page_size/order_by/ascending
    )


asyncio.run(main())
```

The frontend calls `?category_id__in=1&category_id__in=2&price__between=10&price__between=20`
and FastAPI builds the schema via `Depends()`. The schema's
`get_conditions()` drops the fields that were not filled **before** the
dict is built (`to_dict(exclude_none=True)`), so the client sends only
the fields it wants and a `None` from the schema never reaches the
repository as `IS NULL`.

!!! warning "`None` changed meaning in v0.292.0"
    It used to **drop the key** — the filter vanished and the query
    returned *everything*. That is the most expensive failure shape
    available: silent, and in the direction of **more** rows. Measured
    in a consumer, `{"left_at": None}` (the obvious spelling of "still a
    member") also matched people who had left, and the WebSocket
    fan-out built from it kept delivering the group's messages to them.

    If you built the dict by hand with optional keys and relied on the
    drop, leave the key out instead — or use `get_conditions()`, which
    already does. Where `None` has no reading at all (`__gt`,
    `__between`, `__in`), the condition is still dropped, now with a
    `DroppedFilterWarning` naming the key.

**Recap:** one dict, predictable conventions, `None` is `IS NULL`.
Strings on `name` become ILIKE searches; `__op` suffixes give precise
comparisons. Every paginated listing inherits these operators just by
declaring the field.

---

## The wire name of the page size

By default the envelope publishes `page_size`, both in the response and as the
query parameter. For a new service that costs nothing — but a service that
**already** publishes `{"total", "items", "page", "size", "pages"}` cannot
rename it without breaking every paginated endpoint at once, and an app
already in a store does not update in lockstep with its backend.

As of v0.261.0 there is a ready-made variant:

```python
from tempest_fastapi_sdk import (
    CompactPaginationFilterSchema,
    CompactPaginationSchema,
)

# GET /users?size=50
filters = CompactPaginationFilterSchema.model_validate({"size": 50})
filters.page_size   # 50 — the Python name does not move

page = CompactPaginationSchema[int](
    items=[1], total=1, page=1, page_size=20, pages=1
)
page.model_dump(by_alias=True)["size"]   # 20
```

FastAPI serialises a `response_model` with `by_alias=True`, so declaring
`CompactPaginationSchema[Item]` as the `response_model` is all it takes.

!!! important "The repository does not change"
    `BaseRepository.paginate` keeps returning
    `{"items", "total", "page", "page_size", "pages"}` whatever the
    envelope publishes. The rename lives **in the schema and nowhere
    else** — there is nothing to rename in the data layer, and moving a
    service from one envelope to the other touches one line.

If your wire name is neither `page_size` nor `size`, both base classes turn
`populate_by_name` on, so you can override just the field:

```python
from pydantic import Field

from tempest_fastapi_sdk import BasePaginationSchema


class MyPage(BasePaginationSchema[int]):
    """An envelope of your own, renaming only the page size."""

    page_size: int = Field(
        validation_alias="perPage",
        serialization_alias="perPage",
    )
```

The wire name is written **twice**, `validation_alias` to read and
`serialization_alias` to write. `Field(alias=...)` alone looks like it works
and mypy accepts it, but pyright and basedpyright flag it in the consumer —
the SDK does not use that form anywhere.
