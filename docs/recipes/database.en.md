# Database

This is the layer every Tempest service uses to talk to PostgreSQL
(production) or SQLite (development/tests) over **SQLAlchemy 2.0 async**.
It exists so you never rewrite the same engine, the same per-request
session, the same CRUD and the same pagination in every project.

!!! info "Installation"
    The database core ships with `tempest-fastapi-sdk`. The async drivers
    come via extras — `uv add "tempest-fastapi-sdk[postgres]"` (PostgreSQL,
    pulls in `asyncpg`) or `[sqlite]` (SQLite in dev, pulls in `aiosqlite`).

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

!!! tip "Pinning the name is not just taste"
    `USER` is a reserved word in standard SQL. SQLAlchemy always quotes
    the identifier, so your application works — but a `SELECT * FROM
    user` typed by hand in `psql` returns the **database user**, not your
    table, and raises nothing. The plural (`users`) sidesteps that, and
    it is the convention the SDK itself assumes for the token tables
    (`user_tokens`, `user_refresh_tokens`).

### Centralizing table names

A table name almost never appears just once. It is in `__tablename__`
**and** it comes back as a string in every `ForeignKey` pointing at it:

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

Renaming `users` now depends on you remembering every place the string
shows up. And missing a FK does not blow up right away: SQLAlchemy only
resolves the target when it configures the mappers, so the error lands at
**application startup** — or, worse, in a migration pointing at a table
that no longer exists.

The fix is a module that holds nothing but names:

```python
# src/db/configs/names.py
"""Table names for this project. Single source of truth."""

USER_TABLE_NAME = "users"
USER_TOKEN_TABLE_NAME = "user_tokens"
USER_REFRESH_TOKEN_TABLE_NAME = "user_refresh_tokens"
ORDER_TABLE_NAME = "orders"
ORDER_ITEM_TABLE_NAME = "order_items"
```

The `_TABLE_NAME` suffix keeps the constant self-explanatory at the call
site, far from this file: `ForeignKey(f"{USER_TABLE_NAME}.id")` says what
that string is on its own. The prefix follows the model, in the singular
(`UserTokenModel` → `USER_TOKEN_TABLE_NAME`), even when the value is
plural.

Every model imports from there, on both sides of the relationship:

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

The payoff shows up when you change something. Renaming a table becomes
**one line** in `names.py`. Your editor's "find usages" locates everyone
who depends on it, because it is now a symbol instead of a loose string.
And no `ForeignKey` can point at a table that no longer exists without
the import breaking first.

!!! tip "Why `db/configs/` and not `core/constants.py`?"
    A table name is a database detail, and `db/models/` is what consumes
    it. Keeping it under `db/` keeps the dependency inside its own layer,
    and the module ends up importing nothing from the project — it is
    only strings. That is what guarantees it never joins an import cycle:
    `models` imports `configs`, and `configs` imports nobody.

!!! check "It applies to the SDK's tables too"
    The abstract models the SDK ships (`BaseUserModel`,
    `BaseUserTokenModel`, `BaseUserRefreshTokenModel`,
    `BaseWebPushSubscriptionModel`, `BaseOutboxModel`) deliberately leave
    `__tablename__` and the FK for the concrete project to declare —
    precisely so both can come out of your `names.py`, under your naming
    convention.

### Explicit `__tablename__` with Pyright

`BaseModel` declares `__tablename__` as a `@declared_attr.directive`,
SQLAlchemy 2.0's mechanism for deriving the name from the class. mypy
understands a subclass overriding that with a string and **does not
complain** — it is the checker `tempest type` runs, so the default gate
stays clean.

Pyright is stricter: it reads the inherited attribute as a mutable
variable with an invariant type and flags the assignment.

!!! warning "`reportIncompatibleVariableOverride` in Pyright/Pylance"
    ```text
    Type "Literal['users']" is not assignable to declared type
    "_declared_directive[str]" (reportAssignmentType)
    ```

    This is not a defect in your model: the assignment works at runtime,
    and it is the form used throughout these docs. It is Pyright being
    stricter than mypy about overriding a descriptor.

