# Database

This is the layer every Tempest service uses to talk to PostgreSQL
(production) or SQLite (development/tests) over **SQLAlchemy 2.0 async**.
It exists so you never rewrite the same engine, the same per-request
session, the same CRUD and the same pagination in every project.

There are four pieces, and you'll meet them one at a time:

| Piece | Symbol | What for |
| --- | --- | --- |
| Base model | `BaseModel` | The four canonical columns (`id` / `is_active` / `created_at` / `updated_at`) + serialization helpers. |
| Connection | `AsyncDatabaseManager` | Engine, pool, per-request session, `health_check`. |
| Repository | `BaseRepository[Model]` | Async CRUD, convention-based filters, bulk ops, pagination. |
| Migrations | `AlembicHelper` | Alembic bootstrap, autogenerate, CI drift gate. |

Plus three opt-ins that show up when the domain asks for them: the
**mixins** (`SoftDeleteMixin`, `AuditMixin`, `MFAMixin`), **cursor
pagination**, and the **`SlowQueryLogger`**.

!!! tip "How to read this page"
    It's progressive. Start with the model, connect the database, stand up
    a repository, learn the filters, then pagination, migrations and
    observability. Every code block is a complete file — copy, paste, run.
    If you only want the API reference, jump to
    [Reference »](../reference.md).

---

## 1. The base model

Every model in your service inherits from `BaseModel`. You get four
columns without writing any:

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

That already creates the `user` table with **seven** columns: your three
(`name`, `email`, `password_hash`) plus the four inherited ones:

| Column | Type | Default | Role |
| --- | --- | --- | --- |
| `id` | `UUID` (v4) | `uuid4()` | Primary key, portable across Postgres/SQLite/MySQL/MSSQL. |
| `is_active` | `bool` | `True` | Fast soft-delete flag. |
| `created_at` | `datetime` (tz-aware) | `utcnow()` on flush | Creation timestamp. |
| `updated_at` | `datetime` (tz-aware) | `utcnow()` on `onupdate` | Last-write timestamp. |

!!! info "Why is the table named `user` and not `UserModel`?"
    `BaseModel` derives `__tablename__` from the class automatically: it
    strips the `Model` suffix and converts to `snake_case`. `UserModel` →
    `user`, `OrderItemModel` → `order_item`. You can always pin
    `__tablename__ = "users"` explicitly — the explicit declaration wins
    over the automatic one.

### Constraint naming convention

`BaseModel.metadata` ships configured with `NAMING_CONVENTION`. That makes
every PK/FK/index/unique/check get a **deterministic** name —
`ix_user_email`, `uq_user_email`, `fk_order_user_id_user` — identical on
every machine and every engine.

!!! check "The real win is in the migrations"
    Without deterministic names, `alembic revision --autogenerate` invents
    random identifiers and each developer generates a different diff for
    the same schema. With the convention, autogenerate only emits **real
    schema diffs** — no name churn.

### Helpers you get for free

Every `BaseModel` instance gets:

```python
# Serialize to a dict (handy in logs/tests)
data: dict[str, Any] = user.to_dict(exclude=["password_hash"])

# Assign many fields at once, with a whitelist against mass-assignment
user.update_from_dict(
    payload.model_dump(exclude_unset=True),
    allowed_fields={"name", "email"},   # id/role never get written
)
```

`__eq__` and `__hash__` compare by `(type, id)`, so the same row loaded
across different sessions compares equal — handy in tests and `set`s. Rows
not yet persisted (`id is None`) fall back to Python identity.

!!! warning "Always use `allowed_fields` on external payloads"
    `update_from_dict` without `allowed_fields` accepts any mapped column.
    For PATCH bodies coming from the client, pass the whitelist — it's the
    defense against mass-assignment on sensitive columns (`id`, `role`,
    `is_active`).

**Recap:** inherit `BaseModel`, declare only your domain columns, and the
SDK delivers id/timestamps/soft-delete, deterministic constraint names and
serialization helpers.

---

## 2. Connecting to the database

`AsyncDatabaseManager` is instantiated **once** per application and owns
the engine, the pool and the session factory. Put it in the
infrastructure dependencies, not inside `app.py`:

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import AsyncDatabaseManager

from src.core.settings import settings

db = AsyncDatabaseManager(
    settings.DATABASE_URL,
    echo=settings.DEBUG,        # echo SQL to stdout in dev
    pool_size=10,               # ignored for SQLite
    max_overflow=20,
    pool_recycle=3600,
)
```

It detects the backend from the URL (`make_url`), so SQLite gets
`check_same_thread=False` automatically and the pool parameters are
ignored — no substring tricks.

### One session per request

Use `session_dependency` as the FastAPI dependency. It hands out one
session per request and does **not** commit on success — committing is the
repository/service layer's responsibility:

```python
# src/api/dependencies/resources.py (continued)
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

