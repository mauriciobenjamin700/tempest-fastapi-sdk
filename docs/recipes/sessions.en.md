# Server-side sessions

Since v0.34.0 the SDK ships the full **server-side session** auth lifecycle — an alternative to the JWT flow from `UserAuthService`. The cookie carries only an opaque id; real state (user_id, TTL, client metadata, app-level data) lives in a pluggable **`SessionStore`** (Memory for dev/tests, Redis for production).

## JWT vs server-side sessions

| Aspect | JWT (`UserAuthService`) | Sessions (`SessionAuth`) |
|---|---|---|
| State | stateless (in client) | stateful (in Redis/Memory) |
| Cookie size | ~500 B – 1 KB (JWT) | 64 B (opaque id) |
| Revocation | wait for token to expire (~1h typical) | **instant** (delete the row) |
| Global logout | needs a blocklist or JWT_SECRET rotation | `revoke_all(user_id)` in one call |
| CSRF | needs custom bearer header | HttpOnly cookie + native double-submit token |
| Multi-device UI ("signed in on 3 devices") | no state → impossible without extra work | `list_sessions(user_id)` is trivial |
| Multi-replica | trivial (verify-only) | requires Redis (or sticky sessions) |
| Per-request latency | none (CPU decode) | 1 Redis hit (~0.5ms LAN) |

**Use sessions when:** B2C SaaS, admin panels, SSR flows (HTMX/Django-like), instant revocation is a requirement, "active devices" UI is a feature.

**Use JWT when:** public APIs consumed by mobile/SPA, stateless microservices, high scale without a Redis dependency.

## Recipe contents

