# tempest-fastapi-sdk

> Blocos de construção compartilhados de FastAPI / SQLAlchemy / Pydantic usados em todos os serviços de backend da Tempest. **Comece todo projeto com a mesma fundação opinativa já no lugar.**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![PyPI](https://img.shields.io/pypi/v/tempest-fastapi-sdk)

---

## O que você ganha de imediato {#what-you-get-out-of-the-box}

!!! tip "Use o SDK quando você, de outra forma, copiaria e colaria isto"
    `BaseModel` base do SQLAlchemy, `BaseRepository` async, schemas Pydantic + paginação, envelope de exceções + handlers, mixins de settings, managers async de DB / Redis / RabbitMQ / TaskIQ, validadores de documentos brasileiros, utilitários de JWT / senha / email / upload, Server-Sent Events, Web Push, um admin no estilo Django e uma CLI direta ao ponto (`tempest`).

| Módulo | Exports |
| --- | --- |
| `tempest_fastapi_sdk.admin` | `AdminSite`, `AdminModel`, `make_admin_router`, `UserModelAuthBackend` |
| `tempest_fastapi_sdk.api` | `register_exception_handlers`, `apply_cors`, `RequestIDMiddleware`, `make_health_router`, dependências de JWT/role/permissão, `HardenedStaticFiles`, `RateLimitMiddleware`, `WebhookSignatureVerifier`, `run_server` |
| `tempest_fastapi_sdk.cache` | `AsyncRedisManager`, `@cached` |
| `tempest_fastapi_sdk.controllers` | `BaseController` |
| `tempest_fastapi_sdk.core` | `JSONFormatter`, `configure_logging`, contexto de request-ID, `BaseStrEnum` / `BaseIntEnum` |
| `tempest_fastapi_sdk.db` | `BaseModel`, `BaseUserModel`, `BaseRepository`, `AsyncDatabaseManager`, `AlembicHelper`, `AuditMixin`, `SoftDeleteMixin` |
| `tempest_fastapi_sdk.exceptions` | hierarquia `AppException` (404 / 409 / 401 / 403 / 422 / 429 / file-too-large / invalid-file-type / JWT) |
| `tempest_fastapi_sdk.queue` | `AsyncBrokerManager` (FastStream / RabbitMQ) |
| `tempest_fastapi_sdk.schemas` | `BaseSchema`, `BaseResponseSchema`, `BasePaginationFilterSchema`, `BasePaginationSchema`, paginação por cursor |
| `tempest_fastapi_sdk.services` | `BaseService` |
| `tempest_fastapi_sdk.settings` | `BaseAppSettings`, `ServerSettings`, `DatabaseSettings`, `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `CORSSettings`, `EmailSettings`, `LogSettings`, `TokenSettings`, `UploadSettings`, `WebPushSettings`, `TaskIQSettings` |
| `tempest_fastapi_sdk.sse` | `EventStream`, `ServerSentEvent`, `sse_response` |
| `tempest_fastapi_sdk.tasks` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `tempest_fastapi_sdk.testing` | `test_session`, `test_database`, helpers de SQLite em memória |
| `tempest_fastapi_sdk.utils` | `PasswordUtils`, `JWTUtils`, `EmailUtils`, `UploadUtils`, `MetricsUtils`, `LogUtils`, `AttemptThrottle`, `DownloadUtils`, helpers de documento/telefone BR, helpers de token opaco |
| `tempest_fastapi_sdk.webpush` | `WebPushDispatcher`, `WebPushPayloadSchema`, `WebPushSubscriptionSchema` |

## Início rápido em cinco minutos {#five-minute-quickstart}

```bash
# 1. Install the SDK with every extra
pip install "tempest-fastapi-sdk[all]"

# 2. Scaffold a new service in the current directory
tempest new .

# 3. Sync deps + run the smoke test
uv sync
uv run pytest
```

!!! example "O que `tempest new` produz"
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

Continue com **[Instalação »](installation.md)** para o passo a passo por extra, **[Arquitetura »](architecture.md)** para entender a estratificação, ou vá direto ao **[Tutorial »](tutorial.md)**.

## Status {#status}

| Superfície | Estado |
| --- | --- |
| Python | 3.11 / 3.12 / 3.13 (testado em matriz no CI) |
| Testes | 630+ casos de pytest, ≥ 89 % de cobertura |
| Type-checking | `mypy --strict`, `py.typed` incluído (PEP 561) |
| Lint / format | `ruff` (check + fix + format) |
| Pipeline de release | trusted-publishing no PyPI a cada tag `vX.Y.Z` |
