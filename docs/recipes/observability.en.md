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

Call it once at startup, after the app exists and (when you want to trace
queries) after the database has connected:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import AsyncDatabaseManager, setup_tracing

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager("postgresql+asyncpg://...")


@app.on_event("startup")
async def _startup() -> None:
    """Connect the database and turn tracing on."""
    await db.connect()
    setup_tracing(
        app,
        service_name="orders-api",
        otlp_endpoint="http://otel-collector:4317",
        sqlalchemy_engine=db.engine,
    )
```

That's it: every request becomes a parent span, every query and every httpx
call becomes a child span, and the whole trace shows up in Jaeger / Tempo /
Honeycomb under the name `orders-api`.

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
    instrumentation is silently skipped instead of breaking boot.

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
  `[otel]` extra.
- `otlp_endpoint=None` exports spans to the console (local debugging);
  `sample_ratio` controls sampling.
- `SlowQueryLogger(engine, threshold_ms=...).attach()` logs slow queries
  with no extra at all; parameters and `EXPLAIN` sit behind opt-in flags.