### Lifecycle in the lifespan

Open and close the engine alongside the application:

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

`health_check()` runs a `SELECT 1` and swallows any exception, returning
only `True`/`False` — perfect for `/health`:

```python
@router.get("/health")
async def health() -> dict[str, object]:
    """Liveness + database probe."""
    return {
        "status": "ok",
        "database": await db.health_check(),
        "url": db.db_url_safe,   # credentials masked
    }
```

!!! info "Other ways to get a session"
    - `db.get_session_context()` — a context manager that **commits** on
      success and rolls back on error. Use it in scripts and background
      tasks.
    - `db.get_session()` — a raw session; you close it.
    - `db.create_tables()` / `db.drop_tables()` — tests and local dev
      only; in production the schema is Alembic's.

!!! danger "Never log `db_url`, always `db_url_safe`"
    The raw URL carries user and password. `db_url_safe` renders
    `postgresql+asyncpg://***@host/db`. The raw URL lives on a private
    attribute precisely so it doesn't leak through `repr()` or accidental
    logging.

**Recap:** one `AsyncDatabaseManager` per app, in `resources.py`;
`session_dependency` injects the per-request session; `connect`/`disconnect`
in the lifespan; `health_check` + `db_url_safe` on `/health`.

---

## 3. The repository

`BaseRepository[Model]` is the heart of the layer. It encapsulates async
CRUD, filters, bulk ops and pagination. There are two ways to use it.

### Direct mode — plain CRUD

When you have no custom query, instantiate directly:

```python
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

repository = BaseRepository(session, model=UserModel)
user = await repository.get_by_id(user_id)
```

### Subclass mode — when you have your own queries

Subclass it to add domain queries and the three mappers that translate
ORM ↔ DTO. **The constructor is the contract** — you forward `model` to
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
    them, the SDK generates messages from `Model.__name__` (`"User not
    found"`, `"Conflict creating User"`). Pass `not_found_exception=` to
    raise a richer domain exception than the default `NotFoundException`.

### The CRUD you get

Recall the project's collection convention: **single-record** lookups
raise 404; **collection** lookups return `[]`.

```python
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

# Write
created = await repository.add(
    UserModel(name="Ana", email="ana@x.com", password_hash="...")
)
updated = await repository.update(user)         # commits mutations on an attached instance

# Removal
await repository.delete(user_id)                # hard delete (404 if missing)
await repository.delete_many({"is_active": False})  # returns count
await repository.delete_batch([id1, id2, id3])      # by PK, returns count

# Soft-delete via the is_active flag (no SoftDeleteMixin needed)
await repository.soft_delete(user_id)           # is_active = False
await repository.restore(user_id)               # is_active = True
```

!!! note "`update` expects an attached instance"
    The typical flow is: `get_by_id` → mutate with `update_from_dict` →
    `repository.update(instance)`. Don't build a detached model and pass
    it to `update` — it persists mutations on something already loaded in
    the session.

**Recap:** instantiate directly for plain CRUD, subclass for queries +
mappers. 404 only on single lookups; collections return `[]`.
`soft_delete` flips the `is_active` flag; `SoftDeleteMixin` (section 6)
adds a `deleted_at` timestamp when you need temporal auditing.

---

## 4. Convention-based filters

Every method that takes `filters: dict[str, Any]` goes through the same
engine. A `None` value **always skips** the condition (a missing filter ≠
`WHERE col IS NULL`). The conventions:

| Key / value | Generated SQL | Example |
| --- | --- | --- |
| `name` (str) | case-insensitive `ILIKE %value%` | `{"name": "ana"}` |
| `bool` | `col.is_(value)` | `{"is_active": True}` |
| `list` | `col.in_(values)` | `{"id": [id1, id2]}` |
| `date` | `func.date(col) == value` (whole day) | `{"created_at": today}` |
| `start_in` / `end_in` (date) | range on `date`/`created_at` | `{"start_in": d1, "end_in": d2}` |
| `<col>__<op>` | comparison `gt`/`gte`/`lt`/`lte`/`ne` | `{"updated_at__gt": mark}` |
| any other column | `col == value` | `{"email": "a@b.com"}` |