If your editor runs Pyright and you want the file clean, declare the name
through the same mechanism the base class uses:

```python hl_lines="7 8"
from pydantic import BaseModel
from sqlalchemy.orm import declared_attr

from src.db.configs.names import USER_TABLE_NAME


class UserModel(BaseModel):
    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """Pin the table name."""
        return USER_TABLE_NAME
```

More verbose, and equivalent at runtime. Pick by the project's checker:
with mypy (or no static checking in the editor), prefer
`__tablename__ = USER_TABLE_NAME`, which reads more directly.

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

from typing import Any

from src.db.models import UserModel
from src.schemas import UserUpdateSchema

payload = UserUpdateSchema(name="Ana Paula")
user = UserModel(name="Ana", email="ana@example.com")


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

### Lifecycle in the lifespan

Open and close the engine alongside the application:

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

`health_check()` runs a `SELECT 1` and swallows any exception, returning
only `True`/`False` — perfect for `/health`:

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

### Outside a request

Not every consumer has a request to hang `Depends` off. An agent tool, a TaskIQ
task, a FastStream consumer and a maintenance script all run outside the HTTP
cycle — and they all use `get_session_context()`, which opens the session,
**commits** on exit and rolls back on error:

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

The rule is to open as late as possible and close as early as possible: a
process holding the session while it waits on something else — a model
generating tokens, an external API replying — occupies a pool connection
without using it.

!!! tip "An agent tool is the trickiest case"
    An agent run spans several steps and can take minutes.
    [AI agents (database) »](agents-db.md) shows why the session is opened
    **inside** each tool, what the automatic commit means for a tool that
    writes, and why two `AsyncDatabaseManager` instances in one process are two
    pools.

### SQLite with a worker: WAL and the busy timeout

The day the application grows a worker, the development SQLite has
**two processes** writing one file. In the default rollback journal
(`delete`) a reader and a writer exclude each other, so the second one
dies:

```text
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
[SQL: INSERT INTO budget_drafts (...) VALUES (?, ?, ...)]
```

Measured across two processes — one holding a read transaction open
while the other inserts:

| `journal_mode` | What happens to the writer |
| --- | --- |
| `delete` | waits out the whole `busy_timeout`, then `database is locked` |
| `wal` | commits immediately |

So `AsyncDatabaseManager` opens every SQLite file in **WAL**, with a
30-second `busy_timeout`. You don't have to ask:

```python
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
# journal_mode = wal, busy_timeout = 30000 (ms)
```

Both are tunable, and ignored on every other backend:

```python
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager(
    "sqlite+aiosqlite:///./app.db",
    sqlite_wal=False,           # filesystems without working shared memory
    sqlite_busy_timeout=5.0,    # seconds
)
```

From the environment, through `DatabaseSettings`: `DATABASE_SQLITE_WAL`
and `DATABASE_SQLITE_BUSY_TIMEOUT`.

!!! info "WAL is a property of the file"
    Turning it on once is enough: the mode outlives the process and every
    later connection, from any process, opens the file already in WAL. On
    a `:memory:` database the pragma is inert — SQLite answers `memory`
    and carries on.

