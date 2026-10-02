# Observability (tracing + slow queries)

Logs tell you **what** happened in a service; distributed tracing tells you
**where** the time went on a request that crosses several services, and the
`SlowQueryLogger` points at **which** query is dragging your p99. This
recipe covers both.

!!! info "Where this fits"
    The [`RequestIDMiddleware`](http.md) correlates **logs** per request;
    OpenTelemetry correlates **spans** across services. They complement each
    other — use them together.

## Distributed tracing with OpenTelemetry

`setup_tracing` installs an OpenTelemetry provider and auto-instruments the
common layers of a Tempest service: FastAPI (incoming requests), SQLAlchemy
(queries), and httpx (outbound calls). Requires the `[otel]` extra:

```bash
uv add "tempest-fastapi-sdk[otel]"
```

There are **two moments**, and each has its own call:

1. `setup_tracing` at **module level**, right after creating the app (or
   inside your `create_app`). This is what turns requests into spans.
2. `instrument_sqlalchemy_engine` in the **lifespan**, right after
   `db.connect()` — the `AsyncDatabaseManager` engine only exists after
   `connect()`.

```python hl_lines="16 22"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import AsyncDatabaseManager, setup_tracing
from tempest_fastapi_sdk.api import instrument_sqlalchemy_engine

db: AsyncDatabaseManager = AsyncDatabaseManager("postgresql+asyncpg://...")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect the database and instrument the freshly created engine."""
    await db.connect()
    instrument_sqlalchemy_engine(db.engine)
    yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
setup_tracing(
    app,
    service_name="orders-api",
    otlp_endpoint="http://otel-collector:4317",
)
```

That's it: every request becomes a root span, and the queries and httpx
calls made inside it become child spans. Measured with an in-memory
exporter and an in-memory SQLite database, a `GET /orders` request that
calls httpx and runs a `SELECT 1` produces these spans (`span <- parent`;
the `file:tempest_mem_<hex>` suffix is the in-memory database name,
shortened here):

```text
GET <- GET /orders
connect <- GET /orders
BEGIN file:tempest_mem_<hex> <- GET /orders
SELECT file:tempest_mem_<hex> <- GET /orders
GET /orders http send <- GET /orders
GET /orders http send <- GET /orders
GET /orders <- None
```

The whole trace shows up in Jaeger / Tempo / Honeycomb under the name
`orders-api`.

!!! warning "Do not call `setup_tracing` inside the lifespan"
    The request span comes from wrapping the app's middleware stack, and
    Starlette builds that stack on the **first ASGI event** it receives —
    which is the lifespan startup itself. Called from there, `setup_tracing`
    finds the stack already built and **no request becomes a span**; queries
    are still traced, but as orphan roots with no parent request. Since
    0.303.0 the function detects this case and emits a `RuntimeWarning`
    instead of failing silently.

!!! tip "An engine that already exists at module level"
    If the engine exists before the lifespan (your own module-level
    `create_async_engine`), you can pass it directly:
    `setup_tracing(app, ..., sqlalchemy_engine=engine)`. For the
    `AsyncDatabaseManager` engine, use `instrument_sqlalchemy_engine` after
    `connect()`. One engine per process: the SQLAlchemy instrumentor is a
    singleton, and a second call (for another engine) only logs
    `Attempting to instrument while already instrumented` — measured, the
    second engine's queries produce no span.

### No collector (local debugging)

Pass `otlp_endpoint=None` to install a console exporter — spans print to
stdout, no collector required:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import setup_tracing

app: FastAPI = FastAPI()
setup_tracing(app, service_name="orders-api", otlp_endpoint=None)
```

### Sampling

In high-traffic production, tracing 100% of requests is expensive. Pass
`sample_ratio` to sample a fraction (a head-based decision propagated to
child spans):

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import setup_tracing

app: FastAPI = FastAPI()
setup_tracing(
    app,
    service_name="orders-api",
    otlp_endpoint="http://otel-collector:4317",
    sample_ratio=0.1,  # ~10% of requests
    resource_attributes={"deployment.environment": "prod"},
)
```

!!! tip "Arguments, not env vars"
    The endpoint, the sampling and the attributes come from the function
    **arguments** — the call site is the single source of truth. No
    configuring half in code and half in `OTEL_*` env vars.

!!! note "Best-effort instrumentation"
    SQLAlchemy and httpx are only instrumented when the
    `opentelemetry-instrumentation-sqlalchemy` / `...-httpx` packages are
    installed (the `[otel]` extra ships both). If they are missing,
    `setup_tracing` silently skips that instrumentation instead of breaking
    boot. `instrument_sqlalchemy_engine`, being an explicit request, raises
    `ImportError` in that case.

## Slow query logger

`SlowQueryLogger` registers a listener on the SQLAlchemy engine events and
emits a log line whenever a statement exceeds a configurable threshold. It
is the cheapest way to find the N+1 or the missing index. **No extra
needed** — it uses only SQLAlchemy.

```python
import logging

from tempest_fastapi_sdk import AsyncDatabaseManager, SlowQueryLogger

db: AsyncDatabaseManager = AsyncDatabaseManager("postgresql+asyncpg://...")


async def wire_slow_query_log() -> None:
    """Turn slow-query logging on at startup."""
    await db.connect()
    slow: SlowQueryLogger = SlowQueryLogger(
        db.engine,
        threshold_ms=200.0,       # logs queries >= 200ms
        level=logging.WARNING,
    )
    slow.attach()
```

Each slow query becomes a line like:

```text
WARNING ... slow query: 312.4ms >= 200.0ms threshold | SELECT users.id, ...
```

### Parameters and EXPLAIN (dev only)

By default, bind parameters are **not** logged (they often carry
PII/secrets). In development, turn on `log_parameters=True` and/or
`explain=True` to see the execution plan:

```python
import logging

from tempest_fastapi_sdk import SlowQueryLogger

from src.api.dependencies.resources import db


slow: SlowQueryLogger = SlowQueryLogger(
    db.engine,
    threshold_ms=50.0,
    log_parameters=True,  # include the binds — dev only
    explain=True,         # run EXPLAIN and append the plan — costs 1 round-trip
)
slow.attach()
```

!!! warning "EXPLAIN costs a round-trip"
    With `explain=True` every slow query fires an extra `EXPLAIN`. Keep it
    off in production, turn it on only while hunting a bad plan.

To turn it off (e.g. on shutdown or in a test), call `slow.detach()`.

## Recap

- `setup_tracing(app, service_name=..., otlp_endpoint=...)` turns on
  distributed tracing with FastAPI/SQLAlchemy/httpx auto-instrumentation —
  `[otel]` extra. Call it at module level, never in the lifespan.
- `instrument_sqlalchemy_engine(db.engine)` in the lifespan, after
  `connect()`, puts the `AsyncDatabaseManager` queries under the request
  span.
- `otlp_endpoint=None` exports spans to the console (local debugging);
  `sample_ratio` controls sampling.
- `SlowQueryLogger(engine, threshold_ms=...).attach()` logs slow queries
  with no extra at all; parameters and `EXPLAIN` sit behind opt-in flags.
