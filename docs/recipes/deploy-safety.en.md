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

### Database in a container: `docker_container=`

When Postgres runs in its own container, installing `postgresql-client` in the
application image — and keeping it on a version compatible with the server — is
dead weight carried for a nightly job. The database image already ships the
`pg_dump` that matches it exactly:

```python
from pathlib import Path

from tempest_fastapi_sdk import DatabaseBackup

from src.core.settings import settings

backup = DatabaseBackup(settings.DATABASE_URL, docker_container="app-db")

written: Path = backup.backup(Path("backups/app.dump"))
backup.restore(written)
```

With `docker_container` set, `pg_dump` runs **inside** the container and the
dump comes back over stdout into the local file; restore goes the other way,
with the file fed to `pg_restore`/`psql` on stdin. Without it, nothing changes.

!!! note "Three details that make this mode work"
    - **`-h`/`-p` are dropped.** The URL's host and port describe how the
      *application* reaches the database from outside; inside the container that
      route does not exist. User and database still come from the URL.
    - **The password crosses by name.** The command carries `-e PGPASSWORD`
      with no value: Docker copies it from the calling process's environment.
      Spelling it `-e PGPASSWORD=…` would put the password in the container's
      command line, where any `ps` on the host reads it.
    - **Nothing is copied in.** Restore streams over stdin instead of
      `docker cp`, so no temporary file is left inside the container and there
      is no window where one sits there half written.

    What the mode does require is `docker` on the caller's `PATH` — and that is
    what `BackupToolMissingError` checks here, in place of `pg_dump`.

!!! warning "The two errors you will hit first"
    - `BackupToolMissingError` — `pg_dump` / `pg_restore` / `psql` (or `docker`,
      in the mode above) is not on `PATH`. An app container rarely ships the
      Postgres client; install `postgresql-client` in the image that runs the
      deploy, use `docker_container=`, or run the backup elsewhere.
    - `UnsupportedBackupBackendError` — a dialect with no strategy (MySQL, SQL
      Server). Only Postgres and SQLite are covered.

    Both are raised **before** `backups/` is created, so a failure never leaves
    an empty directory behind for someone to mistake for a finished backup.

!!! info "Synchronous on purpose"
    `pg_dump` is a process and a file copy is disk I/O — neither gains anything
    from `async`. Call these from a CLI command or a deploy script; from async
    code, use `asyncio.to_thread(backup.backup)`.

## Graceful shutdown: drain in-flight requests

On rollout, the orchestrator sends `SIGTERM` and, after a while, `SIGKILL`.
If a request is still running when the worker dies, it is severed — an
intermittent 502. `GracefulShutdownMiddleware`:

1. Once **draining**, replies `503` + `Retry-After` to new requests —
   including the health endpoint, which is what makes the load balancer stop
   routing to this pod.
2. **Counts** in-flight requests; `wait_drained()` waits for them to finish
   (with a timeout).

### What uvicorn already does on its own on `SIGTERM`

Before choosing where to trigger draining, look at what happens under
uvicorn. Measured with uvicorn in a subprocess, a 3 s request in flight and
`SIGTERM` 0.5 s later:

```text
after SIGTERM: /health ConnectError
in-flight /slow: 200
LIFESPAN-SHUTDOWN in_flight=0
LIFESPAN-DRAINED True
```

uvicorn **closes the listener right away** (the new request does not even
connect), **waits itself** for the in-flight request to finish, and only
**then** runs the lifespan shutdown. So a `begin_drain()` in the lifespan
shutdown arrives when no request is left — it never emits a `503`, and
`wait_drained()` returns `True` immediately. Under uvicorn, the wait for
in-flight requests is uvicorn's own, bounded by
`--timeout-graceful-shutdown`.

### Drain **before** `SIGTERM`

The `503` is only useful if it goes out while the listener still accepts
connections — that is, before `SIGTERM`. On Kubernetes that moment is the
`preStop` hook, which runs before the signal. Bind draining to a signal
uvicorn does **not** use (`SIGUSR1`) and send it from `preStop`:

```python
import signal

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from tempest_fastapi_sdk import GracefulShutdownMiddleware

shutdown: GracefulShutdownMiddleware = GracefulShutdownMiddleware(drain_timeout=25.0)

app: FastAPI = FastAPI()
app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)
shutdown.install_signal_handlers((signal.SIGUSR1,))


@app.get("/health")
async def health() -> dict[str, str]:
    """Readiness: answers 503 as soon as draining starts."""
    return {"status": "ok"}
```

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "kill -USR1 1 && sleep 15"]
```

Measured with the same app, sending `SIGUSR1` before `SIGTERM`:

```text
after SIGUSR1: /health 503 Retry-After= 5
after SIGTERM: /health ConnectError
in-flight /slow: 200
```

After `SIGUSR1`, every new request gets `503` with `Retry-After` — the
readiness probe fails and the pod leaves the load balancer — while the
in-flight request finishes with `200`. The `preStop` `sleep` gives that time
to happen; `SIGTERM` arrives afterwards, and uvicorn waits for whatever is
still in flight.

!!! warning "One process per pod"
    `kill -USR1 1` reaches the container's PID 1 — your `python main.py`
    when `CMD` uses the exec form. With `uvicorn --workers N`, PID 1 is the
    supervisor and the signal never reaches the workers; in that case run
    one worker per pod, or signal each worker.

!!! info "`install_signal_handlers` needs the main thread"
    `signal.signal` only works on the main thread; elsewhere the method does
    nothing. The measurement above calls the method at module level in a
    `main.py` started with `uvicorn.run(app)`. Do not pass `SIGTERM` or
    `SIGINT`: those belong to uvicorn.

Set the orchestrator's grace period (`terminationGracePeriodSeconds`) above
the `preStop` `sleep` plus uvicorn's `--timeout-graceful-shutdown`.

## Recap

- `AlembicHelper.safe_upgrade()` refuses destructive migrations
  (`DestructiveMigrationError`); `force=True` allows them;
  `pending_destructive_ops()` only inspects.
- `DatabaseBackup(url).backup()` / `.restore(path)` — per-dialect dump and
  restore (Postgres via `pg_dump`/`pg_restore`, SQLite by file copy) off the
  service's own `DATABASE_URL`.
- `GracefulShutdownMiddleware` replies `503` while draining and
  `wait_drained()` waits for in-flight requests. Under uvicorn, trigger
  draining **before** `SIGTERM` (`install_signal_handlers((signal.SIGUSR1,))`
  + `preStop`): by the lifespan shutdown the listener is already closed and
  there is no request left to refuse.
