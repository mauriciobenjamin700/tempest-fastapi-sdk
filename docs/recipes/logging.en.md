# Logging

In this recipe you set up structured JSON logs with per-request correlation, one file per severity level, and an HTTP endpoint to read them back. The goal is that every log line is parseable, traceable to the request that produced it, and inspectable without SSHing into the server.

`configure_logging` installs a JSON handler on the root logger that emits one-line JSON records carrying the active request ID. `LogUtils` is a thin facade that adds level methods accepting structured `**fields`.

```python
from tempest_fastapi_sdk import LogUtils, configure_logging
from tempest_fastapi_sdk.core import get_request_id

from src.db.models import UserModel


def risky() -> None:
    """Blow up, so the log shows a real traceback."""
    raise RuntimeError("boom")


user = UserModel(name="Ana", email="ana@example.com")


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

!!! tip "Adopting it in a service that already logs `%`-style"
    The level methods take `logging`'s positional arguments, so existing call
    sites move over without a rewrite — and keep **lazy** interpolation and the
    stable template a log tool groups on:

    ```python
    from tempest_fastapi_sdk import LogUtils

    log = LogUtils("app.email", level="INFO")

    log.info("Email sent to %s", "ana@example.com")
    log.error("Sending to %s failed: %s", "bruno@x.com", "timeout")

    log.error("Sending to %s failed: %s", "bruno@x.com", "timeout", op="send")
    ```

    The last line shows the two styles coexisting: the positionals build the
    message, and `**fields` still becomes top-level keys on the JSON.

    `funcName`/`lineno` point at **your** call site, not inside the facade —
    the default is `stacklevel=2`. Wrapping `LogUtils` in a layer of your own?
    Pass `stacklevel=3` (or more) to walk past the extra frames.

JSON output (single line — formatted here for readability):

```json
{
  "timestamp": "2026-05-16T20:14:33.412Z",
  "level": "INFO",
  "logger": "app.users",
  "message": "user_created",
  "request_id": "d83e4b0c-7c2f-4bd6-aaa1-7d4f6cf5e5e9",
  "user_id": "9c1a5b2d-...",
  "email": "ana@example.com"
}
```

The middleware accepts a custom header name (`RequestIDMiddleware(app, header_name="X-Correlation-ID")`); the same header is echoed back on every response.


## One handler set, not one per module

`LogUtils(__name__)` is how the class reads, and how it gets used: one line at
the top of every module. Since v0.280.0 it is also what the class does well.

```python
# src/services/user.py
from tempest_fastapi_sdk import LogUtils

logger: LogUtils = LogUtils(__name__)

logger.info("User %s created", "u-1")
```

```python
# src/services/payment.py
from tempest_fastapi_sdk import LogUtils

logger: LogUtils = LogUtils(__name__)

logger.warning("Charge %s expired", "c-9")
```

Both modules write to the same files through the **same** handler set: the
constructor configures the **root** logger, once per process, and binds
`__name__` to it by propagation. The logger name still rides on every line
(the `logger` field), so you still know who wrote what.

!!! danger "Why this matters more than it looks"
    Through v0.279.0 the constructor called
    `configure_logging(logger_name=name)`, which sets `propagate = False` on
    that logger and gives it handlers of its own. Measured:
    **7 handlers per logger** (1 stdout + 6 files), so a service with 27
    modules opened 27 stdout and 162 file handlers, all pointed at the same
    six paths — and `RotatingFileHandler` does not coordinate
    rollover across instances: when the file hits the ceiling several of them
    try to roll the same path, and the result is lost records and interleaved
    `.1`/`.2` backups.

    Measured in a process with five instances: **7 handlers on the root**
    (1 stdout + 6 files) and zero on each module logger.

!!! warning "The first instance decides level and format"
    "Once per process" has a consequence: the second call reconfigures
    nothing, so `level=` and `json_output=` from the second one on are
    ignored. That is what you want in a service — the bootstrap decides — but
    it surprises in tests, where each case wants its own. Call
    `reinitialize_logging()` between them.

To isolate one logger on purpose — a subsystem whose stream you do not want
merged — ask for the old shape:

```python
from tempest_fastapi_sdk import LogUtils

