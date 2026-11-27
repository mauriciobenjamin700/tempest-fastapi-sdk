# Link de paginação, tool-spec, servidor e settings

Use para emitir o header `Link` de paginação, expor `/tool-spec`, subir o uvicorn com `run_server` e compor settings com mixins.

Parte da receita [Camada HTTP](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http.md).

## Headers Link de paginação


`build_pagination_link_header` emite um header `Link` RFC 8288 com os rels `first` / `prev` / `next` / `last` — combine-o com (ou use no lugar de) o wrapper de corpo `BasePaginationSchema` para clientes REST que esperam headers no estilo GitHub. Os query parameters existentes na URL base são preservados.

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

Ajuste `page_param=` / `size_param=` quando seu serviço usa nomes de query parameter não-padrão (ex.: `offset` / `limit`). Passe `extra_params={"sort": "name"}` para embutir o estado atual de sort/filtro em cada link.


## Router de tool-spec


`make_tool_spec_router(spec)` monta um endpoint `GET /tool-spec` expondo um manifesto legível por máquina no prefixo raiz — pensado para ficar ao lado de `/health/liveness` para que callers externos possam descobrir capacidades sem parsear o documento OpenAPI completo.

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

Passe um dict (servido literalmente), um callable sync (chamado a cada requisição) ou um callable async (aguardado). Sobrescreva `path=` para expor o manifesto em uma URL diferente ou `tag=` para agrupá-lo sob uma tag OpenAPI diferente.


## Ponto de entrada programático do servidor


`run_server` é o helper canônico importado de `src/server.py`. Ele centraliza os defaults de `host` / `port` / `reload` — puxando valores de um objeto `settings` no estilo `ServerSettings` quando presente — e mantém o ponto de entrada em uma única linha.

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

A ordem de resolução de cada kwarg é `argumento explícito → settings.SERVER_* → default do SDK` (`"127.0.0.1"` / `8000` / `False`). Kwargs extras do uvicorn (`workers=`, `log_config=`, `ssl_*=`) são encaminhados literalmente.


## Composição de mixins de settings


`BaseAppSettings` é a base `pydantic-settings` configurada (`env_file=".env"`, `extra="ignore"`, `case_sensitive=True`). O SDK também expõe mixins componíveis para as dependências mais comuns; escolha os que o serviço precisa e componha na sua classe `Settings`.

!!! warning "`BaseAppSettings` deve ser a última base"
    Cada mixin herda `BaseAppSettings`, então todos carregam o mesmo `model_config` — inclusive `env_file=".env"` — e o `.env` é lido independente da ordem em que os mixins aparecem. Mas `BaseAppSettings` **deve** ser a **última** base: como os mixins são subclasses dela, listá-la antes de qualquer mixin viola a linearização C3 do Python e quebra o import. A partir da v0.159.1 a metaclasse `AppSettingsMeta` intercepta isso e levanta `TypeError: Settings: BaseAppSettings must be the LAST base — DatabaseSettings already subclasses it ...`, que já indica a correção; antes dela a mensagem era o `TypeError: Cannot create a consistent method resolution order (MRO)` cru do pydantic. Ponha sempre `BaseAppSettings` no fim das bases — veja o [guia de migração](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration.md).

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

Cada mixin é dono do seu próprio prefixo de env var — escolha só os que o serviço precisa:

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

> **Mudança que quebra na 0.8.0:** `ServerSettings` antes expunha os campos crus `HOST` / `PORT` / `DEBUG` / `LOG_LEVEL` / `LOG_JSON`. Eles foram renomeados para `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` / `SERVER_DEBUG`, e `LOG_LEVEL` / `LOG_JSON` migraram para o novo mixin `LogSettings`. Atualize tanto o seu arquivo `.env` (nomes de env var) quanto qualquer código lendo `settings.HOST` etc.
