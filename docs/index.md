# tempest-fastapi-sdk

> Blocos compartilhados de FastAPI / SQLAlchemy / Pydantic usados em todos os serviços backend do Tempest. **Comece todo projeto com a mesma fundação opinativa já pronta.**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![PyPI](https://img.shields.io/pypi/v/tempest-fastapi-sdk)

---

## O que você ganha de fábrica

!!! tip "Use o SDK sempre que for copiar e colar isto"
    `BaseModel` SQLAlchemy, `BaseUserModel` + `BaseUserTokenModel` abstratos, `BaseRepository` async com `bulk_create_values`/`bulk_upsert`, schemas Pydantic + paginação (offset + cursor), envelope de exceções + handlers, mixins de settings com `title`/`description`/`examples`, managers async de DB / Redis / RabbitMQ / TaskIQ, validadores brasileiros, utilitários de JWT / senha / e-mail (com templates Jinja2) / upload (com backends pluggable Local + MinIO), Server-Sent Events, Web Push, painel admin estilo Django, fluxo de auth bundled (signup / activate / login / reset), OAuth2/OIDC (Google/GitHub + genérico), middlewares de CSRF / Idempotency / BodySize / Prometheus / RateLimit, `HTTPClient` httpx tipado com retry + circuit-breaker, MinIO/S3 (`AsyncMinIOClient`), hook de Alembic que reordena colunas-base e uma CLI única (`tempest db`, `tempest user`, `tempest new`, `tempest generate`).

| Módulo | Exporta |
| --- | --- |
| `tempest_fastapi_sdk.admin` | `AdminSite`, `AdminModel`, `make_admin_router`, `UserModelAuthBackend` |
| `tempest_fastapi_sdk.api` | `register_exception_handlers`, `apply_cors`, `RequestIDMiddleware`, `IdempotencyMiddleware`, `BodySizeLimitMiddleware`, `CSRFMiddleware`, `PrometheusMiddleware`, `make_health_router`, `make_logs_router`, `make_prometheus_router`, OAuth (`GoogleOAuthClient` / `GitHubOAuthClient` / `OIDCProvider`), dependências JWT/role/permissão, `HardenedStaticFiles`, `RateLimitMiddleware`, `WebhookSignatureVerifier`, `run_server` |
| `tempest_fastapi_sdk.auth` | `UserAuthService`, `make_auth_router`, schemas (`SignupSchema`, `LoginSchema`, `PasswordResetRequestSchema`, …) |
| `tempest_fastapi_sdk.cache` | `AsyncRedisManager`, `@cached` |
| `tempest_fastapi_sdk.controllers` | `BaseController` |
| `tempest_fastapi_sdk.core` | `JSONFormatter`, `configure_logging`, contexto de request-ID, `BaseStrEnum` / `BaseIntEnum` |
| `tempest_fastapi_sdk.db` | `BaseModel`, `BaseUserModel`, `BaseUserTokenModel`, `UserTokenPurpose`, `BaseRepository` (com `bulk_*`), `AsyncDatabaseManager`, `AlembicHelper`, `AuditMixin`, `SoftDeleteMixin`, `reorder_base_columns_first`, `compose_hooks` |
| `tempest_fastapi_sdk.exceptions` | hierarquia `AppException` (404 / 409 / 401 / 403 / 422 / 429 / arquivo grande demais / tipo de arquivo inválido / JWT) |
| `tempest_fastapi_sdk.queue` | `MessageBroker` (fachada tipada recomendada), `AsyncQueueManager` / `AsyncBrokerManager` (wrappers de ciclo de vida FastStream / RabbitMQ) |
| `tempest_fastapi_sdk.schemas` | `BaseSchema`, `BaseResponseSchema`, `BasePaginationFilterSchema`, `BasePaginationSchema`, paginação por cursor |
| `tempest_fastapi_sdk.services` | `BaseService` |
| `tempest_fastapi_sdk.settings` | `BaseAppSettings`, `ServerSettings`, `DatabaseSettings`, `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `AuthSettings`, `CORSSettings`, `EmailSettings`, `LogSettings`, `TokenSettings`, `UploadSettings`, `WebPushSettings`, `TaskIQSettings` |
| `tempest_fastapi_sdk.sse` | `EventStream`, `ServerSentEvent`, `sse_response`, `SSEBroker` |
| `tempest_fastapi_sdk.storage` | `AsyncMinIOClient`, `ObjectStat` |
| `tempest_fastapi_sdk.tasks` | `TaskQueue` (fachada tipada recomendada), `AsyncTaskBrokerManager`, `AsyncTaskScheduler` |
| `tempest_fastapi_sdk.testing` | `test_session`, `test_database`, helpers de SQLite em memória |
| `tempest_fastapi_sdk.utils` | `PasswordUtils`, `JWTUtils`, `EmailUtils` (com `render_template`), `UploadUtils`, `LocalUploadStorage`, `MinIOUploadStorage`, `HTTPClient`, `RetryPolicy`, `MetricsUtils`, `LogUtils`, `AttemptThrottle`, `DownloadUtils`, helpers BR, helpers de token opaco |
| `tempest_fastapi_sdk.webpush` | `WebPushDispatcher`, `WebPushPayloadSchema`, `WebPushSubscriptionSchema` |

## Início rápido em cinco minutos

```bash
# 1. Instale o CLI `tempest` (com todos os extras) via uv
uv tool install "tempest-fastapi-sdk[all]"

# 2. Gere um novo serviço no diretório atual
tempest new .

# 3. Sincronize as deps do projeto gerado + rode o smoke test
uv sync
uv run pytest
```

!!! note "Do CLI ao projeto"
    O passo 1 instala o **CLI** `tempest` num ambiente próprio (via `uv tool`). O `tempest new` gera um projeto com o **seu próprio `pyproject.toml`**; do passo 3 em diante é o `uv` desse projeto que resolve e roda tudo (`uv sync` cria o `.venv` local a partir das deps geradas). Prefira `uv` de ponta a ponta — misturar `pip install` global com o `uv sync` do projeto resolve dois ambientes diferentes.

!!! note "Sobre o `[all]`"
    O extra `[all]` traz todos os helpers **exceto** os stacks pesados de **modelos locais** de GenAI (`[genai]`, `[genai-quant]`, `[genai-rag]`, `[genai-audio]`) — instale esses à parte quando precisar. Os clients leves de GenAI (Ollama, Chroma) já vêm no `[all]`. Veja **[Instalação »](installation.md)** para a tabela completa de extras.

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