audit: LogUtils = LogUtils("audit", log_dir="logs/audit", scope="logger")
```


## Per-level files + isolated `500.log`

**By default the SDK writes to stdout AND to `logs/`** (one JSON file per level) at the same time. Each file receives **only its own level** (exact match — an `ERROR` never lands in `warning.log`), so every severity becomes an isolated, greppable stream.

```python
from tempest_fastapi_sdk import configure_logging

# Defaults — stdout + logs/{debug,info,warning,error,critical,500}.log
configure_logging(level="INFO")

# Custom directory
configure_logging(level="INFO", log_dir="/var/log/myapp")

# Disable file output (stdout-only — handy for serverless / read-only FS)
configure_logging(level="INFO", file_output=False)

# Disable stdout (sidecar tails from disk)
configure_logging(level="INFO", stdout=False)

# Growth ceiling: each file rotates at ~10 MB, keeping 5 generations
configure_logging(level="INFO", max_bytes=10_000_000, backup_count=5)

# No rotation — when the host's logrotate (or a sidecar) owns retention
configure_logging(level="INFO", max_bytes=0)
```

!!! danger "Files rotate by default — here is why"
    A plain `FileHandler` grows without bound. On a service that logs one line
    per request, running on a long-lived host, `info.log` is what fills the
    disk — and a full disk takes the service down **along with** anything else
    sharing the partition. So the default is a `RotatingFileHandler` with
    `max_bytes=10_000_000` and `backup_count=5`: ~60 MB per level, hard cap.

    The other half of this pair already had its ceiling: `make_logs_router`
    reads at most 20k records per file, added after a service whose log
    directory had grown to gigabytes answered with a dead worker. This is the
    writing half.

    Rotated files (`info.log.1`, `info.log.2`, …) are **not** read by `/logs`:
    the endpoint reads the exact names, so it shows the current window. Longer
    retention is a collector's job.

!!! warning "Don't disable both"
    `configure_logging(stdout=False, file_output=False)` raises
    `ValueError` — silencing every handler leaves the application
    blind.

!!! check "File logging is best-effort — it never crashes startup"
    If `log_dir` cannot be created or its files cannot be opened
    (read-only filesystem, missing write permission, hardened container,
    serverless, CI), the SDK **skips** the file handlers, emits a warning
    (to the logger when stdout is on, otherwise straight to `stderr`) and
    keeps running with stdout only — instead of dying at import with
    `PermissionError: [Errno 13] ... 'logs'`. Pass `file_output=False` to
    opt out of file logging explicitly.

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

The `LogSettings` mixin models the whole configuration, rotation included, and
`logging_kwargs()` hands it over ready to splat — so the field-to-argument
translation lives in one place instead of in a hand-written call where the two
newest arguments are exactly the ones that get left out:

```python
from tempest_fastapi_sdk import configure_logging
from tempest_fastapi_sdk.settings import LogSettings


class Settings(LogSettings):
    """The service's configuration."""


config = Settings()
configure_logging(**config.logging_kwargs())
```

| Variable | Default | What it decides |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Minimum level emitted. |
| `LOG_JSON` | `True` | JSON on stdout (files are always JSON). |
| `LOG_DIR` | `logs` | Directory for the files; empty disables file logging. |
| `LOG_MAX_BYTES` | `10000000` | Size at which each file rotates; `0` disables rotation. |
| `LOG_BACKUP_COUNT` | `5` | Generations kept per level. |

The disk budget is `LOG_MAX_BYTES * (LOG_BACKUP_COUNT + 1)` per level, times
the six files.


## Reporting a 500 of your own — `error_500`

The SDK's catch-all handler marks every unhandled exception with
`http_500=True`, which is what `500.log` filters on. When **your** code detects
a grave failure that never becomes an exception — a wallet credit that rolled
back, a transfer stranded at the gateway — `error_500` puts the record in that
same file:

```python
from tempest_fastapi_sdk import LogUtils