!!! info "`:memory:` gets a real connection per session (v0.252.0)"
    `sqlite+aiosqlite:///:memory:` makes SQLAlchemy pick `StaticPool`:
    **one** DBAPI connection shared by every session. Together with the
    explicit `BEGIN` the manager has emitted since v0.200.0 — needed so
    `RELEASE SAVEPOINT` stops committing on SQLite — that broke any pair of
    overlapping sessions with `cannot start a transaction within a
    transaction`. It hits the test pattern this SDK recommends, and it hits
    an endpoint that answers and finishes its work in a `BackgroundTasks`.

    The manager now rewrites the URL to a **shared-cache** in-memory
    database (`file:<name>?mode=memory&cache=shared&uri=true`), with a normal
    pool, and holds one connection open for as long as the manager lives — a
    shared-cache in-memory database is destroyed when the last connection
    closes. Each manager gets its own name, so two managers stay isolated.

    Measured on both properties: an overlapping session works **and** a
    nested block that exits cleanly is still not durable after an outer
    rollback. Dropping the `BEGIN` — the obvious way out — buys the first and
    loses the second.

    Need the old topology? Pass `poolclass=StaticPool` explicitly: a pool the
    caller names is never overridden.

!!! warning "What waiting does not fix"
    WAL admits **one writer at a time**; the others wait out the
    `busy_timeout`. What no timeout fixes is a transaction that **reads
    first and writes later**: promoting the lock fails at once if another
    connection wrote in between, and `busy_timeout` does not apply
    because there is nothing to wait for. For long work: claim the row,
    do the work with **no session open**, and only then persist.

**Recap:** one `AsyncDatabaseManager` per app, in `resources.py`;
`session_dependency` injects the per-request session; `connect`/`disconnect`
in the lifespan; `health_check` + `db_url_safe` on `/health`; on SQLite,
WAL and the busy timeout ship on so web and worker can share the file.

---

## 3. The repository

`BaseRepository[Model]` is the heart of the layer. It encapsulates async
CRUD, filters, bulk ops and pagination. There are two ways to use it.

### Direct mode — plain CRUD

When you have no custom query, instantiate directly:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

session = None  # provided by db.get_session_context() in your code
user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


repository = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    user = await repository.get_by_id(user_id)


asyncio.run(main())
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
    found"`, `"Conflict creating User"`).

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
    | `bulk_update_conflict_exception` | `update_many`, `bulk_update` |
    | `conflict_exception` | fallback for all four |

    The class is instantiated as `cls(message=...)`, the same contract
    `not_found_exception` already has, so it must accept a `message` keyword.
    Declaring `code` in the class body and taking `message` optionally
    satisfies both. Every kwarg is optional: omit them and the behavior is
    exactly what it was (the generic `ConflictException`). Available from
    0.169.0.

### The CRUD you get

Recall the project's collection convention: **single-record** lookups
raise 404; **collection** lookups return `[]`.

```python
import asyncio
from uuid import UUID, uuid4

from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

id1, id2, id3 = uuid4(), uuid4(), uuid4()
repository = BaseRepository(session, model=UserModel)
user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")
user_or_id = user_id
session = None  # provided by db.get_session_context() in your code


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
    taken = await repository.exists_excluding(
        {"email": "a@b.com"}, exclude_id=user.id
    )

    # id-or-instance → instance (no scattered if isinstance in services)
    user = await repository.resolve(user_or_id)

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

### Eager-loading relationships with `with_`

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

### Lifecycle signals

When you want to react to a write — bust a cache, enqueue an event, sync
a search index, fire a domain event — without scattering callbacks
across every service, register a **signal**. The repository emits four
moments on the unit-of-work path:

```python
from tempest_fastapi_sdk import RepositorySignal, on_signal
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.db import connect, disconnect

from src.core.settings import settings
from src.db.models import UserModel
from src.services.search import SearchIndex

cache = AsyncRedisManager(settings.REDIS_URL, decode_responses=True)
search_index = SearchIndex()


# Decorator form
@on_signal(UserModel, RepositorySignal.POST_SAVE)
async def index_user(user: UserModel) -> None:
    """Reindex the user in search after the row commits."""
    await search_index.upsert(user.id, user.name)


# Imperative form (same thing)
async def bust_cache(user: UserModel) -> None:
    """Drop the user's cache entry once the row has committed."""
    await cache.client.delete(f"user:{user.id}")

connect(UserModel, RepositorySignal.POST_SAVE, bust_cache)
disconnect(UserModel, RepositorySignal.POST_SAVE, bust_cache)  # remove
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

