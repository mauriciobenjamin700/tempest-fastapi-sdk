# Safe deploys (migrations + graceful shutdown)

Two classic deploy risks: a migration that **deletes data** by accident,
and a rollout that **severs in-flight requests** when the old pod dies.
This recipe covers the two defenses the SDK ships.

## Safe migrations: `safe_upgrade`

`AlembicHelper.safe_upgrade()` runs the upgrade **only if** no pending
migration is destructive. It scans each pending revision's `def upgrade()`
for data-deleting calls — `op.drop_table`, `op.drop_column`,
`op.drop_constraint` (and `batch_op` variants) — and, if it finds one,
raises `DestructiveMigrationError` **without touching the database**.

```python
from tempest_fastapi_sdk import AlembicHelper, DestructiveMigrationError


def deploy_migrations() -> None:
    """Apply migrations on deploy, blocking accidental DROPs."""
    helper: AlembicHelper = AlembicHelper(db_url="postgresql+asyncpg://...")
    try:
        helper.safe_upgrade("head")
    except DestructiveMigrationError as exc:
        # CI/CD fails here — someone must review and unblock with force.
        for revision, op in exc.offences:
            print(f"blocked: {revision} → {op}")
        raise
```

The scan looks at the migration **code**, not the generated SQL — so it
never false-positives on the table rebuild SQLite does in batch mode. A
`drop_*` in `downgrade()` (the normal, expected path) is ignored.

### Allowing an intentional DROP

When the DROP is intentional (you took a backup, you reviewed it), pass
`force=True` — the destructive operations are logged and the upgrade runs:

```python
from tempest_fastapi_sdk import AlembicHelper

helper: AlembicHelper = AlembicHelper(db_url="postgresql+asyncpg://...")
helper.safe_upgrade("head", force=True)  # I know what I'm doing
```

!!! tip "Inspect only"
    `helper.pending_destructive_ops("head")` returns the list of
    `(revision, operation)` without running anything — handy for a CI step
    that only reports.

!!! danger "force=True deletes data"
    `DROP COLUMN` / `DROP TABLE` are irreversible. Only use `force=True`
    after a backup and human review.

## Back up before migrating (`DatabaseBackup`)

`safe_upgrade` refuses the destructive migration, but sometimes the DROP is
intentional. The right order then is backup → `force=True` → verify.
`DatabaseBackup` dumps from the very same `DATABASE_URL` the service uses:

```python
# scripts/deploy.py
from pathlib import Path

from tempest_fastapi_sdk import DatabaseBackup

from src.core.settings import settings

backup: DatabaseBackup = DatabaseBackup(settings.DATABASE_URL)
written: Path = backup.backup()
print(f"dump written to {written}")
```

The `+asyncpg` / `+aiosqlite` suffix is stripped for you — you pass the
application URL and keep no second variable just for backups. Without
`output=`, the file lands in `backups/<db>_<YYYYMMDD-HHMMSS>.<ext>`.

| Backend | `backup()` uses | Format |
| --- | --- | --- |
| `postgresql` | `pg_dump` | custom (`-Fc`) by default; a `.sql` `output` (or `plain=True`) writes a text dump |
| `sqlite` | file copy | the `.sqlite` file itself |

Restoring mirrors it — the format comes from the extension:

```python
from pathlib import Path

from tempest_fastapi_sdk import DatabaseBackup

from src.core.settings import settings

backup = DatabaseBackup(settings.DATABASE_URL)


backup.restore(Path("backups/app_20260727-104500.dump"))
```

`clean=True` (default) drops existing objects before recreating, so the restore
is a faithful copy: `pg_restore --clean --if-exists` for the custom format,
`DROP SCHEMA public CASCADE` ahead of `psql -f` for plain, file overwrite for
SQLite. Pass `clean=False` to restore **on top of** an existing database.

!!! warning "The two errors you will hit first"
    - `BackupToolMissingError` — `pg_dump` / `pg_restore` / `psql` is not on
      `PATH`. An app container rarely ships the Postgres client; install
      `postgresql-client` in the image that runs the deploy, or run the backup
      elsewhere.
    - `UnsupportedBackupBackendError` — a dialect with no strategy (MySQL, SQL
      Server). Only Postgres and SQLite are covered.

    Both are raised **before** `backups/` is created, so a failure never leaves
    an empty directory behind for someone to mistake for a finished backup.

!!! info "Synchronous on purpose"
    `pg_dump` is a process and a file copy is disk I/O — neither gains anything
    from `async`. Call these from a CLI command or a deploy script; from async
    code, use `asyncio.to_thread(backup.backup)`.

## Graceful shutdown: drain in-flight requests

On a rollout the orchestrator sends `SIGTERM` and, after a grace period,
`SIGKILL`. If a request is still running when the worker dies, it's
severed — an intermittent 502. `GracefulShutdownMiddleware`:

1. Once **draining**, replies `503` + `Retry-After` to new requests, so
   the load balancer stops routing to this pod.
2. **Counts** in-flight requests; `wait_drained()` waits for them to
   finish (with a timeout) before the process exits.

You hold the instance and drive draining from the `lifespan` (uvicorn runs
the lifespan shutdown on `SIGTERM` — and it owns the signal handling):

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from tempest_fastapi_sdk import GracefulShutdownMiddleware

shutdown: GracefulShutdownMiddleware = GracefulShutdownMiddleware(drain_timeout=25.0)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Drain in-flight requests on shutdown."""
    yield
    shutdown.begin_drain()
    await shutdown.wait_drained()


app: FastAPI = FastAPI(lifespan=lifespan)
app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)
```

Set the orchestrator's grace period a little **above** `drain_timeout`,
and uvicorn's `--timeout-graceful-shutdown` to match.

!!! warning "The signal belongs to your server"
    uvicorn already installs `SIGTERM` handlers and triggers the lifespan
    shutdown — drive draining from there. The opt-in
    `install_signal_handlers()` is only for servers that do **not** manage
    signals themselves; it chains the previous handler and is a no-op off
    the main thread.

## Recap

- `AlembicHelper.safe_upgrade()` refuses destructive migrations
  (`DestructiveMigrationError`); `force=True` allows them;
  `pending_destructive_ops()` only inspects.
- `DatabaseBackup(url).backup()` / `.restore(path)` — per-dialect dump and
  restore (Postgres via `pg_dump`/`pg_restore`, SQLite by file copy) off the
  service's own `DATABASE_URL`.
- `GracefulShutdownMiddleware` replies `503` while draining and
  `wait_drained()` waits for in-flight requests — driven from the
  `lifespan`.
