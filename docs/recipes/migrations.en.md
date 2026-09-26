# Migrations

Every database recipe assumes the schema already exists. This one is about the
step before that: **how the schema comes into being**, and why the hand-written
version of that step is wrong in a way that stays invisible for weeks.

The SDK ships the whole path as one method — `AlembicHelper.sync_schema()` —
and the rest of this page explains what it decides, so you can recognise which
state your database is in.

## The complete bootstrap

```python
# src/db/schema.py
from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcome

from src.core.settings import settings


async def sync_schema() -> SchemaSyncOutcome:
    """Bring the database schema in line with the migration tree."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    return await helper.sync_schema_async()
```

Call it from the lifespan and the service boots with the right schema from
**any** starting state:

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db.schema import sync_schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Sync the schema before the first request is served."""
    outcome = await sync_schema()
    print(f"schema: {outcome.value}")
    yield


app: FastAPI = FastAPI(lifespan=lifespan)
```

## From async code, use the `_async` method

The lifespan runs inside uvicorn's event loop. Every `AlembicHelper` method
that executes `alembic/env.py` has an `_async` twin — `upgrade_async`,
`safe_upgrade_async`, `downgrade_async`, `stamp_async`, `revision_async`,
`check_async`, `current_async`, `has_existing_schema_async`, `adopt_async`,
`sync_schema_async`, `squash_async`, `pending_destructive_ops_async` — with the
same signature, and that is the one you call from async code.

The reason is the `env.py` the SDK generates: it starts the async engine with
`asyncio.run(...)`, and `asyncio.run` cannot be called while a loop is already
running. Calling the sync method from the lifespan now fails **immediately**,
with the right instruction:

```text
RuntimeError: AlembicHelper.upgrade() was called from a running event loop. It runs alembic/env.py, which drives migrations with asyncio.run() and cannot nest inside the loop; use `await helper.upgrade_async(...)` instead.
```

Before, the same error came from deep inside Alembic as `asyncio.run() cannot
be called from a running event loop`, together with a
`RuntimeWarning: coroutine 'run_async_migrations' was never awaited` — and
`check()` did not even raise: it swallowed the error and answered `False`, as
if the schema had drifted.

The `_async` method runs the sync one in a worker thread, which has no loop of
its own, so the `asyncio.run` in `env.py` works there and the service's loop
keeps serving while the migration runs. Since the thread needs nothing new in
`env.py`, **the `env.py` already in your repository keeps working** — no need
to regenerate it.

!!! tip "Sync `current()` still works on the loop"
    Reading the revision does not go through `env.py`: with a sync driver
    installed (the stdlib `sqlite3`, or `psycopg2` on PostgreSQL),
    `helper.current()` works from async code as before. Only an async-only
    install (`asyncpg` with no sync driver) takes the `asyncio.run` path — and
    then it raises too, asking for `current_async()`.

??? info "Technical details: sharing the connection instead of a thread"
    Alembic has an official recipe for running the migration **on the loop
    itself**: open an `AsyncConnection` and hand the sync connection that
    `run_sync` provides over as `config.attributes["connection"]`. The
    `env.py` the SDK generates from this release on accepts it — given the
    connection, it migrates on it, without creating an engine or calling
    `asyncio.run`:

    ```python
    # src/db/shared_connection.py
    from alembic import command
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
    from tempest_fastapi_sdk import AlembicHelper

    from src.core.settings import settings


    async def upgrade_on_shared_connection() -> None:
        """Run every pending migration on a connection this code owns."""
        helper: AlembicHelper = AlembicHelper(
            "alembic.ini",
            db_url=settings.DATABASE_URL,
        )
        config = helper.config

        def _upgrade(connection: Connection) -> None:
            """Hand the sync connection to env.py and upgrade on it."""
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        engine: AsyncEngine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(_upgrade)
        await engine.dispose()
    ```

    The `_async` methods do **not** take that path, and the reason is
    compatibility: an `env.py` generated before this release ignores
    `config.attributes` and calls `asyncio.run` regardless — inside
    `run_sync` the loop is running, and it fails with the same error as
    before. The thread works with every `env.py` already generated. Use the
    path above when the migration must run on **your** connection or
    transaction, and regenerate `env.py` for it: run `tempest db init` in an
    empty directory and copy the generated `alembic/env.py` over yours,
    checking the metadata import (the default is
    `from src.db.models import BaseModel`).

    The new `env.py` also refuses, with its own message, to be executed from
    inside a loop **without** a connection handed over — the case of calling
    `command.upgrade` straight from async code.

## The three states it tells apart

`sync_schema()` asks the question almost every hand-written bootstrap forgets:
**does the database hold tables Alembic did not create?**

| Starting state | What runs | Return |
| --- | --- | --- |
| Empty database | `safe_upgrade()` — the base revision creates the tables | `SchemaSyncOutcome.SYNCED` |
| Database that **predates** Alembic | stamps the **base**, then `safe_upgrade()` | `SchemaSyncOutcome.ADOPTED` |
| Database already under Alembic | `safe_upgrade()` | `SchemaSyncOutcome.SYNCED` |
| Project with no revisions yet | nothing | `SchemaSyncOutcome.NO_MIGRATIONS` |

The empty database is the easy case, and it is what makes the rest work: the
schema the base revision builds is, by construction, **the same one** later
revisions assume they are altering — because it came from them.

## Why `create_tables()` is not the answer

The [Database](database.en.md) recipe says `db.create_tables()` is for tests and
local development only. It is worth also saying **what happens if you use it**,
because the prohibition without the consequence does not make anyone see the
defect in their own code:

!!! danger "`create_all` is `CREATE TABLE IF NOT EXISTS`"
    Against a table that **already exists**, `create_tables()` adds no column at
    all. It does not fail, does not warn, and does not return anything
    different. It is a silent no-op.

The real defect, which took a service down for a day:

```python
# scripts/broken_bootstrap.py — the defect, reproduced; not a recipe.
from tempest_fastapi_sdk import AlembicHelper, AsyncDatabaseManager

from src.core.settings import settings

db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)


async def broken_bootstrap() -> None:
    """Reach the worst possible state: old schema, Alembic reporting head."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    if await helper.current_async() is None:
        await db.create_tables()
        await helper.stamp_async("head")
        return
    await helper.safe_upgrade_async()
