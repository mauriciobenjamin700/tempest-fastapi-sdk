---
description: "Use to create the app with `create_app`, get liveness/readiness, put every 4xx and the 422 in the error envelope, notify failures and localize messages."
---

# App bootstrap and the error envelope

Use to create the app with `create_app`, get liveness/readiness, put every 4xx and the 422 in the error envelope, notify failures and localize messages.

Part of the [HTTP layer](../http.md) recipe.

[Section 2 of the tutorial](../../tutorial.md#2-settings-server-app-factory-entry-point) shows the minimal `create_app()`. This recipe is the **extended** version, wiring everything `tempest_fastapi_sdk.api` ships — exception handlers, CORS, request-ID middleware, the health router with extra checks, a shared-secret token dependency and an extra Redis manager — all from the same canonical `src/api/app.py` location. The bootstrapping pattern stays identical; only the contents of `create_app()` grow.

```python
# src/api/app.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RequestIDMiddleware,
    apply_cors,
    configure_logging,
    make_health_router,
    make_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings


configure_logging(level=settings.LOG_LEVEL, json_output=settings.LOG_JSON)

db = AsyncDatabaseManager(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
)
redis = AsyncRedisManager(settings.REDIS_URL)
require_token = make_token_dependency(settings.TOKEN_SECRET)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await db.connect()
    await redis.connect()
    try:
        yield
    finally:
        await redis.disconnect()
        await db.disconnect()


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    app = FastAPI(
        title="my-service",
        version=settings.VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    apply_cors(app, settings)
    register_exception_handlers(app)

    # Meta endpoints at the root prefix.
    app.include_router(
        make_health_router(
            db=db,
            checks={"redis": redis.health_check},
            version=settings.VERSION,
        ),
    )

    # Business endpoints under /api/<domain>, guarded by the shared secret.
    from src.api.routers import users

    app.include_router(
        users.router,
        prefix="/api",
        dependencies=[Depends(require_token)],
    )
    return app


app = create_app()
```

Key points:

- `src/server.py` and `main.py` (one-liner) stay exactly as in [Section 2 of the tutorial](../../tutorial.md#2-settings-server-app-factory-entry-point) — only `create_app()` changes when you add primitives. Never start uvicorn via `subprocess.run(["uvicorn", ...])`; always import `app` from `src.api.app` or call `uvicorn.run("src.api.app:app", ...)` programmatically from `src/server.py`.
- `RequestIDMiddleware` reads/writes `X-Request-ID` and seeds `request_id_ctx` so every log line emitted during the request carries the correlation ID.
- `apply_cors(app, settings)` reads `CORSSettings` defaults; pass keyword overrides for one-off changes.
- `register_exception_handlers(app)` wires three handlers, each with its own log level (plus a fourth, for the 422, when `envelope_validation_errors=True` — see [The 422 inside the envelope](#the-422-inside-the-envelope)):
    - `AppException` → `{detail, code, details}` envelope + `INFO` log (4xx) or `ERROR` + traceback + `500.log` (5xx).
    - `HTTPException` → keeps Starlette's default body (`{"detail"}`) on 4xx with an `INFO` log; on 5xx swaps in the SDK envelope + traceback + `500.log`.
    - `Exception` (catch-all) → SDK envelope + traceback + `500.log` (fixes Starlette's default, which returns only `"Internal Server Error"` with no log entry).

    Every handler honors `RequestIDMiddleware`: the log line carries the `request_id`, and the envelope exposes it under `details` so the client can correlate. Pass `log_traceback=False` when an APM (Sentry, OpenTelemetry) is already capturing the stack trace.
- `make_health_router(db=db, checks={"redis": redis.health_check}, version=...)` mounts `GET /health/liveness` and `GET /health/readiness` (returns `503` when any check fails) at the root prefix — see [Liveness and readiness](#liveness-and-readiness).
- `make_token_dependency(secret)` returns an async dependency that validates `X-Token` via `hmac.compare_digest`; an empty secret raises `ValueError` when the dependency is built — for a local run without the gate, pass `allow_unauthenticated=True`. The dependency lives next to the rest of the auth glue in `src/api/dependencies/auth.py` once it grows beyond the one-liner above.

## Liveness and readiness

They are two different questions, asked by whoever decides two different
things:

- **Liveness** — *"is the process alive?"*. When it fails, the orchestrator
  (Kubernetes, ECS) **restarts** the container. That is why
  `GET /health/liveness` touches no dependency at all: a Redis outage is not
  fixed by restarting the API, and a liveness probe that depended on it would
  take every replica down at once, in a loop.
- **Readiness** — *"can I take traffic right now?"*. When it fails, the load
  balancer **takes the replica out of rotation** and puts it back once it
  passes again. This is where the database, cache and broker belong:
  `GET /health/readiness` runs every check and answers `503` when any fails.

A single `GET /health` mixes the two — it either restarts over a dependency
or sends traffic to a replica that cannot serve it. When you ask for the
health endpoints of a new service, ask for both.

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import make_health_router


async def redis_check() -> bool:
    """Answer right away: a healthy dependency."""
    return True


async def search_check() -> bool:
    """Hang, like a half-open socket that never answers."""
    await asyncio.sleep(60)
    return True


app = FastAPI()
app.include_router(
    make_health_router(
        checks={"redis": redis_check, "search": search_check},
        timeout=0.5,
    ),
)


async def main() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        liveness = await client.get("/health/liveness")
        readiness = await client.get("/health/readiness")
    print(liveness.status_code, liveness.json())
    print(readiness.status_code, readiness.json())


asyncio.run(main())
```

Output (the whole process took 1.7 s, despite the `sleep(60)`):

```text
Health check 'search' timed out after 0.5s
200 {'status': 'ok'}
503 {'status': 'not_ready', 'checks': {'redis': True, 'search': False}}
```

What happens in the readiness probe:

- **Checks run concurrently**, each bounded by `timeout` (default `3.0`
  seconds; `None` disables it). One that overruns counts as a failure **for
  itself only** — `redis` above stays `True` — and the response leaves right
  after the `timeout`, not when the orchestrator's probe gives up. Keep
  `timeout` below the probe timeout.
- **The log never carries the exception message.** A check that raises logs
  `Health check 'database' raised ConnectionRefusedError` — name and type,
  never the text, because database and broker driver messages often carry
  the DSN with its user and password.

!!! warning "Cancellation only happens at an `await`"
    `timeout` cancels the check at its next `await` point. A check that
    blocks the event loop with synchronous I/O (a non-async driver, a
    `time.sleep`) cannot be interrupted — and it stalls the whole process,
    not just the readiness probe. Run that kind of call with
    `asyncio.to_thread`.

!!! tip "Queue"
    `MessageBroker.health_check` runs FastStream's real `ping`, with
    `timeout=2.0` by default — below the router's 3 s. With RabbitMQ stopped
    after boot it returns `False`:
    `make_health_router(checks={"queue": mq.health_check})`.


## Every 4xx inside the envelope

The 422 was the most visible gap, not the only one. The other four
client-error categories **do reach this SDK's handler** — six cases, each
measured in the table below — and answered Starlette's bare body, with no `code` and in English,
even in a service with a configured `MessageCatalog`:

| Path | Before | With `envelope_client_errors=True` |
| --- | --- | --- |
| `raise HTTPException(404, "order 42 does not exist")` | `{"detail": "order 42 does not exist"}` | same message **plus** `code: NOT_FOUND` |
| `raise HTTPException(403)` | `{"detail": "Forbidden"}` | `Acesso negado` + `code: FORBIDDEN` |
| `raise HTTPException(409)` | `{"detail": "Conflict"}` | `Conflito de recurso` + `code: CONFLICT` |
| `Depends(HTTPBearer())`, no token | `{"detail": "Not authenticated"}` | same message **plus** `code: UNAUTHORIZED` |
| unknown route | `{"detail": "Not Found"}` | `Recurso não encontrado` + `code: NOT_FOUND` |
| wrong method | `{"detail": "Method Not Allowed"}` | same message + `code: HTTP_405` |

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


def create_app() -> FastAPI:
    """Build the application.

    Returns:
        FastAPI: The configured application.
    """
    app: FastAPI = FastAPI()
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),
        default_locale="pt-BR",
        envelope_client_errors=True,       # ← every 4xx, the 422 included
    )
    return app
```

!!! danger "A message you wrote is **not** translated"
    This is the line between localizing and losing information. Starlette
    fills `detail` with the status *phrase* when the caller passes none, so
    the two cases are distinguishable:

    - `HTTPException(404, "order 42 does not exist")` → your message, **preserved**
    - `HTTPException(404)` → `detail == "Not Found"`, the phrase → translated

    Replacing the first with the catalog's generic entry would destroy the
    more informative of the two. FastAPI's own `"Not authenticated"` for a
    missing bearer token falls in the first group too — measured — and is
    preserved.

!!! info "Why two flags rather than one rename"
    `envelope_validation_errors` shipped in v0.284.0 covering only the 422.
    `envelope_client_errors` is the **superset**: it turns on every 4xx,
    the 422 included. A consumer who had already opted into the first sees
    no behaviour change on upgrade — which is exactly what a silent rename
    would cause.

    Want everything? `envelope_client_errors=True` alone. Want only the
    422? The other one alone.

!!! note "`HTTP_<status>` for a status the SDK does not model"
    The status-to-`code` mapping is **declared**, not derived: measured,
    thirteen `AppException` subclasses carry a 401 code and four a 404, so
    there is no status to read a single code off.

    A status outside the mapping answers `HTTP_<status>` and keeps the
    framework's phrase. 400 and 405 are in that group today — no
    `AppException` subclass carries a code for either. A client still
    parses one shape; what is missing is the translation, not the `code`.

## The 422 inside the envelope

`register_exception_handlers` wires three handlers, and the 422 goes
through none of them: FastAPI intercepts `RequestValidationError` in its
own default handler. So the error a client sees most often was the only one
outside the SDK's error model — no `code`, and never through the
`MessageCatalog` the service configured.

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


def create_app() -> FastAPI:
    """Build the application.

    Returns:
        FastAPI: The configured application.
    """
    app: FastAPI = FastAPI()
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),
        default_locale="pt-BR",
        envelope_validation_errors=True,        # ← put the 422 in the envelope
    )
    return app
```

With the flag on, `POST /login` with a short `password` answers:

```json
{
  "detail": "Erro de validação",
  "code": "VALIDATION_ERROR",
  "details": {
    "errors": [
      {
        "loc": ["body", "password"],
        "type": "string_too_short",
        "msg": "O texto deve ter no mínimo 12 caractere(s)"
      }
    ]
  }
}
```

!!! danger "The default 422 sends the submitted value back — the password included"
    The body FastAPI generates includes `input` for every field that
    failed. Measured, with `password` failing `min_length`:

    ```json
    {"detail": [{"type": "string_too_short", "loc": ["body", "password"],
                 "msg": "String should have at least 12 characters",
                 "input": "hunter2"}]}
    ```

    And `SecretStr` does **not** protect it: validation runs before the
    secret wrapper exists, so a failing `SecretStr` field returns the raw
    value too. Measured in both cases.

    That is the strongest reason to turn the flag on. `details` carries
    `loc` and `type` and **never** `input` — this is not a warning for you
    to act on, it is a line the handler does not write.

!!! info "The table is keyed by error type, not by field"
    Messages come from the catalog under `VALIDATION.<pydantic type>` —
    `string_too_short`, `missing`, `int_parsing` and the other 101. That is
    pydantic's vocabulary, identical in every service, so **a new field on
    a schema is born translated**: there is no per-field table to keep.

    There are 104 types, exactly the set of `pydantic_core.ErrorType`, and
    `tests/test_pydantic_error_types_guard.py` fails when the installed
    pydantic and the table disagree in either direction.

    The catalog carries **Portuguese only**, on purpose: pydantic's own
    `msg` already *is* the English message, so the handler falls back to it
    and the SDK keeps no copy that could drift from upstream. A type the
    catalog does not know falls back the same way — it degrades to English,
    never to blank.

!!! info "The SDK's BR validators localize the whole phrase"
    Pydantic files **everything** a field validator raises under the single
    type `value_error`, with the exception in `ctx["error"]`. Since the
    catalog localizes by type, `CPFOrCNPJField`, `PhoneBRField`, `UFField`,
    postal codes and PIX keys arrived half-translated:

    ```text
    Valor inválido: invalid CPF/CNPJ
    ```

    As of **v0.290.0** those validators raise `ValidationValueError`, which
    carries a **code** (`INVALID_CPF_CNPJ`, `INVALID_CEP`, `INVALID_UF`, …)
    resolved as `VALIDATION.<code>` in both languages — `CPF ou CNPJ
    inválido` in pt-BR, `Invalid CPF or CNPJ` in en-US. English *is* carried
    here, unlike the pydantic table: upstream's `msg` for this case is
    `Value error, invalid CPF/CNPJ`, prefixed and lowercase.

    Your own validators are unaffected: a plain `ValueError` still falls back
    to `VALIDATION.value_error`, and `ValidationValueError` subclasses
    `ValueError`, so every `except ValueError` keeps catching it.

!!! check "A placeholder never reaches the client"
    A template whose param is missing from `ctx` is no longer sent raw.
    The real case: pydantic's e-mail check (`EmailStr`) reports
    `value_error` with `ctx={"reason": ...}`, no `error`, and the client
    literally received `Valor inválido: {error}`. The message now falls
    back to the same locale's generic `VALIDATION.INVALID_VALUE`:

    ```json
    {"loc": ["body", "email"], "type": "value_error", "msg": "Valor inválido"}
    ```

    Underneath, `MessageCatalog.resolve` returns `None` when a param is
    missing (it used to return the template, braces included).
    `MessageCatalog.template(key, locale)` reads the raw string.

!!! warning "Turning the flag on changes the body, so it is opt-in"
    A frontend doing `body.detail.map(...)` and a client generated from the
    OpenAPI schema both model the 422 as an array. That is why the default
    is `False`, and turning it on is the service's decision.

    The schema follows: with the flag on, the `HTTPValidationError`
    component is rewritten to the envelope (`detail` as a string, plus
    `code` and `details`) and the item loses `input`. Every route
    references that component, so the docs and the runtime agree in both
    positions of the flag — without it the schema would lie in exactly the
    response a generated client uses most.

## Telling someone, and choosing where the record lands

`log_level` decides the **severity** of a 5xx. Two things it does not
decide: who finds out, and which logging configuration the record lands in.

```python
# src/api/app.py

import logging

from fastapi import FastAPI, Request

from tempest_fastapi_sdk import EmailUtils, LogUtils, register_exception_handlers

from src.core.settings import settings

log: LogUtils = LogUtils(__name__, scope="logger")


async def alert_ops(request: Request, exc: Exception) -> None:
    """Alert operations about a 5xx, off the response path.

    Args:
        request (Request): The request that failed.
        exc (Exception): The exception being reported.
    """
    mailer: EmailUtils = EmailUtils(**settings.email_kwargs())
    await mailer.send(
        to=[settings.OPS_EMAIL],
        subject=f"5xx on {request.url.path}",
        body=f"{type(exc).__name__}: {exc}",
    )


def create_app() -> FastAPI:
    """Build the application.

    Returns:
        FastAPI: The configured application.
    """
    app: FastAPI = FastAPI()
    register_exception_handlers(
        app,
        logger=log.logger,                # ← where the record lands
        on_server_error=alert_ops,        # ← who finds out
        log_level=logging.ERROR,
    )
    return app
```

!!! info "`on_server_error` runs as a background task"
    Called with `(request, exc)` **after** the response is built, so it
    never delays or alters what the client receives. It fires on all
    **three** paths that produce a 5xx — the catch-all,
    `HTTPException(500)` and a 5xx `AppException` — and does **not** fire
    on 4xx: an alert that pages on a 404 is an alert nobody reads.

!!! warning "An exception inside the callback is logged and swallowed"
    On purpose. Measured without that protection: the 500 response is
    still correct, but the notifier's exception **propagates up the ASGI
    stack** and becomes what the server reports — the client's 500 turns
    into a worse 500 with no trace of the real cause. With it, what
    propagates is the original exception.

!!! tip "`logger=` matters when logging is scoped to a logger"
    The handlers log to **their own** logger
    (`tempest_fastapi_sdk.api.handlers`). With
    `LogUtils(..., scope="root")` — the default — that already reaches the
    service's `500.log` and `error.log`; measured, one line in each.

    With `scope="logger"`, which does not configure the root, the SDK's
    logger sits outside the configured tree and the record reaches
    **none** of the files — measured, 0 and 0. Passing `logger=log.logger`
    closes that: 1 and 1.

    The type is `logging.Logger` rather than the `RetryLogger` protocol
    because the handlers pass `extra=` and `exc_info=` and the protocol
    has neither. Measured: `LogUtils.error(extra=...)` stores a field
    *named* `extra`, and `logging.Logger.error(request_id=...)` raises
    `TypeError` — no single type spans the two. `LogUtils` exposes
    `.logger`, which is the object that fits.

## Localized error messages (i18n)

By default the envelope `detail` is the exception's literal message (English for the built-ins). To return the message **in the client's language** without translating at each `raise`, pass a `MessageCatalog` to `register_exception_handlers`:

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(...)
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),                   # ← bundled PT-BR + EN-US
        default_locale="pt-BR",
    )
    ...
```

The handler negotiates the locale from the `Accept-Language` header (ordered by `q`), falls back to `default_locale` when nothing matches, and resolves the exception **key** — `message_key` if set, else the `code` — against the catalog. With no catalog, or when the key is unknown, the literal `detail` is kept (zero breakage).

```python
# Same NotFoundException, language decided by the client's Accept-Language:
#   Accept-Language: pt-BR  →  {"detail": "Recurso não encontrado", "code": "NOT_FOUND"}
#   Accept-Language: en-US  →  {"detail": "Resource not found",     "code": "NOT_FOUND"}
```

For domain codes (and messages with parameters), extend the catalog with `merge` and pass `message_params` at the `raise`:

```python
# src/core/i18n.py
from tempest_fastapi_sdk import MessageCatalog, default_message_catalog

CATALOG: MessageCatalog = default_message_catalog().merge(
    {
        "pt-BR": {"USER_NOT_FOUND": "Usuário {email} não encontrado"},
        "en-US": {"USER_NOT_FOUND": "User {email} not found"},
    }
)
```

```python
# src/services/user.py
from tempest_fastapi_sdk import NotFoundException


def require_user(email: str) -> None:
    """Raise a localized 404 carrying the offending e-mail.

    Args:
        email (str): The e-mail that was not found.

    Raises:
        NotFoundException: Always — keyed to ``USER_NOT_FOUND`` so the
            handler localizes it from the request locale.
    """
    raise NotFoundException(
        "User not found",                                    # literal fallback
        code="USER_NOT_FOUND",
        message_params={"email": email},
    )
```

!!! tip "The key defaults to the `code`"
    You rarely pass `message_key` — it falls back to the exception's `code`. Set `message_key` only to decouple the translated string from the error code. A template referencing a missing param is returned uninterpolated rather than raising.
