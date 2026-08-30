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
import asyncio

from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcome

from src.core.settings import settings


async def sync_schema() -> SchemaSyncOutcome:
    """Bring the database schema in line with the migration tree."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    return await asyncio.to_thread(helper.sync_schema)
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

Alembic is synchronous, hence `asyncio.to_thread` — calling it straight from
async code blocks the event loop for the whole migration.

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
import asyncio

from tempest_fastapi_sdk import AlembicHelper, AsyncDatabaseManager

from src.core.settings import settings

db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)


async def broken_bootstrap() -> None:
    """Reach the worst possible state: old schema, Alembic reporting head."""
    helper: AlembicHelper = AlembicHelper(
        "alembic.ini",
        db_url=settings.DATABASE_URL,
    )
    if await asyncio.to_thread(helper.current) is None:
        await db.create_tables()
        await asyncio.to_thread(helper.stamp, "head")
        return
    await asyncio.to_thread(helper.safe_upgrade)
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