logger: LogUtils = LogUtils(__name__)


def settle(charge_id: str) -> None:
    """Credit a wallet, escalating a rollback to the 500 stream."""
    try:
        raise RuntimeError("commit failed")
    except RuntimeError:
        logger.error_500(
            "CRITICAL: wallet credit failed for charge %s",
            charge_id,
            charge_id=charge_id,
        )
```

The record lands in `500.log` **and** in `error.log` — the isolated file exists
so a grave failure is not buried, not to remove it from the level stream. The
traceback rides along whenever one is being handled.

!!! tip "`exc_info="auto"` for the call site that is sometimes in an `except`"
    `logger.exception(...)` requires being inside an `except`; `exc_info=True`
    outside one writes the useless `NoneType: None`. A helper called from both
    places wants the third option:

    ```python
    from tempest_fastapi_sdk import LogUtils

    logger: LogUtils = LogUtils(__name__)

    logger.error("Failed to send to %s", "ana@x.com", exc_info="auto")
    ```

    The default stays `False`, so existing call sites do not change output.


## `exc_info` on every level, and the name `LogRecord` will not give up

`debug`, `info`, `warning`, `error` and `critical` all take `exc_info` as a
named parameter — a `bool` or `"auto"`:

```python
from tempest_fastapi_sdk import LogUtils

logger: LogUtils = LogUtils(__name__)


def refresh_cache(key: str) -> None:
    """Reload one entry, tolerating an unavailable cache."""
    try:
        raise ConnectionError("redis down")
    except ConnectionError:
        logger.warning("Cache miss on %s", key, exc_info="auto", op="refresh")
```

!!! danger "Up to 0.280.0 that line was a dormant `KeyError`"
    Only `error` had the parameter. On the other four levels `exc_info=` fell
    into `**fields`, became `extra=`, and `logging` refuses to overwrite a
    reserved `LogRecord` attribute — **inside `makeRecord`, after the level
    check**. Measured on 0.280.0:

    ```text
    level ERROR, warning(exc_info=...) call: does NOT raise
    level DEBUG, same call -> KeyError: "Attempt to overwrite 'exc_info'
                                         in LogRecord"
    ```

    A service running at INFO carried the failure asleep until someone raised
    the verbosity — which is to say it went off during the incident, the minute
    the log was the only tool left.

This holds for any name `LogRecord` already uses, not just `exc_info`:
`stack_info`, `msg`, `args`, `levelname`, `name`, `asctime` and more. A
field by one of those names is now refused **at the call**, with `TypeError`,
regardless of level:

```python
from tempest_fastapi_sdk import LogUtils

logger: LogUtils = LogUtils(__name__)

try:
    logger.info("order processed", levelname="FAKE")
except TypeError as error:
    print(error)
```

```text
LogUtils: reserved LogRecord attribute used as a structured field:
'levelname'. logging would raise KeyError while building the record — after
the level check, so the failure stays dormant until the verbosity goes up.
Rename the field, or pass exc_info= as the named parameter every level method
accepts.
```

The set of names is read off a real `LogRecord` rather than typed out, so it
tracks the interpreter. Measured: **22** names on 3.11, **23** on 3.12 and
3.13 — the addition is `taskName`, which does not exist on 3.11.

!!! tip "Rename the field, not the intent"
    Need a structured field with that meaning? Prefix it: `op_name` instead of
    `name`, `record_args` instead of `args`. The name `logging` reserves
    belongs to the record's machinery, not to your domain.

## After Alembic — `reinitialize_logging`

`logging.config.fileConfig()` disables, by default, every logger that already
existed. The `env.py` this SDK ships calls exactly that, so a migration run
silences the application's own loggers for the rest of the process — records
are dropped before any handler sees them, which looks like a logging
configuration problem and is not one.

```python
from tempest_fastapi_sdk import reinitialize_logging

