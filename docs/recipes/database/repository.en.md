# 3. The repository

Use for async CRUD with `BaseRepository`: direct or subclass mode, constraint errors, eager-loading, signals and `F`/`Q` expressions.

Part of the [Database](../database.md) recipe.

`BaseRepository[Model]` is the heart of the layer. It encapsulates async
CRUD, filters, bulk ops and pagination. There are two ways to use it.

## Direct mode — plain CRUD

When you have no custom query, instantiate directly:

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


repository = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    user = await repository.get_by_id(user_id)


asyncio.run(main())
```

## Subclass mode — when you have your own queries

Subclass it to add domain queries and the mappers that translate
ORM ↔ DTO. There are three: `map_to_model` (default `self.model(**data)`),
`map_to_response` and `map_to_schema` — the last two raise
`NotImplementedError` until you override them, so implement the ones your
code calls (the example implements `map_to_response` and `map_to_model`).
**The constructor is the contract** — you forward `model` to
`super().__init__`, there are no magic class attributes:

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
            not_found_message="User not found",
            create_conflict_message="Email already registered",
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

!!! tip "Per-repository error messages"
    The kwargs `not_found_message`, `create_conflict_message`,
    `update_conflict_message`, `bulk_create_conflict_message` and
    `bulk_update_conflict_message` customize the exception text. Without
    them, the SDK generates messages from `Model.__name__` — the whole
    class name, suffix included: `"UserModel not found"`,
    `"Conflict creating UserModel"`.

!!! tip "Per-repository exception classes"
    Every `*_message` has a matching `*_exception`. A message alone gives the
    client nothing to branch on: the default `ConflictException` answers
    `code = "CONFLICT"`, so a duplicate coin pack name is indistinguishable
    from any other 409 — and `error_responses()` cannot document it. Passing a
    domain subclass (one that **declares its own `code` in the class body**)
    makes the 409 identifiable without the repository knowing anything about
    the domain:

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

    Resolution runs most-specific-first — `create_conflict_exception` if given,
    else `conflict_exception`, else `ConflictException` — so a single kwarg
    (`conflict_exception=`) covers every write, or each write can carry its
    own:

    | Kwarg | Covers |
    | --- | --- |
    | `create_conflict_exception` | `add`, `save_with_outbox`, `add_audited` |
    | `update_conflict_exception` | `update`, `update_audited` |
    | `bulk_create_conflict_exception` | `add_all`, `bulk_create_values`, `bulk_upsert` |
    | `bulk_update_conflict_exception` | `update_many`, `bulk_update`, `update_returning` |
    | `conflict_exception` | fallback for all four |

    The class is instantiated as `cls(message=...)`, the same contract
    `not_found_exception` already has, so it must accept a `message` keyword.
    Declaring `code` in the class body and taking `message` optionally
    satisfies both. Every kwarg is optional: omit them and the behavior is
    exactly what it was (the generic `ConflictException`). Available from
    0.169.0.

## Which constraint refused — `parse_integrity_error`

Swapping `ConflictException` for a class of your own answers *there was a
conflict*. Answering *on which field* means reading what the server said — and
what it says has the driver's shape, not your application's.

```python
from typing import Any

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk import parse_integrity_error


def to_response(error: IntegrityError) -> dict[str, Any]:
    """Translate an integrity error into the envelope clients parse."""
    failure = parse_integrity_error(error)
    return {
        "code": failure.kind.value,
        "field": failure.column,
        "constraint": failure.constraint,
    }
