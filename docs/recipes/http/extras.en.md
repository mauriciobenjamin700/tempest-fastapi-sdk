---
description: "Use to emit the pagination `Link` header, expose `/tool-spec`, start uvicorn with `run_server` and compose settings from mixins."
---

# Pagination links, tool-spec, server and settings

Use to emit the pagination `Link` header, expose `/tool-spec`, start uvicorn with `run_server` and compose settings from mixins.

Part of the [HTTP layer](../http.md) recipe.

## Pagination Link headers


`build_pagination_link_header` emits an RFC 8288 `Link` header with `first` / `prev` / `next` / `last` rels — pair it with (or use instead of) the `BasePaginationSchema` body wrapper for REST clients that expect GitHub-style headers. Existing query parameters on the base URL are preserved.

```python
from fastapi import APIRouter, Depends, Request, Response

from tempest_fastapi_sdk import BasePaginationSchema, build_pagination_link_header

from src.api.dependencies.controllers import get_user_controller
from src.controllers import UserController
from src.schemas import UserFilterSchema, UserResponseSchema

router = APIRouter()


@router.get("", response_model=list[UserResponseSchema])
async def list_users(
    request: Request,
    response: Response,
    filters: UserFilterSchema = Depends(),
    controller: UserController = Depends(get_user_controller),
) -> list[UserResponseSchema]:
    result = await controller.paginate(
        filters=filters.get_conditions(),
        order_by=filters.order_by,
        page=filters.page,
        page_size=filters.page_size,
        ascending=filters.ascending,
    )
    page = BasePaginationSchema[UserResponseSchema](**result)
    response.headers["Link"] = build_pagination_link_header(
        str(request.url),
        page=page.page,
        page_size=page.page_size,
        pages=page.pages,
    )
    response.headers["X-Total-Count"] = str(page.total)
    return page.items
```

Tweak `page_param=` / `size_param=` when your service uses non-standard query parameter names (e.g. `offset` / `limit`). Pass `extra_params={"sort": "name"}` to bake the current sort/filter state into every link.


## Tool-spec router


`make_tool_spec_router(spec)` mounts a `GET /tool-spec` endpoint exposing a machine-readable manifest at the root prefix — meant to sit alongside `/health/liveness` so external callers can discover capabilities without parsing the full OpenAPI document.

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import make_health_router, make_tool_spec_router

from src.api.dependencies.resources import db
from src.core.settings import settings


def _tool_spec() -> dict[str, object]:
    """Computed per request — keeps version + counts in sync with state."""
    return {
        "service": "my-service",
        "version": settings.VERSION,
        "tools": [
            {"path": "/api/users", "method": "GET", "summary": "List users"},
            {"path": "/api/orders", "method": "POST", "summary": "Place order"},
        ],
    }


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.include_router(make_health_router(db=db))
    app.include_router(make_tool_spec_router(_tool_spec))
    ...
    return app
```

Pass a dict (served verbatim), a sync callable (called every request) or an async callable (awaited). Override `path=` to expose the manifest at a different URL or `tag=` to group it under a different OpenAPI tag.


## Programmatic server entry point


`run_server` is the canonical helper imported from `src/server.py`. It centralizes the `host` / `port` / `reload` defaults — pulling values from a `ServerSettings`-flavoured `settings` object when present — and keeps the entry point a single line.

```python
# src/server.py
from tempest_fastapi_sdk import run_server

from src.api.app import app  # noqa: F401 — re-exported for external runners
from src.core.settings import settings


def run() -> None:
    """Start the API server programmatically."""
    run_server("src.api.app:app", settings=settings)


__all__: list[str] = ["app", "run"]
```

```python
# main.py
from src.server import run

if __name__ == "__main__":
    run()
