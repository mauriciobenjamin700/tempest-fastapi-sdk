# HTTP layer

Middlewares, dependencies, routers and middleware composition for the API surface.

## Application bootstrap


[Section 2 of the tutorial](../tutorial.md#2-settings-server-app-factory-entry-point) shows the minimal `create_app()`. This recipe is the **extended** version, wiring everything `tempest_fastapi_sdk.api` ships — exception handlers, CORS, request-ID middleware, the health router with extra checks, a shared-secret token dependency and an extra Redis manager — all from the same canonical `src/api/app.py` location. The bootstrapping pattern stays identical; only the contents of `create_app()` grow.

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

Key points:

- `src/server.py` and `main.py` (one-liner) stay exactly as in [Section 2 of the tutorial](../tutorial.md#2-settings-server-app-factory-entry-point) — only `create_app()` changes when you add primitives. Never start uvicorn via `subprocess.run(["uvicorn", ...])`; always import `app` from `src.api.app` or call `uvicorn.run("src.api.app:app", ...)` programmatically from `src/server.py`.
- `RequestIDMiddleware` reads/writes `X-Request-ID` and seeds `request_id_ctx` so every log line emitted during the request carries the correlation ID.
- `apply_cors(app, settings)` reads `CORSSettings` defaults; pass keyword overrides for one-off changes.
- `register_exception_handlers(app)` wires three handlers, each with its own log level:
    - `AppException` → `{detail, code, details}` envelope + `INFO` log (4xx) or `ERROR` + traceback + `500.log` (5xx).
    - `HTTPException` → keeps Starlette's default body (`{"detail"}`) on 4xx with an `INFO` log; on 5xx swaps in the SDK envelope + traceback + `500.log`.
    - `Exception` (catch-all) → SDK envelope + traceback + `500.log` (fixes Starlette's default, which returns only `"Internal Server Error"` with no log entry).

    Every handler honors `RequestIDMiddleware`: the log line carries the `request_id`, and the envelope exposes it under `details` so the client can correlate. Pass `log_traceback=False` when an APM (Sentry, OpenTelemetry) is already capturing the stack trace.
- `make_health_router(db=db, checks={"redis": redis.health_check}, version=...)` mounts `GET /health/liveness` and `GET /health/readiness` (returns `503` when any check fails) at the root prefix.
- `make_token_dependency(secret)` returns an async dependency that validates `X-Token` via `hmac.compare_digest`; pass an empty string to disable in dev. The dependency lives next to the rest of the auth glue in `src/api/dependencies/auth.py` once it grows beyond the one-liner above.


### Localized error messages (i18n)

By default the envelope `detail` is the exception's literal message (English for the built-ins). To return the message **in the client's language** without translating at each `raise`, pass a `MessageCatalog` to `register_exception_handlers`:

```python
# src/api/app.py
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


## JWT bearer / current-user / role dependencies


Four dependency factories live in `tempest_fastapi_sdk.api.dependencies.auth` — pick the level of abstraction you need.

| Factory | What you get |
| --- | --- |
| `make_token_dependency(secret)` | Validate the `X-Token` shared-secret header (constant time). |
| `make_bearer_token_dependency(tokens, soft=False)` | Decode `Authorization: Bearer <jwt>` and return the claims dict. |
| `make_jwt_user_dependency(tokens, user_loader, soft=False, subject_claim="sub")` | Decode the bearer JWT, await `user_loader(subject)`, return the loaded user. |
| `make_role_dependency(tokens, ["admin"], require_all=False, roles_claim="roles")` / `make_permission_dependency(tokens, ["users:write"], require_all=True, permissions_claim="permissions")` | Decode the bearer JWT and gate the route on roles / permissions. |

!!! tip "Using the bundled flow? Skip `load_user`"
    If you wire auth with `UserAuthService` + `make_auth_router`, you don't write `load_user` or instantiate a `JWTUtils` here — call `auth_service.current_user_dependency()` (and `.current_user_dependency(soft=True)`), which reuses the service's internal `JWTUtils`. See the [auth recipe »](auth-flow.en.md#getting-the-current_user-from-the-request). The example below is the manual wiring, for when you're **not** using the service.

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

`soft=True` returns `None` instead of raising on missing/invalid tokens — useful for endpoints that work both authenticated and anonymous. `subject_claim` defaults to `"sub"` but can be any custom claim (`"user_id"`, `"uid"`, ...). Role dependencies accept either a string or a list of strings on the JWT claim; `require_all=True` requires every listed role/permission, `False` (default for roles, override for permissions) requires any.


## Rate limit middleware


`RateLimitMiddleware` is a sliding-window limiter — each unique key (client IP by default) is allowed at most `max_requests` requests inside every `window_seconds` window. Exceeded requests get a `429 Too Many Requests` with a `Retry-After` header. Two axes are pluggable: the **store** (memory or Redis) and the **key** (IP, user, tenant, API key) — see below.

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

### Limit per user / tenant / API key

By default the key is the client IP. To limit **per principal** (authenticated user, tenant, API key), pass a `key_func`. The SDK ships ready-made factories:

| Factory | Key produced | Use |
| --- | --- | --- |
| `key_by_ip(trusted_header=...)` | `ip:<addr>` | Per IP (default). |
| `key_by_jwt_subject(jwt)` | `user:<sub>` | Per authenticated user (`sub` claim). |
| `key_by_jwt_claim(jwt, "tenant_id", scope="tenant")` | `tenant:<id>` | Per arbitrary token claim. |
| `key_by_header("x-api-key", scope="apikey")` | `apikey:<value>` | Per header value. |

!!! warning "The middleware runs before dependencies"
    `RateLimitMiddleware` runs **before** FastAPI `Depends` resolve — so the user authenticated by your auth dependency does not exist yet when the key is computed. That is why the `key_by_jwt_*` factories decode the bearer **from the raw request** (via `JWTUtils.decode_or_none`, no exception raised). Anonymous traffic falls back to the IP, so it stays limited.

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
        key_func=key_by_jwt_subject(get_jwt_utils()),        # ← limit per user
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

### Distributed state with Redis

The default store (`MemoryRateLimitStore`) counts **in-process** — correct for a single worker. For multi-replica deployments, pass `store=RedisRateLimitStore(redis)`: each key becomes a sorted set and a single Lua script prunes expired members, counts, and adds the new hit **atomically** (no race between count and add). On a Redis error, `fail_open=True` (default) allows the request rather than locking everyone out.

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
        store=RedisRateLimitStore(redis),                    # ← shared across replicas
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

The sliding-window semantics are identical across both stores; only where the counters live changes. You can still push rate limiting to the edge (nginx / Cloudflare / AWS WAF) when you prefer.


## Webhook signature verification


`WebhookSignatureVerifier` validates HMAC-signed inbound webhooks (Stripe / GitHub style) and exposes a FastAPI dependency that reads the raw body, checks the signature with `hmac.compare_digest`, and yields the body bytes so the route handler can re-parse it without re-reading the stream.

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

Supports `hex` (default) and `base64` encodings, any hashlib algorithm guaranteed across platforms, and an optional `prefix` (e.g. `"sha256="`) stripped before comparison. Use the imperative `verifier.verify(body, signature)` from queue handlers when validation happens outside the FastAPI pipeline.

For providers that sign with an RSA private key (Apple App Store, Google Play, custom enterprise services), swap `WebhookSignatureVerifier` for `RSAWebhookSignatureVerifier` — same `verify(body, signature)` surface, but it validates the signature against a PEM-encoded public key. Uses `RSASSA-PKCS1-v1_5` over SHA-256/384/512 (configurable via `algorithm=`). Requires the `cryptography` package (installed by the `[webpush]` extra).

```python
from tempest_fastapi_sdk import RSAWebhookSignatureVerifier

apple = RSAWebhookSignatureVerifier(
    public_key_pem=settings.APPLE_PUBLIC_KEY_PEM,
    header_name="X-Apple-Signature",
    algorithm="sha256",
)

# From queue handlers / outside FastAPI:
ok: bool = apple.verify(raw_body_bytes, base64_signature_header_value)
```


## Pagination Link headers


`build_pagination_link_header` emits an RFC 8288 `Link` header with `first` / `prev` / `next` / `last` rels — pair it with (or use instead of) the `BasePaginationSchema` body wrapper for REST clients that expect GitHub-style headers. Existing query parameters on the base URL are preserved.

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

Tweak `page_param=` / `size_param=` when your service uses non-standard query parameter names (e.g. `offset` / `limit`). Pass `extra_params={"sort": "name"}` to bake the current sort/filter state into every link.


## Tool-spec router


`make_tool_spec_router(spec)` mounts a `GET /tool-spec` endpoint exposing a machine-readable manifest at the root prefix — meant to sit alongside `/health/liveness` so external callers can discover capabilities without parsing the full OpenAPI document.

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


`BaseAppSettings` is the configured `pydantic-settings` base. The SDK also exposes composable mixins for the most common dependencies; pick the ones the service needs and put `BaseAppSettings` at the **end** of the MRO so its `model_config` wins.

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

> **Breaking change in 0.8.0:** `ServerSettings` previously exposed bare `HOST` / `PORT` / `DEBUG` / `LOG_LEVEL` / `LOG_JSON` fields. They were renamed to `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` / `SERVER_DEBUG`, and `LOG_LEVEL` / `LOG_JSON` moved to the new `LogSettings` mixin. Update both your `.env` file (env var names) and any code reading `settings.HOST` etc.


## Authentication


End-to-end signup + login + protected route using `PasswordUtils` and `JWTUtils`. Requires the `[auth]` extra.

#### Wire the utility singletons

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

Reuse the `UserService.create` defined in the tutorial — it already hashes the password.

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

#### Protect a route — JWT dependency

Use `make_jwt_user_dependency` to wire the bearer scheme + JWT decode + user load in one call. The single seam is `user_loader(subject)`, an async callable that maps the JWT subject claim to your domain `UserModel`.

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

#### Soft auth (optional user)

`get_current_user_or_none` above already uses `soft=True` — it returns `None` instead of raising on a missing or invalid token, so endpoints can work both authenticated and anonymous:

```python
@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> FeedResponseSchema:
    return await feed_service.list(viewer=current)
```

Under the hood `soft=True` calls `tokens.decode_or_none` (no exception on expired/invalid tokens) and skips the loader when the subject is missing.

---


## File uploads


Avatar endpoint with validation + cleanup. Requires the `[upload]` extra.

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

`verify_magic_bytes=True` reads the first bytes of each upload and confirms the file *really is* one of the allowed types — an HTML+JS payload sent as `image/png` is rejected even though its extension and `Content-Type` header look valid. Only enable it when every accepted format is one `sniff_mime` recognizes (JPEG, PNG, GIF, BMP, WebP, PDF); otherwise a legitimate but unsniffable upload would be refused. For finer control, pass a `content_validator` predicate to `save()` (`save(file, content_validator=lambda b: sniff_mime(b) in {"image/png"})`), and pass `filename="..."` for a deterministic, addressable name (e.g. `f"{user_id}.jpg"`) instead of the default UUID.

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

Add `set_avatar` to both the service and the controller (the controller stays a thin pass-through unless orchestration is needed — e.g. firing an "avatar updated" event):

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

`UploadUtils.save()` raises `FileTooLargeException` (413) or `InvalidFileTypeException` (415) on rejection — the SDK's exception handler already returns the right status code with a `code` field on the response.

#### Serving the file back

Local-disk uploads are best served by an upstream (nginx / Caddy) so FastAPI doesn't stream bytes. For dev:

```python
from fastapi.staticfiles import StaticFiles

app.mount(
    "/static/uploads",
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="uploads",
)
```

Construct the public URL in the response schema:

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

#### Serving private files through the API (`DownloadUtils`)

When a file must stay **behind auth** — invoices, contracts, medical scans — a public `/static` URL leaks it to anyone who learns the path. `DownloadUtils` streams the bytes through the endpoint itself, so the same `Depends(get_current_user)` / permission checks that guard every other route guard the download too. No public link is ever exposed. It needs **no extra** (uses Starlette's `FileResponse` / `StreamingResponse`, which ship with FastAPI).

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

Any relative path that escapes `base_dir` (`../` traversal, absolute paths, symlink escapes) raises `NotFoundException` (404) instead of leaking the file — the same 404 you get for a genuinely missing file, so callers never distinguish "forbidden" from "absent". `file_response` guesses the MIME type from the filename (override with `media_type=`), and `as_attachment=False` serves **inline** (e.g. preview a PDF in-browser).

For payloads built on the fly — a generated report, an in-memory zip, decrypted bytes — use `stream()` instead of touching disk:

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

`stream()` accepts raw `bytes`, a sync `Iterable[bytes]`, or an `AsyncIterable[bytes]`, so a large export can be yielded chunk-by-chunk without buffering it all in memory. Both methods set a UTF-8-safe `Content-Disposition` (non-ASCII filenames survive via the RFC 5987 `filename*` parameter); `build_content_disposition()` is exported if you need to set that header on a hand-rolled response.

---


## Transactional email


Password reset flow using `EmailUtils` + a short-lived JWT. Requires the `[email]` extra.

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

