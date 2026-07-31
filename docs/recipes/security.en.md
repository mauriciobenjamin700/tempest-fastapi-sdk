# Security

Defensive primitives: rate-limit by failure (login/OTP), opaque single-use tokens, hardened static-file serving with security headers, HttpOnly/Secure/SameSite cookie helpers, and a client-IP resolver scoped to trusted proxy headers.

## Brute-force throttling

`AttemptThrottle` counts failed attempts per key (typically `<endpoint>:<identifier>` — login email, password-reset target, IP, etc.). When the threshold is crossed, `raise_if_blocked` throws `TooManyRequestsException` directly; or you can read `status`/`hit` and decide what to do.

The constructor takes a `backend` (anything matching the `ThrottleBackend` Protocol — `redis.asyncio.Redis` works out of the box) + `max_attempts` + `window_seconds`. No "in-memory" backend is bundled — use the Redis client from `AsyncRedisManager`, or a fake in tests.

```python
from tempest_fastapi_sdk import (
    AttemptThrottle,
    TooManyRequestsException,
    UnauthorizedException,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings

cache = AsyncRedisManager(settings.REDIS_URL)
throttle: AttemptThrottle


async def on_startup() -> None:
    """Connect Redis and build the throttle at application startup.

    `cache.client` raises RuntimeError until `connect()` runs, so the
    manager must be connected before the throttle is built. Wire this
    to your app lifespan (`FastAPI(lifespan=...)`).
    """
    global throttle
    await cache.connect()   # required — `cache.client` raises RuntimeError until connected
    # `cache.client` is `redis.asyncio.Redis` — matches the ThrottleBackend Protocol
    throttle = AttemptThrottle(
        cache.client,
        max_attempts=5,
        window_seconds=300,     # fixed window; also the TTL applied on the first failure
        namespace="login",      # key prefix — multiple throttles can share a backend
        fail_open=True,         # Redis outage = allow, instead of locking everyone out
    )


async def login(email: str, password: str) -> User:
    key = f"login:{email}"
    await throttle.raise_if_blocked(key)            # 429 if already over budget

    user = await users_repo.get_or_none({"email": email})
    if user is None or not password_utils.verify(password, user.hashed_password):
        await throttle.hit(key)                     # +1 failure, apply TTL
        raise UnauthorizedException(message="Invalid credentials.")

    await throttle.reset(key)                       # clear counter on success
    return user
```

`throttle.status(key)` (peek, no increment) and `throttle.hit(key)` (increment) both return a `ThrottleStatus` — a frozen dataclass with:

- `attempts: int` — failures recorded in the current window.
- `blocked: bool` — `True` when `attempts >= max_attempts`.
- `retry_after_seconds: int` — seconds until the window resets (`0` when not blocked).

Use the fields to build friendly error payloads. `raise_if_blocked` already crafts a `TooManyRequestsException` with the `Retry-After` header — you don't need to read them by hand.

!!! note "Connect the `AsyncRedisManager` at startup"
    `cache.client` raises `RuntimeError` until `connect()` is called. Connect the manager at application startup (via `FastAPI(lifespan=...)` or `on_startup`) before accessing `cache.client` — and call `cache.disconnect()` on shutdown.

