# Camada HTTP

Middlewares, dependências, routers e composição de middleware para a superfície da API.

Você vai montar aqui a superfície HTTP inteira de um serviço a partir dos primitivos que o `tempest_fastapi_sdk` entrega — sem escrever middleware, exception handler ou glue de bootstrap na mão. Cada seção é independente: pegue só a que você precisa agora. Esta receita cobre ~11 primitivos:

- **`create_app()` + `register_exception_handlers`** — bootstrap canônico e o envelope de erro padronizado (com i18n opcional via `MessageCatalog`).
- **`RequestIDMiddleware`** — correlação `X-Request-ID` em cada linha de log.
- **`apply_cors`** — CORS a partir de `CORSSettings`.
- **`make_health_router` / `make_token_dependency`** — liveness/readiness + o guarda de segredo compartilhado `X-Token`.
- **Dependências JWT / bearer / role / permission** — controle de rota por token e por papel.
- **`RateLimitMiddleware`** — janela deslizante, chave por IP/usuário/tenant, store em memória ou Redis.
- **`WebhookSignatureVerifier` / `RSAWebhookSignatureVerifier`** — validação de webhooks assinados (HMAC ou RSA).
- **`build_pagination_link_header`** — header `Link` RFC 8288 no estilo GitHub.
- **`make_tool_spec_router`** — manifesto legível por máquina no prefixo raiz.
- **`run_server`** — ponto de entrada programático do uvicorn.
- **`BaseAppSettings` + mixins `*Settings`** — configuração componível por env var.

!!! tip "As três últimas seções são flows completos"
    Autenticação, upload e e-mail transacional aparecem aqui em forma resumida; cada uma tem uma receita dedicada e mais profunda — veja o [Recap](#recap-proximos-passos) no fim da página.

## Bootstrap da aplicação


[A seção 2 do tutorial](../tutorial.md#2-settings-server-factory-do-app-entrypoint) mostra o `create_app()` mínimo. Esta receita é a versão **estendida**, conectando tudo que `tempest_fastapi_sdk.api` entrega — exception handlers, CORS, middleware de request-ID, o health router com checks extras, uma dependência de token de segredo compartilhado e um manager extra de Redis — tudo a partir da mesma localização canônica `src/api/app.py`. O padrão de bootstrap continua idêntico; só o conteúdo de `create_app()` cresce.

```python
# src/api/app.py
from collections.abc import AsyncIterator
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
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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

Pontos-chave:

- `src/server.py` e `main.py` (one-liner) ficam exatamente como na [seção 2 do tutorial](../tutorial.md#2-settings-server-factory-do-app-entrypoint) — só `create_app()` muda quando você adiciona primitivos. Nunca inicie o uvicorn via `subprocess.run(["uvicorn", ...])`; sempre importe `app` de `src.api.app` ou chame `uvicorn.run("src.api.app:app", ...)` programaticamente de `src/server.py`.
- `RequestIDMiddleware` lê/escreve `X-Request-ID` e semeia `request_id_ctx` para que toda linha de log emitida durante a requisição carregue o ID de correlação.
- `apply_cors(app, settings)` lê os defaults de `CORSSettings`; passe overrides nomeados para mudanças pontuais.
- `register_exception_handlers(app)` conecta três handlers, cada um com seu nível de log:
    - `AppException` → envelope `{detail, code, details}` + log `INFO` (4xx) ou `ERROR` + traceback + `500.log` (5xx).
    - `HTTPException` → mantém o body padrão do Starlette (`{"detail"}`) em 4xx com log `INFO`; em 5xx aplica o envelope SDK + traceback + `500.log`.
    - `Exception` (catch-all) → envelope SDK + traceback + `500.log` (corrige o default do Starlette, que devolve só `"Internal Server Error"` sem log).

    Todos os handlers respeitam `RequestIDMiddleware`: a linha de log carrega o `request_id`, e o envelope expõe ele em `details` para correlacionar com o cliente. Passe `log_traceback=False` se um APM (Sentry, OpenTelemetry) já estiver capturando a trace.
- `make_health_router(db=db, checks={"redis": redis.health_check}, version=...)` monta `GET /health/liveness` e `GET /health/readiness` (retorna `503` quando algum check falha) no prefixo raiz.
- `make_token_dependency(secret)` retorna uma dependência async que valida `X-Token` via `hmac.compare_digest`; passe uma string vazia para desabilitar no dev. A dependência vive ao lado do resto da cola de auth em `src/api/dependencies/auth.py` quando crescer além do one-liner acima.


### Mensagens de erro localizadas (i18n)

Por padrão o `detail` do envelope é a mensagem literal da exceção (em inglês nos built-ins). Para devolver a mensagem **no idioma do cliente** sem traduzir em cada `raise`, passe um `MessageCatalog` para `register_exception_handlers`:

```python
# src/api/app.py
from tempest_fastapi_sdk import default_message_catalog, register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(...)
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),                   # ← PT-BR + EN-US embutidos
        default_locale="pt-BR",
    )
    ...
