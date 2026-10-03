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
6. **[Login without a user table](#login-without-a-user-table)** — fixed credential, HTML route with a redirect, no middleware.
7. **[Security](#security)** — anti-fixation rotation, hash-at-rest, anti-enumeration, CSRF.
8. **[Trade-offs and when NOT to use](#trade-offs)** — multi-replica, mobile, edge.

---

## Minimum setup

Four objects compose the flow. Mount once in `app.py`:

```python
# src/api/app.py
from fastapi import FastAPI

from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RedisSessionStore,
    SessionAuth,
    SessionMiddleware,
    SessionSettings,
    make_session_router,
    register_exception_handlers,
)

from src.core.settings import settings
from src.db.models import UserModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
session_settings = SessionSettings()

session_store = RedisSessionStore(
    Redis.from_url(settings.REDIS_URL, decode_responses=True),
    prefix=f"{settings.APP_NAME}:",
)
session_auth = SessionAuth(
    user_model=UserModel,
    store=session_store,
    settings=session_settings,
)


def create_app() -> FastAPI:
    app = FastAPI(title="my-app")
    register_exception_handlers(app)

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

!!! note "Why `Redis.from_url` here, not `AsyncRedisManager`?"
    This client feeds a **middleware** (`SessionMiddleware`), built in
    `create_app` (sync), before any async lifespan runs. `Redis.from_url()` is
    **lazy** — it constructs without opening a connection, so it fits here.
    If your service already has an `AsyncRedisManager`, prefer
    `cache.client_proxy` (v0.256.0) over opening a loose client: it is a stable
    handle, constructible before `connect()` and valid across a reconnect, and
    it keeps the manager's `disconnect()` and `health_check()`. What does not
    fit here is `cache.client`, which raises `RuntimeError` before the lifespan.
    All of them need the `[cache]` extra (the `redis` package).

Done. The user calls `POST /auth/session/login` with email+password; the SDK sets the HttpOnly+Secure cookie; every subsequent request that carries the cookie has `request.state.session` populated.

### What each object does

1. **`SessionStore`** (`RedisSessionStore` / `MemorySessionStore`) — the persistence layer. Keeps the real session state indexed by the SHA-256 hash of the opaque id. It is the only object that talks to Redis.
2. **`SessionAuth`** — the logic layer. Verifies credentials against the `UserModel`, mints, rotates, and revokes sessions through the `store`. Knows nothing about HTTP.
3. **`SessionMiddleware`** — the HTTP → session bridge. On every request it reads the cookie, resolves it through `SessionAuth`/`store`, and populates `request.state.session` **before** any router runs. Without it, `make_session_dependency()` finds no session and answers `401` even with a valid cookie — unless you pass `session_auth=` to the dependency, which then resolves the cookie itself ([no middleware](#login-without-a-user-table)).
4. **`make_session_router`** — exposes the five bundled endpoints (`login` / `logout` / `me` / `list` / `{id}`). Takes the same `session_auth` plus a `session_factory` to open the DB session on login.

!!! note "The order between `add_middleware` and `include_router` does not matter"
    Starlette builds the middleware stack on the first request, around the whole app, so `SessionMiddleware` also wraps routers included before it. Measured here (Starlette 1.6.0, FastAPI 0.141.1): with `include_router` before `add_middleware`, login answers `200` and the following `GET /auth/session/me` answers `200` too, same as the order in the example. What matters is registering the middleware **before the app starts serving**: `add_middleware` after the first request raises `RuntimeError: Cannot add middleware after an application has started`. Wire everything inside `create_app`.

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

`SESSION_COOKIE_SAMESITE` is `Literal["lax", "strict", "none"]` (alias `SessionCookieSameSite`). Any other value — `Strict`, `LAX`, `lax;` — fails `Settings()` construction with `literal_error`, at boot rather than on the first response. Surrounding whitespace (`lax `) is trimmed, as before.

To write and delete the cookie, use the two mappers instead of repeating seven settings in `set_cookie` and three in `delete_cookie`:

```python
from fastapi import Response

from tempest_fastapi_sdk import BaseAppSettings, SessionSettings


class Settings(SessionSettings, BaseAppSettings):
    pass


settings = Settings()


def start_session(response: Response, plaintext: str) -> None:
    response.set_cookie(value=plaintext, **settings.session_cookie_kwargs())


def end_session(response: Response) -> None:
    response.delete_cookie(**settings.session_cookie_delete_kwargs())
```

`session_cookie_kwargs()` carries the cookie name as `key`, so the call site passes only the id. `session_cookie_delete_kwargs()` repeats `path`, `domain`, `secure`, `httponly` and `samesite` from the same fields — a browser only drops the cookie when the deleting `Set-Cookie` matches the `path` and `domain` it was set with. Both return a `TypedDict` (`SessionCookieKwargs` / `SessionCookieDeleteKwargs`), so mypy checks the splat against Starlette's signature. `make_session_router` uses the same mappers.

!!! danger "`SESSION_COOKIE_SECURE=false` is dev-HTTP only"
    The default is `true`: the browser only sends the cookie over HTTPS. Setting `false` makes the session cookie travel in clear text over HTTP — any network intermediary can capture the id and hijack the session. Use `false` **exclusively** in local dev without TLS; never in staging or production. The same holds for keeping `SESSION_COOKIE_HTTPONLY=true` (default) — turning it off exposes the cookie to XSS.

---

## Stores

### `MemorySessionStore` — dev/tests

```python
from tempest_fastapi_sdk import MemorySessionStore

session_store = MemorySessionStore()
```

State lives in the process dict. **Does not scale** — uvicorn restart wipes everything; one replica does not see another's sessions. Use in tests and local dev.

!!! warning "`MemorySessionStore` does not survive a restart nor scale horizontally"
    State lives in an in-process dict. Every uvicorn restart/redeploy logs everyone out, and with more than one replica each worker sees only its own sessions (a cookie issued by one replica hits `401` on the other). It is strictly for tests and local dev — in production always use `RedisSessionStore`.

### `RedisSessionStore` — production

```python
from redis.asyncio import Redis

from tempest_fastapi_sdk import RedisSessionStore

from src.core.settings import settings


session_store = RedisSessionStore(
    Redis.from_url(settings.REDIS_URL, decode_responses=True),
    prefix="myapp:",
)
```

Internal schema:

- `myapp:sess:<sha256-hex>` — JSON of the `Session`, TTL = `expires_at - now`
- `myapp:user:<user-uuid>` — Redis SET of session hashes (index for `list_by_user` / `delete_by_user`)

Redis handles TTL automatically — no janitor process needed.

!!! note "`RedisSessionStore` requires the `[cache]` extra"
    `RedisSessionStore` depends on the async `redis` client, which only ships with the `[cache]` extra. Since it feeds a middleware, hand it a `Redis.from_url(...)` (lazy) or `AsyncRedisManager.client_proxy` — never `cache.client`, which raises before the lifespan. Install with `uv add "tempest-fastapi-sdk[cache]"` (add `auth` etc. as your service needs). `MemorySessionStore` needs no extra at all.

### Custom

Any class that implements the `SessionStore` protocol (5 async methods) plugs in out of the box — DynamoDB, a Postgres table, Memcached, etc.

---

## Middleware

`SessionMiddleware` runs **before** the routers, reads the cookie, resolves through the store, and populates `request.state.session`:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import Session, make_session_dependency

router = APIRouter()


@router.get("/profile")
async def profile(session: Session = Depends(make_session_dependency(required=True))):
    return {"user_id": str(session.user_id), "data": session.data}
```

**`required=True`** (default): no cookie → `UnauthorizedException` → `401` in the SDK envelope.

**`required=False`**: the handler accepts both — `session` is `Session | None`. Use on public endpoints that adapt content for logged-in users.

Direct access (no dependency):

```python
from fastapi import APIRouter, Request

from tempest_fastapi_sdk import Session

router = APIRouter()


@router.get("/anything")
async def handler(request: Request) -> dict:
    s: Session | None = request.state.session
    return {"authenticated": s is not None}
```

---

## Login without a user table

`SessionAuth(user_model=...)` looks the e-mail up in a table and verifies the hash. An admin panel guarded by **one** root credential from the environment has no table to point at. For that case, `SessionAuth.from_credentials(username, password, ...)` builds the service on a `StaticCredentialAuthenticator`, and `make_session_dependency(session_auth=..., on_missing=redirect_to("/login"))` guards the HTML route with no middleware at all.

The whole example — login form, protected page and logout:

```python
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import SecretStr

from tempest_fastapi_sdk import (
    BaseAppSettings,
    MemorySessionStore,
    Session,
    SessionAuth,
    SessionSettings,
    UnauthorizedException,
    make_session_dependency,
    redirect_to,
    register_exception_handlers,
)


class Settings(SessionSettings, BaseAppSettings):
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: SecretStr


settings = Settings()
session_auth = SessionAuth.from_credentials(
    settings.ADMIN_USERNAME,
    settings.ADMIN_PASSWORD,
    store=MemorySessionStore(),
    settings=settings,
)
require_admin = make_session_dependency(
    session_auth=session_auth,
    on_missing=redirect_to("/login"),
)

app = FastAPI()
register_exception_handlers(app)


@app.get("/login", response_class=HTMLResponse)
async def login_page() -> str:
    return (
        '<form method="post" action="/login">'
        '<input name="username"> <input name="password" type="password">'
        "<button>Entrar</button></form>"
    )


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(),
    password: str = Form(),
) -> Response:
    try:
        _session, plaintext = await session_auth.login_with_credentials(
            username,
            password,
            previous_session_id=request.cookies.get(settings.SESSION_COOKIE_NAME),
        )
    except UnauthorizedException:
        return HTMLResponse("Credenciais inválidas", status_code=401)
    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(value=plaintext, **settings.session_cookie_kwargs())
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin(session: Session = Depends(require_admin)) -> str:
    return f"<h1>Painel</h1><p>Sessão expira em {session.expires_at:%H:%M}</p>"


@app.post("/logout")
async def logout(request: Request) -> Response:
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie:
        await session_auth.revoke(cookie)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(**settings.session_cookie_delete_kwargs())
    return response
```

With `ADMIN_USERNAME=root`, `ADMIN_PASSWORD=s3cret` and `SESSION_COOKIE_SECURE=false` in the environment, driven by `httpx.AsyncClient`:

```text
GET /admin sem cookie: 303 /login
POST /login errado: 401 Credenciais inválidas
POST /login certo: 303 /admin
  Set-Cookie: tempest_session=63XC...; HttpOnly; Max-Age=86400; Path=/; SameSite=lax
GET /admin com cookie: 200 <h1>Painel</h1><p>Sessão expira em 16:55</p>
POST /logout: 303 /login
  Set-Cookie: tempest_session=""; expires=...; HttpOnly; Max-Age=0; Path=/; SameSite=lax
GET /admin com cookie revogado: 303 /login
```

(The labels are the Portuguese ones the run printed: no cookie, wrong login, right login, with cookie, logout, revoked cookie.)

### Piece by piece

1. **`SessionAuth.from_credentials(...)`** — accepts exactly one pair. Both halves are always compared, each with `hmac.compare_digest` over the value's SHA-256, and the results are combined with `&`, not `and`: a wrong username costs the same as a wrong password, and the message (`invalid username or password`) is the same for both. It takes a `SecretStr`, so the `Settings` field goes straight in. An empty credential raises `ValueError` at construction — an empty env var does not turn into "accept an empty form". It does not need the `[auth]` extra: the comparison is stdlib only, and the `PasswordUtils` (bcrypt) the `user_model=` mode uses is never built here.
2. **The session owner** is `uuid5(STATIC_CREDENTIAL_NAMESPACE, username)` by default: stable across restarts and replicas, so `session_auth.revoke_all(...)` reaches every root session. Pass `user_id=` to pick another.
3. **`login_with_credentials(...)`** — verifies and opens the session in one call. Wrong credentials raise `UnauthorizedException` without creating a session and without revoking the previous one; the route decides what to render.
4. **`make_session_dependency(session_auth=..., on_missing=redirect_to("/login"))`** — reads the cookie and resolves the session itself (with the sliding TTL, like the middleware). With no session it raises `HTTPException(303, Location=/login)`; FastAPI's handler and the SDK's `register_exception_handlers` both keep the header, and the browser follows. Without `on_missing`, the answer stays `401`.
5. **Logout** — `revoke(cookie)` deletes the session from the store (the same cookie then redirects to the login) and `delete_cookie(**settings.session_cookie_delete_kwargs())` drops the cookie in the browser.

!!! tip "Another credential backend"
    `from_credentials` is a shortcut. Any object with `async def authenticate(self, username: str, password: str, /) -> UUID` that raises `UnauthorizedException` on refusal satisfies the `SessionAuthenticator` protocol — LDAP, an upstream API — and goes into `SessionAuth(authenticator=..., store=..., settings=...)`. Pass `user_model=` **or** `authenticator=`, never both. `make_session_router` (JSON login by e-mail) needs `user_model=` and refuses, at build time, a service built with `authenticator=`.

!!! warning "`SessionMiddleware` is a `BaseHTTPMiddleware` and sits in the path of every response"
    The middleware wraps the whole app, and Starlette's `BaseHTTPMiddleware` relays every chunk of the response body through an in-memory stream. Measured here (Starlette 1.6.0, direct ASGI call with an empty `send`, median of 7 runs): a 256 MiB `StreamingResponse` in 64 KiB chunks took **~4 ms** with no middleware and **~64 ms** with `SessionMiddleware` — about 15 µs per chunk, paid by **every** route, including the upload/download that never reads the session. In a service that streams files and uses a session on three routes, prefer the dependency with `session_auth=`: only the routes that declare it pay for the lookup. If the middleware is mounted anyway, the dependency reuses the session it resolved instead of resolving again.

### `SessionAuth` built after import

In the example above the `SessionAuth` exists at import, because `Settings()` runs at module level. In a service whose settings sit behind an `@lru_cache` — which the tests clear and rebuild per test — there is no `SessionAuth` to pass when the dependency is declared. Pass a **factory** that takes the request:

```python hl_lines="25-27 34 58"
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import SecretStr

from tempest_fastapi_sdk import (
    BaseAppSettings,
    MemorySessionStore,
    Session,
    SessionAuth,
    SessionSettings,
    make_session_dependency,
    redirect_to,
    register_exception_handlers,
)


class Settings(SessionSettings, BaseAppSettings):
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: SecretStr


def get_session_auth(request: Request) -> SessionAuth:
    auth: SessionAuth = request.app.state.session_auth
    return auth


AdminSession = Annotated[
    Session,
    Depends(
        make_session_dependency(
            session_auth=get_session_auth,
            on_missing=redirect_to("/login"),
        )
    ),
]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.state.session_auth = SessionAuth.from_credentials(
        settings.ADMIN_USERNAME,
        settings.ADMIN_PASSWORD,
        store=MemorySessionStore(),
        settings=settings,
    )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin(session: AdminSession) -> str:
        return f"<h1>Panel</h1><p>Session expires at {session.expires_at:%H:%M}</p>"

    return app
```

1. **`get_session_auth(request)`** — returns the service of the app serving the request. `make_session_dependency` calls it per request, and only when the middleware did not resolve the session first; declaring the dependency never calls it. Two apps in one process — a test that runs `get_settings.cache_clear()` and builds another — each resolve against their own: one app's cookie does not open the other's panel.
2. **`AdminSession`** — the alias lives at module level, before any `SessionAuth` exists, and routes use it as is.
3. **The type is `Session`, not `Session | None`.** With `required=True` (the default) the dependency raises instead of returning `None`, and the signature says so: no `if session is None` just for the type checker. With `required=False` the type stays `Session | None`.

---

## Security

- **Hash at rest**: the cookie carries a 32-byte URL-safe plaintext; the store keeps only the SHA-256. A leak of the `sessions` table **does not** grant logins.
- **Session-fixation prevention**: `SESSION_ROTATE_ON_LOGIN=True` (default) — a successful login always mints a fresh id, even if the browser already had one. Closes the "attacker plants a known cookie before login" vector.
- **Native CSRF via SameSite**: `SESSION_COOKIE_SAMESITE=lax` (default) blocks cross-site POSTs. Pair with [`CSRFMiddleware`](security.en.md) for GET-state-changing endpoints and form submissions.
- **HttpOnly + Secure**: `SESSION_COOKIE_HTTPONLY=True` + `SESSION_COOKIE_SECURE=True` by default. JavaScript cannot read (anti-XSS); the browser does not send over HTTP.
- **Sliding TTL with floor**: `SESSION_SLIDING=True` (default) refreshes on every hit, but `created_at` stays put — you can force an absolute logout after N days via a job that prunes rows where `created_at < now - 30d`.
- **Anti-enumeration, in the body and in the timing**: `/auth/session/login` rejects an unknown e-mail, a wrong password and an inactive account with the **same** `UnauthorizedException` and the same message (`invalid email or password`). And all three refusals pay one bcrypt verification: an unknown e-mail is checked against a throwaway hash (`PasswordUtils.dummy_verify`), and an inactive account's password is verified before `is_active`. Measured on this machine (bcrypt cost 12, `SessionAuth.authenticate` called directly, median of N=21): wrong password **153.9 ms**, unknown e-mail **154.1 ms** — before the fix, the unknown e-mail answered in 0.4 ms. What is not bcrypt still varies (the query, the network), and the process's first refusal for an unknown e-mail pays one extra hash to build the throwaway hash. Rate-limit attempts per IP/identifier anyway: that closes brute force, which equal timing does not.
- **Instant revocation**: `revoke_all(user_id)` on password change / suspected compromise → logout on every device on the next request.

---

## Trade-offs

**When NOT to use:**

- **Public APIs for mobile** — native apps care little about cookies; bearer JWT in the `Authorization` header is still better.
- **Stateless microservices** — every replica decodes JWT without a DB hit. Sessions require a shared Redis.
- **Edge/CDN auth** — Cloudflare Workers and friends validate JWT at the edge without reaching the origin. Sessions require a backend round-trip.

**When to combine JWT + Session:**

Possible. A web SPA uses the session cookie; mobile on the same backend uses `UserAuthService.login` → JWT. Both flows coexist without conflict — `UserAuthService` and `SessionAuth` speak to the same `UserModel`, differing only in the post-verify step (mint JWT vs mint Session).

## Recap

- A server-side session is the alternative to JWT when instant revocation is a
  requirement: the cookie carries only an opaque id, and the state lives in the
  store.
- Four objects make the flow — `SessionStore`, `SessionAuth`,
  `SessionMiddleware` and `make_session_router` — mounted once in `app.py`.
- Five bundled endpoints cover the whole cycle, and the middleware populates
  `request.state.session` before any router runs.
- The cookie carries the plaintext; the store keeps only the SHA-256. Leaking
  the sessions table does **not** log anybody in.
- Without a user table, `SessionAuth.from_credentials(...)` compares both
  halves of the credential in constant time, and
  `make_session_dependency(session_auth=..., on_missing=redirect_to("/login"))`
  guards an HTML route with a `303`, no middleware. With the `SessionAuth`
  built after import, `session_auth=` takes a `(request) -> SessionAuth`
  factory, and the dependency alias comes out typed as `Session`.
- `settings.session_cookie_kwargs()` / `session_cookie_delete_kwargs()` write
  and delete the cookie with the same attributes.
- `MemorySessionStore` covers dev and tests; swap the store, not the rest of
  the wiring, to move to Redis or a database.

## Next steps

- **[Auth flow »](auth-flow.en.md)** — bundled JWT flow (signup / activate / reset). Sessions only cover login/logout.
- **[Security »](security.en.md)** — `CSRFMiddleware` to harden POSTs against cross-site attacks even with `SameSite=lax`.
- **[Cache »](cache.en.md)** — `AsyncRedisManager` and `client_proxy`, the right handle for a middleware store like `RedisSessionStore`.