!!! warning "`AttemptThrottle` ships no in-memory backend"
    For tests without Redis, use a fake/double via [fakeredis](https://github.com/cunla/fakeredis-py) (`pip install fakeredis`) — it satisfies the `ThrottleBackend` Protocol (`get`, `incr`, `expire`, `ttl`, `delete`) with a fully in-memory Redis API.

## JWT token types (`typ`)

A service running the bundled auth flow mints three JWTs **with one secret**: the access token, the refresh token, and the intermediate token bridging the two steps of an MFA login. A valid signature therefore says nothing about *which* one arrived — and a route guard that only reads `sub` would take all three.

`typ` is what separates them. `UserAuthService` stamps one on everything it issues:

| Token | `typ` | Valid at |
| --- | --- | --- |
| Access | `ACCESS_TOKEN_TYPE` (`"access"`) | Any authenticated route |
| Refresh | `REFRESH_TOKEN_TYPE` (`"refresh"`) | `POST /auth/refresh` only |
| MFA pending | `MFA_TOKEN_TYPE` (`"mfa"`) | `POST /auth/mfa/verify` only |

`make_bearer_token_dependency` and `make_jwt_user_dependency` accept **only** `access` by default:

```python
from tempest_fastapi_sdk import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    JWTUtils,
    make_bearer_token_dependency,
)

tokens = JWTUtils(secret="…" * 8)

# Default: access-only. A refresh or MFA-pending token gets a 401.
require_claims = make_bearer_token_dependency(tokens)

# A route that deliberately takes another type (e.g. a rotation endpoint):
require_refresh = make_bearer_token_dependency(
    tokens,
    accepted_typ=(REFRESH_TOKEN_TYPE,),
)
```

!!! danger "Why this matters"
    `/login` returns the `mfa_token` to a client that has proven **only the password**. Without the type check it works as a bearer on every authenticated route — the second factor becomes decoration. Same for the refresh token: it is long-lived on purpose, and accepting it as an access token defeats the reason the access token is short.

!!! note "A token without `typ` still works"
    Projects signing JWTs directly with `JWTUtils.encode()` need no change: a token with no `typ` is accepted, otherwise upgrading the SDK would log every live session out. The two legacy markers the SDK already stamped — `refresh: True` and `purpose: "mfa_pending"` — are recognized and **rejected** as access. Use `token_type_allowed()` when you need the same decision outside a dependency.

## Opaque single-use tokens

`generate_opaque_token()` returns `(plaintext, token_hash)` in one call — `plaintext` is a URL-safe string (default 32 bytes ≈ 43 chars), `token_hash` is the lowercase SHA-256 hex digest (64 chars). You store **only the hash** in the DB; `plaintext` leaves via email/SMS exactly once. Use it for password reset, email confirmation, API keys, opaque session IDs — anything where the issued secret is never inspected again.

!!! info "No pepper, no HMAC"
    The hash is plain SHA-256 (`hashlib.sha256(plain).hexdigest()`) by design: opaque tokens carry 256 bits of entropy (already beyond brute-force reach), so an extra pepper buys no practical security. For low-entropy credentials (human passwords), use `PasswordUtils.hash` (bcrypt) — not these helpers.

```python
from uuid import UUID

from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)


async def issue_reset_token(user_id: UUID) -> str:
    plaintext, token_hash = generate_opaque_token()
    await reset_tokens_repo.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=utcnow() + timedelta(hours=1),
        ),
    )
    return plaintext   # show once — never store


async def consume_reset_token(plaintext: str, user_id: UUID) -> bool:
    record = await reset_tokens_repo.get_or_none(
        {"user_id": user_id, "used_at": None},
    )
    if record is None or record.expires_at < utcnow():
        return False
    if not verify_opaque_token(plaintext, record.token_hash):
        return False
    record.used_at = utcnow()
    await reset_tokens_repo.update(record)
    return True
```

!!! tip "For the full flow, use `UserAuthService`"
    Signup + activation + login + password reset with opaque one-shot tokens, TTL, anti-enumeration, and bundled Jinja2 email already ship in [`auth-flow.md`](auth-flow.en.md). Use these helpers directly only when you need a custom flow outside `UserAuthService`.

## Hardened static files

`HardenedStaticFiles` extends `starlette.staticfiles.StaticFiles` by stamping anti-XSS headers on every response — defense in depth in case a malicious file ever lands in the directory (upload-validation bypass, manual operator action) and gets served as a stored-XSS primitive.

`DEFAULT_STATIC_SECURITY_HEADERS` applies:

- `X-Content-Type-Options: nosniff` — the browser doesn't sniff the MIME from the bytes.
- `Content-Security-Policy: default-src 'none'; sandbox` — embedded scripts cannot execute; sandbox blocks forms and top-level navigation.
- `Cross-Origin-Resource-Policy: same-site` — bounds cross-origin readability.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import DEFAULT_STATIC_SECURITY_HEADERS, HardenedStaticFiles

app = FastAPI()
app.mount(
    "/static",
    HardenedStaticFiles(
        directory="public/",
        # Override or extend the defaults — merging is the caller's job.
        security_headers={
            **DEFAULT_STATIC_SECURITY_HEADERS,
            "Cache-Control": "public, max-age=86400, immutable",
        },
    ),
    name="static",
)
```

## CSRF for cookie-based flows (`CSRFMiddleware`)

A cookie session has a problem a bearer token does not: the browser resends the
cookie **on its own**, even on a request triggered by another site. `SameSite=lax`
blocks most of that, but not a subdomain form POST nor an old client.
`CSRFMiddleware` closes the gap with double-submit:

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import CSRFMiddleware

app: FastAPI = FastAPI()

app.add_middleware(
    CSRFMiddleware,
    exclude_paths=("/api/", "/webhooks/"),
)
```

On `POST`/`PUT`/`PATCH`/`DELETE` the request must carry **both** values, equal to
each other: the `csrf_token` cookie and the `X-CSRF-Token` header. Missing or
mismatched, the answer is `403` in the SDK's canonical envelope. `GET`/`HEAD`/
`OPTIONS` always pass. The defaults live in `CSRF_COOKIE_NAME` and
`CSRF_HEADER_NAME`; override with `cookie_name=`/`header_name=`.

!!! info "Why exclude `/api/`"
    A route authenticated by `Authorization: Bearer` is **not** CSRF-vulnerable —
    the browser never attaches that header by itself. Demanding a token there
    would only break mobile clients. Same for a signed webhook
    (`WebhookSignatureVerifier`): the signature already is the authentication.
    `exclude_paths` matches by prefix (`startswith`).

To issue the token, mount `make_csrf_token_dependency()` on the route that
renders the page — it writes the cookie when absent and returns the value for
the template:

```python
# src/api/routers/pages.py
from fastapi import APIRouter, Depends
from tempest_fastapi_sdk import make_csrf_token_dependency

router: APIRouter = APIRouter()
csrf_token = make_csrf_token_dependency()


@router.get("/login")
async def login_page(token: str = Depends(csrf_token)) -> dict[str, str]:
    """Render the login shell carrying the CSRF token."""
    return {"csrf_token": token}
```

The client echoes that value in the header on every write:

```javascript
await fetch("/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "X-CSRF-Token": token, "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});
```

`generate_csrf_token(n_bytes=32)` is exported for issuing the token outside a
dependency (an SSR handler, for instance).

!!! warning "CSRF only matters when the credential travels automatically"
    Use it when the session lives in a cookie — `AUTH_TOKEN_DELIVERY=cookie`/`both`
    ([auth recipe](auth-flow.md)) or a server-side session
    ([sessions recipe](sessions.md)). A bearer-only service does not need the
    middleware.

## Session cookies

`set_cookie` / `clear_cookie` write cookies with secure defaults (`HttpOnly=True`, `Secure=True`, `samesite="lax"`). `SameSite` is a **type alias** `Literal["lax", "strict", "none"]` — pass the string literal, not an enum.

```python
from fastapi import Response

from tempest_fastapi_sdk import clear_cookie, set_cookie


def login(response: Response, token: str) -> None:
    set_cookie(
        response,
        "session",                 # name (positional)
        token,                     # value (positional)
        max_age=3600,
        samesite="lax",            # "lax" (default), "strict" or "none"
        # secure=True,             # default — set False only for plain HTTP local dev
        # http_only=True,          # default
        path="/",
    )


def logout(response: Response) -> None:
    clear_cookie(response, "session", path="/")
```

!!! warning "`SameSite=\"none\"` requires `Secure=True`"
    When the browser sees `SameSite=None` without `Secure`, it rejects the cookie. The SDK does **not** auto-enable `secure=True` — pass `samesite="none", secure=True` explicitly for cross-site scenarios (iframe widget, OAuth callback from another domain).

## Client IP extraction

`get_client_ip(request)` and `get_client_ip_from_scope(scope)` return the real client IP behind proxies. By a simple design: the function accepts **one** trusted header name (`trusted_header=`) that your infrastructure guarantees only the edge proxy can set (typical: `"x-real-ip"` behind Nginx, `"x-forwarded-for"` behind an ALB with sanitized headers). Without `trusted_header=`, the function falls back to the peer address.

```python
from fastapi import Request

from tempest_fastapi_sdk import get_client_ip


@router.post("/login")
async def login(request: Request, payload: LoginIn) -> LoginOut:
    # Behind Nginx that overwrites X-Real-IP with the actual peer:
    ip = get_client_ip(request, trusted_header="x-real-ip")
    await throttle.raise_if_blocked(f"login:{ip}")
    ...
```

!!! warning "Configure trust at the edge, not in Python"
    Defense against `X-Forwarded-For` spoofing must happen at the proxy (Nginx, ALB, CloudFront) — the proxy **overwrites** the header with the real peer before the request hits FastAPI. The SDK only reads the header you trust. If you expose the app directly to the internet, **do not** pass `trusted_header=` — fall back to the peer address.

Use `get_client_ip_from_scope(scope, trusted_header=...)` in middleware or WebSocket handlers where only the ASGI scope is reachable.
