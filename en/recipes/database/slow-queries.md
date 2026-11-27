# 9. Detect slow queries

Use to log queries above a threshold with `SlowQueryLogger`.

Part of the [Database](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/database.md) recipe.

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
