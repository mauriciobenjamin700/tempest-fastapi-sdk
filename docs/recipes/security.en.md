# Security

Defensive primitives: rate-limit by failure (login/OTP), opaque single-use tokens, hardened static-file serving with security headers, HttpOnly/Secure/SameSite cookie helpers, and a client-IP resolver scoped to trusted proxy headers.

## Brute-force throttling

`AttemptThrottle` counts failed attempts per key (typically `<endpoint>:<identifier>` — login email, password-reset target, IP, etc.). When the threshold is crossed, `raise_if_blocked` throws `TooManyRequestsException` directly; or you can read `status`/`hit` and decide what to do.

The constructor takes a `backend` (anything matching the `ThrottleBackend` Protocol — `redis.asyncio.Redis` works out of the box) + `max_attempts` + `window_seconds`. No "in-memory" backend is bundled — use the Redis client from `AsyncRedisManager`, or a fake in tests.

```python
from tempest_fastapi_sdk import (
    AsyncRedisManager,
    AttemptThrottle,
    TooManyRequestsException,
    UnauthorizedException,
)
from src.core.settings import settings

cache = AsyncRedisManager(settings.REDIS_URL)
# `cache.client` is `redis.asyncio.Redis` — matches the ThrottleBackend Protocol
throttle = AttemptThrottle(
    cache.client,
    max_attempts=5,
    window_seconds=300,         # fixed window; also the TTL applied on the first failure
    namespace="login",          # key prefix — multiple throttles can share a backend
    fail_open=True,             # Redis outage = allow, instead of locking everyone out
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

!!! warning "`AttemptThrottle` ships no in-memory backend"
    For tests without Redis, use a fake/double via [fakeredis](https://github.com/cunla/fakeredis-py) (`pip install fakeredis`) — it satisfies the `ThrottleBackend` Protocol (`get`, `incr`, `expire`, `ttl`, `delete`) with a fully in-memory Redis API.

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