```

Resolution order for each kwarg is `explicit argument → settings.SERVER_* → SDK default` (`"127.0.0.1"` / `8000` / `False`). Extra uvicorn kwargs (`workers=`, `log_config=`, `ssl_*=`) are forwarded verbatim.


## Settings mixins composition


`BaseAppSettings` is the configured `pydantic-settings` base (`env_file=".env"`, `extra="ignore"`, `case_sensitive=True`). The SDK also exposes composable mixins for the most common dependencies; pick the ones the service needs and compose them on your `Settings` class.

!!! warning "`BaseAppSettings` must be the last base"
    Every mixin inherits `BaseAppSettings`, so they all carry the same `model_config` — including `env_file=".env"` — and `.env` is read regardless of the order in which the mixins appear. But `BaseAppSettings` **must** be the **last** base: since the mixins subclass it, listing it before any mixin violates Python's C3 linearization and breaks the import. As of v0.159.1 the `AppSettingsMeta` metaclass intercepts this and raises `TypeError: Settings: BaseAppSettings must be the LAST base — DatabaseSettings already subclasses it ...`, which names the fix; before that the message was pydantic's raw `TypeError: Cannot create a consistent method resolution order (MRO)`. Always put `BaseAppSettings` at the end of the bases — see the [migration guide](../../migration.md).

```python
# src/core/settings.py
from pydantic import Field

from tempest_fastapi_sdk import (
    BaseAppSettings,
    CORSSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    LogSettings,
    RabbitMQSettings,
    RedisSettings,
    ServerSettings,
    TaskIQSettings,
    TokenSettings,
    UploadSettings,
    WebPushSettings,
)


class Settings(
    ServerSettings,
    LogSettings,
    DatabaseSettings,
    RedisSettings,
    RabbitMQSettings,
    TaskIQSettings,
    JWTSettings,
    CORSSettings,
    EmailSettings,
    UploadSettings,
    TokenSettings,
    WebPushSettings,
    BaseAppSettings,
):
    """Service-wide settings."""

    VERSION: str = Field(default="0.0.0")


settings = Settings()
```

Each mixin owns its own env-var prefix — pick only the ones the service needs:

| Mixin | Env vars |
| --- | --- |
| `ServerSettings` | `SERVER_HOST`, `SERVER_PORT`, `SERVER_RELOAD`, `SERVER_DEBUG` |
| `LogSettings` | `LOG_LEVEL`, `LOG_JSON`, `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `LOG_ROTATE_WHEN`, `LOG_ROTATE_INTERVAL`, `LOG_REDACT` |
| `DatabaseSettings` | `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_RECYCLE`, `DATABASE_SQLITE_WAL`, `DATABASE_SQLITE_BUSY_TIMEOUT`, `DATABASE_SQLITE_FOREIGN_KEYS` |
| `RedisSettings` | `REDIS_URL`, `REDIS_DECODE_RESPONSES` |
| `RabbitMQSettings` | `RABBITMQ_URL`, `RABBITMQ_PREFETCH_COUNT` |
| `KafkaSettings` | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_CLIENT_ID`, `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD`, `KAFKA_SSL_CAFILE`, `KAFKA_SSL_CERTFILE`, `KAFKA_SSL_KEYFILE`, `KAFKA_SSL_KEY_PASSWORD` |
| `TaskIQSettings` | `TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND_URL`, `TASKIQ_STORE_RESULTS`, `TASKIQ_RESULT_TTL_SECONDS`, `TASKIQ_WORKER_HEARTBEAT_FILE`, `TASKIQ_SCHEDULER_HEARTBEAT_FILE`, `TASKIQ_HEARTBEAT_SECONDS` |
| `JWTSettings` | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS`, `JWT_ISSUER` |
| `CORSSettings` | `CORS_ORIGINS`, `CORS_ORIGIN_REGEX`, `CORS_ALLOW_CREDENTIALS`, `CORS_ALLOW_METHODS`, `CORS_ALLOW_HEADERS`, `CORS_EXPOSE_HEADERS`, `CORS_MAX_AGE` |
| `EmailSettings` | `EMAIL_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDR`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_TIMEOUT_SECONDS` |
| `UploadSettings` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_ALLOWED_EXTENSIONS`, `UPLOAD_ALLOWED_MIMETYPES` |
| `TokenSettings` | `TOKEN_SECRET` |
| `WebPushSettings` | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `WEBPUSH_DEFAULT_TTL_SECONDS` |

> **Breaking change in 0.8.0:** `ServerSettings` previously exposed bare `HOST` / `PORT` / `DEBUG` / `LOG_LEVEL` / `LOG_JSON` fields. They were renamed to `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` / `SERVER_DEBUG`, and `LOG_LEVEL` / `LOG_JSON` moved to the new `LogSettings` mixin. Update both your `.env` file (env var names) and any code reading `settings.HOST` etc.
