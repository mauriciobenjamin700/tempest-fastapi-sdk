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
- `GracefulShutdownMiddleware` replies `503` while draining and
  `wait_drained()` waits for in-flight requests — driven from the
  `lifespan`.
