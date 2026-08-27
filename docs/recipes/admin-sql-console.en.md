# SQL console in the admin

Every serious admin panel grows one of these — phpMyAdmin, Adminer,
Metabase's native query, Django SQL Explorer — because eventually someone
needs an answer the list view cannot give. This is that console, with the
guard rails that make it survivable.

```bash
uv add "tempest-fastapi-sdk[admin,admin-sql]"
```

!!! danger "Read this before enabling it"
    **A SQL filter in the application is defence in depth, not a security
    boundary.**

    The analyser here parses properly (via `sqlglot`) rather than matching
    strings, and that stops the ordinary accidents: a `DROP` typed by someone
    who only meant to `SELECT`, an `UPDATE` with no `WHERE`, a query against a
    table holding card data. It will **not** stop a determined operator with
    time — SQL has CTEs, subqueries, functions, dialect extensions and comment
    tricks, and any parser-based allowlist is a game of coverage.

    The boundary that **actually** holds is the database user:

    ```sql
    CREATE ROLE admin_console LOGIN PASSWORD '…';
    GRANT CONNECT ON DATABASE app TO admin_console;
    GRANT SELECT ON orders, customers, invoices TO admin_console;
    ```

    Point the console at *that* connection, then use the policy below to
    narrow further and to produce a readable refusal instead of a database
    error. Used that way the two layers add up. Used alone, the policy is a
    speed bump.

## Turning it on

The console is **off by default** — without `sql_shell=`, the route does not
exist.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import AsyncDatabaseManager, UserModelAuthBackend
from tempest_fastapi_sdk.admin import (
    AdminSite,
    SqlCapability,
    SqlShellPolicy,
    SqlShellService,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel
from src.services.audit import record_sql_attempt

auth_backend = UserModelAuthBackend(UserModel)

console_db = AsyncDatabaseManager(settings.READONLY_DATABASE_URL)

app = FastAPI()


console = SqlShellService(
    console_db,                       # the restricted connection, not the app's
    policy=SqlShellPolicy(
        capabilities={SqlCapability.READ},
        denied_tables={"users", "user_tokens"},
        max_rows=500,
    ),
    dialect="postgres",
    auditor=record_sql_attempt,
)

app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=auth_backend,
        secret_key=settings.SECRET_KEY,
        sql_shell=console,
    ),
)
```

A "SQL console" entry appears in the sidebar, under "System".

## The policy

| Field | Default | What it does |
| --- | --- | --- |
| `capabilities` | `{READ}` | Statement families allowed. |
| `allowed_tables` | empty (all) | When set, **only** these tables. |
| `denied_tables` | empty | Never touchable — **deny beats allow**. |
| `max_rows` | `1000` | Rows fetched before truncating. |
| `max_statements` | `1` | Statements per submission. |
| `require_where` | `True` | Refuses `UPDATE`/`DELETE` without `WHERE`. |
| `statement_timeout_ms` | `10000` | Server-side timeout where the dialect supports it. |

### Capabilities

Split the way an operator thinks about risk, not the way SQL groups
keywords:

| Capability | Covers |
| --- | --- |
| `READ` | `SELECT`, `WITH … SELECT`, `EXPLAIN`, `SHOW` |
| `INSERT` | adds rows |
| `UPDATE` | changes rows |
| `DELETE` | removes rows |
| `DDL` | `CREATE`, `ALTER`, `COMMENT` |
| `DROP` | `DROP` and `TRUNCATE` — irreversible loss |
| `ADMIN` | `GRANT`, `REVOKE`, `SET`, **and anything the analyser cannot classify** |

!!! check "The unknown lands on the most privileged capability"
    A construct nobody anticipated becomes `ADMIN`, so it needs the highest
    permission rather than slipping through as harmless. It is the safe
    default: a parser that does not recognise something must not wave it
    through.

### Deny beats allow

```python
from tempest_fastapi_sdk.admin import SqlShellPolicy