```python
# "active rows updated after the watermark" — timestamp precision
changed = await repository.list({
    "is_active": True,
    "updated_at__gt": watermark,
})

# "created between two dates" — whole day
report = await repository.list({"start_in": start, "end_in": end})

# text search + membership in a set
hits = await repository.list({"name": "silva", "id": selected_ids})
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

**Recap:** one dict, predictable conventions, `None` skips. Strings on
`name` become ILIKE searches; `__op` suffixes give precise comparisons;
`None` never becomes `IS NULL`.

---

## 5. Bulk operations

For volume, row-by-row ORM is expensive. The repository offers two
families: those that **keep** the unit-of-work (instances refreshed back)
and those that **bypass** it (a single statement, no refresh).

```python
# Keeps the UoW — attached, refreshed instances
created = await repository.add_all([m1, m2, m3])      # several INSERTs, 1 tx
updated = await repository.update_many([u1, u2])      # several UPDATEs, 1 tx

# Bypasses the UoW — one statement, scales better (>= 50 rows)
n = await repository.bulk_create_values([
    {"name": "A", "email": "a@x.com", "password_hash": "..."},
    {"name": "B", "email": "b@x.com", "password_hash": "..."},
])  # INSERT ... VALUES (...), (...) — returns row count

n = await repository.bulk_update(
    filters={"is_active": False},
    values={"is_active": True},
)  # UPDATE ... WHERE — returns affected row count

n = await repository.bulk_upsert(
    rows=[{"sku": "ABC", "price": 10}, {"sku": "DEF", "price": 20}],
    conflict_columns=["sku"],          # requires a UNIQUE index
    update_columns=["price"],          # None = update everything but PK + conflict
)  # INSERT ... ON CONFLICT DO UPDATE — Postgres and SQLite
```

!!! warning "`bulk_update` refuses an empty filter"
    Passing `filters={}` raises `ValueError` — it's the guard against an
    accidental table-wide UPDATE. To genuinely update every row, pass an
    explicit always-true condition.

!!! danger "`bulk_*` does not refresh the session"
    `bulk_create_values`, `bulk_update` and `bulk_upsert` emit a raw
    statement and do **not** refresh or attach instances to the session.
    Use them when you don't need the ORM objects back. If you need the
    instances, use `add_all` / `update_many`.

!!! note "`bulk_upsert` is dialect-specific"
    Postgres and SQLite have native upsert. Other dialects raise
    `NotImplementedError` — fall back to a `SELECT FOR UPDATE` + `UPDATE`
    loop.

**Recap:** `add_all`/`update_many` when you want the instances back;
`bulk_*` when you want throughput. An empty filter on `bulk_update` is a
deliberate error.

---

## 6. Soft-delete and auditing (mixins)

The mixins are **opt-in**: you mix them alongside `BaseModel` only when
the domain asks. `SoftDeleteMixin` adds `deleted_at` (+ `mark_deleted()` /
`mark_restored()` / `is_deleted`). `AuditMixin` adds `created_by` /
`updated_by` (+ `stamp_created_by` / `stamp_updated_by`).

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

Filtering is the caller's responsibility — the mixin does **not** install
a global filter. Hide soft-deleted rows by passing `deleted_at=None`, or
filter in the subclass. Stamping audit columns belongs in the service,
where the current user is in scope:

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

        ``_apply_filters`` skips ``None`` by design (a missing filter !=
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

!!! tip "Two delete stamps, different purposes"
    Use `repository.soft_delete(id)` (the `is_active` flag) when the
    boolean is enough. Use the `SoftDeleteMixin` helpers (`mark_deleted` →
    `deleted_at`) when you need to know **when** the delete happened —
    auditing, retention policies.

!!! info "MFA is another opt-in mixin"
    `MFAMixin` adds `totp_secret` / `totp_enabled_at` to the user model
    when the project turns on the bundled MFA flow. Details in
    [MFA (TOTP / 2FA) »](mfa.md).

**Recap:** mixins enter only when the domain needs them; soft-delete
filtering is yours (`deleted_at IS NULL` via a raw query); the audit stamp
lives in the service.

---

## 7. Pagination

The SDK paginates two ways, **both built into the repository**. You almost
never write the pagination query by hand.

### Offset — when the client wants "page 3 of 12"

```python
# src/db/repositories/user.py — convenience method
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

`BaseRepository.paginate` returns a `dict` with `items` / `total` / `page`
/ `page_size` / `pages`. The total is computed from the **same** filtered
query, so custom joins still report a correct total. When `order_by` is
`None`, it orders by `created_at desc`.

### Cursor — when the table is large

Cursor pagination scales better than offset on large tables (no
`COUNT(*)`, stable under concurrent inserts) at the cost of losing random
access. It's **already built in** as `cursor_paginate` — it orders by
`(order_by, id)` and encodes the opaque cursor automatically:

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