### `F` and `Q` expressions

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
    pid = product_id
    product_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # stock = stock - 1, in the database
        await repository.bulk_update({"id": product_id}, {"stock": F("stock") - 1})

        # arithmetic from either side and between columns
        await repository.bulk_update({"id": pid}, {"stock": 100 - F("stock")})
        await repository.bulk_update({"id": pid}, {"total": F("price") * F("qty")})


    asyncio.run(main())
```

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

`Q` uses the same conventions as the filter dict (`name` ILIKE,
`field__gte`, iterable → `IN`, …), so `Q(priority__gte=5, name="ana")` is the
`AND` of those conditions. `where=` works on `list` / `first` / `get` /
`get_or_none` / `count` / `exists` / `paginate` / `delete_many`.

Available `field__op` suffix operators (in `Q` **and** the dict):

| Suffix | SQL | Example |
|--------|-----|---------|
| `gt` `gte` `lt` `lte` `ne` | comparison | `Q(priority__gte=5)` |
| `in` `notin` `not_in` | `IN` / `NOT IN` (value is any non-string iterable: `list`/`set`/`tuple`/generator; `not_in` aliases `notin`) | `Q(status__in={"open", "paid"})` |
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

---

## 4. Convention-based filters

Every method that takes `filters: dict[str, Any]` goes through the same
engine. A `None` value **always skips** the condition (a missing filter ≠
`WHERE col IS NULL`). The conventions:

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

### Every paginated listing inherits the operators

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

from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel
from src.schemas import ProductFilterSchema

f = ProductFilterSchema(name="silva", page=1, page_size=20)
repo = BaseRepository(session, model=UserModel)
session = None  # provided by db.get_session_context() in your code


async def main() -> None:
    """Run this example."""
    # In the service/repo the whole schema becomes filters + pagination:
    data = await repo.paginate(
        filters=f.get_conditions(),          # name/category_id__in/price__between/…
        **f.get_pagination_conditions(),     # page/page_size/order_by/ascending
    )


asyncio.run(main())
```

The frontend calls `?category_id__in=1&category_id__in=2&price__between=10&price__between=20`
and FastAPI builds the schema via `Depends()`. A `None` drops out (absent
filter), so the client sends only the fields it wants.

**Recap:** one dict, predictable conventions, `None` skips. Strings on
`name` become ILIKE searches; `__op` suffixes give precise comparisons;
`None` never becomes `IS NULL`. Every paginated listing inherits these
operators just by declaring the field.

---

## 5. Bulk operations

For volume, row-by-row ORM is expensive. The repository offers two
families: those that **keep** the unit-of-work (instances refreshed back)
and those that **bypass** it (a single statement, no refresh).

```python
import asyncio

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    m1, m2, m3 = (UserModel(name=n, email=f"{n}@x.com") for n in "abc")
    u1, u2 = created[0], created[1]
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
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


    asyncio.run(main())
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

### Locale — the user's preferred language

`LocaleColumnMixin` adds a `locale` column (BCP-47, e.g. `"pt-BR"`,
`"en-US"`, nullable) so a model can carry the language its notifications and
localized text should render in — without every project re-declaring the
same column. Mix it in like any other mixin:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, LocaleColumnMixin


class UserModel(BaseModel, LocaleColumnMixin):
    """Users — carry a notification locale."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
```

To write the value, use the `Locale` enum (a curated list of BCP-47 tags)
instead of typing the string by hand — each member **is** the tag itself, so
it compares and stores as that tag:

```python
from tempest_fastapi_sdk import Locale

from src.db.models import UserModel

user = UserModel(name="Ana", email="ana@example.com")


user.locale = Locale.PT_BR          # stores "pt-BR"
user.locale = "en-US"               # a raw string works too
assert Locale.PT_BR == "pt-BR"      # a member is a str
```