reinitialize_logging()
```

Call it after any code that reconfigures Python's logging system. It re-enables
the loggers and clears the "root already configured" latch, so the next
`LogUtils(...)` installs handlers again.


## Reading logs over HTTP — `make_logs_router`

`make_logs_router` mounts `GET /logs`, which parses the on-disk JSON files and returns a paginated `BasePaginationSchema[LogEntrySchema]` (newest first).

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import make_logs_router

from src.core.settings import settings

app = FastAPI()


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
| `start` / `end` | ISO-8601 | Limit records to a time window. A value with no offset (`2026-05-31T00:00:00`, or a bare date) is read as UTC. |
| `page` / `page_size` | integers | Pagination (1-indexed). |

!!! info "The read is bounded per file"
    Each request reads the **newest 20,000 records** of every selected file
    (`DEFAULT_MAX_RECORDS_PER_FILE`), not the whole file. The endpoint sorts
    newest-first and paginates, so what was left out was unreachable anyway —
    and without the bound a multi-gigabyte log directory went into memory whole
    on every request, taking the worker down before it answered. Tune it with
    `make_logs_router(max_records_per_file=...)`; when the cap bites, a
    `WARNING` is logged naming the `source`.

### Clearing — `DELETE /logs`

The same router mounts the opposite verb, behind the same `X-Token`:

```bash
# Empty one level
curl -X DELETE -H "X-Token: $TOKEN_SECRET" \
  "http://localhost:8000/logs?source=info"

# Empty everything, 500.log included
curl -X DELETE -H "X-Token: $TOKEN_SECRET" \
  "http://localhost:8000/logs"
```

The response names what it emptied:

```json
{"cleared": ["debug.log", "info.log", "warning.log", "error.log", "critical.log", "500.log"]}
```

!!! warning "`all` reaches `500.log` here; on the read, it does not"
    It is the one place the two verbs disagree, and it is deliberate. Reading
    `all` does **not** include `500.log` because every record in it is also in
    `error.log` — the page would list the same record twice. Clearing `all`
    **does** include it, because a clear that left the 500 stream behind is
    the surprising outcome.

!!! info "Truncates, never unlinks"
    The handlers `configure_logging` installed hold an open descriptor on each
    path. Removing the file would leave them writing to an inode nothing can
    read back — the endpoint would look like it worked exactly once. A missing
    file is created empty, so the post-condition is the same.

Anything that needs the paths outside a route — an archiving job, a collector,
a counter — uses the same resolver the router uses, instead of re-deriving the
level-to-filename map:

```python
from pathlib import Path

from tempest_fastapi_sdk.api.routers.logs import resolve_log_files

paths: list[Path] = resolve_log_files("logs", "error")
every: list[Path] = resolve_log_files("logs", "all", include_http_500=True)
```


!!! check "Recap"
    - `configure_logging(log_dir=...)` → stdout **+** one file per level.
    - Exact-level routing: each file holds only its own severity.
    - `500.log` isolates uncaught 500s (the `http_500` marker).
    - `make_logs_router` serves those files, paginated and authenticated.

## One line per request — `AccessLogMiddleware`

`configure_logging` formats what you log and `RequestIDMiddleware` binds the
correlation id, but none of that emits the **one line per request** that makes
`make_logs_router` worth reading. That is what `AccessLogMiddleware` is for:

```python
# src/api/app.py
import logging

from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware, RequestIDMiddleware

app: FastAPI = FastAPI()