```

O handler negocia o locale a partir do header `Accept-Language` (ordenado por `q`), cai em `default_locale` quando nada casa, e resolve a **chave** da exceção — `message_key` se definido, senão o `code` — contra o catálogo. Sem catálogo, ou quando a chave não existe, mantém o `detail` literal (zero quebra de compatibilidade).

```python
# Mesmo NotFoundException, idioma decidido pelo Accept-Language do cliente:
#   Accept-Language: pt-BR  →  {"detail": "Recurso não encontrado", "code": "NOT_FOUND"}
#   Accept-Language: en-US  →  {"detail": "Resource not found",     "code": "NOT_FOUND"}
```

Para códigos de domínio (e mensagens com parâmetros), estenda o catálogo com `merge` e passe `message_params` no `raise`:

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
        "User not found",                                    # fallback literal
        code="USER_NOT_FOUND",
        message_params={"email": email},
    )
```

!!! tip "A chave segue o `code` por padrão"
    Você raramente passa `message_key` — ele cai no `code` da exceção. Defina `message_key` só quando quiser desacoplar a string traduzida do código de erro. Um template que referencia um parâmetro ausente volta sem interpolar, em vez de estourar.


## Dependências JWT bearer / usuário atual / role


Quatro factories de dependência vivem em `tempest_fastapi_sdk.api.dependencies.auth` — escolha o nível de abstração que você precisa.

| Factory | O que você ganha |
| --- | --- |
| `make_token_dependency(secret)` | Valida o header de segredo compartilhado `X-Token` (tempo constante). |
| `make_bearer_token_dependency(tokens, soft=False)` | Decodifica `Authorization: Bearer <jwt>` e retorna o dict de claims. |
| `make_jwt_user_dependency(tokens, user_loader, soft=False, subject_claim="sub")` | Decodifica o bearer JWT, aguarda `user_loader(subject)`, retorna o usuário carregado. |
| `make_role_dependency(tokens, ["admin"], require_all=False, roles_claim="roles")` / `make_permission_dependency(tokens, ["users:write"], require_all=True, permissions_claim="permissions")` | Decodifica o bearer JWT e controla a rota por roles / permissões. |

