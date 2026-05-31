# Security

Defensive primitives: rate-limit by failure (login/OTP), opaque single-use tokens, hardened static-file serving with security headers, and the spoof-resistant client IP resolver.

## Brute-force throttling


`AttemptThrottle` counts failed attempts per key (typically `<endpoint>:<identifier>` — login email, password-reset target, etc.) and either yields a free attempt, locks the caller for a cooldown, or signals "back off" once the threshold is crossed. Two backends ship: `MemoryThrottleBackend` (defaults — process-local, perfect for tests and single-process services) and `RedisThrottleBackend` (multi-process / multi-host deployments).

```python
from tempest_fastapi_sdk import AttemptThrottle, ThrottleStatus, TooManyRequestsException
from tempest_fastapi_sdk.utils.throttle import RedisThrottleBackend

throttle = AttemptThrottle(
    backend=RedisThrottleBackend(redis_manager=cache),
    max_attempts=5,
    window_seconds=300,           # rolling window
    lock_seconds=900,             # cooldown applied when threshold trips
)


async def login(email: str, password: str) -> User:
    status = await throttle.check(f"login:{email}")
    if status is ThrottleStatus.LOCKED:
        raise TooManyRequestsException(message="Too many attempts; try again later.")

    user = await users_repo.get_by_email(email)
    if not password_utils.verify(password, user.password_hash):
        await throttle.record_failure(f"login:{email}")
        raise UnauthorizedException(message="Invalid credentials.")

    await throttle.reset(f"login:{email}")
    return user
```

`check()` returns a `ThrottleStatus` enum (`ALLOWED` / `LOCKED`); inspecting `.attempts_left` and `.retry_after_seconds` on the result lets you surface friendly error payloads. Pair with `TooManyRequestsException` so the SDK exception handler emits the canonical `{detail, code, details}` envelope with HTTP 429 and a `Retry-After` header.


## Opaque tokens


`generate_opaque_token` produces a high-entropy URL-safe token (default 32 bytes / 256 bits via `secrets.token_urlsafe`); `hash_opaque_token` stores it as an HMAC-SHA-256 digest so a leaked database row is useless on its own; `verify_opaque_token` performs constant-time comparison. Use them for password reset links, email confirmation, API keys, opaque session IDs — anything where the issued secret is never inspected by the recipient.

```python
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)
from src.core.settings import settings


def issue_reset_token(user_id: UUID) -> str:
    plain = generate_opaque_token()
    digest = hash_opaque_token(plain, secret=settings.OPAQUE_TOKEN_PEPPER)
    await reset_tokens_repo.add(
        PasswordResetToken(user_id=user_id, digest=digest, expires_at=...),
    )
    return plain  # send this in the email — never store it


async def consume_reset_token(plain: str, user_id: UUID) -> bool:
    record = await reset_tokens_repo.get_or_none({"user_id": user_id})
    if record is None or record.is_expired:
        return False
    return verify_opaque_token(
        plain,
        record.digest,
        secret=settings.OPAQUE_TOKEN_PEPPER,
    )
```

`secret=` is optional — passing the same pepper across `hash_*` / `verify_*` adds a service-wide secret so the digest column alone cannot be brute-forced. Defaults: 32 bytes of entropy, HMAC-SHA-256, constant-time compare. Override `nbytes=` for longer keys (API keys / refresh tokens).


## Hardened static files + cookie helpers


`HardenedStaticFiles` extends Starlette's `StaticFiles` with three production-grade defaults: it resolves the served path against a symlink-free base, refuses any path that escapes that base (path-traversal defense in depth), and attaches a configurable set of security headers (`DEFAULT_STATIC_SECURITY_HEADERS` — `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-site`).

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

For cookie-based session flows, `set_cookie` / `clear_cookie` write headers that already include the safe combo (`HttpOnly`, `Secure`, `SameSite=Lax`) so the caller only picks the bits they want to deviate from. `SameSite` is a `BaseStrEnum` (`SameSite.LAX` / `STRICT` / `NONE`) — using `SameSite.NONE` forces `Secure=True` to honor the browser requirement.

```python
from fastapi import Response

from tempest_fastapi_sdk import SameSite, clear_cookie, set_cookie


def login(response: Response, token: str) -> None:
    set_cookie(
        response,
        key="session",
        value=token,
        max_age=3600,
        same_site=SameSite.LAX,         # default
        # secure=True,                  # auto-enabled for SameSite.NONE
        # http_only=True,               # default
        path="/",
    )


def logout(response: Response) -> None:
    clear_cookie(response, key="session", path="/")
```


## Client IP extraction


`get_client_ip(request)` and `get_client_ip_from_scope(scope)` return the real client IP behind an arbitrary chain of proxies. They inspect `Forwarded` (RFC 7239) first, fall back to `X-Forwarded-For` honoring an explicit `trusted_proxies` allowlist, and finally use the raw socket address — never naively trusting an inbound header.

```python
from fastapi import Request

from tempest_fastapi_sdk import get_client_ip


@router.post("/login")
async def login(request: Request, payload: LoginIn) -> LoginOut:
    ip = get_client_ip(
        request,
        trusted_proxies={"10.0.0.0/8", "192.168.0.0/16"},
    )
    await throttle.check(f"login:{ip}")
    ...
```

Use `get_client_ip_from_scope(scope)` from middleware or websocket handlers where only the ASGI scope is in reach. Both helpers normalize IPv6 brackets and refuse to return private addresses when `accept_private=False` is passed — handy when only public traffic should ever populate audit logs.

