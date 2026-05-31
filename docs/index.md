# tempest-fastapi-sdk

> Blocos compartilhados de FastAPI / SQLAlchemy / Pydantic usados em todos os serviços backend do Tempest. **Comece todo projeto com a mesma fundação opinativa já pronta.**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![PyPI](https://img.shields.io/pypi/v/tempest-fastapi-sdk)

---

## O que você ganha de fábrica

!!! tip "Use o SDK sempre que for copiar e colar isto"
    `BaseModel` SQLAlchemy, `BaseRepository` async, schemas Pydantic + paginação, envelope de exceções + handlers, mixins de settings, managers async de DB / Redis / RabbitMQ / TaskIQ, validadores de documentos brasileiros, utilitários de JWT / senha / e-mail / upload, Server-Sent Events, Web Push, um painel admin no estilo Django e uma CLI única (`tempest`).

| Módulo | Exporta |
| --- | --- |
| `tempest_fastapi_sdk.admin` | `AdminSite`, `AdminModel`, `make_admin_router`, `UserModelAuthBackend` |
| `tempest_fastapi_sdk.api` | `register_exception_handlers`, `apply_cors`, `RequestIDMiddleware`, `make_health_router`, dependências de JWT/role/permissão, `HardenedStaticFiles`, `RateLimitMiddleware`, `WebhookSignatureVerifier`, `run_server` |
| `tempest_fastapi_sdk.cache` | `AsyncRedisManager`, `@cached` |
| `tempest_fastapi_sdk.controllers` | `BaseController` |
| `tempest_fastapi_sdk.core` | `JSONFormatter`, `configure_logging`, contexto de request-ID, `BaseStrEnum` / `BaseIntEnum` |
| `tempest_fastapi_sdk.db` | `BaseModel`, `BaseUserModel`, `BaseRepository`, `AsyncDatabaseManager`, `AlembicHelper`, `AuditMixin`, `SoftDeleteMixin` |
| `tempest_fastapi_sdk.exceptions` | hierarquia `AppException` (404 / 409 / 401 / 403 / 422 / 429 / arquivo grande demais / tipo de arquivo inválido / JWT) |
| `tempest_fastapi_sdk.queue` | `AsyncBrokerManager` (FastStream / RabbitMQ) |
| `tempest_fastapi_sdk.schemas` | `BaseSchema`, `BaseResponseSchema`, `BasePaginationFilterSchema`, `BasePaginationSchema`, paginação por cursor |
| `tempest_fastapi_sdk.services` | `BaseService` |
| `tempest_fastapi_sdk.settings` | `BaseAppSettings`, `ServerSettings`, `DatabaseSettings`, `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `CORSSettings`, `EmailSettings`, `LogSettings`, `TokenSettings`, `UploadSettings`, `WebPushSettings`, `TaskIQSettings` |
| `tempest_fastapi_sdk.sse` | `EventStream`, `ServerSentEvent`, `sse_response` |
| `tempest_fastapi_sdk.tasks` | `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `tempest_fastapi_sdk.testing` | `test_session`, `test_database`, helpers de SQLite em memória |
| `tempest_fastapi_sdk.utils` | `PasswordUtils`, `JWTUtils`, `EmailUtils`, `UploadUtils`, `MetricsUtils`, `LogUtils`, `AttemptThrottle`, `DownloadUtils`, helpers de documento/telefone BR, helpers de token opaco |
| `tempest_fastapi_sdk.webpush` | `WebPushDispatcher`, `WebPushPayloadSchema`, `WebPushSubscriptionSchema` |

## Início rápido em cinco minutos

```bash
# 1. Instale o SDK com todos os extras
pip install "tempest-fastapi-sdk[all]"

# 2. Gere um novo serviço no diretório atual
tempest new .

# 3. Sincronize as deps + rode o smoke test
uv sync
uv run pytest
```

!!! example "O que o `tempest new` produz"
    ```text
    my-service/
    ├── main.py                 # one-liner que importa run de src.server
    ├── pyproject.toml
    ├── .env.example
    └── src/
        ├── server.py           # entrypoint uvicorn + app no nível do módulo
        ├── api/                # routers, dependencies, factory do app
        ├── controllers/        # orquestração fina sobre os services
        ├── services/           # lógica de negócio
        ├── schemas/            # DTOs de request/response
        ├── db/
        │   ├── models/
        │   └── repositories/
        └── core/               # settings + constants + exceptions
    ```

Continue com **[Instalação »](installation.md)** para o passo a passo por extra, **[Arquitetura »](architecture.md)** para entender o fatiamento em camadas, ou vá direto para o **[Tutorial »](tutorial.md)**.

## Status

| Superfície | Estado |
| --- | --- |
| Python | 3.11 / 3.12 / 3.13 (matriz testada no CI) |
| Testes | 630+ casos de pytest, cobertura ≥ 89 % |
| Type-checking | `mypy --strict`, `py.typed` distribuído (PEP 561) |
| Lint / format | `ruff` (check + fix + format) |
| Pipeline de release | publicação confiável no PyPI a cada tag `vX.Y.Z` |
