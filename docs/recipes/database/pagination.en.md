# 7. Pagination

Use to paginate by offset ("page 3 of 12") or by cursor (large tables) and restrict sortable columns and page size.

Part of the [Database](../database.md) recipe.

The SDK paginates two ways, **both built into the repository**. You almost
never write the pagination query by hand.

## Offset — when the client wants "page 3 of 12"

```python
# src/db/repositories/user.py — convenience method

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

`BaseRepository.paginate` returns a `dict` with `items` / `total` / `page`
/ `page_size` / `pages`. The total is computed from the **same** filtered
query, so custom joins still report a correct total. When `order_by` is
`None`, it orders by `created_at desc`.

!!! warning "`order_by` is validated against the model's columns"
    It arrives straight from a query parameter (`BasePaginationFilterSchema`
    declares a `str`), so it is untrusted input. `paginate` and
    `cursor_paginate` resolve the name through the mapper and raise
    `OrderByNotAllowedException` (**422**, a `ValidationException` subclass)
    when it is not a mapped column — including an attribute that exists on the
    class but is not a column, like `metadata`. The refusal **never** lists the
    model's columns: since 0.298.0 `details["allowed"]` only appears when you
    declared `orderable_columns`, and then it holds only that set. See the next
    section.

!!! tip "Forward the schema without unpacking it by hand"
    The `get_conditions()` / `get_pagination_conditions()` pair covers both
    sides of the filter: the former returns only the domain filters, the
    latter only the pagination keys (`page`, `page_size`, `order_by`,
    `ascending`). The service forwards the filter straight through, no
    `**f` — which would leak domain filters (`is_active`, etc.) as kwargs
    the repository does not accept:

    ```python
    data = await repo.paginate(
        filters=f.get_conditions(),
        **f.get_pagination_conditions(),
    )
    ```

    `CursorPaginationFilterSchema` exposes the same pair (with `cursor` /
    `limit` instead of `page` / `page_size`).

## Which columns sort, and how many rows fit in a page

Checking that `order_by` is a column is not enough on a public listing.
**Sorting is an oracle**: `?order_by=wallet` ranks everyone by balance, and a
caller who controls their own row (writes their own `email`, their own tax
id) learns the neighbouring row's value by binary search over where theirs
lands. And with no ceiling on `page_size`, one request reads the whole table.

Declare both on the listing's filter:

```python
from typing import Annotated

from fastapi import FastAPI, Query
from tempest_fastapi_sdk import BasePaginationFilterSchema, register_exception_handlers


class ProducerFilterSchema(BasePaginationFilterSchema):
    """Filter of the public producer listing."""

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

Piece by piece:

- **`orderable_columns`** is a `ClassVar[frozenset[str] | None]`. With the set
  declared, any other value becomes `OrderByNotAllowedException` during schema
  validation, before it reaches the repository:

    ```json
    {
      "detail": "Cannot order by this field",
      "code": "ORDER_BY_NOT_ALLOWED",
      "details": {"order_by": "hashed_password", "allowed": ["created_at", "name"]},
      "field": "order_by"
    }
    ```

    `details["allowed"]` is **only** the declared set, sorted. The default is
    `None`, which accepts any value and leaves the check to the repository —
    the previous behavior. An empty `order_by` (`?order_by=`) means absent and
    becomes `None`, never a 422.

- **`max_page_size`** is the inclusive ceiling on `page_size`. The base
  default is `DEFAULT_MAX_PAGE_SIZE` (**100**); declaring it on a subclass
  rewrites the field's `le=`, so the refusal is pydantic's own
  `less_than_equal` and OpenAPI publishes `maximum: 50` on the parameter —
  with `Depends()` and with `Annotated[..., Query()]`. `None` removes the
  ceiling.

!!! warning "The ceiling of 100 is new in 0.298.0"
    Up to 0.297.x `page_size` only had `ge=1`. A client asking for
    `page_size=500` now gets a 422 — declare `max_page_size` with the value the
    listing really needs (or `None`) instead of redeclaring the field.

