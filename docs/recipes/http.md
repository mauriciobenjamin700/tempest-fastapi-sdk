# Camada HTTP

Middlewares, dependências, routers e composição de middleware para a superfície da API.

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


## Dependências JWT bearer / usuário atual / role


Quatro factories de dependência vivem em `tempest_fastapi_sdk.api.dependencies.auth` — escolha o nível de abstração que você precisa.

| Factory | O que você ganha |
| --- | --- |
| `make_token_dependency(secret)` | Valida o header de segredo compartilhado `X-Token` (tempo constante). |
| `make_bearer_token_dependency(tokens, soft=False)` | Decodifica `Authorization: Bearer <jwt>` e retorna o dict de claims. |
| `make_jwt_user_dependency(tokens, user_loader, soft=False, subject_claim="sub")` | Decodifica o bearer JWT, aguarda `user_loader(subject)`, retorna o usuário carregado. |
| `make_role_dependency(tokens, ["admin"], require_all=False, roles_claim="roles")` / `make_permission_dependency(tokens, ["users:write"], require_all=True, permissions_claim="permissions")` | Decodifica o bearer JWT e controla a rota por roles / permissões. |

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


`RateLimitMiddleware` é um limitador leve de janela deslizante em processo — cada chave única (IP do cliente por padrão) é permitida no máximo `max_requests` requisições dentro de cada janela `window_seconds`. Requisições que excedem ganham um `429 Too Many Requests` com um header `Retry-After`.

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

Passe `key_func=` para particionar o estado por header de tenant, usuário autenticado ou qualquer atributo da requisição. A factory completa do app então fica:

```python
# src/api/app.py
from fastapi import FastAPI, Request

from tempest_fastapi_sdk import RateLimitMiddleware


def by_tenant(request: Request) -> str:
    """Bucket every request under its tenant header, falling back to IP."""
    return request.headers.get(
        "X-Tenant",
        request.client.host if request.client else "anon",
    )


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=by_tenant,                                  # ← swap the default IP key
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

As duas peças destacadas — o helper `by_tenant` e a conexão `key_func=by_tenant` — são o único diff em relação ao snippet padrão acima.

O estado é mantido **em processo** — para deploys multi-worker, ou rode um único worker uvicorn atrás de um único nó de reverse-proxy, ou empurre o rate limiting para a borda (nginx / Cloudflare / AWS WAF). O middleware é intencionalmente simples; um limitador de janela deslizante apoiado em Redis está a uma issue de distância se surgir como necessidade real.


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

from tempest_fastapi_sdk import EmailUtils, JWTUtils, NotFoundException

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
