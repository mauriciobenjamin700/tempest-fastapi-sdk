# DB-backed refresh tokens (rotation + revocation)

Since **v0.66.0** the bundled auth flow can swap the *stateless* JWT refresh token for an **opaque token persisted in the database**, gaining three things a plain JWT never gives you: **real rotation**, **reuse detection** (stolen token), and **revocation** (logout that kills the session before its expiry).

It is all **opt-in**: you pass a `refresh_token_model` to the service. Without it, the SDK keeps the stateless behavior it always had — zero breaking change.

## What's in this recipe

1. **[Stateless vs DB-backed in 30 seconds](#stateless-vs-db-backed)** — what changes and why.
2. **[Setup](#setup)** — the `BaseUserRefreshTokenModel` table.
3. **[Wiring](#wiring)** — pass `refresh_token_model` to the service + router.
4. **[How rotation works](#rotation)** — families, single-use, reuse.
5. **[Logout](#logout)** — the `POST /auth/logout` endpoint.
6. **[Using only `UserAuthService`](#service-direct)** — without the router.
7. **[Security](#security)**.
8. **[Next steps](#next-steps)**.

---

## Stateless vs DB-backed

A **stateless refresh token** is just a JWT signed with the `refresh` claim. The server trusts it if the signature matches and it has not expired — **there is no database row**. Simple, but:

- You cannot **revoke** it (logout kills nothing — the token lives until `exp`).
- There is no **real rotation**: you mint a new pair, but the old one stays valid in parallel.
- There is no **reuse detection**: a stolen token works for days and nobody notices.

A **DB-backed refresh token** is an **opaque** value (random, no claims) whose SHA-256 hash lives in a table. Every `POST /auth/refresh`:

1. Marks the presented token `used_at` (single-use).
2. Mints a new token in the **same family** (the rotation lineage of that login).
3. If anyone replays an **already-rotated** token, that is the classic stolen-token signal → **the whole family is revoked**.

!!! info "Why opaque and not JWT-with-jti?"
    An opaque token forces the database to be the single source of truth. There are no claims to decode, no window between "signature valid" and "row revoked". The access token **stays** a stateless JWT (short, no per-request lookup) — only the refresh becomes DB-backed.

---

## Setup

The table is abstract in the SDK (`BaseUserRefreshTokenModel`) — your application ships the concrete table, just like `BaseUserTokenModel` / `BaseUserRecoveryCodeModel`. Use the `make_user_refresh_token_model` helper to bind the FK to your users table:

```python
# src/db/models/__init__.py
from tempest_fastapi_sdk import (
    make_user_refresh_token_model,
    make_user_token_model,
)

from src.db.models.user import UserModel

UserRefreshTokenModel = make_user_refresh_token_model(
    user_table="users",
    tablename="user_refresh_tokens",
    class_name="UserRefreshTokenModel",
)
```

Or, if you prefer a hand-written class (recommended in production, for refactors and stable imports):

```python
# src/db/models/user_refresh_token.py
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from tempest_fastapi_sdk import BaseUserRefreshTokenModel


class UserRefreshTokenModel(BaseUserRefreshTokenModel):
    """Concrete opaque refresh-token table."""

    __tablename__ = "user_refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

The table carries: `token_hash` (unique, indexed), `family_id` (rotation lineage), `expires_at`, `used_at` (rotated), and `revoked_at` (logout / family kill).

!!! warning "Migration required"
    It is a new table. Run `uv run tempest db revision -m "refresh tokens"` + `uv run tempest db upgrade` before shipping.

---

## Wiring

Pass the concrete model to `UserAuthService`. **That alone** turns on DB-backed mode — the router detects it and mounts `/auth/logout` on its own:

```python
# src/api/dependencies/services.py
from tempest_fastapi_sdk import UserAuthService

from src.db.models import UserModel, UserRefreshTokenModel, UserTokenModel


def get_auth_service() -> UserAuthService:
    """Build the bundled auth service in DB-backed refresh mode."""
    return UserAuthService(
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=settings,
        jwt_settings=settings,
        refresh_token_model=UserRefreshTokenModel,  # <- turns on DB-backed mode
    )
```

The refresh-token TTL reuses `JWT_REFRESH_TTL_SECONDS` (default 7 days) — no new setting.

!!! check "Migrating from stateless"
    Adopting DB-backed mode does not invalidate existing sessions up front, but old JWT refresh tokens **stop being accepted** (`/refresh` now looks them up in the database). Force a fresh login after the deploy, or run a grace period accepting both in your own handler.

---

## Rotation

Every login (or auto-activated signup / activation / reset / mfa-verify) creates a token in a **fresh family**. Every refresh rotates within the same family:

```text
login ──> tokenA (family F)
  │
  └─ POST /refresh (tokenA) ──> tokenA.used_at set, mint tokenB (family F)
        │
        └─ POST /refresh (tokenB) ──> tokenB.used_at set, mint tokenC (family F)
```

If an attacker steals `tokenA` and tries to use it **after** you have already rotated to `tokenB`:

```text
POST /refresh (tokenA)  # tokenA.used_at != null  ->  REUSE DETECTED
  └─ revoke the WHOLE family F (tokenA, tokenB, tokenC...)
  └─ 401
```

Result: both the attacker and the victim are logged out on their next attempt. The victim logs in again (a small annoyance), the attacker loses access (a big win).

!!! danger "Single-use is mandatory for reuse detection to work"
    The client **must** discard the old refresh token after each `/refresh` and keep the new one. Reusing a rotated token triggers the family kill — that is not a bug, it is the feature.

---

## Logout

With DB-backed mode on, the router mounts `POST /auth/logout`:

```python
import httpx


async def logout(refresh_token: str) -> None:
    """Revoke a session via the bundled logout endpoint."""
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        response = await client.post(
            "/auth/logout",
            json={"refresh_token": refresh_token, "all_sessions": False},
        )
        response.raise_for_status()  # 204 No Content
```

- `all_sessions=False` (default) — revoke only the token's **family** (that login).
- `all_sessions=True` — revoke **all** of the user's refresh tokens (log out everywhere).

The endpoint is **idempotent**: an unknown or already-revoked token still returns `204` and never leaks whether the token existed.

!!! note "Absent in stateless mode"
    Without `refresh_token_model` the `/auth/logout` route is **not mounted** — a stateless JWT cannot be revoked, so the endpoint would make no sense.

---

## Service direct

Those who build their own endpoints use the service without the router. The three methods:

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import UserAuthService


async def issue(service: UserAuthService, session: AsyncSession, user: object) -> None:
    """Emit a DB-backed pair, rotate it, then revoke the session."""
    access, refresh = await service.issue_token_pair(session, user)
    await session.commit()

    # Rotate: mark the old token used, mint a new one in the same family.
    _user, new_access, new_refresh = await service.refresh_tokens(
        session, refresh_token=refresh
    )
    await session.commit()

    # Logout: revoke the family (or all_sessions=True for everything).
    await service.revoke_refresh_token(session, refresh_token=new_refresh)
    await session.commit()
```

| Method | What it does |
| --- | --- |
| `issue_token_pair(session, user, *, family_id=None)` | Emit `(access, refresh)`. Opaque+persisted when a model is wired; stateless JWT otherwise. |
| `refresh_tokens(session, *, refresh_token)` | Rotate. Detects reuse → revokes the family. Returns `(user, access, refresh)`. |
| `revoke_refresh_token(session, *, refresh_token, all_sessions=False)` | Logout. Revokes the family (or everything). Idempotent. |

!!! tip "issue_jwt_pair still exists"
    The synchronous `issue_jwt_pair(user)` (pure stateless) stays available for back-compat. In DB-backed mode prefer `issue_token_pair`, which takes the `session` and persists the row.

---

## Security

- **Only the hash in the database.** The refresh token plaintext is returned **once** at issuance; the database stores only the SHA-256. A database leak yields no usable tokens.
- **Single-use + family.** Mandatory rotation + family kill turn a refresh-token theft from "access for days" into "one attempt and both go down".
- **Access token unchanged.** Still a short stateless JWT (`JWT_ACCESS_TTL_SECONDS`, default 1h) — no per-request lookup. DB-backed is for the refresh only.
- **CASCADE.** The FK with `ondelete="CASCADE"` wipes the tokens alongside the user.

!!! warning "Change the `JWT_SECRET`"
    The access token is still signed with `JWT_SECRET`. The default `"change-me-change-me-change-me-32"` is a placeholder — override it in production, otherwise the access token is forgeable (and then the DB-backed refresh does not save you).

---

## Next steps

- **[Auth flow (signup/reset)](auth-flow.md)** — the full flow where tokens are issued.
- **[MFA (TOTP / 2FA)](mfa.md)** — second factor; `mfa-verify` also emits the DB-backed pair.
- **[Security](security.md)** — rate limit, idempotency, CSRF middlewares.

### Recap

- `refresh_token_model=` turns on DB-backed mode — without it, stateless as always.
- Refresh becomes **opaque** (hash in the database); access stays a JWT.
- **Single-use** rotation + **family** = reuse detection → `POST /auth/refresh` kills the family on a theft.
- `POST /auth/logout` revokes a session (or all with `all_sessions=true`); mounted only in DB-backed mode.