```

`IntegrityViolationEnum` classifies into `unique`, `foreign_key`, `not_null`,
`check` and `unknown`. `IntegrityFailure` carries what the server reported:
`constraint`, `table`, `columns` (in the order listed) and `column`, the
shortcut for the single-column case.

!!! warning "An empty field means 'this server did not say'"
    Never 'there is none'. The two dialects speak differently, and the
    differences are not cosmetic:

    | | Postgres | SQLite |
    | --- | --- | --- |
    | unique | names constraint, table and columns | names `table.column`; no constraint |
    | not null | names column and table | names `table.column` |
    | check | names the constraint and the table | returns the **expression** when the constraint is unnamed |
    | foreign key | names constraint, table and column | says only `FOREIGN KEY constraint failed` |

    A measured absence worth repeating: a SQLite foreign key carries nothing
    beyond the kind.

!!! info "On Postgres, the driver's fields come before the text"
    `asyncpg` exposes the server's diagnostics as attributes of the
    exception — `sqlstate`, `detail`, `constraint_name`, `table_name`,
    `column_name` — and `parse_integrity_error` reads those first, falling
    back to the sentence only for what they leave empty. The text is not a
    stable carrier. Measured against Postgres 16 with `asyncpg` 0.31.0:

    | SQLAlchemy | `str(error.orig)` | where the driver exception is |
    | --- | --- | --- |
    | 2.0.52 (the SDK's floor) | first sentence **plus** the `DETAIL:` line | `error.orig.__cause__` |
    | 2.1.1 | first sentence only | `error.orig.orig` (and `__cause__`) |

    Reading only the text, a unique under 2.1.1 came back with
    `columns=()`. The fields are read by attribute name: `asyncpg` is not
    imported, and an installation without `[postgres]` does not need it. The
    `table` of a unique comes from `table_name`; a bare message, without the
    driver exception behind it, still leaves it `None`, because the sentence
    does not say it and splitting the constraint name on a convention would
    be a guess. The `sqlstate` (`23505`, `23503`, `23502`, `23514`) also
    classifies a violation whose sentence the server translated: with
    `lc_messages=de_DE.utf8`, on both SQLAlchemy versions, a unique came back
    `unique` with constraint and table, and with `columns=()`, because the
    `DETAIL` is translated too.

!!! info "Reads `error.orig`, not `str(error)`"
    `str()` on a SQLAlchemy `IntegrityError` appends `[SQL: <statement>]`, and
    a statement contains user data. A value carrying the text
    `UNIQUE constraint failed: x.y` would be parsed as a column name. The
    function reads the DBAPI exception, whose message is the server's sentence
    alone.

!!! check "Never raises"
    An unknown message comes back as `IntegrityViolationEnum.UNKNOWN` with the
    text in `failure.message`. A parser for error prose that raises turns a
    handled 409 into an unhandled 500, which is worse than the generic
    conflict it was added to improve on.

## The CRUD you get

Recall the project's collection convention: **single-record** lookups
raise 404; **collection** lookups return `[]`.

```python
import asyncio
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

id1, id2, id3 = uuid4(), uuid4(), uuid4()
repository = BaseRepository(session, model=UserModel)
user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")
user_or_id = user_id


async def main() -> None:
    """Run this example."""
    # Read — single record (404 when missing)
    user = await repository.get_by_id(user_id)
    user = await repository.get({"email": "a@b.com"})

    # Read — may not exist (None, no 404)
    user = await repository.get_or_none({"email": "a@b.com"})
    first = await repository.first({"is_active": True})

    # Read — collection (always [], never 404)
    users = await repository.list({"is_active": True})

    # Existence / count
    exists = await repository.exists({"email": "a@b.com"})
    total = await repository.count({"is_active": True})

    # "Is this value already used by ANOTHER row?" — uniqueness check on update
    taken = await repository.exists_excluding({"email": "a@b.com"}, exclude_id=user.id)

    # id-or-instance → instance (no scattered if isinstance in services)
    user = await repository.resolve(user_or_id)

    # Write
    created = await repository.add(
        UserModel(name="Ana", email="ana@x.com", password_hash="...")
    )
    updated = await repository.update(user)  # commits mutations on an attached instance

    # Removal
    await repository.delete(user_id)  # hard delete (404 if missing)
    await repository.delete_many({"is_active": False})  # returns count
    await repository.delete_batch([id1, id2, id3])  # by PK, returns count

    # Soft-delete via the is_active flag (no SoftDeleteMixin needed)
    await repository.soft_delete(user_id)  # is_active = False
    await repository.restore(user_id)  # is_active = True


asyncio.run(main())
```

!!! note "`update` expects an attached instance"
    The typical flow is: `get_by_id` → mutate with `update_from_dict` →
    `repository.update(instance)`. Don't build a detached model and pass
    it to `update` — it persists mutations on something already loaded in
    the session.

!!! tip "`resolve` and `exists_excluding` — two helpers you'll reach for constantly"
    **`resolve(id_or_instance)`** settles the old dilemma: your method
    takes `UUID | UserModel` and you don't want to write
    `if isinstance(x, UUID): ... else: ...` in every service. `resolve`
    does it for you — pass a `UUID` and it fetches (404 if missing); pass
    an instance and it returns the same one. One line:

    ```python
    user_model = await self.repository.resolve(user)  # user is UUID OR UserModel
    ```

    **`exists_excluding(filters, exclude_id=...)`** answers "is this
    email/phone/username already someone **else's**?" — exactly what you
    need when **updating** a unique field. Plain `exists` would say `True`
    even for the row itself; `exists_excluding` ignores the id you pass:

    ```python
    if await self.repository.exists_excluding(
        {"phone": new_phone}, exclude_id=user.id
    ):
        raise UserWithPhoneExistsException(phone=new_phone)
    ```

    Pass `exclude_id=None` on create (when there's no row to exclude yet)
    — then it behaves just like `exists`.

**Recap:** instantiate directly for plain CRUD, subclass for queries +
mappers. 404 only on single lookups; collections return `[]`.
`soft_delete` flips the `is_active` flag; `SoftDeleteMixin` (section 6)
adds a `deleted_at` timestamp when you need temporal auditing.

## Eager-loading relationships with `with_`

Touching a relationship (`user.orders`) **after** the async session
closed raises the dreaded `MissingGreenlet` — SQLAlchemy would attempt a
lazy query in a context that can no longer await I/O. The fix is to load
the relationship up front, in the same query. Every read method (`get`,
`get_or_none`, `get_by_id`, `first`, `list`) accepts `with_=`:

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
        # Load the user and its orders in a single round trip
        user = await repository.get_by_id(user_id, with_=["orders"])
        for order in user.orders:      # no lazy load, no MissingGreenlet
            print(order.total)

        # Several relationships + nested (dotted)
        user = await repository.get_by_id(
            user_id,
            with_=["profile", "orders.items"],   # orders → and each order's items
        )

        # Works on collections too
        users = await repository.list({"is_active": True}, with_=["orders"])


asyncio.run(main())
```

