# Logging


`configure_logging` installs a JSON handler on the root logger that emits one-line JSON records carrying the active request ID. `LogUtils` is a thin facade that adds level methods accepting structured `**fields`.

```python
from tempest_fastapi_sdk import LogUtils, configure_logging
from tempest_fastapi_sdk.core import get_request_id

# Imperative — call once during bootstrap.
configure_logging(level="INFO", json_output=True)

# Facade — handy for service-wide singletons.
log = LogUtils("app.users", level="INFO")
log.info("user_created", user_id=str(user.id), email=user.email)
log.warning("login_throttled", ip="1.2.3.4", attempts=5)

try:
    risky()
except RuntimeError:
    log.exception("risky_failed", op="reconcile")  # appends traceback

# Surface the correlation ID outside the log line if needed.
request_id = get_request_id()
```

JSON output (single line — formatted here for readability):

```json
{
  "timestamp": "2026-05-16T20:14:33.412+00:00Z",
  "level": "INFO",
  "logger": "app.users",
  "message": "user_created",
  "request_id": "d83e4b0c-7c2f-4bd6-aaa1-7d4f6cf5e5e9",
  "user_id": "9c1a5b2d-...",
  "email": "ana@example.com"
}
```

The middleware accepts a custom header name (`RequestIDMiddleware(app, header_name="X-Correlation-ID")`); the same header is echoed back on every response.


## Per-level files + isolated `500.log`

By default logs go to **stdout** only. Pass `log_dir` to `configure_logging` and, on top of stdout, the SDK writes **one JSON file per level** inside that directory. Each file receives **only its own level** (exact match — an `ERROR` never lands in `warning.log`), so every severity becomes an isolated, greppable stream.

```python
from tempest_fastapi_sdk import configure_logging

# Keeps stdout AND writes logs/{debug,info,warning,error,critical,500}.log
configure_logging(level="INFO", json_output=True, log_dir="logs")
```

On disk:

```text
logs/
├── debug.log      # only DEBUG records
├── info.log       # only INFO records
├── warning.log    # only WARNING records
├── error.log      # only ERROR records (a 500 lands here too)
├── critical.log   # only CRITICAL records
└── 500.log        # only uncaught-500 records (isolated)
```

!!! danger "500s are grave — that's why they get their own file"
    The catch-all handler registered by `register_exception_handlers`
    flags every uncaught exception with the `http_500=True` extra.
    `configure_logging(log_dir=...)` routes those records to a dedicated
    `500.log` **in addition** to `error.log`. The gravest failure is
    never buried among the other errors.

!!! tip "Always in the logs, never in the body"
    The traceback goes to the files/terminal via logging — **not** to the
    response body. A 500 body is just the generic envelope
    (`{"detail": "Internal server error", "code": "INTERNAL_SERVER_ERROR"}`).
    See [HTTP layer](http.md) for the `log_traceback` /
    `include_traceback` flags.

!!! note "Files are always JSON"
    File handlers use `JSONFormatter` regardless of `json_output`, so the
    `/logs` endpoint can parse them back. `json_output` only controls the
    stdout format.

In the scaffold the directory comes from `LOG_DIR` (defaults to
`"logs"`; set it empty to disable file logging). Add `logs/` to your
`.gitignore`.


## Reading logs over HTTP — `make_logs_router`

`make_logs_router` mounts `GET /logs`, which parses the on-disk JSON files and returns a paginated `BasePaginationSchema[LogEntrySchema]` (newest first).

```python
from tempest_fastapi_sdk import make_logs_router

app.include_router(
    make_logs_router(log_dir="logs", token_secret=settings.TOKEN_SECRET),
)
```

!!! warning "Protect the endpoint in production"
    The payload exposes tracebacks and request metadata. The endpoint is
    gated by a shared-secret `X-Token` header via
    `make_token_dependency`. An empty `TOKEN_SECRET` **disables** the
    check (dev only) — never expose `/logs` unauthenticated in
    production.

Query examples:

```bash
# Latest 20 records across every level
curl -H "X-Token: $TOKEN_SECRET" "http://localhost:8000/logs"

# Only the isolated 500s, page 1, 50 per page
curl -H "X-Token: $TOKEN_SECRET" "http://localhost:8000/logs?source=500&page_size=50"

# Errors mentioning "timeout" in a time window
curl -H "X-Token: $TOKEN_SECRET" \
  "http://localhost:8000/logs?source=error&q=timeout&start=2026-05-31T00:00:00Z"
```

Query parameters:

| Parameter | Values | Description |
| --- | --- | --- |
| `source` | `all` (default), `debug`, `info`, `warning`, `error`, `critical`, `500` | Which file to read. `all` merges every level; `500` returns only the isolated 500s. |
| `q` | text | Case-insensitive substring match on the message. |
| `start` / `end` | ISO-8601 | Limit records to a time window. |
| `page` / `page_size` | integers | Pagination (1-indexed). |

!!! check "Recap"
    - `configure_logging(log_dir=...)` → stdout **+** one file per level.
    - Exact-level routing: each file holds only its own severity.
    - `500.log` isolates uncaught 500s (the `http_500` marker).
    - `make_logs_router` serves those files, paginated and authenticated.


## Base enums


`BaseStrEnum` / `BaseIntEnum` extend the stdlib `Enum` with helpers tuned for Pydantic + SQLAlchemy round-tripping (lookup by value, JSON-serializable `str` / `int` inheritance, `__contains__` that accepts raw values). Use them for every enum that crosses the API boundary.

```python
from tempest_fastapi_sdk import BaseIntEnum, BaseStrEnum


class OrderStatus(BaseStrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class Priority(BaseIntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2

assert OrderStatus.PENDING == "pending"          # str inheritance
assert "paid" in OrderStatus                      # raw value membership
assert OrderStatus("paid") is OrderStatus.PAID    # canonical lookup
assert Priority.NORMAL + 1 == Priority.HIGH       # int math
```

Because they inherit from `str` / `int`, Pydantic serializes them transparently as their underlying value and SQLAlchemy can persist them via the standard `Enum` column without an extra converter.

