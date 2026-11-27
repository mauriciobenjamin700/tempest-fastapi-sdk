# 2. Connect to the database

Use to open the engine and sessions with `AsyncDatabaseManager`: per-request session, lifespan, health check, use outside a request and SQLite (WAL, foreign keys).

Part of the [Database](../database.md) recipe.

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

## One session per request

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

## A manager built on demand — `session_dependency_for`

`Depends(db.session_dependency)` needs `db` to exist when the module is
imported. Some services prefer to build the manager on first use: to read
settings **after** the boot guard, and so tests can swap `DATABASE_URL`
before any engine exists. In that case, hand the **factory** to
`session_dependency_for`:

```python
# src/api/dependencies/resources.py
import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import AsyncDatabaseManager, session_dependency_for


@lru_cache
def get_db() -> AsyncDatabaseManager:
    """Build the manager on first use, never at import."""
    return AsyncDatabaseManager(os.environ["DATABASE_URL"])


get_session = session_dependency_for(get_db)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
```

The returned dependency has the same semantics as `session_dependency`: one
session per request, **no** commit on success, and the session closed when
the request ends — also when the endpoint raises. `get_db` is called on each
request, never at import; with `@lru_cache`, every request gets the same
manager.

!!! tip "Build the dependency once, at module level"
    FastAPI caches a dependency per request by the callable's **identity**.
    Two calls to `session_dependency_for(get_db)` return two different
    functions — and, used in the same request, two sessions. Keep the result
    under a name (`get_session`) and reuse it.

## Lifecycle in the lifespan

Open and close the engine alongside the application, and bring the schema
to the migration head **before** the first request is served:

```python
# src/api/app.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.configs import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Migrate the schema, open the database, dispose it on shutdown."""
    await run_migrations(settings.DATABASE_URL)
    await db.connect()
    yield
    await db.disconnect()
```

`run_migrations` is the one `tempest new` generates in
`src/db/configs/migrations.py`: a single call to
`AlembicHelper.sync_schema_async()` with the absolute path of
`alembic.ini`.

```python
# src/db/configs/migrations.py
from pathlib import Path

from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcomeEnum

ALEMBIC_INI_PATH: Path = Path(__file__).resolve().parents[3] / "alembic.ini"


async def run_migrations(database_url: str) -> SchemaSyncOutcomeEnum:
    """Bring the database schema to the Alembic head."""
    return await AlembicHelper(
        str(ALEMBIC_INI_PATH),
        db_url=database_url,
    ).sync_schema_async()
```

The scaffold also wraps the call in
`if settings.DATABASE_MIGRATE_ON_STARTUP:` — a field of the generated
`Settings`, not of an SDK mixin — so it can be switched off when several
replicas start together and a one-off job runs `tempest db upgrade`.

