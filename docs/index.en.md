# tempest-fastapi-sdk

> Shared FastAPI / SQLAlchemy / Pydantic building blocks used across every Tempest backend service. **Start every project with the same opinionated foundation already in place.**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![PyPI](https://img.shields.io/pypi/v/tempest-fastapi-sdk)

---

## What you get out of the box

!!! tip "Reach for the SDK when you would otherwise copy-paste these"
    SQLAlchemy `BaseModel`, abstract `BaseUserModel` + `BaseUserTokenModel`, async `BaseRepository` with `bulk_create_values`/`bulk_upsert`, Pydantic schemas + pagination (offset + cursor), exception envelope + handlers, settings mixins with `title`/`description`/`examples`, async DB / Redis / RabbitMQ / TaskIQ managers, Brazilian validators, JWT / password / email (with Jinja2 templates) / upload (pluggable Local + MinIO backends) utilities, Server-Sent Events, Web Push, Django-style admin site, bundled auth flow (signup / activate / login / reset), OAuth2/OIDC (Google/GitHub + generic), CSRF / Idempotency / BodySize / Prometheus / RateLimit middlewares, typed httpx `HTTPClient` with retry + circuit-breaker, MinIO/S3 (`AsyncMinIOClient`), an Alembic hook that reorders base columns, and a single CLI (`tempest db`, `tempest user`, `tempest new`, `tempest generate`).

| Module | Exports |
| --- | --- |
| `tempest_fastapi_sdk.admin` | `AdminSite`, `AdminModel`, `make_admin_router`, `UserModelAuthBackend` |
| `tempest_fastapi_sdk.api` | `register_exception_handlers`, `apply_cors`, `RequestIDMiddleware`, `IdempotencyMiddleware`, `BodySizeLimitMiddleware`, `CSRFMiddleware`, `PrometheusMiddleware`, `make_health_router`, `make_logs_router`, `make_prometheus_router`, OAuth (`GoogleOAuthClient` / `GitHubOAuthClient` / `OIDCProvider`), JWT/role/permission dependencies, `HardenedStaticFiles`, `RateLimitMiddleware`, `WebhookSignatureVerifier`, `run_server` |
| `tempest_fastapi_sdk.auth` | `UserAuthService`, `make_auth_router`, schemas (`SignupSchema`, `LoginSchema`, `PasswordResetRequestSchema`, …) |
| `tempest_fastapi_sdk.cache` | `AsyncRedisManager`, `@cached` |
| `tempest_fastapi_sdk.controllers` | `BaseController` |
| `tempest_fastapi_sdk.core` | `JSONFormatter`, `configure_logging`, request-ID context, `BaseStrEnum` / `BaseIntEnum` |
| `tempest_fastapi_sdk.db` | `BaseModel`, `BaseUserModel`, `BaseUserTokenModel`, `UserTokenPurpose`, `BaseRepository` (with `bulk_*`), `AsyncDatabaseManager`, `AlembicHelper`, `AuditMixin`, `SoftDeleteMixin`, `reorder_base_columns_first`, `compose_hooks` |
| `tempest_fastapi_sdk.exceptions` | `AppException` hierarchy (404 / 409 / 401 / 403 / 422 / 429 / file-too-large / invalid-file-type / JWT) |
| `tempest_fastapi_sdk.queue` | `MessageBroker` (recommended typed facade), `AsyncQueueManager` / `AsyncBrokerManager` (FastStream / RabbitMQ lifecycle wrappers) |
| `tempest_fastapi_sdk.schemas` | `BaseSchema`, `BaseResponseSchema`, `BasePaginationFilterSchema`, `BasePaginationSchema`, cursor pagination |
| `tempest_fastapi_sdk.services` | `BaseService` |
| `tempest_fastapi_sdk.settings` | `BaseAppSettings`, `ServerSettings`, `DatabaseSettings`, `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `AuthSettings`, `CORSSettings`, `EmailSettings`, `LogSettings`, `TokenSettings`, `UploadSettings`, `WebPushSettings`, `TaskIQSettings` |
| `tempest_fastapi_sdk.sse` | `EventStream`, `ServerSentEvent`, `sse_response`, `SSEBroker` |
| `tempest_fastapi_sdk.storage` | `AsyncMinIOClient`, `ObjectStat` |
| `tempest_fastapi_sdk.tasks` | `TaskQueue` (recommended typed facade), `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `tempest_fastapi_sdk.testing` | `test_session`, `test_database`, in-memory SQLite helpers |
| `tempest_fastapi_sdk.utils` | `PasswordUtils`, `JWTUtils`, `EmailUtils` (with `render_template`), `UploadUtils`, `LocalUploadStorage`, `MinIOUploadStorage`, `HTTPClient`, `RetryPolicy`, `MetricsUtils`, `LogUtils`, `AttemptThrottle`, `DownloadUtils`, BR helpers, opaque-token helpers |
| `tempest_fastapi_sdk.webpush` | `WebPushDispatcher`, `WebPushPayloadSchema`, `WebPushSubscriptionSchema` |

## Five-minute quickstart

```bash
# 1. Install the `tempest` CLI (with every extra) via uv
uv tool install "tempest-fastapi-sdk[all]"

# 2. Scaffold a new service in the current directory
tempest new .

# 3. Sync the generated project's deps + run the smoke test
uv sync
uv run pytest
```

!!! note "From CLI to project"
    Step 1 installs the **`tempest` CLI** in its own environment (via `uv tool`). `tempest new` scaffolds a project with **its own `pyproject.toml`**; from step 3 on it's that project's `uv` that resolves and runs everything (`uv sync` creates the local `.venv` from the generated deps). Prefer `uv` end to end — mixing a global `pip install` with the project's `uv sync` resolves two different environments.

!!! note "About `[all]`"
    The `[all]` extra brings the application helpers, and leaves out the **15** extras that pull a heavy stack or a native binary: the local-model ones (`[genai]`, `[genai-audio]`, `[genai-diarization]`, `[genai-hub]`, `[genai-image]`, `[genai-onnx]`, `[genai-quant]`, `[genai-rag]`, `[genai-structured]`, `[genai-vlm]`), the ONNX/vision ones (`[faces]`, `[modelops-onnx]`, `[modelops-sklearn]`), plus `[admin-sql]` and `[firebase]`. Install those separately when you need them. The lightweight GenAI clients (Ollama, Chroma) are already in `[all]`. See **[Installation »](installation.md)** for the full extras table.

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

## Where to go next

<div class="grid cards" markdown>

-   **[Starting from zero »](getting-started/uv.md)**

    The beginner track: install `uv`, pick a Python version, build your first project, and where the official docs live.

-   **[Installation »](installation.md)**

    The per-extra walkthrough: what each one pulls in, what `[all]` leaves out, and how to pin the version.

-   **[Architecture »](architecture.md)**

    The layering (router → controller → service → repository) and why it is not optional.

-   **[Tutorial »](tutorial.md)**

    Linear, from scratch: builds the whole *Users* feature step by step.

-   **[Recipes »](recipes/index.md)**

    The heart of the docs: the **SDK tour** (one minimal example per block), the index of **86 recipes**, the **complete examples** (Pix checkout, marketplace, admin, fullstack web, GenAI) and **typed SSR**.

-   **[Reference »](reference.md)**

    The full API generated from the docstrings — signature, parameters, return type, raised exceptions and a link to the source.

-   **[Learning projects »](learning/index.md)**

    A guided project (marketplace) with business rules, domain model, critical flows and an endpoint map — to study the decisions, not just the APIs.

-   **[Roadmap »](roadmap.md)**

    What shipped in which version and what is queued. Honest, not aspirational.

-   **[Migration guide »](migration.md)** · **[Changelog »](changelog.md)**

    Breaking changes and the version-by-version history.

</div>

!!! tip "Something missing or wrong in the docs?"
    [Open an issue](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new/choose) — we treat a docs regression like a code regression. See **[Contributing »](contributing.md)**.

## Status

| Surface | State |
| --- | --- |
| Python | 3.11 / 3.12 / 3.13 (matrix-tested in CI) |
| Tests | 2,650+ pytest cases, ≥ 90 % coverage |
| Type-checking | `mypy --strict`, `py.typed` shipped (PEP 561) |
| Lint / format | `ruff` (check + fix + format) |
| Release pipeline | PyPI trusted-publishing on every `vX.Y.Z` tag |