A `NULL` `locale` means "no preference": resolve it to your app's default
when rendering (typically via [`MessageCatalog`](../reference.md)), **not**
as an error. This is exactly the pair the [Web Push recipe »](webpush.md)
uses to localize each notification's `title`/`body` by the recipient's
`locale`.

!!! note "`Locale` is curated, not exhaustive"
    The enum covers the most widely used locales (pt/en/es/fr/de/… plus
    regional variants). Need a tag outside the list? The column is a `str`,
    so store the raw string and propose the new member upstream once it
    becomes common.

**Recap:** mixins enter only when the domain needs them; soft-delete
filtering is yours (`deleted_at IS NULL` via a raw query); the audit stamp
lives in the service; the user's `locale` comes from `LocaleColumnMixin` +
the `Locale` enum.

---

## 7. Pagination

The SDK paginates two ways, **both built into the repository**. You almost
never write the pagination query by hand.

### Offset — when the client wants "page 3 of 12"

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
    `ValidationException` (**422**) when it is not a mapped column — including
    an attribute that exists on the class but is not a column, like `metadata`.
    Before that, an unknown name became an `AttributeError`, i.e. a **500** on a
    request that was merely invalid.

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

### Cursor — when the table is large

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

!!! check "A new `NOT NULL` column no longer explodes (v0.67.0)"
    Adding a `NOT NULL` column to a table that **already has rows** blows
    up on PostgreSQL with `NotNullViolationError: column "x" contains null
    values` — because a Python-side `default=` only fires on ORM inserts,
    never as DDL. The SDK now installs a second hook,
    `backfill_non_nullable_defaults`: every added column that is
    `nullable=False`, has **no** `server_default`, but **does** declare a
    scalar Python `default` gets a `server_default` derived from that
    default — so the generated migration backfills existing rows in the
    same statement.

    ```python
    # In the model — just the Python default:
    is_professional: Mapped[bool] = mapped_column(default=False)
    ```

    ```python
    # The generated migration now reads (note server_default):
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

    Covers `bool` / `int` / `float` / `str` / `Enum` (uses `.value`). It
    does **not** act when the default is a callable (`uuid4`,
    `func.now()`) or absent — those need a hand-written data migration,
    since the SDK cannot infer a safe backfill value.

    Already have an old `env.py`? Update the import + wiring to compose
    both hooks:

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

    # ...and pass it to context.configure(process_revision_directives=...)
    ```

    For a migration that **already** exploded, add
    `server_default=sa.text("...")` by hand to the `op.add_column` (or
    backfill + `alter_column` to drop the default afterwards).

**Recap:** `init` once, `revision --autogenerate` per change, `upgrade` on
startup, `check` in CI, `safe_upgrade` to protect data.

---

## 9. Detecting slow queries

`SlowQueryLogger` registers an engine listener and emits a log line for
every statement above a threshold. Attach it once at boot:

```python
# src/api/app.py — after db.connect()

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

## Recap

- `BaseModel` brings `id`, `is_active`, `created_at` and `updated_at`; you
  declare only your domain columns, and the Alembic hook keeps that order in
  generated migrations.
- One `AsyncDatabaseManager` per application, in `resources.py` — not one per
  request.
- `BaseRepository` works instantiated for plain CRUD and subclassed once real
  queries appear; a filter is a dict with predictable conventions, and `None`
  skips instead of turning into an accidental `IS NULL`.
- Bulk work comes in two families: the one that hands the instances back
  (`add_all`, `update_many`) and the one that does not, but is a single trip to
  the database.
- A mixin joins when the domain asks for it: soft-delete and auditing cost a
  column and an implicit filter.
- Pagination has two shapes with different purposes: `paginate` to walk pages,
  `cursor_paginate` for a list that grows while the user reads it.
- Migrations are `init` once, `revision --autogenerate` per change, `upgrade` on
  deploy — and `SlowQueryLogger` on the engine shows the slow query with
  `EXPLAIN` before a user complains.

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