`alembic.ini` and `alembic/env.py` come from `AlembicHelper.init(...)` (or
`tempest db init`), not written by hand — see
[Bootstrap, once per project](migrations.md#bootstrap-once-per-project). And
`sync_schema_async()` only creates tables from a revision: with no revision
in `alembic/versions/`, it returns `SchemaSyncOutcomeEnum.NO_MIGRATIONS` and
leaves the database alone. Generate the initial revision
(`tempest db revision -m "initial"`) before the first boot.

!!! danger "No `create_tables()` in the lifespan"
    It is the shape that looks equivalent, and is not:

    ```python
    # src/api/app.py — wrong: the schema stops evolving on the second boot.
    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from src.api.dependencies.resources import db


    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Create the tables on every boot."""
        await db.connect()
        await db.create_tables()
        yield
        await db.disconnect()
    ```

    The first boot creates everything. On the next one, with a new column
    on the model, `create_tables()` neither adds the column, nor fails, nor
    warns — measured on SQLite: the `notes` table still holds
    `['title', 'id', 'is_active', 'created_at', 'updated_at']` after the
    model gained `body`. The error only shows up on the first query that
    reads the column. `create_tables()` belongs in a test fixture over
    in-memory SQLite, where the database is born and dies inside the
    process — see
    [Migrations »](../migrations.md#where-create_tables-is-legitimate).

## Health check

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
    - `db.get_session_context()` / `db.transaction()` — the same context
      manager under two names: it **commits** on exit and rolls back on
      error. Use it in scripts and background tasks.
    - `db.get_session()` — a raw session; you close it.
    - `db.create_tables()` / `db.drop_tables()` — tests and local dev
      only; in production the schema is Alembic's. Without an argument,
      only the models of the SDK's `BaseModel` — see
      [Your own `DeclarativeBase`](#your-own-declarativebase).

!!! warning "`get_session_context()` commits — never in a request dependency"
    The code after a dependency's `yield` runs **after** the response went
    out. Measured on FastAPI 0.141.1, with an ASGI middleware recording the
    messages: `http.response.start` and `http.response.body` are sent before
    the `COMMIT`. A dependency wrapping `get_session_context()` answers `204`
    to a `DELETE` whose commit has not happened yet — the client's next `GET`
    may still see the row — and, if that commit fails, the status the client
    already received can no longer change. For requests use
    `session_dependency` (or `session_dependency_for`) and commit in the
    service/repository layer.

!!! danger "`create_tables()` is a silent no-op on an existing table"
    `create_all` is `CREATE TABLE IF NOT EXISTS`: against a table that already
    exists it **adds no column at all**, does not fail and does not warn. Using
    it at boot and stamping `head` afterwards leaves an old schema with Alembic
    declaring itself up to date — the failure surfaces weeks later, far from the
    cause. The correct bootstrap, for all three states a database can be in, is
    in [Migrations »](../migrations.en.md).

!!! danger "Never log the raw URL, always `db_url_safe`"
    The raw URL carries user and password. `db_url_safe` replaces **only
    the password**: `postgresql+asyncpg://user:pass@host:5432/db` comes out
    as `postgresql+asyncpg://user:***@host:5432/db` — user, host, port,
    database and query string stay visible, so treat the result as internal
    diagnostics if the database user is itself sensitive. The raw URL lives
    on a private attribute precisely so it doesn't leak through `repr()` or
    accidental logging.

## Your own `DeclarativeBase`

`create_tables()` without an argument creates only what inherits the SDK's
`BaseModel`. A service with its own `DeclarativeBase` — because it addresses
rows by a natural key, say, and `BaseModel` imposes a UUID and timestamps —
gets no table at all: the first query fails with `no such table` (SQLite).
Pass your base's `metadata`:

```python
import asyncio

from sqlalchemy import Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from tempest_fastapi_sdk import AsyncDatabaseManager


class Base(DeclarativeBase):
    """A base of your own: natural key, no BaseModel UUID or timestamps."""


class ObjectModel(Base):
    __tablename__ = "objects"

    bucket: Mapped[str] = mapped_column(String(63), primary_key=True)
    key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables(Base.metadata)
    async with db.transaction() as session:
        session.add(ObjectModel(bucket="photos", key="cat.jpg", size=2048))
    async with db.transaction() as session:
        rows = (await session.scalars(select(ObjectModel))).all()
        print([(row.bucket, row.key, row.size) for row in rows])
    await db.disconnect()


asyncio.run(main())
```

Output: `[('photos', 'cat.jpg', 2048)]`. `drop_tables(Base.metadata)` goes
the other way. Both remain tools for tests and local dev.

## Outside a request

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
    [AI agents (database) »](../agents-db.md) shows why the session is opened
    **inside** each tool, what the automatic commit means for a tool that
    writes, and why two `AsyncDatabaseManager` instances in one process are two
    pools.

## SQLite with a worker: WAL and the busy timeout

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

!!! info "A refused `COMMIT` no longer leaves the pooled connection dirty"
    Without WAL, a reader open on another connection makes `COMMIT` fail
    with `database is locked`. The transaction stays open on the driver,
    and SQLAlchemy does not close it: the connection went back to the pool
    inside its `BEGIN`, and its next use failed with `cannot start a
    transaction within a transaction`. `enable_sqlite_savepoints` — which
    the manager and `create_test_engine` apply — rolls back on check-in
    whenever the driver is still in a transaction. The refused `COMMIT`
    still raises `OperationalError`; what changes is that the next
    connection comes out clean, and the refused write does not become
    durable. An engine you build by hand must call
    `enable_sqlite_savepoints` to get the same reset.

## SQLite with foreign keys: `PRAGMA foreign_keys`

SQLite parses the `REFERENCES` clause but **only checks** the foreign key on
a connection that ran `PRAGMA foreign_keys=ON` — off is its default, and the
pragma lives on the connection, not on the file. Left off, three things pass
on SQLite that fail (or do something else) on PostgreSQL:

| Operation | FK off | FK on |
| --- | --- | --- |
| insert a child pointing at a missing parent | accepted | `IntegrityError` |
| delete the parent with `ON DELETE CASCADE` | child left orphaned | child deleted |
| `add_all([child, parent])` with no `relationship()` ordering the flush | accepted | `IntegrityError` |

Measured on SQLAlchemy 2.0.52 and 2.1.3, aiosqlite 0.22.1, SQLite 3.47.1.
So `AsyncDatabaseManager` turns the pragma on for every SQLite engine — file
and `:memory:` alike —, the same way it does WAL:

```python
import asyncio

from sqlalchemy import ForeignKey, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager


class Base(DeclarativeBase):
    """Declarative base for this example only."""


class Org(Base):
    """Organization: the parent row."""

    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(primary_key=True)


class Member(Base):
    """Member: deleted along with its organization."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))


async def main() -> None:
    """Show the orphan refused and the cascade applied."""
    db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables(Base.metadata)

    async with await db.get_session() as session:
        session.add(Member(id=1, org_id=999))
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            print("orphan:", exc.orig)

        session.add(Org(id=1))
        await session.flush()
        session.add(Member(id=2, org_id=1))
        await session.commit()

        org: Org | None = await session.get(Org, 1)
        await session.delete(org)
        await session.commit()
        left: int = (
            await session.execute(select(func.count()).select_from(Member))
        ).scalar_one()
        print("members after deleting the organization:", left)

    await db.disconnect()


asyncio.run(main())
```

```text
orphan: FOREIGN KEY constraint failed
members after deleting the organization: 0
```

!!! tip "Out-of-order INSERT"
    With no `relationship()` between the two models, SQLAlchemy's unit of
    work does not know the member depends on the organization and sends the
    INSERTs in `add_all` order. Declare the `relationship()`, or `flush()`
    after the parent, as the example does.

To turn it off — a legacy database with orphan rows you cannot clean up yet:

```python
from tempest_fastapi_sdk import AsyncDatabaseManager

db: AsyncDatabaseManager = AsyncDatabaseManager(
    "sqlite+aiosqlite:///./legacy.db",
    sqlite_foreign_keys=False,
)
```

From the environment, through `DatabaseSettings`:
`DATABASE_SQLITE_FOREIGN_KEYS=false`. In tests,
`create_test_engine(foreign_keys=False)` — see
[Testing](../testing.md#the-test-engine-checks-foreign-keys).

!!! info "Why the pragma runs on `connect`"
    SQLite ignores `PRAGMA foreign_keys` inside a transaction, without an
    error. Since the manager emits an explicit `BEGIN` (that is what stops
    `RELEASE SAVEPOINT` from committing), a pragma issued through the session
    would never take effect: measured, it keeps answering `0`. The SDK
    registers a listener on the engine's `connect` event that runs the pragma
    through the driver cursor before any transaction. An engine you build by
    hand gets the same behaviour from `enable_sqlite_foreign_keys(engine)`.

!!! warning "Migrations do not run with FK on"
    Alembic's batch mode rebuilds the table on SQLite, and with FK on the
    `DROP TABLE` of the old copy fires `ON DELETE CASCADE`: measured, the
    children go from 3 rows to 0, with no error. The SDK's migration engines
    keep FK off, and the generated `env.py` refuses a handed-over connection
    that has it on — see [Migrations](../migrations.md#sqlite-migrations-run-with-foreign-keys-off).

**Recap:** one `AsyncDatabaseManager` per app, in `resources.py`;
`session_dependency` injects the per-request session (`session_dependency_for`
when the manager is built on demand), while `get_session_context()`/
`transaction()`, which commit, stay outside requests; `create_tables(metadata)`
accepts your own `DeclarativeBase`; `connect`/`disconnect`
in the lifespan; `health_check` + `db_url_safe` on `/health`; on SQLite,
WAL and the busy timeout ship on so web and worker can share the file, and
foreign keys are checked the way PostgreSQL checks them.