1. **[Minimum setup](#minimum-setup)** — wire 4 objects (`SessionStore`, `SessionAuth`, `SessionMiddleware`, `make_session_router`).
2. **[Bundled endpoints](#endpoints)** — login / logout / me / list / revoke.
3. **[Settings (`SessionSettings`)](#settings)** — flags + defaults.
4. **[Stores](#stores)** — `MemorySessionStore` vs `RedisSessionStore`.
5. **[How the middleware injects the session](#middleware)** — `request.state.session` + dependency.
6. **[Security](#security)** — anti-fixation rotation, hash-at-rest, anti-enumeration, CSRF.
7. **[Trade-offs and when NOT to use](#trade-offs)** — multi-replica, mobile, edge.

---

## Minimum setup

Four objects compose the flow. Mount once in `app.py`:

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RedisSessionStore,
    SessionAuth,
    SessionMiddleware,
    SessionSettings,
    make_session_router,
    register_exception_handlers,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings
from src.db.models import UserModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
cache = AsyncRedisManager(settings.REDIS_URL)
session_settings = SessionSettings()

session_store = RedisSessionStore(cache.client, prefix=f"{settings.APP_NAME}:")
session_auth = SessionAuth(
    user_model=UserModel,
    store=session_store,
    settings=session_settings,
)


def create_app() -> FastAPI:
    app = FastAPI(title="my-app")
    register_exception_handlers(app)

    # Order matters: middleware BEFORE the routers.
    app.add_middleware(
        SessionMiddleware,
        session_auth=session_auth,
        settings=session_settings,
    )

    app.include_router(
        make_session_router(
            session_auth,
            session_factory=db.session_dependency,
        )
    )
    return app


app = create_app()
```

Done. The user calls `POST /auth/session/login` with email+password; the SDK sets the HttpOnly+Secure cookie; every subsequent request that carries the cookie has `request.state.session` populated.

---

## Endpoints

Five bundled endpoints cover the entire lifecycle:

| Method | Path | Body / Output | Behavior |
|---|---|---|---|
| POST | `/auth/session/login` | `SessionLoginSchema` → `SessionResponseSchema` | Verifies bcrypt. Mints a new session. Sets `Set-Cookie: tempest_session=<id>; HttpOnly; Secure; SameSite=Lax`. When a previous cookie exists, **rotates** it (anti-fixation). |
| POST | `/auth/session/logout` | — → `204 No Content` | Revokes the current session and clears the cookie. Idempotent. |
| GET | `/auth/session/me` | — → `Session` | Returns the live session (`user_id`, timestamps, ip, user_agent, data). `401` when no cookie. |
| GET | `/auth/session/list` | — → `list[SessionSummarySchema]` | Lists every live session the user owns ("active devices" UI). Flags the current row with `is_current=True`. |
| DELETE | `/auth/session/{id}` | — → `204 No Content` | Revokes one specific session by its public id (first 32 chars of the hash). Clearing the cookie too when the user revokes their own session. |

---

## Settings

Mix `SessionSettings` into your `Settings`:

```python
from tempest_fastapi_sdk import BaseAppSettings, SessionSettings


class Settings(SessionSettings, BaseAppSettings):
    pass
```

```bash
# .env
SESSION_TTL_SECONDS=86400              # 24h (default)
SESSION_SLIDING=true                   # refresh expires_at on every hit (default)
SESSION_COOKIE_NAME=tempest_session
SESSION_COOKIE_DOMAIN=                 # None = exact host
SESSION_COOKIE_PATH=/
SESSION_COOKIE_SECURE=true             # HTTPS only — set false only for local HTTP dev
SESSION_COOKIE_HTTPONLY=true           # JavaScript cannot read — always true
SESSION_COOKIE_SAMESITE=lax            # lax / strict / none
SESSION_ROTATE_ON_LOGIN=true           # anti-fixation
```

---

## Stores

### `MemorySessionStore` — dev/tests

```python
from tempest_fastapi_sdk import MemorySessionStore

session_store = MemorySessionStore()
```

State lives in the process dict. **Does not scale** — uvicorn restart wipes everything; one replica does not see another's sessions. Use in tests and local dev.

### `RedisSessionStore` — production

```python
from tempest_fastapi_sdk import RedisSessionStore
from tempest_fastapi_sdk.cache import AsyncRedisManager

cache = AsyncRedisManager(settings.REDIS_URL)
session_store = RedisSessionStore(cache.client, prefix="myapp:")
```

Internal schema:

- `myapp:sess:<sha256-hex>` — JSON of the `Session`, TTL = `expires_at - now`
- `myapp:user:<user-uuid>` — Redis SET of session hashes (index for `list_by_user` / `delete_by_user`)

Redis handles TTL automatically — no janitor process needed.

**Requires the `[cache]` extra** (`redis` async client).

### Custom

Any class that implements the `SessionStore` protocol (5 async methods) plugs in out of the box — DynamoDB, a Postgres table, Memcached, etc.

---

## Middleware

`SessionMiddleware` runs **before** the routers, reads the cookie, resolves through the store, and populates `request.state.session`:

```python
@router.get("/profile")
async def profile(session: Session = Depends(make_session_dependency(required=True))):
    return {"user_id": str(session.user_id), "data": session.data}
```

**`required=True`** (default): no cookie → `UnauthorizedException` → `401` in the SDK envelope.

**`required=False`**: the handler accepts both — `session` is `Session | None`. Use on public endpoints that adapt content for logged-in users.

Direct access (no dependency):

```python
@router.get("/anything")
async def handler(request: Request) -> dict:
    s: Session | None = request.state.session
    return {"authenticated": s is not None}
```

---

## Security

- **Hash at rest**: the cookie carries a 32-byte URL-safe plaintext; the store keeps only the SHA-256. A leak of the `sessions` table **does not** grant logins.
- **Session-fixation prevention**: `SESSION_ROTATE_ON_LOGIN=True` (default) — a successful login always mints a fresh id, even if the browser already had one. Closes the "attacker plants a known cookie before login" vector.
- **Native CSRF via SameSite**: `SESSION_COOKIE_SAMESITE=lax` (default) blocks cross-site POSTs. Pair with [`CSRFMiddleware`](security.en.md) for GET-state-changing endpoints and form submissions.
- **HttpOnly + Secure**: `SESSION_COOKIE_HTTPONLY=True` + `SESSION_COOKIE_SECURE=True` by default. JavaScript cannot read (anti-XSS); the browser does not send over HTTP.
- **Sliding TTL with floor**: `SESSION_SLIDING=True` (default) refreshes on every hit, but `created_at` stays put — you can force an absolute logout after N days via a job that prunes rows where `created_at < now - 30d`.
- **Anti-enumeration**: `/auth/session/login` rejects wrong-email and wrong-password with the **same** `UnauthorizedException` and approximately the same timing (bcrypt always runs).
- **Instant revocation**: `revoke_all(user_id)` on password change / suspected compromise → logout on every device on the next request.

---

## Trade-offs

**When NOT to use:**

- **Public APIs for mobile** — native apps care little about cookies; bearer JWT in the `Authorization` header is still better.
- **Stateless microservices** — every replica decodes JWT without a DB hit. Sessions require a shared Redis.
- **Edge/CDN auth** — Cloudflare Workers and friends validate JWT at the edge without reaching the origin. Sessions require a backend round-trip.

**When to combine JWT + Session:**

Possible. A web SPA uses the session cookie; mobile on the same backend uses `UserAuthService.login` → JWT. Both flows coexist without conflict — `UserAuthService` and `SessionAuth` speak to the same `UserModel`, differing only in the post-verify step (mint JWT vs mint Session).

## Next steps

- **[Auth flow »](auth-flow.en.md)** — bundled JWT flow (signup / activate / reset). Sessions only cover login/logout.
- **[Security »](security.en.md)** — `CSRFMiddleware` to harden POSTs against cross-site attacks even with `SameSite=lax`.
- **[Cache »](cache.en.md)** — `AsyncRedisManager`, which backs `RedisSessionStore`.
