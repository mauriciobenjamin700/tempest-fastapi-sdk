# tempest-fastapi-sdk

> Shared FastAPI / SQLAlchemy / Pydantic building blocks used across every Tempest backend service. **Start every project with the same opinionated foundation already in place.**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![PyPI](https://img.shields.io/pypi/v/tempest-fastapi-sdk)

---

## What you get out of the box

!!! tip "Reach for the SDK when you would otherwise copy-paste these"
    Base SQLAlchemy `BaseModel`, async `BaseRepository`, Pydantic schemas + pagination, exception envelope + handlers, settings mixins, async DB / Redis / RabbitMQ / TaskIQ managers, Brazilian document validators, JWT / password / email / upload utilities, Server-Sent Events, Web Push, a Django-style admin site, and a one-shot CLI (`tempest`).

| Module | Exports |
| --- | --- |
| `tempest_fastapi_sdk.admin` | `AdminSite`, `AdminModel`, `make_admin_router`, `UserModelAuthBackend` |
| `tempest_fastapi_sdk.api` | `register_exception_handlers`, `apply_cors`, `RequestIDMiddleware`, `make_health_router`, JWT/role/permission dependencies, `HardenedStaticFiles`, `RateLimitMiddleware`, `WebhookSignatureVerifier`, `run_server` |
| `tempest_fastapi_sdk.cache` | `AsyncRedisManager`, `@cached` |
| `tempest_fastapi_sdk.controllers` | `BaseController` |
| `tempest_fastapi_sdk.core` | `JSONFormatter`, `configure_logging`, request-ID context, `BaseStrEnum` / `BaseIntEnum` |
| `tempest_fastapi_sdk.db` | `BaseModel`, `BaseUserModel`, `BaseRepository`, `AsyncDatabaseManager`, `AlembicHelper`, `AuditMixin`, `SoftDeleteMixin` |
| `tempest_fastapi_sdk.exceptions` | `AppException` hierarchy (404 / 409 / 401 / 403 / 422 / 429 / file-too-large / invalid-file-type / JWT) |
| `tempest_fastapi_sdk.queue` | `AsyncBrokerManager` (FastStream / RabbitMQ) |
| `tempest_fastapi_sdk.schemas` | `BaseSchema`, `BaseResponseSchema`, `BasePaginationFilterSchema`, `BasePaginationSchema`, cursor pagination |
| `tempest_fastapi_sdk.services` | `BaseService` |
| `tempest_fastapi_sdk.settings` | `BaseAppSettings`, `ServerSettings`, `DatabaseSettings`, `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `CORSSettings`, `EmailSettings`, `LogSettings`, `TokenSettings`, `UploadSettings`, `WebPushSettings`, `TaskIQSettings` |
| `tempest_fastapi_sdk.sse` | `EventStream`, `ServerSentEvent`, `sse_response` |
| `tempest_fastapi_sdk.tasks` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `tempest_fastapi_sdk.testing` | `test_session`, `test_database`, in-memory SQLite helpers |
| `tempest_fastapi_sdk.utils` | `PasswordUtils`, `JWTUtils`, `EmailUtils`, `UploadUtils`, `MetricsUtils`, `LogUtils`, `AttemptThrottle`, `DownloadUtils`, BR document/phone helpers, opaque-token helpers |
| `tempest_fastapi_sdk.webpush` | `WebPushDispatcher`, `WebPushPayloadSchema`, `WebPushSubscriptionSchema` |

## Five-minute quickstart

```bash
# 1. Install the SDK with every extra
pip install "tempest-fastapi-sdk[all]"

# 2. Scaffold a new service in the current directory
tempest new .

# 3. Sync deps + run the smoke test
uv sync
uv run pytest
```

!!! example "What `tempest new` produces"
    ```text
    my-service/
    ├── main.py                 # one-liner that imports run from src.server
    ├── pyproject.toml
    ├── .env.example
    └── src/
        ├── server.py           # uvicorn entrypoint + module-level app
        ├── api/                # routers, dependencies, app factory
        ├── controllers/        # thin orchestration over services
        ├── services/           # business logic
        ├── schemas/            # request/response DTOs
        ├── db/
        │   ├── models/
        │   └── repositories/
        └── core/               # settings + constants + exceptions
    ```

Continue with **[Installation »](installation.md)** for the per-extra walkthrough, **[Architecture »](architecture.md)** to understand the layering, or jump straight into the **[Tutorial »](tutorial.md)**.

## Status

| Surface | State |
| --- | --- |
| Python | 3.11 / 3.12 / 3.13 (matrix-tested in CI) |
| Tests | 630+ pytest cases, ≥ 89 % coverage |
| Type-checking | `mypy --strict`, `py.typed` shipped (PEP 561) |
| Lint / format | `ruff` (check + fix + format) |
| Release pipeline | PyPI trusted-publishing on every `vX.Y.Z` tag |