app.add_middleware(AccessLogMiddleware, level=logging.INFO)
app.add_middleware(RequestIDMiddleware)
```

Every request becomes a record whose `message` is the familiar line
(`GET /api/users 200 12.4ms`) and whose details go through `extra=` as real
fields — `http_method`, `http_path`, `http_query`, `http_status`,
`duration_ms`, `client_ip`. That difference is what makes `JSONFormatter` write
keys rather than an interpolated string, and therefore what lets `GET /logs`
filter on them.

!!! info "Registration order no longer matters (since v0.277.0)"
    The `request_id` comes from two sources, because neither alone survives
    both orders: the context variable `RequestIDMiddleware` binds, and the
    header it stamps on the response. Measured:

    ```text
    AccessLogMiddleware inside RequestIDMiddleware   context variable: set
                                                     response header:  not yet written
    AccessLogMiddleware outside RequestIDMiddleware  context variable: cleared
                                                     response header:  present
    ```

    `RequestIDMiddleware` is a `BaseHTTPMiddleware`: it stamps the header
    **after** the app returns, so an inner middleware's `send` wrapper has
    already run; and it clears the context variable as it unwinds, so an outer
    one finds nothing there. Reading both covers both.

    Up to v0.276.0 this was a `!!! danger` telling you to register in the right
    order. A warning about a mechanical step with one right answer is code that
    was not written — the repository's own rule caught the SDK itself.

### Level: `ERROR` is where the failure is

A response below `500` is logged at the `level` you configured (`INFO` by
default). A `5xx` is logged at `ERROR` — both the one the application rendered
and the one that escaped a handler. Finding failed requests by filtering on
level is the reason to write these lines at all.

The escaping exception is the case that most needs a log line and the one
hand-written versions most often miss: the handler blew up before sending
anything, so there is no status to read. The middleware records `500`, adds an
`error` field naming the exception class, and **re-raises** — your own error
handling still decides what the client sees.

### A stream is not a one-hour request

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware

app: FastAPI = FastAPI()

app.add_middleware(
    AccessLogMiddleware,
    exempt_paths=("/api/sse", "/api/metrics"),
)
```

`exempt_paths` matches by **prefix**, so `("/api/sse",)` covers
`/api/sse/stream`. An SSE connection held open for an hour would otherwise be
logged, on close, as a request that took an hour.

### A secret in the URL is already logged

A deprecated endpoint that took a bearer-equivalent token as a path parameter
reaches the middleware with the token in the URL — refusing the request in the
handler does **not** un-log it. `redact` is the seam for that, applied to the
path and the query separately:

```python
# src/api/app.py
import re

from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware

_LEGACY_TOKEN_PATH: re.Pattern[str] = re.compile(
    r"^(?P<prefix>/api)?/auth/google/[^/]+$"
)

app: FastAPI = FastAPI()


def redact_path(value: str) -> str:
    """Replace the secret segment of a legacy path before it is logged."""
    if _LEGACY_TOKEN_PATH.match(value):
        return _LEGACY_TOKEN_PATH.sub(r"\g<prefix>/auth/google/<redacted>", value)
    return value


app.add_middleware(AccessLogMiddleware, redact=redact_path)
```

### Behind a proxy

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware

app: FastAPI = FastAPI()

app.add_middleware(AccessLogMiddleware, trusted_ip_header="x-real-ip")
```

`client_ip` comes from `get_client_ip_from_scope`. Never point
`trusted_ip_header` at a bare `X-Forwarded-For`: that header is **appended** to
whatever the client sent, so its leftmost entry is attacker-chosen — and the log
would start attributing requests to whatever address they picked. See
[Security »](security.en.md).

!!! check "Recap"
    - `AccessLogMiddleware` is pure ASGI: it reads the status off
      `http.response.start` and leaves the exception path untouched.
    - The `request_id` reaches the line in either registration order: from the
      context variable when inner, from the response header when outer.
    - `5xx` (rendered or escaped) is logged at `ERROR`; everything else at the
      configured `level`.
    - `exempt_paths` matches by prefix — that is what keeps SSE out of the log.
    - `redact` rewrites path and query before the record exists.

## Recap

- `configure_logging` writes structured JSON to stdout **and** to `logs/`, one
  file per level, each file carrying only its own level.
- `500.log` is isolated on purpose: the file you open first during an incident
  does not arrive mixed with everything else.
- The request id lands on every line, so a user complaint becomes a `grep` —
  that is what separates structured logging from pretty logging.
- `make_logs_router` mounts a paginated `GET /logs` over those files, newest
  first, so you can read them without shell access to the container.
- Every level takes `exc_info` (`bool` or `"auto"`); a structured field that
  collides with a `LogRecord` attribute is refused at the call, with
  `TypeError`.