Each path uses `selectinload`: N related rows cost **one** extra query
per level (a `SELECT ... IN (...)`), not N — no `JOIN` row
multiplication, and it works for both collections and scalars.

!!! warning "A wrong name fails loudly"
    A `with_` segment that is not a relationship on the model reached at
    that hop raises `ValueError` immediately — not a silent runtime
    error. `with_=["orders.ghost"]` → `ValueError: Order has no
    relationship 'ghost'`.

## Lifecycle signals

When you want to react to a write — bust a cache, enqueue an event, sync
a search index, fire a domain event — without scattering callbacks
across every service, register a **signal**. The repository emits four
moments on the unit-of-work path:

```python
from tempest_fastapi_sdk import RepositorySignalEnum, on_signal
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.db import connect, disconnect

from src.core.settings import settings
from src.db.models import UserModel
from src.services.search import SearchIndex

cache = AsyncRedisManager(settings.REDIS_URL, decode_responses=True)
search_index = SearchIndex()


# Decorator form
@on_signal(UserModel, RepositorySignalEnum.POST_SAVE)
async def index_user(user: UserModel) -> None:
    """Reindex the user in search after the row commits."""
    await search_index.upsert(user.id, user.name)


# Imperative form (same thing)
async def bust_cache(user: UserModel) -> None:
    """Drop the user's cache entry once the row has committed."""
    await cache.client.delete(f"user:{user.id}")

connect(UserModel, RepositorySignalEnum.POST_SAVE, bust_cache)
disconnect(UserModel, RepositorySignalEnum.POST_SAVE, bust_cache)  # remove
```

!!! note "`search_index` is illustrative; `cache` is not"
    `search_index.upsert(...)` is a placeholder from your project (a search
    client) — it is not part of the SDK. Swap in your domain's real object.

    `cache` is a real `AsyncRedisManager`, which is why the call goes through
    `cache.client`: the manager owns the lifecycle, and the Redis commands
    live on the client. Building the handler before the lifespan runs, use
    `cache.client_proxy` — `cache.client` raises `RuntimeError` until
    `connect()` has run.

The four moments:

| Signal | Fires when | Typical use |
|--------|------------|-------------|
| `PRE_SAVE` | before the `INSERT`/`UPDATE` commits | cross-cutting validation; **raising here vetoes the write** (rollback + re-raise) |
| `POST_SAVE` | after commit + refresh | reindex, cache-bust, domain event |
| `PRE_DELETE` | before a single-row delete | clean up external dependencies |
| `POST_DELETE` | after the delete commits | notify that the row is gone |

