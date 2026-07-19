# Introspection auth (resource server)

Not every service issues its own tokens. Often your service is just a
**resource server** in the OAuth2 pattern: it receives an opaque bearer
minted by an upstream identity provider and must validate it by asking
that provider **who owns the token**. You don't decode a JWT or verify a
signature — you call a `userinfo` / introspection endpoint and trust the
response.

`IntrospectionAuth` wraps exactly that pattern:

- it validates the bearer by calling `GET <userinfo_url>` with
  `Authorization: Bearer <token>`,
- it **caches** successful lookups in-process for a short TTL, so a burst
  of requests carrying the same token doesn't hammer the upstream,
- it can optionally **gate access** on an application claim
  (`access_apps` by default), and
- it extracts the user id from the subject claim (`sub` by default).

!!! info "Installation"
    `IntrospectionAuth` needs the `[http]` extra — `uv add "tempest-fastapi-sdk[http]"` (pulls in `httpx`, used to call the upstream `userinfo`).

!!! info "When to use this"
    Use it when **another service** (IAGRO, a Keycloak, an Auth0, your
    own identity service) mints the tokens and your service only needs to
    accept them. If **your** service is the one that logs users in and
    issues tokens, you want `UserAuthService` + `make_auth_router` (see
    [Auth flow](auth-flow.md)), not this.

## The minimal path

Instantiate once, pointing at the provider's userinfo endpoint, and use
the two methods as FastAPI dependencies:

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import IntrospectionAuth

from src.core.settings import settings

auth = IntrospectionAuth(
    userinfo_url=settings.IAGRO_USERINFO_URL,   # e.g. https://id.iagro.gov/users/me
    required_app="famacha",                     # per-app access gate
)
```

```python
# src/api/routers/animals.py
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.dependencies.auth import auth

router = APIRouter(prefix="/api/animals", tags=["animals"])


@router.get("/me")
async def whoami(
    claims: dict[str, Any] = Depends(auth.get_claims),
) -> dict[str, Any]:
    """Return the raw claims of the authenticated user."""
    return claims


@router.get("/")
async def list_animals(
    user_id: UUID = Depends(auth.get_user_id),
) -> list[str]:
    """List the user's animals — id already resolved from the token."""
    return await service.list_for(user_id)
```

!!! note "`service` is your application's glue"
    `service.list_for(...)` stands in for your own service/repository layer —
    it is not part of the SDK. Swap it for your project's real call.

That's it. A request without `Authorization` gets **401**; a token the
upstream rejects gets **401**; a user without `famacha` in `access_apps`
gets **403**.

!!! tip "Wire up the exception handlers"
    `IntrospectionAuth` raises the SDK's own `UnauthorizedException`
    (401) and `ForbiddenException` (403). Call
    `register_exception_handlers(app)` (the SDK's `create_app()` already
    does) so they become the right HTTP statuses instead of 500.

## How it works, piece by piece

### `get_claims` — the heart

`get_claims` is the primary dependency. It:

1. reads the bearer via `HTTPBearer(auto_error=False)` — **no** header →
   `UnauthorizedException`;
2. calls `fetch_userinfo(token)` (cached);
3. if `required_app` is set, requires it to be in
   `claims.get(app_claim) or []`, otherwise `ForbiddenException`;
4. returns the claims dict.

```python
claims: dict[str, Any] = await auth.get_claims(credentials)
# {"sub": "…", "access_apps": ["famacha"], "email": "…", ...}
```

### `get_user_id` — the common shortcut

Most routes only want the user id, not the whole dict. `get_user_id`
depends on the same bearer, calls `get_claims` internally, and does
`UUID(str(claims["sub"]))`:

```python
user_id: UUID = await auth.get_user_id(credentials)
```

A missing subject, or one that isn't a valid UUID, becomes
`UnauthorizedException`.

!!! note "Why doesn't `get_user_id` declare `Depends(self.get_claims)`?"
    Default arguments are evaluated **at method-definition time**, when
    the `self` instance doesn't exist yet — so you can't write
    `Depends(self.get_claims)` in the signature. The fix: `get_user_id`
    depends on the **bearer directly** and calls
    `await self.get_claims(credentials)` in its body. It still wires
    cleanly as `Depends(auth.get_user_id)`.

### The cache

Every `200` response is stored for `cache_ttl_seconds` (30 by default),
with a `time.monotonic()` clock and the raw token as the key. Within the
TTL, the second call does **not** hit the upstream. A `401`/`403`
**evicts** the token immediately. `cache_ttl_seconds=0` disables caching.

```python
auth = IntrospectionAuth(
    userinfo_url=settings.IAGRO_USERINFO_URL,
    cache_ttl_seconds=60,   # tolerate up to 60s of a revoked token
)
```

!!! warning "TTL is a revocation window"
    While a claim is cached, an upstream revocation isn't seen. Pick a
    short TTL (seconds to a few minutes) to balance provider load against
    freshness. `0` disables the cache and always revalidates.

### Lazy URL (callable)

`userinfo_url` accepts a `str` **or** a zero-argument callable, resolved
**on every call**. That lets you pass a settings property read only at
runtime (handy when the URL arrives late from the environment, or varies
per tenant):

```python
auth = IntrospectionAuth(
    userinfo_url=lambda: settings.IAGRO_USERINFO_URL,
)
```

### Custom claims

If your provider uses different names, tweak them:

```python
auth = IntrospectionAuth(
    userinfo_url=settings.IDP_USERINFO_URL,
    required_app="famacha",
    app_claim="apps",        # instead of "access_apps"
    subject_claim="user_id", # instead of "sub"
)
```

### HTTP client

By default the instance creates **one** shared `httpx.AsyncClient`
(lazily, with `httpx.Timeout(timeout)`) and reuses it. You can inject
your own — handy in tests or to share a pool / limits:

```python
import httpx

client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
auth = IntrospectionAuth(
    userinfo_url=settings.IDP_USERINFO_URL,
    http_client=client,
)
```

The cache is **per instance**, not global — the app can create several
`IntrospectionAuth` objects (one per upstream) without one leaking state
into another.

## Testing

Inject an `httpx.AsyncClient` with a `MockTransport` so you never touch
the network:

```python
import httpx
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from tempest_fastapi_sdk import IntrospectionAuth


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer tok"
    return httpx.Response(200, json={"sub": "…", "access_apps": ["famacha"]})


@pytest.mark.asyncio
async def test_valid_token() -> None:
    auth = IntrospectionAuth(
        userinfo_url="https://id.example.com/users/me",
        required_app="famacha",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    claims = await auth.get_claims(creds)
    assert claims["access_apps"] == ["famacha"]
```

## Recap

- `IntrospectionAuth` is for the **resource server** pattern: it
  validates opaque bearers against an upstream `userinfo` — it never
  issues tokens.
- `get_claims` and `get_user_id` are **bound methods** usable directly as
  `Depends(auth.get_claims)` / `Depends(auth.get_user_id)`.
- No credentials → 401; rejected token / upstream down → 401; app not
  granted → 403; invalid subject → 401.
- In-process per-token cache with a `time.monotonic()` TTL; `401`/`403`
  evicts; `cache_ttl_seconds=0` disables it.
- `userinfo_url` can be a `str` or a callable (resolved per call); claims
  (`app_claim`, `subject_claim`) and the `httpx` client are configurable.
  Cache and client are **per instance**.