policy = SqlShellPolicy(
    allowed_tables={"users", "orders"},
    denied_tables={"users"},
)
policy.table_allowed("users")   # False
policy.table_allowed("orders")  # True
```

Deliberate order: putting a table in `denied_tables` cannot be undone by a
broad allow rule elsewhere.

### Hidden tables are found

The analyser walks the whole tree, so subqueries, CTEs and joins count:

```python
from tempest_fastapi_sdk.admin import analyze_sql

analyze_sql("SELECT * FROM orders WHERE id IN (SELECT id FROM secrets)")[0].tables
```

```text
['orders', 'secrets']
```

A policy that only looked at the top-level `FROM` would miss exactly where
someone hides a table they should not read.

!!! note "A CTE alias is not a table"
    `WITH recent AS (SELECT * FROM orders) SELECT * FROM recent` reports
    `['orders']`, not `recent`. Otherwise an `allowed_tables` policy would
    refuse a query that only reads what it should.

### Multi-statement is refused by default

```text
SELECT 1; DROP TABLE users
```

```text
2 statements submitted; the policy allows 1
```

That is how an allowed `SELECT` carries a `DROP` past someone skimming the
box. Open it with `max_statements` if you genuinely need it.

### `UPDATE`/`DELETE` without `WHERE`

```text
update without a WHERE clause is refused; add one, or disable
require_where in the policy
```

The most common console accident. On by default.

## What the operator sees

The page shows the policy **before** you type — capabilities, tables, row cap
— so the limits are known rather than discovered through refusals. A console
that can write shows a warning.

A refusal and a database error render differently on purpose: "you may not do
that" and "your SQL is wrong" lead to different fixes.

## Auditing

```python
import logging

from src.db.models import SqlAudit

logger = logging.getLogger(__name__)


async def record_sql_attempt(entry: SqlAudit) -> None:
    """Persist every console attempt, allowed or refused."""
    logger.warning(
        "sql_console",
        extra={
            "principal": entry.principal,
            "allowed": entry.allowed,
            "capability": entry.capability,
            "tables": entry.tables,
            "reason": entry.reason,
            "sql": entry.sql,
        },
    )
```

**Every** attempt is audited, including refused ones — a record of what
someone *tried* to run is usually more interesting than the list of what
worked.

!!! warning "An auditor failure does not take the console down"
    By design, so a broken sink cannot remove the tool. But a console you
    cannot audit is one you should turn off — log inside it.

## Reads run in a rolled-back transaction

Under a read-only policy, each statement runs inside a transaction that is
rolled back. A `SELECT` that turns out to mutate — a function with a side
effect, a dialect quirk — leaves nothing behind.

## Using it outside the admin

The service does not depend on the page:

```python
import asyncio

from tempest_fastapi_sdk.admin import SqlShellDenied, SqlShellPolicy, SqlShellService

from src.api.dependencies.resources import db

policy = SqlShellPolicy(allowed_tables={"users", "orders"})


service = SqlShellService(db, policy=policy, dialect="postgres")


async def main() -> None:
    """Run this example."""
    try:
        result = await service.execute("SELECT 1", principal="job@internal")
    except SqlShellDenied as exc:
        print("refused:", exc)


asyncio.run(main())
```

## Checklist before enabling in production

- [ ] The console points at a **restricted role**, not the app user.
- [ ] `denied_tables` covers credential, token and personal-data tables.
- [ ] `capabilities` is the minimum that solves the case — start at `{READ}`.
- [ ] The `auditor` writes somewhere you actually read.
- [ ] Admin access already requires MFA (`[mfa]`) for whoever reaches the page.
- [ ] `statement_timeout_ms` is set, on a dialect that honours it.

## Recap

- **Off by default**; without `sql_shell=` the route does not exist.
- **The policy explains and narrows; the GRANTs enforce.** Use both.
- **Deny beats allow**, and the unknown lands on `ADMIN`.
- **Subqueries and CTEs are inspected**; a CTE alias is not a table.
- **Every attempt is audited**, refusals included.

See also: [Admin site](admin.md) for the rest of the panel and
[Security](security.md) for what surrounds access to it.