Handlers may be sync **or** `async` — an awaitable return value is
awaited. Registering on a base model applies to its subclasses (resolved
through the instance's MRO).

!!! danger "Signals cover the unit-of-work path only"
    `add` / `add_all` / `update` / `update_many` / `soft_delete` /
    `restore` / `delete` fire signals. The set-based bulk methods
    (`bulk_update`, `bulk_create_values`, `bulk_upsert`, `delete_many`,
    `delete_batch`) issue a single SQL statement and **bypass** signals
    by design — they never materialize the affected rows.
    `soft_delete`/`restore` fire `PRE_SAVE`/`POST_SAVE` (they are an
    `UPDATE`), not the delete signals.

!!! tip "Test isolation"
    The registry is process-global. In tests, call `clear_signals()`
    (from `tempest_fastapi_sdk.db.signals`) in a fixture teardown so one
    test's handler never leaks into the next.

## `F` and `Q` expressions

For Django refugees: `F` references a column inside the query and `Q`
composes conditions with `OR`/`NOT`. Both plug straight into the
repository.

**`F` — atomic in-database update.** Decrementing stock with
read-modify-write races: two requests read `10`, both write `9`.
`F("stock") - 1` computes in the database, in one statement — no lost
update:

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
        # stock = stock - 1, in the database
        await repository.bulk_update({"id": product_id}, {"stock": F("stock") - 1})

        # arithmetic from either side and between columns
        await repository.bulk_update({"id": pid}, {"stock": 100 - F("stock")})
        await repository.bulk_update({"id": pid}, {"total": F("price") * F("qty")})


asyncio.run(main())
```

**`update_returning` — the decision and the new value in one statement.**
`bulk_update` returns only the count. When the write is conditional and
you need its outcome — debit only if the balance covers it, with the new
balance in hand —, `update_returning` runs the same `UPDATE ... WHERE`
with `RETURNING`. No returned row means the condition did not match;
there is no read first, so there is no window for another request:

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
            print("new balance:", rows[0]["wallet_cents"])
        else:
            print("refused: balance below 500")


asyncio.run(main())
```

`where=` takes a `Q` or a ready SQLAlchemy clause, for what the dict
cannot express — the [wallet](../wallet.md) discounts the held balance with
a correlated subquery there. `RETURNING` needs PostgreSQL, or SQLite
3.35+.

**`Q` — the `OR` / `NOT` the filter dict can't express.** The dict ANDs
everything; `Q` combines with `&` / `|` / `~` and enters via `where=`:

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
        # status open OR pending
        open_ = await repository.list(where=Q(status="open") | Q(status="pending"))

        # active and NOT guest
        active = await repository.list(where=Q(is_active=True) & ~Q(role="guest"))

        # combine with the dict (AND): stock >= 5 AND (open OR closed)
        rows = await repository.list(
            {"stock__gte": 5}, where=Q(status="open") | Q(status="closed")
        )


asyncio.run(main())
```

!!! warning "`~Q(...)` does not return the `NULL` row"
    The negation becomes `role != 'guest'` in SQL, and
    `NULL != 'guest'` is `NULL`, not true: the row whose `role` is null
    **is left out** along with the `guest` one. With three users (`guest`,
    `admin` and `role=None`), `~Q(role="guest")` returns only `admin`. If the
    null row belongs in the result, ask for it explicitly:
    `~Q(role="guest") | Q(role=None)` returns `admin` and the null one
    (`None` on a bare column is `IS NULL`). `role__ne="guest"` has the same
    semantics as `~Q` here — it also drops the null.

`Q` uses the same conventions as the filter dict (`name` ILIKE,
`field__gte`, iterable → `IN`, …), so `Q(priority__gte=5, name="ana")` is the
`AND` of those conditions. `where=` works on `list` / `first` / `get` /
`get_or_none` / `count` / `exists` / `paginate` / `delete_many`.

Available `field__op` suffix operators (in `Q` **and** the dict):

| Suffix | SQL | Example |
|--------|-----|---------|
| `gt` `gte` `lt` `lte` `ne` | comparison | `Q(priority__gte=5)` |
| `in` `notin` `not_in` | `IN` / `NOT IN` (value is any non-string iterable: `list`/`set`/`tuple`/generator, or a scalar, wrapped in a one-item list; `not_in` aliases `notin`) | `Q(status__in={"open", "paid"})` |
| `between` | `col BETWEEN lo AND hi` (value is an ordered pair `(lo, hi)` as a `list`/`tuple`) | `Q(price__between=(10, 20))` |
| `iexact` | case-insensitive equality (`lower(col) == lower(v)`) | `Q(email__iexact="Ana@X.com")` |
| `like` `ilike` | raw `LIKE` / `ILIKE` with the caller's own `%`/`_` wildcards, **not** escaped | `Q(sku__ilike="ab_-%")` |
| `isnull` | `IS NULL` (True) / `IS NOT NULL` (False) | `Q(closed_at__isnull=True)` |
| `contains` `icontains` | `ILIKE %v%` (value escaped) | `Q(name__contains="ana")` |
| `startswith` `endswith` | `ILIKE v%` / `%v` (value escaped) | `Q(sku__startswith="SKU-")` |

!!! warning "`like` case-sensitivity is backend-defined"
    `ilike` is always case-insensitive. Plain `like` follows the database's
    `LIKE` semantics: SQLite folds ASCII case, PostgreSQL does not. For
    **portable** case handling, reach for `ilike` or `iexact`.

!!! note "Raw SQLAlchemy is still there"
    `F`/`Q` are typed sugar over expressions SQLAlchemy already has.
    Need something they don't cover? Use `select(...)` directly — the
    repository doesn't get in the way.
