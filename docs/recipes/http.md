# HTTP layer

Middlewares, dependencies, routers and middleware composition for the API surface.

## Application bootstrap


[Section 2 of the tutorial](#2-settings-server-app-factory--entry-point) shows the minimal `create_app()`. This recipe is the **extended** version, wiring everything `tempest_fastapi_sdk.api` ships — exception handlers, CORS, request-ID middleware, the health router with extra checks, a shared-secret token dependency and an extra Redis manager — all from the same canonical `src/api/app.py` location. The bootstrapping pattern stays identical; only the contents of `create_app()` grow.

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

- `src/server.py` and `main.py` (one-liner) stay exactly as in [Section 2 of the tutorial](#2-settings-server-app-factory--entry-point) — only `create_app()` changes when you add primitives. Never start uvicorn via `subprocess.run(["uvicorn", ...])`; always import `app` from `src.api.app` or call `uvicorn.run("src.api.app:app", ...)` programmatically from `src/server.py`.
- `RequestIDMiddleware` reads/writes `X-Request-ID` and seeds `request_id_ctx` so every log line emitted during the request carries the correlation ID.
- `apply_cors(app, settings)` reads `CORSSettings` defaults; pass keyword overrides for one-off changes.
- `register_exception_handlers(app)` wires every `AppException` subclass to the canonical `{detail, code, details}` envelope.
- `make_health_router(db=db, checks={"redis": redis.health_check}, version=...)` mounts `GET /health/liveness` and `GET /health/readiness` (returns `503` when any check fails) at the root prefix.
- `make_token_dependency(secret)` returns an async dependency that validates `X-Token` via `hmac.compare_digest`; pass an empty string to disable in dev. The dependency lives next to the rest of the auth glue in `src/api/dependencies/auth.py` once it grows beyond the one-liner above.


## JWT bearer / current-user / role dependencies


Four dependency factories live in `tempest_fastapi_sdk.api.dependencies.auth` — pick the level of abstraction you need.

| Factory | What you get |
| --- | --- |
| `make_token_dependency(secret)` | Validate the `X-Token` shared-secret header (constant time). |
| `make_bearer_token_dependency(tokens, soft=False)` | Decode `Authorization: Bearer <jwt>` and return the claims dict. |
| `make_jwt_user_dependency(tokens, user_loader, soft=False, subject_claim="sub")` | Decode the bearer JWT, await `user_loader(subject)`, return the loaded user. |
| `make_role_dependency(tokens, ["admin"], require_all=False, roles_claim="roles")` / `make_permission_dependency(tokens, ["users:write"], require_all=True, permissions_claim="permissions")` | Decode the bearer JWT and gate the route on roles / permissions. |

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


`RateLimitMiddleware` is a lightweight in-process sliding-window limiter — each unique key (client IP by default) is allowed at most `max_requests` requests inside every `window_seconds` window. Exceeded requests get a `429 Too Many Requests` with a `Retry-After` header.

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

Pass `key_func=` to partition state by tenant header, authenticated user, or any request attribute. The full app factory then looks like:

```python
# src/api/app.py
from fastapi import FastAPI, Request

from tempest_fastapi_sdk import RateLimitMiddleware


def by_tenant(request: Request) -> str:
    """Bucket every request under its tenant header, falling back to IP."""
    return request.headers.get("X-Tenant", request.client.host or "anon")


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

The two highlighted pieces — the `by_tenant` helper and the `key_func=by_tenant` wiring — are the only diff against the default snippet above.

The state is held **in-process** — for multi-worker deployments either run a single uvicorn worker behind a single reverse-proxy node, or push rate limiting to the edge (nginx / Cloudflare / AWS WAF). The middleware is intentionally simple; a Redis-backed sliding-window limiter is one issue away if it shows up as a real need.


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

For providers that sign with an RSA private key (Apple App Store, Google Play, custom enterprise services), swap `WebhookSignatureVerifier` for `RSAWebhookSignatureVerifier` — same `dependency()` / `verify()` surface, but it validates the signature against a PEM-encoded public key (`PKCS1v15` over SHA-256 by default; pass `hash_algorithm="sha512"` or `padding="pss"` to match the provider).

```python
from tempest_fastapi_sdk import RSAWebhookSignatureVerifier

apple = RSAWebhookSignatureVerifier(
    public_key_pem=settings.APPLE_PUBLIC_KEY_PEM,
    header_name="X-Apple-Signature",
    encoding="base64",
    hash_algorithm="sha256",
)
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
    page: BasePaginationSchema[UserResponseSchema] = await controller.list_paginated(filters)
    response.headers["Link"] = build_pagination_link_header(
        str(request.url),
        page=page.page,
        size=page.size,
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