```

Every line is plausible. Together they produce the worst possible state: **an
old schema, and Alembic declaring itself up to date.**

```console
$ alembic current
a3f9c21e88b4 (head)

$ alembic upgrade head
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
```

Nothing to do. `alembic history` flags nothing. Weeks later, the first query
touching a new column blows up far from the cause:

```text
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such column: messages.edited_at
```

On the user's side that reads as "sending messages stopped working": the history
`GET` returns 500, and nothing at boot said a word.

## Adopting a database that predates Alembic

This is the case the code above was trying to handle. The right answer is not to
stamp `head` — it is to stamp the **base revision**:

```python
# src/db/schema.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings


def adopt_existing_database() -> bool:
    """Bring a pre-Alembic schema under Alembic, without upgrading it."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    return helper.adopt()
```

Stamping the base says *"the baseline is already applied"* — which is true,
because the tables exist — and leaves **every revision after it pending**, which
is also true. The `safe_upgrade()` that follows runs exactly those.

Stamping `head` says *"everything is already applied"*, which is false for all
but the first.

`adopt()` does nothing when that is not the case: a database already stamped has
nothing to adopt, and an empty one needs the baseline to **run**, not to be
skipped. That is why `sync_schema()` can call it unconditionally.

!!! tip "The question is answered by `has_existing_schema()`"
    It lists the tables and discounts `alembic_version`, which Alembic writes
    itself — its presence says nothing about the application schema.

## Repairing a database stamped wrong

If you are already in the bad state, the fix has two steps. The first clears the
pointer; the second re-adopts properly:

```python
# scripts/repair_schema.py
from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcome

from src.core.settings import settings


def repair() -> SchemaSyncOutcome:
    """Clear a wrong stamp and re-adopt the schema from the base."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    helper.stamp("base")
    return helper.sync_schema()
```

`stamp("base")` is not the same as `stamp(helper.base_revision())`: `"base"` is
Alembic's word for *no revision applied*, and it deletes the `alembic_version`
row. After that the database looks like what it is — an existing schema with no
pointer — and `sync_schema()` takes the adoption path.

!!! warning "Check what is pending before you upgrade"
    After `stamp("base")`, the revisions between base and head will run against
    a schema that may already carry part of them. Run
    `helper.pending_destructive_ops()` and read `helper.history()` first — and
    have a backup. `safe_upgrade` refuses destructive migrations without
    `force=True`, which helps, but does not replace looking.

## Where `create_tables()` is legitimate

In tests and throwaway local development — where the database is born and dies
in the same process, and there is no migration to drift from:

```python
# tests/conftest.py
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import AsyncDatabaseManager


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory schema."""
    db: AsyncDatabaseManager = AsyncDatabaseManager(
        "sqlite+aiosqlite:///:memory:"
    )
    await db.create_tables()
    async with db.get_session_context() as opened:
        yield opened
    await db.drop_tables()
```

The technical reason it works here and not in production is the same in both
cases: `create_all` only knows how to create what is missing. In a database that
was just born, "what is missing" is everything — so it gets it right. In one
that has already run, "what is missing" is no table at all, but possibly several
columns — and columns it does not look at.

!!! check "Recap"
    - `sync_schema()` is the whole bootstrap: it tells an empty database, a
      pre-Alembic one and an already-migrated one apart, and reports which path
      it took.
    - From async code (lifespan, endpoint), call the `_async` twin
      (`await helper.sync_schema_async()`); the sync method raises with a loop
      running, naming the `_async` one to use.
    - `create_tables()` is `CREATE TABLE IF NOT EXISTS` — a **silent no-op** on
      an existing table. It is never the step that evolves a schema.
    - When adopting an existing schema, stamp the **base revision**
      (`helper.base_revision()`, or just `helper.adopt()`), never `head`.
    - To repair a wrong `stamp("head")`: `stamp("base")`, then `sync_schema()`.
    - `create_tables()` stays legitimate where there is no migration to drift
      from: tests and in-memory SQLite.

## See also

- [Database »](database.en.md) — session, repository, `AsyncDatabaseManager`.
- [CLI »](cli.en.md) — `tempest db upgrade`, `revision`, `stamp`, `check`.
- [Safe deploys »](deploy-safety.en.md) — `safe_upgrade` and the CI drift gate.