The second line is the repository, for callers that reach it without the
schema (a script, the panel, another route). The same names exist on
`BaseRepository`, as a class attribute **or** a constructor argument (the
constructor wins):

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

Output:

```text
{'order_by': 'wallet', 'allowed': ['created_at', 'name']}
PAGE_SIZE_TOO_LARGE {'page_size': 51, 'max_page_size': 50}
```

- Without `orderable_columns` on the repository, any mapped column still
  sorts — but refusing a name that is not a column answers `details` with just
  `{"order_by": ...}`, no `allowed` key.
- A declared name that is not a model column raises `ValueError` when the
  repository is built: a typo in the declaration would otherwise refuse, on
  every request, exactly the column you meant to allow.
- The repository's `max_page_size` defaults to `None` (no ceiling), because
  internal callers such as the `/admin` panel export page by the thousand.
  Once declared, it applies to `paginate`, `cursor_paginate` (over `limit`)
  and `GeoRepositoryMixin.paginate_nearby`.

!!! tip "The repository's set governs every path"
    `orderable_columns` on the repository covers `paginate`, `cursor_paginate`
    and `changes_since` — include the watermark column (`updated_at`) if you
    use sync. And the `/admin` panel sorts through the same repository: if it
    uses this class via `repository_class`, sorting by a column outside the
    set is refused there too.

`CursorPaginationFilterSchema` has the same `orderable_columns`, and
`max_limit` (default `DEFAULT_MAX_CURSOR_LIMIT`, **500** — the `le=500` the
field always had) in the role of `max_page_size`.

**Recap:** declare `orderable_columns` and `max_page_size` on the filter of
every public listing; the 422 lists only what you declared. On the
repository, the same names are the second line.

## Cursor — when the table is large

Cursor pagination scales better than offset on large tables (no
`COUNT(*)`, stable under concurrent inserts) at the cost of losing random
access. It's **already built in** as `cursor_paginate` — it orders by
`(order_by, id)` and encodes the opaque cursor automatically:

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

Router, with the filter coming from a schema via `Depends()`:

```python
# src/api/routers/user.py
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import CursorPaginationFilterSchema

from src.api.dependencies.resources import SessionDep
from src.db.repositories import UserCursorPage, UserRepository

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCursorFilter(CursorPaginationFilterSchema):
    """Cursor filter for the user listing."""

    name: str | None = None   # ILIKE %value% by the repository convention


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

!!! info "The cursor is opaque"
    `next_cursor` is url-safe base64 JSON. The client never inspects it; it
    echoes the value back verbatim until `next_cursor` becomes `null`.
    Under the hood, `cursor_paginate` uses `encode_cursor`/`decode_cursor`
    and a `(order_by, id)` tuple comparison that's stable on Postgres.

!!! info "The cursor value comes back in the column's type"
    The cursor is JSON, so a `datetime` leaves page 1 as an ISO string.
    Before building the `WHERE`, `cursor_paginate` rehydrates the value
    into the column's Python type (`datetime`, `date`, `time`, `UUID`,
    `Decimal`, enum). Without that, PostgreSQL refused the whole
    comparison on the **second** page:

    ```text
    operator does not exist: timestamp with time zone < character varying
    ```

    And SQLite did not complain — it compared the two strings and returned
    a **wrong** page: measured, a 7-row walk in pages of 3 repeated `r02`
    and skipped `r05`. A text column is left untouched, and so is a
    `TypeDecorator` that declares no `python_type`.

!!! tip "For offline-first sync there's a third mode"
    `changes_since` + `SyncPaginationSchema` do delta pagination (rows
    changed since a watermark). See [Offline sync »](../offline-sync.md).

**Recap:** `paginate` (offset) for page navigation; `cursor_paginate` for
feeds/large tables. Both ready — you only map the result to the response
schema.