!!! tip "For offline-first sync there's a third mode"
    `changes_since` + `SyncPaginationSchema` do delta pagination (rows
    changed since a watermark). See [Offline sync »](offline-sync.md).

**Recap:** `paginate` (offset) for page navigation; `cursor_paginate` for
feeds/large tables. Both ready — you only map the result to the response
schema.

---

## 8. Alembic migrations

`AlembicHelper` wraps Alembic with a curated config (UTC timezone,
date-prefixed files, `target_metadata` already wired, batch mode). Full
flow: bootstrap → revision → apply → CI gate.

### Bootstrap, once per project

```python
# scripts/alembic_init.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper(config_path="alembic.ini", db_url=settings.DATABASE_URL)
helper.init(
    directory="alembic",
    metadata_module="src.db.models",   # exposes BaseModel
    metadata_attr="BaseModel",
    db_url=settings.DATABASE_URL,
)
```

```bash
uv run python scripts/alembic_init.py
```

Creates:

```text
alembic.ini                 # SDK-curated config (UTC, date prefix, post-write hooks)
alembic/
├── env.py                  # SDK template (target_metadata, compare_type, batch)
├── script.py.mako
└── versions/
```

### Generate revisions

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

The file lands in
`alembic/versions/2026_05_16_1432-ae12cd34_add_users_table.py` — the date
prefix orders files chronologically and makes merge conflicts obvious.

!!! check "Migrations come out lint-clean"
    The `alembic.ini` that `init()` writes includes `[post_write_hooks]`
    that runs `ruff check --fix` then `ruff format` on every revision.
    Without it, Alembic's files fail `tempest lint` (`W291` on the empty
    `Revises:`, `E501` on long `sa.Column(...)` lines). The hooks use
    **your** project's `ruff` config. Requires `ruff` on `PATH` — already
    a dev dependency in every `tempest new` scaffold.

### Apply on startup

```python
# src/api/app.py — inside the lifespan
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

!!! warning "Destructive migrations: use `safe_upgrade`"
    `helper.pending_destructive_ops()` lists pending column/table DROPs;
    `helper.safe_upgrade()` raises `DestructiveMigrationError` instead of
    silently dropping data. The full deploy guide (migration + graceful
    shutdown) is in [Safe deploys »](deploy-safety.md).

### CI gate — the schema must match the models

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

!!! info "Base columns always first"
    The SDK's `env.py` installs the `reorder_base_columns_first` hook, so
    every generated migration lists `id` / `is_active` / `created_at` /
    `updated_at` ahead of your columns — consistent diffs across people.

**Recap:** `init` once, `revision --autogenerate` per change, `upgrade` on
startup, `check` in CI, `safe_upgrade` to protect data.

---

## 9. Detecting slow queries

`SlowQueryLogger` registers an engine listener and emits a log line for
every statement above a threshold. Attach it once at boot:

```python
# src/api/app.py — after db.connect()
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

| Parameter | Default | What for |
| --- | --- | --- |
| `threshold_ms` | `500.0` | Statements at or above this duration are logged. |
| `level` | `logging.WARNING` | Level of the slow-query lines. |
| `log_parameters` | `False` | Includes bind params in the line. **Dev only** — they may carry PII. |
| `explain` | `False` | Runs `EXPLAIN` and appends the plan. Costs a round-trip per slow query. |

!!! danger "`log_parameters=True` in development only"
    Bind parameters may contain secrets and PII. Keep it `False` in
    production — the default is already safe.

**Recap:** `SlowQueryLogger(db.engine, threshold_ms=...).attach()` in the
lifespan turns slow queries into actionable log lines, with optional
`EXPLAIN` to investigate plans.

---

## Next steps

This page covered the core. The advanced database features have dedicated
recipes:

- [Multi-tenant »](multi-tenant.md) — `TenantScopedRepository` for
  per-tenant isolation.
- [Audit trail »](audit-trail.md) — `BaseAuditLogModel`, `add_audited` /
  `update_audited` / `delete_audited` (who changed what, in the same tx).
- [Transactional outbox »](outbox.md) — `BaseOutboxModel` + `OutboxRelay`,
  `save_with_outbox` to publish events atomically with the write.
- [Offline sync »](offline-sync.md) — `changes_since` + delta pagination
  for offline-first clients.
- [Safe deploys »](deploy-safety.md) — destructive migrations + graceful
  shutdown.
- [Testing »](testing.md) — in-memory SQLite, fixtures, `create_tables`.