!!! tip "Usa o flow bundled? Pule o `load_user`"
    Se você monta auth com `UserAuthService` + `make_auth_router`, não precisa escrever `load_user` nem instanciar um `JWTUtils` aqui — chame `auth_service.current_user_dependency()` (e `.current_user_dependency(soft=True)`), que reusa o `JWTUtils` interno do service. Veja a [receita de auth »](auth-flow.md#pegando-o-current_user-da-requisicao). O exemplo abaixo é a montagem manual, pra quando você **não** usa o service.

```python
# src/api/dependencies/auth.py
from uuid import UUID

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    make_jwt_user_dependency,
    make_permission_dependency,
    make_role_dependency,
)

from src.api.app import db
from src.core.settings import settings
from src.db.models import UserModel
from src.db.repositories import UserRepository


tokens = JWTUtils(
    secret=settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
)


async def load_user(subject: str) -> UserModel:
    """Resolve the JWT subject (a UUID string) to a persisted user."""
    async with db.get_session_context() as session:
        repo = UserRepository(session)
        return await repo.get_by_id(UUID(subject))


require_bearer = make_bearer_token_dependency(tokens)
get_current_user = make_jwt_user_dependency(tokens, load_user)
get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)

require_admin = make_role_dependency(tokens, ["admin"])
require_users_write = make_permission_dependency(tokens, ["users:write"])
```

```python
# src/api/routers/users.py
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import (
    get_current_user,
    require_admin,
    require_users_write,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current)


@router.delete("/{user_id}", dependencies=[Depends(require_admin)])
async def delete_user(user_id: UUID) -> None:
    ...


@router.patch(
    "/{user_id}/permissions",
    dependencies=[Depends(require_users_write)],
)
async def update_perms(user_id: UUID) -> None:
    ...
```

`soft=True` retorna `None` em vez de levantar em tokens ausentes/inválidos — útil para endpoints que funcionam tanto autenticados quanto anônimos. `subject_claim` é `"sub"` por padrão, mas pode ser qualquer claim custom (`"user_id"`, `"uid"`, ...). As dependências de role aceitam uma string ou uma lista de strings no claim do JWT; `require_all=True` exige cada role/permissão listada, `False` (default para roles, sobrescrito para permissões) exige qualquer uma.


## Middleware de rate limit


`RateLimitMiddleware` é um limitador de janela deslizante — cada chave única (IP do cliente por padrão) é permitida no máximo `max_requests` requisições dentro de cada janela `window_seconds`. Requisições que excedem ganham um `429 Too Many Requests` com um header `Retry-After`. Dois eixos são plugáveis: o **store** (memória ou Redis) e a **chave** (IP, usuário, tenant, API key) — veja abaixo.

```python
# src/api/app.py
from tempest_fastapi_sdk import RateLimitMiddleware


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=120,
        window_seconds=60.0,
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    ...
```

### Limite por usuário / tenant / API key

Por padrão a chave é o IP do cliente. Para limitar **por principal** (usuário autenticado, tenant, API key), passe um `key_func`. O SDK traz factories prontas:

| Factory | Chave gerada | Uso |
| --- | --- | --- |
| `key_by_ip(trusted_header=...)` | `ip:<addr>` | Por IP (default). |
| `key_by_jwt_subject(jwt)` | `user:<sub>` | Por usuário autenticado (claim `sub`). |
| `key_by_jwt_claim(jwt, "tenant_id", scope="tenant")` | `tenant:<id>` | Por claim arbitrária do token. |
| `key_by_header("x-api-key", scope="apikey")` | `apikey:<valor>` | Por valor de header. |

!!! warning "O middleware roda antes das dependencies"
    O `RateLimitMiddleware` executa **antes** das `Depends` do FastAPI resolverem — então o usuário autenticado pela sua dependency de auth ainda não existe quando a chave é calculada. Por isso as factories `key_by_jwt_*` decodificam o bearer **do request cru** (via `JWTUtils.decode_or_none`, sem levantar exceção). Tráfego anônimo cai de volta no IP, então continua limitado.

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import RateLimitMiddleware, key_by_jwt_subject

from src.api.dependencies.resources import get_jwt_utils


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=key_by_jwt_subject(get_jwt_utils()),        # ← limite por usuário
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

### Estado distribuído com Redis

O store padrão (`MemoryRateLimitStore`) conta **em processo** — correto para um único worker. Para deploys multi-réplica, passe `store=RedisRateLimitStore(redis)`: cada chave vira um sorted set e um único script Lua poda os expirados, conta e adiciona o novo hit **atomicamente** (sem corrida entre contar e adicionar). Em erro do Redis, `fail_open=True` (default) libera a requisição em vez de derrubar todo mundo.

```python
# src/api/app.py
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RedisRateLimitStore,
    key_by_jwt_subject,
)

from src.api.dependencies.resources import get_jwt_utils


def create_app() -> FastAPI:
    redis: Redis = Redis.from_url("redis://localhost:6379/0")
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=key_by_jwt_subject(get_jwt_utils()),
        store=RedisRateLimitStore(redis),                    # ← compartilhado entre réplicas
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

A semântica de janela deslizante é idêntica nos dois stores; só muda onde os contadores vivem. Ainda dá para empurrar o rate limiting para a borda (nginx / Cloudflare / AWS WAF) quando preferir.


## Verificação de assinatura de webhook


`WebhookSignatureVerifier` valida webhooks de entrada assinados com HMAC (estilo Stripe / GitHub) e expõe uma dependência FastAPI que lê o corpo cru, checa a assinatura com `hmac.compare_digest` e entrega os bytes do corpo para que o handler da rota possa reparsear sem reler o stream.

```python
# src/api/dependencies/webhooks.py
from tempest_fastapi_sdk import WebhookSignatureVerifier

from src.core.settings import settings


github = WebhookSignatureVerifier(
    secret=settings.GITHUB_WEBHOOK_SECRET,
    algorithm="sha256",
    header_name="X-Hub-Signature-256",
    prefix="sha256=",
)
stripe = WebhookSignatureVerifier(
    secret=settings.STRIPE_WEBHOOK_SECRET,
    algorithm="sha256",
    header_name="Stripe-Signature",
    encoding="hex",
)
```

```python
# src/api/routers/webhooks.py
from fastapi import APIRouter, Depends

from src.api.dependencies.webhooks import github

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github")
async def github_event(body: bytes = Depends(github.dependency())) -> None:
    payload = json.loads(body)
    ...
```

Suporta encodings `hex` (default) e `base64`, qualquer algoritmo hashlib garantido entre plataformas, e um `prefix` opcional (ex.: `"sha256="`) removido antes da comparação. Use o imperativo `verifier.verify(body, signature)` de handlers de fila quando a validação acontece fora do pipeline FastAPI.

Para provedores que assinam com uma chave privada RSA (Apple App Store, Google Play, serviços enterprise custom), troque `WebhookSignatureVerifier` por `RSAWebhookSignatureVerifier` — mesma superfície `verify(body, signature)`, mas valida a assinatura contra uma chave pública codificada em PEM. Usa `RSASSA-PKCS1-v1_5` sobre SHA-256/384/512 (configurável via `algorithm=`). Requer o pacote `cryptography` (instalado com o extra `[webpush]`).

```python
from tempest_fastapi_sdk import RSAWebhookSignatureVerifier

apple = RSAWebhookSignatureVerifier(
    public_key_pem=settings.APPLE_PUBLIC_KEY_PEM,
    header_name="X-Apple-Signature",
    algorithm="sha256",
)

# Em handlers de fila / fora do FastAPI:
ok: bool = apple.verify(raw_body_bytes, base64_signature_header_value)
```

### Entrega de webhooks de saída — `WebhookSender`

A contraparte: **enviar** eventos assinados pros seus assinantes.
`WebhookSender` faz POST do evento em JSON, assina o corpo com o
**mesmo** `WebhookSignatureVerifier` (então o receptor valida com aquele
verifier) e re-tenta falhas transitórias (erro de conexão, 5xx, 429) com
backoff exponencial. Outros 4xx **não** são re-tentados. O cliente httpx
é injetado (você é dono do ciclo de vida).

!!! info "Instalação"
    O resto da camada HTTP já vem com `tempest-fastapi-sdk`. O
    `WebhookSender` depende do extra `[http]` —
    `uv add "tempest-fastapi-sdk[http]"` (traz `httpx`).

```python
import httpx

from tempest_fastapi_sdk import WebhookSender, WebhookSignatureVerifier

verifier = WebhookSignatureVerifier(settings.WEBHOOK_SECRET, prefix="sha256=")

async with httpx.AsyncClient() as client:
    sender = WebhookSender(client, signer=verifier, max_attempts=4)
    result = await sender.send(
        "https://assinante.example.com/hooks",
        event="order.paid",
        payload={"id": str(order.id), "total": 4200},
    )
    if not result.delivered:
        # result.status_code / result.attempts / result.error
        ...  # enfileira pra reprocessar, alerta, etc.

# Mesmo evento pra vários assinantes, concorrente:
results = await sender.send_many(
    [(sub.url, {"id": str(order.id)}) for sub in subscribers],
    event="order.paid",
)
```

Cada entrega envia os headers `X-Webhook-Event`, `X-Webhook-Id` (uuid
único) e `X-Webhook-Timestamp`, mais a assinatura HMAC no header do
`signer`. Devolve um `WebhookDelivery` (`delivered`, `status_code`,
`attempts`, `error`, `delivery_id`).

!!! tip "Casa com o outbox"
    Pareie com `BaseOutboxModel` + `OutboxRelay`: grave o evento na mesma
    transação do negócio e deixe o relay chamar o `WebhookSender` —
    entrega ao menos uma vez, com a assinatura que o assinante verifica.


## Headers Link de paginação


`build_pagination_link_header` emite um header `Link` RFC 8288 com os rels `first` / `prev` / `next` / `last` — combine-o com (ou use no lugar de) o wrapper de corpo `BasePaginationSchema` para clientes REST que esperam headers no estilo GitHub. Os query parameters existentes na URL base são preservados.

```python
from fastapi import Request, Response

from tempest_fastapi_sdk import (
    BasePaginationSchema,
    build_pagination_link_header,
)


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
from tempest_fastapi_sdk import (
    make_health_router,
    make_tool_spec_router,
)


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


`BaseAppSettings` é a base `pydantic-settings` configurada. O SDK também expõe mixins componíveis para as dependências mais comuns; escolha os que o serviço precisa e ponha `BaseAppSettings` no **final** da MRO para que seu `model_config` vença.

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
| `LogSettings` | `LOG_LEVEL`, `LOG_JSON` |
| `DatabaseSettings` | `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_RECYCLE` |
| `RedisSettings` | `REDIS_URL`, `REDIS_DECODE_RESPONSES` |
| `RabbitMQSettings` | `RABBITMQ_URL`, `RABBITMQ_PREFETCH_COUNT` |
| `TaskIQSettings` | `TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND_URL` |
| `JWTSettings` | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS`, `JWT_ISSUER` |
| `CORSSettings` | `CORS_ORIGINS`, `CORS_ALLOW_CREDENTIALS`, `CORS_ALLOW_METHODS`, `CORS_ALLOW_HEADERS`, `CORS_EXPOSE_HEADERS`, `CORS_MAX_AGE` |
| `EmailSettings` | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDR`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_TIMEOUT_SECONDS` |
| `UploadSettings` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_ALLOWED_EXTENSIONS`, `UPLOAD_ALLOWED_MIMETYPES` |
| `TokenSettings` | `TOKEN_SECRET` |
| `WebPushSettings` | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `WEBPUSH_DEFAULT_TTL_SECONDS` |

> **Mudança que quebra na 0.8.0:** `ServerSettings` antes expunha os campos crus `HOST` / `PORT` / `DEBUG` / `LOG_LEVEL` / `LOG_JSON`. Eles foram renomeados para `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` / `SERVER_DEBUG`, e `LOG_LEVEL` / `LOG_JSON` migraram para o novo mixin `LogSettings`. Atualize tanto o seu arquivo `.env` (nomes de env var) quanto qualquer código lendo `settings.HOST` etc.


## Autenticação


Signup + login + rota protegida de ponta a ponta usando `PasswordUtils` e `JWTUtils`. Requer o extra `[auth]`.

#### Conecte os singletons utilitários

```python
# src/core/security.py
from datetime import timedelta

from tempest_fastapi_sdk import JWTUtils, PasswordUtils

from src.core.settings import settings


passwords = PasswordUtils(rounds=12)

tokens = JWTUtils(
    secret=settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
    default_ttl=timedelta(seconds=settings.JWT_ACCESS_TTL_SECONDS),
    issuer="my-app",
)
```

#### Signup

Reutilize o `UserService.create` definido no tutorial — ele já faz hash da senha.

#### Login

```python
# src/schemas/auth.py
from pydantic import EmailStr

from tempest_fastapi_sdk import BaseSchema


class LoginSchema(BaseSchema):
    email: EmailStr
    password: str


class TokenResponseSchema(BaseSchema):
    access_token: str
    token_type: str = "bearer"
```

```python
# src/services/auth.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import JWTUtils, PasswordUtils, UnauthorizedException

from src.db.repositories import UserRepository
from src.schemas.auth import LoginSchema, TokenResponseSchema


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        passwords: PasswordUtils,
        tokens: JWTUtils,
    ) -> None:
        self.repo = UserRepository(session)
        self.passwords = passwords
        self.tokens = tokens

    async def login(self, data: LoginSchema) -> TokenResponseSchema:
        user = await self.repo.get_or_none({"email": data.email})
        if user is None or not self.passwords.verify(
            data.password, user.password_hash
        ):
            # Same error for both cases — don't leak which one failed.
            raise UnauthorizedException(message="E-mail ou senha inválidos")
        token = self.tokens.encode({"sub": str(user.id)})
        return TokenResponseSchema(access_token=token)
```

```python
# src/api/routers/auth.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.app import db
from src.core.security import passwords, tokens
from src.schemas.auth import LoginSchema, TokenResponseSchema
from src.services.auth import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(
    session: AsyncSession = Depends(db.session_dependency),
) -> AuthService:
    return AuthService(session, passwords, tokens)


@router.post("/login", response_model=TokenResponseSchema)
async def login(
    data: LoginSchema,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponseSchema:
    return await service.login(data)
```

#### Proteja uma rota — dependência JWT

Use `make_jwt_user_dependency` para conectar o esquema bearer + decode do JWT + carga do usuário em uma chamada. A única costura é `user_loader(subject)`, um callable async que mapeia o claim de subject do JWT para o seu `UserModel` de domínio.

```python
# src/api/dependencies/auth.py
from uuid import UUID

from tempest_fastapi_sdk import make_jwt_user_dependency

from src.api.app import db
from src.core.security import tokens
from src.db.models import UserModel
from src.db.repositories import UserRepository


async def load_user(subject: str) -> UserModel:
    """Resolve the JWT subject (a UUID string) to a persisted user.

    Opens its own session so the dependency stays request-scope-agnostic
    (the loader is called once per request, and SDK exceptions raised
    inside translate to the canonical 401/404 envelope).
    """
    async with db.get_session_context() as session:
        repo = UserRepository(session)
        return await repo.get_by_id(UUID(subject))


get_current_user = make_jwt_user_dependency(tokens, load_user)
get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)
```

```python
# Use in any route
@router.get("/me", response_model=UserResponseSchema)
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current)
```

#### Auth suave (usuário opcional)

`get_current_user_or_none` acima já usa `soft=True` — ele retorna `None` em vez de levantar em um token ausente ou inválido, para que endpoints funcionem tanto autenticados quanto anônimos:

```python
@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> FeedResponseSchema:
    return await feed_service.list(viewer=current)
```

Por baixo dos panos, `soft=True` chama `tokens.decode_or_none` (sem exceção em tokens expirados/inválidos) e pula o loader quando o subject está ausente.

---


## Upload de arquivos


Endpoint de avatar com validação + limpeza. Requer o extra `[upload]`.

```python
# src/core/storage.py
from tempest_fastapi_sdk import UploadUtils

from src.core.settings import settings


avatar_storage = UploadUtils(
    f"{settings.UPLOAD_DIR}/avatars",
    max_size_bytes=5 * 1024 * 1024,            # 5 MiB
    allowed_extensions={"png", "jpg", "jpeg", "webp"},
    allowed_mimetypes={"image/png", "image/jpeg", "image/webp"},
    verify_magic_bytes=True,                   # sniff bytes, reject polyglots
)
```

`verify_magic_bytes=True` lê os primeiros bytes de cada upload e confirma que o arquivo *realmente é* um dos tipos permitidos — um payload HTML+JS enviado como `image/png` é rejeitado mesmo que sua extensão e header `Content-Type` pareçam válidos. Só ative quando todo formato aceito é um que o `sniff_mime` reconhece (JPEG, PNG, GIF, BMP, WebP, PDF); caso contrário, um upload legítimo mas não-snifável seria recusado. Para controle mais fino, passe um predicado `content_validator` para `save()` (`save(file, content_validator=lambda b: sniff_mime(b) in {"image/png"})`), e passe `filename="..."` para um nome determinístico e endereçável (ex.: `f"{user_id}.jpg"`) em vez do UUID padrão.

```python
# src/api/routers/users.py (extension)
from fastapi import UploadFile

from src.api.dependencies import get_user_controller
from src.controllers.user import UserController
from src.core.storage import avatar_storage


@router.post("/{user_id}/avatar", response_model=UserResponseSchema)
async def upload_avatar(
    user_id: UUID,
    file: UploadFile,
    current: UserModel = Depends(get_current_user),
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    if current.id != user_id:
        raise ForbiddenException(message="Só pode editar o próprio avatar")
    path = await avatar_storage.save(file, subdir=str(user_id))
    return await controller.set_avatar(user_id, str(path))
```

Adicione `set_avatar` tanto ao service quanto ao controller (o controller fica como um pass-through fino a menos que orquestração seja necessária — ex.: disparar um evento de "avatar atualizado"):

```python
# src/services/user.py
class UserService:
    async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
        user = await self.repo.get_by_id(user_id)
        # Delete previous file when replacing.
        if user.avatar_path and user.avatar_path != path:
            await avatar_storage.delete(user.avatar_path)
        user.avatar_path = path
        user = await self.repo.update(user)
        return self.repo.map_to_response(user)


# src/controllers/user.py
class UserController:
    async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
        return await self.service.set_avatar(user_id, path)
```

`UploadUtils.save()` levanta `FileTooLargeException` (413) ou `InvalidFileTypeException` (415) na rejeição — o exception handler do SDK já retorna o status code certo com um campo `code` na resposta.

#### Servindo o arquivo de volta

Uploads em disco local são melhor servidos por um upstream (nginx / Caddy) para que o FastAPI não fique transmitindo bytes. Para dev:

```python
from fastapi.staticfiles import StaticFiles

app.mount(
    "/static/uploads",
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="uploads",
)
```

Construa a URL pública no schema de resposta:

```python
class UserResponseSchema(BaseResponseSchema):
    name: str
    email: EmailStr
    avatar_url: str | None = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _absolute_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # avatar_path stored as relative path → public URL
        return f"/static/uploads/{value}"
```

#### Servindo arquivos privados pela API (`DownloadUtils`)

Quando um arquivo deve ficar **atrás de auth** — faturas, contratos, exames médicos — uma URL pública `/static` o vaza para qualquer um que descubra o caminho. `DownloadUtils` transmite os bytes pelo próprio endpoint, para que os mesmos `Depends(get_current_user)` / checks de permissão que guardam toda outra rota guardem o download também. Nenhum link público é exposto. Não precisa de **nenhum extra** (usa `FileResponse` / `StreamingResponse` do Starlette, que vêm com o FastAPI).

```python
# src/core/storage.py
from tempest_fastapi_sdk import DownloadUtils

from src.core.settings import settings


invoice_files = DownloadUtils(f"{settings.UPLOAD_DIR}/invoices")
```

```python
# src/api/routers/invoices.py
from fastapi.responses import FileResponse

from src.api.dependencies import get_invoice_controller
from src.controllers.invoice import InvoiceController
from src.core.storage import invoice_files


@router.get("/{invoice_id}/file")
async def download_invoice(
    invoice_id: UUID,
    current: UserModel = Depends(get_current_user),
    controller: InvoiceController = Depends(get_invoice_controller),
) -> FileResponse:
    invoice = await controller.get_by_id(invoice_id)
    if invoice.owner_id != current.id:
        raise ForbiddenException(message="Fatura de outro usuário")
    # base_dir confines the read — a stored "../../etc/passwd" path 404s.
    return invoice_files.file_response(
        invoice.file_path,                 # relative to base_dir
        filename=f"fatura-{invoice.number}.pdf",
        as_attachment=True,                # force a download dialog
    )
```

Qualquer caminho relativo que escape de `base_dir` (traversal `../`, caminhos absolutos, escapes via symlink) levanta `NotFoundException` (404) em vez de vazar o arquivo — o mesmo 404 que você ganha para um arquivo genuinamente ausente, então callers nunca distinguem "proibido" de "ausente". `file_response` adivinha o tipo MIME pelo nome do arquivo (sobrescreva com `media_type=`), e `as_attachment=False` serve **inline** (ex.: pré-visualizar um PDF no navegador).

Para payloads construídos na hora — um relatório gerado, um zip em memória, bytes descriptografados — use `stream()` em vez de tocar o disco:

```python
import io

from fastapi.responses import StreamingResponse

from src.core.storage import invoice_files


@router.get("/{invoice_id}/receipt.csv")
async def download_receipt(
    invoice_id: UUID,
    current: UserModel = Depends(get_current_user),
    controller: InvoiceController = Depends(get_invoice_controller),
) -> StreamingResponse:
    csv_bytes: bytes = await controller.render_receipt_csv(invoice_id, current.id)
    return invoice_files.stream(
        csv_bytes,                         # bytes, or a (sync/async) byte generator
        filename="recibo.csv",
    )
```

`stream()` aceita `bytes` cru, um `Iterable[bytes]` sync ou um `AsyncIterable[bytes]`, então um export grande pode ser entregue pedaço a pedaço sem bufferizar tudo em memória. Ambos os métodos definem um `Content-Disposition` seguro em UTF-8 (nomes de arquivo não-ASCII sobrevivem via o parâmetro `filename*` da RFC 5987); `build_content_disposition()` é exportado se você precisar definir esse header em uma resposta feita à mão.

---


## E-mail transacional


Fluxo de reset de senha usando `EmailUtils` + um JWT de vida curta. Requer o extra `[email]`.

```python
# src/core/mailer.py
from tempest_fastapi_sdk import EmailUtils

from src.core.settings import settings


mailer = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    from_addr=settings.SMTP_FROM_ADDR,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    use_starttls=True,
)
```

```python
# src/services/password_reset.py
from datetime import timedelta
from uuid import UUID

from tempest_fastapi_sdk import (
    EmailUtils,
    InvalidTokenException,
    JWTUtils,
    PasswordUtils,
)

from src.db.repositories import UserRepository


class PasswordResetService:
    def __init__(
        self,
        repo: UserRepository,
        tokens: JWTUtils,
        mailer: EmailUtils,
    ) -> None:
        self.repo = repo
        self.tokens = tokens
        self.mailer = mailer

    async def request_reset(self, email: str) -> None:
        """Send a password-reset link to `email`.

        Always returns silently — don't reveal whether the email
        is registered or not (avoids account enumeration).
        """
        user = await self.repo.get_or_none({"email": email})
        if user is None:
            return
        token = self.tokens.encode(
            {"sub": str(user.id), "purpose": "password_reset"},
            ttl=timedelta(minutes=15),
        )
        reset_url = f"https://my-app.com/reset-password?token={token}"
        await self.mailer.send(
            to=user.email,
            subject="Reset your password",
            body=f"Click here to reset your password: {reset_url}",
            html=f'<p>Click <a href="{reset_url}">here</a> to reset.</p>',
        )

    async def consume_reset(
        self,
        token: str,
        new_password: str,
        passwords: PasswordUtils,
    ) -> None:
        # `decode` raises InvalidTokenException / ExpiredTokenException
        # (both 401). Caught by the SDK handler.
        payload = self.tokens.decode(token)
        if payload.get("purpose") != "password_reset":
            raise InvalidTokenException()
        user = await self.repo.get_by_id(UUID(payload["sub"]))
        user.password_hash = passwords.hash(new_password)
        await self.repo.update(user)
```

---

## Recap / próximos passos

Você agora conhece a superfície HTTP inteira: bootstrap do app, exception handlers com i18n, dependências de auth, rate limit, verificação de webhook, headers de paginação, tool-spec, o ponto de entrada do servidor e a composição de settings. As três últimas seções (autenticação, upload, e-mail) foram um resumo — cada uma tem uma receita dedicada que vai mais fundo:

- [Fluxo de autenticação »](auth-flow.md) — o flow bundled completo (`UserAuthService` + `make_auth_router`): signup, ativação, login, reset de senha, refresh tokens e `current_user`.
- [Upload de arquivos »](uploads.md) — backends de storage plugáveis (`LocalUploadStorage` / `MinIOUploadStorage`), URLs pré-assinadas e validação de conteúdo.
- [E-mail transacional »](email.md) — `EmailUtils` com templates Jinja2, os defaults embutidos (`activation.html`, `password_reset.html`) e como sobrescrevê-los.
- [Downloads privados »](downloads.md) — servir arquivos atrás de auth com `DownloadUtils` (`file_response` / `stream`), sem vazar links públicos.
