# Bundled auth flow (signup / activate / login / reset)

Since v0.31.0 the SDK ships the full local-account lifecycle — email + password signup, link-based activation, JWT-pair login, password reset — via `UserAuthService` + `make_auth_router`. **Endpoints ready to mount** (including `POST /auth/refresh` since v0.65.0), Jinja2 templates bundled, settings flags decide whether the link is emailed or returned in the response body, and four pre-thought modes for dev / staging / production / CI.

!!! tip "Jump straight to your case"
    The table below maps **what the user wants to do** → the section and
    endpoints. Start at your case; come back to [setup](#minimum-setup)
    only when you need to wire the pieces.

    | I want to… | Logged in? | Section | Endpoints |
    | --- | --- | --- | --- |
    | Create account + activate by email | — | [Setup](#minimum-setup) | `signup` → `activate/{token}` |
    | Log in | — | [Endpoints](#endpoints) | `login` |
    | **I forgot my password** | ❌ no | **[Password recovery](#password-recovery)** | `password-reset/request` → `password-reset/confirm` |
    | Change my password | ✅ yes | [Change password logged in](#change-your-password-logged-in) | `password-change` |
    | Change my email | ✅ yes | [Change email](#change-email-logged-in) | `email-change/request` → `email-change/confirm` |
    | Re-verify my current email | ✅ yes | [Re-verify email](#re-verify-the-current-email) | `email-verify/request` → `email-verify/confirm` |
    | **I lost access to my email** | ❌ no | **[Email recovery](#email-recovery)** | `email-recovery/request` → `email-change/confirm` |
    | My session expired | — | [Refresh](#renewing-the-session-with-the-refresh-token) | `refresh` |

    **The two "I forgot"**: lost password → [Password recovery](#password-recovery); lost mailbox → [Email recovery](#email-recovery).

## Recipe contents

1. **[Minimum setup](#minimum-setup)** — extras install + wiring four objects (`AsyncDatabaseManager`, `EmailUtils`, `UserAuthService`, `make_auth_router`).
2. **[Concrete UserTokenModel](#concrete-usertokenmodel)** — `BaseUserTokenModel` is abstract, your project owns the concrete table.
3. **[Endpoints](#endpoints)** — table of all endpoints + payload + behavior.
4. **[Password recovery](#password-recovery)** — the "forgot password" flow, step by step, plus changing the password while logged in.
5. **[Email change and recovery](#email-change-and-recovery)** — change email logged in, re-verify, and recover when the mailbox is lost.
6. **[Settings — environment variables](#settings-environment-variables)** — env vars in **groups** (JWT, password policy, email flow, TTL, URLs/templates, backend pages) — each in a typed table, not one blob.
5. **[Email anatomy: how link, template and URL fit together](#email-anatomy)** — disambiguates the three concepts that confuse readers the most.
6. **[Five operating modes](#five-operating-modes)** — production, dev with local SMTP (Mailhog / smtp4dev), dev without SMTP, CI without activation, and **backend-only** (links and pages served directly by the backend).
7. **[Mailhog vs smtp4dev — which to pick for local dev](#mailhog-vs-smtp4dev)** — comparison + copy-paste docker-compose snippets.
8. **[Customizing email templates](#customizing-templates)** — override `activation.html` and `password_reset.html` + variables exposed to the Jinja2 context.
9. **[Security](#security)** — token storage, TTL, anti-enumeration.
10. **[Next steps](#next-steps)**.

---

## Minimum setup

Requires:

- `[auth]` (bcrypt + PyJWT) — always required.
- `[email]` (aiosmtplib + Jinja2 + email-validator) — optional; when missing, the link lands in the response body instead of an email.

```bash
uv add "tempest-fastapi-sdk[auth,email]>=0.89.0"
```

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    EmailUtils,
    UserAuthService,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

app = FastAPI()


db = AsyncDatabaseManager(settings.DATABASE_URL)

# EmailUtils — only instantiate when [email] is installed AND you want real
# email (modes A and B below). In modes C and D, pass email=None to the service.
emails = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    from_addr=settings.SMTP_FROM_ADDR,
    template_dir="emails",  # directory where your custom templates live
)

auth_service = UserAuthService(
    db=db,                    # required for current_user_dependency (see final section)
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,   # mixes AuthSettings (see section 4)
    jwt_settings=settings,    # mixes JWTSettings
    email=emails,             # or None — controls real send vs link in body
)

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
    ),
)
```

!!! tip "Four-object TL;DR"
    `AsyncDatabaseManager` → connection. `EmailUtils` → SMTP + Jinja2. `UserAuthService` → business rules (5 methods). `make_auth_router` → glues it all into 5 HTTP endpoints.

---

## Concrete UserTokenModel

`BaseUserTokenModel` is abstract — your project owns the concrete table because the FK to `users` needs your table name. Example `src/db/models/user_token.py`:

```python
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseUserTokenModel


class UserTokenModel(BaseUserTokenModel):
    """Concrete token table for activation / reset / email-verification."""

    __tablename__ = "user_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

Re-export from `src/db/models/__init__.py` so Alembic picks it up:

```python
from src.db.models.user import UserModel
from src.db.models.user_token import UserTokenModel

__all__: list[str] = ["UserModel", "UserTokenModel"]
```

Generate the migration (first time round, bootstrap Alembic with `tempest db init`):

```bash
# First time only — generates alembic/, alembic.ini and env.py:
uv run tempest db init

# Then the usual revision cycle:
uv run tempest db revision -m "users + user_tokens"
uv run tempest db upgrade
```

---

## Endpoints

| Method | Path | Body / Output | Behavior |
|--------|------|---------------|----------|
| POST | `/auth/signup` | `SignupSchema` → `SignupResponseSchema` | Creates user. Not mounted when `AUTH_SIGNUP_ENABLED=false` (v0.272.0+). Emits email (modes A/B) **or** returns the link in the body (mode C). With `AUTH_AUTO_ACTIVATE=True`, the user is born active and the JWT pair returns immediately (mode D). |
| POST | `/auth/activate/{token}` | — → `ActivationResponseSchema` | Consumes token + sets `is_active=True` + issues JWT pair. |
| POST | `/auth/login` | `LoginSchema` → `LoginResponseSchema` | Email + password → JWT pair. Generic errors (no account enumeration). |
| GET | `/auth/me` *(v0.198.0+)* | — → `AuthUserSchema` | **Authenticated.** Returns the account owning the bearer token. Never serializes the password hash: the handler returns the whole model and the `response_model` filters. Swap in your own schema with `me_response_model=` to expose extra columns. |
| POST | `/auth/password-reset/request` | `PasswordResetRequestSchema` → `PasswordResetResponseSchema` | Always HTTP 202 + generic body. Link via email (A/B) or body (C). |
| POST | `/auth/password-reset/confirm` | `PasswordResetConfirmSchema` → `LoginResponseSchema` | Consumes token + writes new password + issues JWT pair. |
| POST | `/auth/password-change` | `PasswordChangeSchema` → `204` | **Authenticated** (bearer token). Change your own password: confirm the current password + write the new one. No email token. |
| POST | `/auth/email-change/request` *(v0.92.0+)* | `EmailChangeRequestSchema` → `EmailChangeResponseSchema` | **Authenticated.** Confirm the current password + email a confirmation link to the **new** address. Always 202. |
| POST | `/auth/email-change/confirm` *(v0.92.0+)* | `EmailChangeConfirmSchema` → `EmailChangeResponseSchema` | Consume the token + apply the new email + (optionally) notify the old address. |
| POST | `/auth/email-verify/request` *(v0.92.0+)* | — → `EmailChangeResponseSchema` | **Authenticated.** Re-send a verification link to the **current** email. |
| POST | `/auth/email-verify/confirm` *(v0.92.0+)* | `EmailChangeConfirmSchema` → `EmailChangeResponseSchema` | Consume the token + mark the account active. |
| POST | `/auth/email-recovery/request` *(v0.92.0+, opt-in)* | `EmailRecoveryRequestSchema` → `EmailChangeResponseSchema` | **Unauthenticated.** Recover an account whose mailbox is lost: password (+ MFA if enrolled). Only mounted with `AUTH_EMAIL_RECOVERY_ENABLED=True`. Always 202. |
| GET | `/auth/oauth/{provider}/login` *(v0.273.0+)* | — → `302` | Mints the `state`, writes it to an `HttpOnly` cookie and redirects to the provider. Mounted only with `AUTH_OAUTH_ENABLED=true`. See the [social-login recipe](oauth.en.md). |
| GET | `/auth/oauth/{provider}/callback` *(v0.273.0+)* | — → `LoginResponseSchema` | Checks the `state`, trades the `code`, resolves `(provider, subject)` in the database and issues **the same JWT pair** as `/auth/login` — `typ`, opaque refresh, rotation and `/auth/logout` included. |
| GET | `/auth/oauth/accounts` *(v0.273.0+)* | — → `list[OAuthAccountSchema]` | **Authenticated.** Providers linked to the account. An empty list is a 200, not a 404. |
| POST | `/auth/oauth/accounts/unlink` *(v0.273.0+)* | `OAuthUnlinkSchema` → `204` | **Authenticated.** Detaches one provider. 404 when it is not linked to this account. |
| POST | `/auth/refresh` *(v0.65.0+)* | `RefreshSchema` → `LoginResponseSchema` | Exchange a valid **refresh token** for a fresh JWT pair. **No email/password.** Rejects a replayed access token (401) and inactive accounts (403). |

!!! tip "`password-reset/confirm` vs `password-change` — which is which?"
    These are **different** flows, don't mix them up:

    - **`/auth/password-reset/confirm`** — the user **forgot** their
      password. They're not logged in; they prove identity with the
      **token** emailed to them. (See `/auth/password-reset/request`
      first.)
    - **`/auth/password-change`** — the user **remembers** their password
      and is **logged in**. They send the `access_token` in the
      `Authorization: Bearer …` header and re-confirm their
      `current_password`. No email or reset token involved. Returns
      **204** and the current tokens stay valid.

### Closed system — no registration door *(v0.272.0+)*

Not every service wants `POST /auth/signup` reachable. In a **closed**
system — accounts created by an administrator, never by whoever reaches the
door — the public registration route is exactly the hole you don't want open.

Turn it off with an environment variable:

```dotenv
AUTH_SIGNUP_ENABLED=false
```

Or per router, when one process mounts several and only one of them is
closed:

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    UserAuthService,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

app = FastAPI()

db = AsyncDatabaseManager(settings.DATABASE_URL)

auth_service = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        allow_signup=False,
    ),
)
```

The argument wins over the setting in both directions;
`allow_signup=None` (the default) defers to `AUTH_SIGNUP_ENABLED`. It is the
same pair `token_delivery` already formed with `AUTH_TOKEN_DELIVERY`.

What the service answers now:

```console
$ curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"whoever@example.com","password":"strong-pass-12"}'
404
```

!!! warning "Filtering `router.routes` after mounting is not equivalent"
    The workaround left before was dropping the route from the built
    list:

    ```python
    router.routes = [
        route for route in router.routes
        if getattr(route, "path", "") != "/auth/signup"
    ]
    ```

    It matches a **path string** — going quiet the day the path changes —
    and it says nothing to the schema: `/openapi.json` keeps advertising
    a door that is no longer there. With `AUTH_SIGNUP_ENABLED` the route
    is never registered, so it is gone from the application **and** from
    the schema.

!!! note "Activation stays mounted — deliberately"
    Turning signup off does **not** unmount `/auth/activate/{token}`. An
    account created by an administrator still has to be activated, and
    that endpoint is what consumes the emailed token. Nothing beyond
    `POST /auth/signup` moves.

**Recap.** `AUTH_SIGNUP_ENABLED=false` closes the public door without
touching any other endpoint; `allow_signup=` overrides per router; and the
route leaves the OpenAPI schema along with it, which the manual filter never
delivered.

### An account born with more than email and password *(v0.278.0+)*

Almost no product has an account that is only `email`, `password` and `name`.
There is the phone, the tax document, the flag that separates a customer from a
producer. Until now the way out was to unmount `POST /auth/signup` and write the
route by hand — and that hand-written route had to re-derive the password
policy, the 409 on a duplicate email, the activation branch and the JWT pair.
Four things the bundled route already gets right, and that start drifting one
release at a time.

Two arguments settle it: **`signup_schema`** replaces the request body,
**`on_signup`** writes the extra columns.

```python
# src/api/app.py

from fastapi import FastAPI
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    SignupSchema,
    UserAuthService,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel


class ProducerSignupSchema(SignupSchema):
    """This product's signup: email, password, name — and the rest."""

    phone: str | None = Field(default=None, max_length=20)
    is_producer: bool = Field(default=False)


async def write_profile(
    session: AsyncSession,
    user: UserModel,
    payload: ProducerSignupSchema,
) -> None:
    """Copy the product fields onto the freshly created row.

    Args:
        session (AsyncSession): The transaction the row was inserted in.
        user (UserModel): The instance to write onto.
        payload (ProducerSignupSchema): The validated body.
    """
    user.phone = payload.phone
    user.is_producer = payload.is_producer


app = FastAPI()

db = AsyncDatabaseManager(settings.DATABASE_URL)

auth_service = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        signup_schema=ProducerSignupSchema,
        on_signup=write_profile,
    ),
)
```

```console
$ curl -s -X POST localhost:8000/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"ana@example.com","password":"strong-pass-12","phone":"5511999999999","is_producer":true}'
{"user_id":"0e5cf2fc-…","activation_required":false,"activation_url":null,"access_token":"eyJ…","refresh_token":"eyJ…"}
```

Annotate the hook with **your** classes — the concrete model and the concrete
schema — as above: that is what makes `user.phone = …` type-check on your side.

The schema **must** subclass `SignupSchema` — the route reads `email`,
`password` and `name` off it by name, so a body without them fails
`make_auth_router` at wiring time, not on the first request. The new fields
reach `/openapi.json` too: a generated client sees `phone` and `is_producer`
without anyone hand-writing a schema.

!!! danger "The hook runs **before** the commit, and that is the point"
    `on_signup` is awaited right after the insert and before
    `session.commit()`, inside the **same** transaction. A hook that raises
    takes the account down with it.

    The alternative — writing the fields afterwards, in a second commit — is
    the classic defect: a rejected document leaves behind an account with an
    email, a password and none of the fields that make it usable. It shows up
    nowhere in the product, yet it holds the email (which is `UNIQUE`), answers
    `POST /auth/password-reset/request`, and blocks the correct signup with a
    409 nobody can explain.

!!! tip "What does not change yet"
    The response is still `SignupResponseSchema`. When the client needs the
    full profile right after signup, `GET /auth/me` already returns it — and
    takes your own schema through `me_response_model`.

**Recap.** `signup_schema` is the input contract — a `SignupSchema` subclass,
visible in OpenAPI. `on_signup` is where its fields become columns, inside the
insert's transaction. Nothing else has to leave the SDK for your service.


## Password recovery

The classic "I forgot my password". The user is **not logged in** and
proves identity with a **single-use token** delivered by email. Two steps.

### Base case (SPA / JSON)

**Step 1 — request the link.** The user types their email; the backend
always answers **202** with the same generic message (it never leaks
whether the email exists):

```bash
curl -X POST localhost:8000/auth/password-reset/request \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com"}'
```

```json
{ "message": "If the email exists, we sent a link.", "reset_url": null }
```

**Step 2 — set the new password.** The user opens the emailed link; the
front-end reads the `token` from the URL and sends it with the new
password. On success the password is replaced **and the user is logged
in** (a JWT pair comes back):

```bash
curl -X POST localhost:8000/auth/password-reset/confirm \
  -H "Content-Type: application/json" \
  -d '{"token": "abc123…", "new_password": "new-strong-pass-12"}'
```

```json
{
  "user_id": "7d8e4d5a-…",
  "access_token": "eyJhbGciOi…",
  "refresh_token": "eyJhbGciOi…",
  "mfa_required": false,
  "mfa_token": null
}
```

!!! check "Errors you'll see"
    - Unknown / already-used / expired token → **400**.
    - `new_password` violates `AUTH_PASSWORD_MIN_LENGTH` / complexity → **422**.
    - `request` **never** errors on a missing email — always **202**.

!!! note "Dev mode: link in the body (no SMTP)"
    Without the `[email]` extra, or with `AUTH_RETURN_TOKEN_IN_RESPONSE=True`,
    the `reset_url` comes back **in the `request` body** instead of by
    email — you can drive the whole flow with no inbox. In production it
    stays `null`.

??? note "No front-end? Backend-rendered HTML pages (Mode E)"
    With `AUTH_BACKEND_LINKS=True` the SDK mounts **GET/POST**
    `/auth/password-reset/{token}`: the emailed link points at the
    backend, which renders an HTML form, validates it and shows a
    success/error page — no front-end route at all. See
    [Five operating modes](#five-operating-modes) (Mode E).

### Change your password (logged in)

Different from a reset: here the user **remembers** their password and
**is logged in**. No token, no email — they send the `access_token` in
the header and re-confirm their `current_password`:

```bash
curl -X POST localhost:8000/auth/password-change \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "current-pass", "new_password": "new-strong-pass-12"}'
```

Returns **204**; current tokens stay valid (this endpoint doesn't revoke
sessions). Wrong `current_password` → **401**.

!!! tip "Reset vs change — which is which?"
    - **Forgot** the password, not logged in → **[Password recovery](#password-recovery)** (`password-reset/request` → `confirm`, prove with an emailed token).
    - **Remembers** the password, logged in → **change password** (`password-change`, prove with the current password in the header).

## Email change and recovery

The mirror of the password flow, but for the account **email** (v0.92.0+).
Three distinct cases — start at yours.

### Change email (logged in)

The base case: the user **is logged in** and wants to change their own
email. They confirm `current_password` and supply `new_email`; a
confirmation link goes to the **new** address. The email only changes
once that link is confirmed.

```bash
# Step 1 — request the change (authenticated). Always 202.
curl -X POST localhost:8000/auth/email-change/request \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"new_email": "new@example.com", "current_password": "current-pass"}'

# Step 2 — confirm (the front reads the token from the link sent to the new email).
curl -X POST localhost:8000/auth/email-change/confirm \
  -H "Content-Type: application/json" \
  -d '{"token": "abc123…"}'
```

On confirmation, if `AUTH_EMAIL_CHANGE_NOTIFY_OLD=True` (default) a
**security notice goes to the old address** — the banks/Google pattern,
so a hijacked account still alerts its owner.

!!! check "Errors"
    - Wrong current password → **401**.
    - `new_email` already in use (at request **or** confirm — a race) → **409**.
    - Invalid / expired / used token → **400**.

### Re-verify the current email

Re-sends a verification link to the **current address** — useful when the
activation email was lost. Authenticated, changes nothing; confirming the
link marks the account active.

```bash
curl -X POST localhost:8000/auth/email-verify/request \
  -H "Authorization: Bearer eyJhbGciOi…"
# then: POST /auth/email-verify/confirm  {"token": "…"}
```

### Email recovery

The "I forgot / lost access to my email". An **unauthenticated** and
**opt-in** endpoint — only mounted with `AUTH_EMAIL_RECOVERY_ENABLED=True`.
The user proves identity with their **password** (and an **MFA code** when
TOTP is enrolled) and supplies the new address; the confirmation link goes
to the **new** email and reuses the same `email-change/confirm`.

```bash
# Only exists if AUTH_EMAIL_RECOVERY_ENABLED=True. Always 202 (anti-enumeration).
curl -X POST localhost:8000/auth/email-recovery/request \
  -H "Content-Type: application/json" \
  -d '{
        "email": "old@example.com",
        "new_email": "new@example.com",
        "current_password": "current-pass",
        "mfa_code": "123456"
      }'
# then: POST /auth/email-change/confirm  {"token": "…"}  (link went to the new email)
```

!!! danger "Recovery is sensitive — enable it deliberately"
    This endpoint lets whoever has the **password** move the account to
    another email **without** accessing the old mailbox — an account-takeover
    vector if the password leaked. Rules:

    - **Opt-in**: off until `AUTH_EMAIL_RECOVERY_ENABLED=True`.
    - Always a generic **202** for every soft failure (unknown email,
      wrong password, missing/invalid `mfa_code`) — no account enumeration.
    - When TOTP is enrolled, a valid `mfa_code` is required.
    - Keep `AUTH_EMAIL_CHANGE_NOTIFY_OLD=True` so the old email is always
      alerted of the change.

!!! info "Where the new email is stored"
    The pending address travels in the token's `payload` column
    (`EMAIL_CHANGE`), not on the `UserModel`. Confirmation re-reads the
    `payload`, re-checks the address is still free (a request→confirm race
    → **409**) and only then writes it. See the 0.92.0 migration note.

### Renewing the session with the refresh token

The `access_token` is short-lived by design (`JWT_ACCESS_TTL_SECONDS`,
1 h default). When it expires, **don't force the user to log in again** —
exchange the `refresh_token` (long-lived, 7 days default) for a fresh
pair at `POST /auth/refresh`:

```bash
curl -X POST localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGciOi…"}'
```

```json
{
  "user_id": "7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab",
  "access_token": "eyJhbGciOi…(new)",
  "refresh_token": "eyJhbGciOi…(new)",
  "mfa_required": false,
  "mfa_token": null
}
```

The endpoint decodes the token, requires it to actually carry the
`refresh` claim (a replayed **access** token is rejected with **401**),
resolves the `sub` to an **active** user and mints a new pair.

!!! warning "Both tokens rotate"
    The response carries a **new** `refresh_token`. Persist that one and
    discard the token you sent. In **stateless** mode (the default) the
    SDK issues JWTs, so the old pair is not revoked — it stays valid
    until its own `exp`.

!!! tip "Need real revocation? The SDK already ships it"
    Don't write your own refresh-token table — since **v0.66.0** the SDK
    offers **opaque DB-backed** refresh tokens with single-use rotation,
    **reuse detection** (a stolen token revokes the whole family) and
    `POST /auth/logout`. It is opt-in: pass a `refresh_token_model` to
    `UserAuthService`. See the
    [Refresh tokens (rotation/revocation)](refresh-tokens.md) recipe.

!!! tip "When the refresh token expires too"
    Then no renewal is possible — the **401** is final and the client
    falls back to `POST /auth/login` with email + password.

No frontend? The service method behind the endpoint is public —
`await service.refresh_tokens(session, refresh_token=...)` returns
`(user, access_token, refresh_token)`.

---

## Settings — environment variables

Every knob in the flow comes from settings mixins. Mix them into your `Settings` class:

```python
# src/core/settings.py
from tempest_fastapi_sdk import (
    AuthSettings,
    BaseAppSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    ServerSettings,
)


class Settings(
    ServerSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    AuthSettings,
    BaseAppSettings,
):
    pass


settings: Settings = Settings()
```

!!! info "The attribute name **is** the env var name"
    Every attribute in the tables below is read from an environment variable of the **same name**, case-sensitive, **no prefix**. `AUTH_PASSWORD_MIN_LENGTH` in `.env` → `settings.AUTH_PASSWORD_MIN_LENGTH`. They all have defaults — you only set what you want to change.

The variables split across **two mixins** and **six concern groups**. They're separated on purpose: a password is not the same thing as an email, and authentication (JWT) is not the same thing as account activation.

### Group 1 — Authentication / JWT (`JWTSettings`)

Controls the signing and lifetime of the tokens login returns. **It's the same `JWT_SECRET` the `get_current_user` dependency uses to verify** (see [Getting the `current_user`](#getting-the-current_user-from-the-request)).

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `JWT_SECRET` | `str` (≥32 bytes) | `change-me-…-32` | HMAC secret that signs the JWT. **Must be overridden in production.** |
| `JWT_ALGORITHM` | `str` | `HS256` | JOSE algorithm. `HS256`/`HS512` (symmetric secret) or `RS256` (key pair). |
| `JWT_ACCESS_TTL_SECONDS` | `int` (≥1) | `3600` | **Access token** lifetime (1 h). Short by design — renew via refresh. |
| `JWT_REFRESH_TTL_SECONDS` | `int` (≥1) | `604800` | **Refresh token** lifetime (7 days). |
| `JWT_ISSUER` | ``str | None`` | `None` | `iss` claim. `None` omits the claim. |

!!! danger "The default `JWT_SECRET` leaks tokens"
    The default `change-me-change-me-change-me-32` exists only to boot locally. In production, **anyone** with the default can forge a valid JWT. Generate a strong secret (`openssl rand -base64 48`) and inject it via a secret manager — never commit it.

### Group 2 — Password policy (`AuthSettings`)

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_PASSWORD_MIN_LENGTH` | `int` (≥1) | `12` | Minimum length accepted on signup **and** reset. |
| `AUTH_PASSWORD_MAX_BYTES` | `int` (≥1) | `72` | Maximum length in UTF-8 **bytes**. This is bcrypt's hard limit. |
| `AUTH_PASSWORD_REQUIRE_COMPLEXITY` | `bool` | `false` | `true` = require 1 lowercase + 1 uppercase + 1 digit + 1 special character. |

These two interact — **this is where it usually gets confusing**. The exact rule:

- **`complexity=false` (default):** only length matters. Any password with `≥ AUTH_PASSWORD_MIN_LENGTH` characters passes, with no composition requirement.
- **`complexity=true`:** on top of the 4 character classes, the **effective** length floor becomes `max(AUTH_PASSWORD_MIN_LENGTH, 8)`. That is, an `AUTH_PASSWORD_MIN_LENGTH` below 8 is **ignored** while complexity is on.

Decision table:

| `MIN_LENGTH` | `REQUIRE_COMPLEXITY` | Password accepted when |
|--------------|----------------------|------------------------|
| `12` | `false` | `≥ 12` chars, any composition |
| `4` | `false` | `≥ 4` chars, any composition (low floor, dev-only) |
| `4` | `true` | `≥ 8` chars (floor 8 **overrides** the 4) **+** the 4 classes |
| `16` | `true` | `≥ 16` chars **+** the 4 classes |

!!! warning "The floor is the single source of truth"
    The request schemas (`SignupSchema`, `PasswordResetConfirmSchema`) impose **no** length bound of their own — they delegate to these two vars. Lowering `AUTH_PASSWORD_MIN_LENGTH` to `4` genuinely relaxes validation on the route too. There is no hidden second limit in the schema "protecting" you.

!!! danger "The ceiling counts bytes, not characters"
    `AUTH_PASSWORD_MAX_BYTES` exists because bcrypt **refuses** input over 72 bytes: `hashpw` raises `ValueError`, and without the ceiling that surfaced as a **500** on signup / reset / password change. Bytes is the unit the hash sees, and 72 bytes arrive well before 72 characters on non-ASCII text — an emoji costs 4 bytes, an accented letter 2, so `"🔒" * 19` (19 characters) is already over the limit and answers **422**. Only raise the value if you swap the hasher for one without the limit.

### Group 3 — Email flow control (`AuthSettings`)

Decide **whether** and **how** the link reaches the user. They map directly to the [five operating modes](#five-operating-modes).

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_AUTO_ACTIVATE` | `bool` | `false` | `true` = user is born active, skips activation, signup returns the JWT pair directly (Mode D). **Never in production.** |
| `AUTH_RETURN_TOKEN_IN_RESPONSE` | `bool` | `false` | `true` = activation/reset link goes in the JSON body instead of the email (Mode C). |

### Group 4 — Account token TTL (`AuthSettings`)

Lifetime of the **single-use** tokens (activation / reset) — distinct from the Group 1 JWTs.

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_ACTIVATION_TTL_SECONDS` | `int` (≥60) | `604800` | Activation token lifetime (7 days). |
| `AUTH_PASSWORD_RESET_TTL_SECONDS` | `int` (≥60) | `3600` | Reset token lifetime (1 h). Shorter is safer. |

### Group 5 — Email URLs and templates (`AuthSettings`)

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_ACTIVATION_URL_TEMPLATE` | `str` | `http://localhost:3000/activate?token={token}` | URL that goes in the email; `{token}` is substituted. **Points at the frontend** (except in Mode E). |
| `AUTH_PASSWORD_RESET_URL_TEMPLATE` | `str` | `http://localhost:3000/reset-password?token={token}` | Same, for reset. |
| `AUTH_ACTIVATION_TEMPLATE` | `str` | `activation.html` | Jinja2 filename of the activation **email HTML**, resolved against `EmailUtils.template_dir`. |
| `AUTH_PASSWORD_RESET_TEMPLATE` | `str` | `password_reset.html` | Same, for reset. |

!!! warning "URL template ≠ Jinja2 template"
    `*_URL_TEMPLATE` is a `.format()` string with `{token}` — it's the **link**. `*_TEMPLATE` is the name of an `.html` file — it's the **email that wraps the link**. Confusing the two is the #1 mistake. Full detail in [Email anatomy](#email-anatomy).

### Group 6 — Backend-rendered pages (Mode E, `AuthSettings`)

Only relevant when `AUTH_BACKEND_LINKS=true`. See [Mode E](#five-operating-modes) for the full flow.

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_BACKEND_LINKS` | `bool` | `false` | `true` = mounts 5 extra HTML endpoints; the email link points at the **backend**, not the frontend. |
| `AUTH_LOGIN_URL` | ``str | None`` | `None` | Login URL on the "go to login" button of success pages. `None` hides the button. |
| `AUTH_ACTIVATION_SUCCESS_TEMPLATE` | `str` | `activation_success.html` | Activation OK HTML page. |
| `AUTH_ACTIVATION_ERROR_TEMPLATE` | `str` | `activation_error.html` | Activation error HTML page. |
| `AUTH_PASSWORD_RESET_FORM_TEMPLATE` | `str` | `password_reset_form.html` | New-password HTML form. |
| `AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE` | `str` | `password_reset_success.html` | Reset OK HTML page. |
| `AUTH_PASSWORD_RESET_ERROR_TEMPLATE` | `str` | `password_reset_error.html` | Reset error HTML page. |

### Group 7 — Language of emails and pages (`AuthSettings`)

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_DEFAULT_LOCALE` | `str` | `pt-BR` | Language of the bundled **emails** and **HTML pages** when no other signal exists. Accepts `pt-BR` and `en-US` (normalized: `PT-BR`, `pt_br`, `ptbr` → `pt-BR`). |
| `AUTH_STAMP_LOCALE_IN_LINK` | `bool` | `True` | Stamps `?lang=<locale>` onto the emailed link, so the page opens in the language of the email that produced it. |

There's a whole section dedicated to this, explained step by step:
[Email and page language (i18n)](#email-and-page-language-i18n).

### Group 8 — Token delivery: bearer / cookie / both (`AuthSettings`) *(v0.87.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_TOKEN_DELIVERY` | ``"bearer" | "cookie" | "both"`` | `bearer` | How login/refresh return the JWT pair. See [Token delivery](#token-delivery). |
| `AUTH_COOKIE_SECURE` | `bool` | `true` | Flag cookies as `Secure` (HTTPS only). **Turn off only on plain HTTP** — otherwise the browser drops the cookie. |
| `AUTH_COOKIE_SAMESITE` | ``"lax" | "strict" | "none"`` | `lax` | A cross-site SPA needs `none` (+ `Secure=true`). |
| `AUTH_COOKIE_DOMAIN` | ``str | None`` | `None` | Cookie `Domain`. `None` = exact host. Use `.example.com` to share across subdomains. |
| `AUTH_ACCESS_COOKIE_NAME` | `str` | `access_token` | Access-token cookie name. |
| `AUTH_REFRESH_COOKIE_NAME` | `str` | `refresh_token` | Refresh-token cookie name (scoped to the refresh endpoint path). |

### Group 9 — Closed system (`AuthSettings`) *(v0.272.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_SIGNUP_ENABLED` | `bool` | `true` | `false` = `make_auth_router` does not mount `POST /auth/signup`; the route is gone from the application and from the OpenAPI schema. Activation, reset and everything else stay intact. See [Closed system](#closed-system-no-registration-door-v02720). |

### Group 10 — Social login (`AuthSettings` + `OAuthSettings`) *(v0.273.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_OAUTH_ENABLED` | `bool` | `false` | `true` mounts the four `/auth/oauth/*` routes. Requires a client in `oauth_clients=`, an `oauth_account_model` on the service and a `name` column on the user model — each missing piece raises `RuntimeError` at router construction. |
| `AUTH_OAUTH_STATE_COOKIE_NAME` | `str` | `oauth_state` | Cookie carrying the CSRF `state` between the redirect and the callback. Always `HttpOnly` and always `SameSite=Lax`. |
| `AUTH_OAUTH_STATE_TTL_SECONDS` | `int` | `600` | How long the user has to finish consenting at the provider. |
| `AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL` | `bool` | `false` | `true` attaches a new identity to an existing account whose email matches — **only** when `email_verified is True`. Off by default because this is the knob that turns a provider's word about an email into control of an account. |
| `AUTH_OAUTH_ALLOW_ACCOUNT_CREATION` | `bool` or empty | empty | Whether the callback may create an account. Empty inherits `AUTH_SIGNUP_ENABLED`, so closing signup closes this door with it. |
| `OAUTH_REDIRECT_BASE_URL` | `str` | `""` | Public origin of the service. The redirect URI is derived from it by `oauth_redirect_uri(provider)`. |
| `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET` | `str` | `""` | Credentials from the Google console. `google_kwargs()` builds the client. |
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | `str` | `""` | Credentials of the GitHub OAuth app. `github_kwargs()` builds the client. |

### Group 11 — One link at a time (`AuthSettings`) *(v0.274.0+)*

| Env var | Type | Default | What it does |
|---------|------|---------|--------------|
| `AUTH_SINGLE_ACTIVE_TOKEN` | `bool` | `true` | Issuing an account token spends that user's other unused tokens **of the same purpose**, so only the newest activation / reset / email-change link opens the account. `false` restores the pre-v0.274.0 behaviour. See the [migration guide](../migration.en.md#02740-only-the-newest-link-opens-the-account). |

!!! danger "Without this, the user's correct reaction does not close the window"
    An attacker fires `POST /auth/password-reset/request` at a victim. The
    victim receives a recovery email they never asked for, gets suspicious,
    and **on their own** requests a reset and completes it — exactly the right
    reaction. With several links alive, the attacker's link stays valid until
    `AUTH_PASSWORD_RESET_TTL_SECONDS`, and a token leaked through any side
    channel (a proxy log, a browser extension, a forwarded email, a shared
    device) still resets the password after the incident looked handled.

    The scope is narrow in both directions: same purpose only (requesting a
    reset does not kill a pending email change) and same user only. The old row
    is marked `used_at`, not deleted, so the audit trail stays.

!!! note "MFA / TOTP has its own vars"
    When `AUTH_MFA_ENABLED=true`, `AuthSettings` also exposes `AUTH_MFA_ISSUER`, `AUTH_MFA_RECOVERY_CODES_COUNT`, `AUTH_MFA_TOKEN_TTL_SECONDS` and `AUTH_MFA_VERIFY_WINDOW`. They're out of scope for this recipe (signup/activate/login/reset) — covered in the MFA recipe.

---

## Email anatomy

Three different concepts that look the same. Here's what each one does, exactly once, in pseudo-code:

```text
1. SDK generates a random opaque token (64-char string).
2. AUTH_ACTIVATION_URL_TEMPLATE.format(token=…)  →  link with the token embedded.
3. Renders AUTH_ACTIVATION_TEMPLATE (Jinja2 HTML) passing { user, activation_url, expires_at, expires_at_str }.
4. EmailUtils.send(to=user.email, subject=..., html=<rendered HTML>).
```

In prose:

- **Opaque token** — random string the SDK generates, hashes (SHA-256), and stores in the `user_tokens` table. The plaintext leaves over email **only once**; the database keeps just the hash.
- **URL template** (`AUTH_ACTIVATION_URL_TEMPLATE`) — literal format string used to build the URL the user will click. **It points at the frontend, not the backend.** The frontend reads `?token=…` from the query string and calls `POST /auth/activate/{token}` on the backend.
- **Jinja2 template** (`AUTH_ACTIVATION_TEMPLATE`) — filename of the HTML template inside `EmailUtils.template_dir`. It's **the HTML body of the email**, not the URL. It receives the `{ user, activation_url, expires_at, expires_at_str }` context and renders the final markup. Use `{{ expires_at_str }}` in the template — it's the expiry already formatted short (e.g. `2026-06-21 23:25 (UTC)`, no seconds); `expires_at` is still available as the raw `datetime` if you want to format it yourself.

!!! warning "URL template ≠ Jinja2 template"
    `AUTH_ACTIVATION_URL_TEMPLATE` is a Python `.format()`-style string with just the `{token}` placeholder. **Don't confuse it** with the `.html` file Jinja2 renders. The formatted URL **is injected as a variable** into the Jinja2 context under the name `activation_url`, and the HTML template wraps it in a button.

Visual flow:

```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend
    participant API as Backend (SDK)
    participant E as SMTP
    participant DB as Database

    U->>F: fills email + password
    F->>API: POST /auth/signup
    API->>DB: INSERT user (is_active=false) + INSERT token (hash, TTL)
    API->>API: token plaintext + AUTH_ACTIVATION_URL_TEMPLATE.format(token=…)
    API->>API: render Jinja2 (user, activation_url, expires_at)
    alt AUTH_RETURN_TOKEN_IN_RESPONSE=false
        API->>E: SMTP send (rendered HTML)
        API->>F: 201 + {message: "check your email"}
    else AUTH_RETURN_TOKEN_IN_RESPONSE=true
        API->>F: 201 + {activation_url: "https://app/activate?token=..."}
    end
    Note right of F: user (or dev) opens the URL
    F->>API: POST /auth/activate/{token}
    API->>DB: hash(token) match? expired? already used?
    API->>F: 200 + JWT pair
```

---

## Email and page language (i18n)

Since **v0.59.0**, the emails and HTML pages the SDK ships out of the box
speak **two languages**: 🇧🇷 **Brazilian Portuguese (`pt-BR`)** — which is
the **default** — and 🇺🇸 **US English (`en-US`)**. You don't need to
create any template for this to work. 🚀

### The golden rule (memorize just this)

**One flow, one language.** The email and the page that email opens
resolve the language **the same way**, in the same order — the first
signal that exists wins:

| # | Signal | Where it comes from |
|---|--------|---------------------|
| 1 | `?lang=` on the link | The SDK stamps the language **that** email went out in onto its own link. |
| 2 | `user.locale` | The preference **you** stored on the user row (the `LocaleColumnMixin` column). It is what the **email** resolves from — there is no link yet when it is built. |
| 3 | `Accept-Language` | The browser of whoever clicked (pages only — an email has no browser). |
| 4 | `AUTH_DEFAULT_LOCALE` | The last resort. |

!!! info "Why does the link outrank the stored preference?"
    Because it records the language of **that** email. If the person
    changes their language in the app between the send and the click, the
    page still matches the message they are reading. Preferring the row
    would recreate the very split this order exists to close, only
    inverted.

!!! warning "Through v0.263.0 the two ends disagreed"
    The email read **only** `AUTH_DEFAULT_LOCALE` and the page negotiated
    **only** `Accept-Language`. A signup with the `pt-BR` default and an
    English browser sent the email in Portuguese and opened the page in
    English — and **no configuration fixed it**, because setting
    `AUTH_DEFAULT_LOCALE=pt-BR` was exactly the value the header
    overrode. If you store the user's language, it now drives both ends.

!!! info "Why does the link need to carry the language?"
    At signup the account was just born: there's no stored preference
    yet. The only place the email's choice survives until the click is
    the link itself — hence `?lang=`. Turn it off with
    `AUTH_STAMP_LOCALE_IN_LINK=false` if your front-end route rejects
    unknown query parameters.

### Step 1 — choose the default language

Just one environment variable. That's it:

```env
# .env
AUTH_DEFAULT_LOCALE=pt-BR   # default — you can even omit it
```

Want everything in English? Switch to:

```env
AUTH_DEFAULT_LOCALE=en-US
```

!!! tip "You don't need the exact case/format"
    The value is normalized for you. All of these become `pt-BR`:
    `pt-BR`, `PT-BR`, `pt_br`, `ptbr`, `pt`. And all of these become
    `en-US`: `en-US`, `EN_us`, `enus`, `en`. If you type something the
    SDK doesn't know (like `klingon`), it falls back to the `pt-BR`
    default instead of crashing.

### Step 2 — (optional) store the user's preference

If your service lets people pick a language, store it on their row with
`LocaleColumnMixin` — and the SDK honors it on both ends, with no further
configuration:

```python
from tempest_fastapi_sdk import BaseUserModel, LocaleColumnMixin


class UserModel(LocaleColumnMixin, BaseUserModel):
    """The BCP-47 `locale` column comes from the mixin."""

    __tablename__ = "users"
```

Nothing breaks without the mixin: the SDK reads the attribute with
`getattr`, so a model without the column simply falls through to the next
signal.

```text
user.locale = "pt-BR", en-US browser  →  email AND page in Portuguese
link says ?lang=pt-BR, row says en-US →  page in Portuguese (matches the email)
neither, en-US browser                →  page in English
none of the above                     →  AUTH_DEFAULT_LOCALE
```

### Step 3 — (optional) translate/customize it yourself

The bundled templates live in per-language subfolders (`pt-BR/`,
`en-US/`). To change the text/look of **just** one language, drop a file
with the same name into the right subfolder of your `template_dir`
(e.g. `template_dir/en-US/activation_success.html`). The full lookup
order is in the "Override per language" tip further down, under
**Mode E**.

### Bonus — short, readable expiry timestamp

The email used to show the raw, ugly expiry like this:

```text
This link expires at 2026-06-21 23:25:49.742054+00:00
```

Now the SDK injects an `expires_at_str` variable into the template,
already formatted and **without seconds**, in the language's format:

| Language | How it looks |
|----------|--------------|
| `pt-BR` | `21/06/2026 23:25 (UTC)` |
| `en-US` | `2026-06-21 23:25 (UTC)` |

In your custom templates, use `{{ expires_at_str }}` (short and pretty).
If you want to format it yourself, the raw `datetime` is still available
in `{{ expires_at }}`.

!!! check "Recap"
    - **One flow, one language**: email and page resolve in the same
      order — the link's `?lang=` → `user.locale` → `Accept-Language` →
      `AUTH_DEFAULT_LOCALE`.
    - **Stored the user's language?** It drives both ends.
    - **The default is `pt-BR`.** Set `en-US` if you want English.
    - `AUTH_STAMP_LOCALE_IN_LINK=false` turns the `?lang=` stamp off.
    - Use `{{ expires_at_str }}` to show the expiry without seconds.

---

## Five operating modes

| Mode | When to use | Flags | Where the link appears |
|------|-------------|-------|------------------------|
| **A. Production (SPA)** | Public SaaS, real email, frontend SPA owns the pages | `AUTH_AUTO_ACTIVATE=false`<br>`AUTH_RETURN_TOKEN_IN_RESPONSE=false`<br>`AUTH_BACKEND_LINKS=false`<br>Real SMTP (Mailgun, SES, Postmark…) | The user's inbox → frontend processes the token |
| **B. Local dev with fake SMTP** | Daily development without sending real email | `AUTH_AUTO_ACTIVATE=false`<br>`AUTH_RETURN_TOKEN_IN_RESPONSE=false`<br>SMTP pointing at Mailhog (`localhost:1025`) or smtp4dev (`localhost:2525`) | Mailhog/smtp4dev web UI at `localhost:8025` / `localhost:5000` |
| **C. Dev without SMTP** | Quick validation without spinning up any email container | `AUTH_AUTO_ACTIVATE=false`<br>`AUTH_RETURN_TOKEN_IN_RESPONSE=true`<br>`email=None` or invalid SMTP | HTTP signup response body |
| **D. CI / tests** | Test suite that doesn't exercise activation | `AUTH_AUTO_ACTIVATE=true` | Nowhere — signup returns the JWT pair directly |
| **E. Backend-only** *(v0.32.0+)* | You want 100% control on the backend — zero responsibility on the frontend. Ideal for APIs without a SPA, MVPs, internal tools. | `AUTH_BACKEND_LINKS=true`<br>URL templates point at the **backend** (`https://api.example.com/auth/activate/{token}`)<br>`AUTH_LOGIN_URL=https://app.example.com/login` (optional — shows a "Go to login" button on the HTML pages) | The backend renders HTML success/error directly — the user only clicks the link in the email |

### Mode A — production

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USERNAME=postmaster@mg.example.com
SMTP_PASSWORD=...                          # secret, don't commit
SMTP_FROM_ADDR=noreply@example.com
AUTH_ACTIVATION_URL_TEMPLATE=https://app.example.com/activate?token={token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=https://app.example.com/reset?token={token}
```

Flow: signup → real email lands in the inbox → user clicks → frontend calls `POST /auth/activate/{token}` → login.

### Mode B — dev with local SMTP (Mailhog or smtp4dev)

Same `.env` as mode A, but point SMTP at a local container that **intercepts** the emails instead of actually mailing them. **Use this mode in day-to-day dev** — the flow is identical to production, so you catch template bugs, encoding issues, charset problems, etc. while avoiding real-email spam.

```bash
# .env.dev
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false
SMTP_HOST=localhost
SMTP_PORT=1025                             # Mailhog SMTP default
SMTP_USERNAME=                             # empty — Mailhog doesn't authenticate
SMTP_PASSWORD=
SMTP_FROM_ADDR=dev@local
AUTH_ACTIVATION_URL_TEMPLATE=http://localhost:5173/activate?token={token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=http://localhost:5173/reset?token={token}
```

Open `http://localhost:8025` (Mailhog) or `http://localhost:5000` (smtp4dev) to inspect the intercepted emails. See **[Mailhog vs smtp4dev](#mailhog-vs-smtp4dev)** below.

### Mode C — dev without SMTP (link in body)

No SMTP container at all. Signup returns the activation link in the JSON body:

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=true
AUTH_ACTIVATION_URL_TEMPLATE=http://localhost:5173/activate?token={token}
```

Request:

```bash
curl -X POST localhost:8000/auth/signup \
  -H 'content-type: application/json' \
  -d '{"email":"dev@local","password":"abcdefghijkl","name":"Dev"}'
```

Response (the actual `SignupResponseSchema` shape):

```json
{
  "user_id": "0193e9ea-7c4b-7c8e-bc05-2a3a8d9f7e10",
  "activation_required": true,
  "activation_url": "http://localhost:5173/activate?token=aBcD...xYz",
  "access_token": null,
  "refresh_token": null
}
```

Paste the URL into the browser / curl to exercise `POST /auth/activate/{token}`.

### Mode D — CI / tests (skip everything)

```bash
AUTH_AUTO_ACTIVATE=true
```

Signup skips activation entirely and returns `{access_token, refresh_token}` straight away. Use **only in tests** or when the product is internal and every user is already trusted.

### Mode E — backend-only (v0.32.0+)

When you'd rather have the **whole** link experience happen on the backend, with no frontend page in the loop, flip `AUTH_BACKEND_LINKS=True`. The router then mounts **five extra HTML endpoints** — `GET /auth/activate/{token}`, `GET /auth/password-reset/{token}`, `POST /auth/password-reset/{token}` (form-encoded), `GET /auth/email-change/{token}` and `GET /auth/email-verify/{token}`. The email points the user straight at those endpoints; the backend activates the account / processes the reset / renders HTML success or error — using bundled Jinja2 templates you can shadow.

```bash
# .env — Mode E (backend-only)
AUTH_BACKEND_LINKS=true
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false

# IMPORTANT: URL templates point at the BACKEND, not the frontend.
AUTH_ACTIVATION_URL_TEMPLATE=https://api.example.com/auth/activate/{token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=https://api.example.com/auth/password-reset/{token}

# Optional: your login URL. When set, the backend-rendered success/error
# pages display a "Go to login" button. When null, the button is hidden
# (pure server-side, zero coupling with any frontend).
AUTH_LOGIN_URL=https://app.example.com/login

SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_FROM_ADDR=noreply@example.com
```

!!! danger "Link returns 404? Align the template with the router's mount prefix"
    The endpoints above are **relative to wherever you mount `make_auth_router`**.
    If you include the router under a prefix — common to separate business routes:

    ```python
    app.include_router(make_auth_router(...), prefix="/api")
    ```

    then the real activation route becomes `GET /api/auth/activate/{token}`, **not**
    `/auth/activate/{token}`. But `AUTH_ACTIVATION_URL_TEMPLATE` is a literal string
    — it has **no** idea about the prefix. If the template points at
    `.../auth/activate/{token}` (without `/api`), the email link hits a
    non-existent route and returns **404**, even though signup returned `201`.

    ```bash
    # ❌ router mounted with prefix="/api", but the template lacks /api → 404
    AUTH_ACTIVATION_URL_TEMPLATE=https://api.example.com/auth/activate/{token}

    # ✅ template aligned with the actual mount prefix
    AUTH_ACTIVATION_URL_TEMPLATE=https://api.example.com/api/auth/activate/{token}
    ```

    Two checks when configuring Mode E: **(1)** the host is the backend's
    **public domain** (never `localhost` — the link runs in the user's browser,
    not on the server); **(2)** the path includes **every prefix** you mounted the
    router under.

Flow:

```mermaid
sequenceDiagram
    participant U as User
    participant E as Inbox
    participant API as Backend
    participant DB as Database

    U->>API: POST /auth/signup
    API->>DB: INSERT user (is_active=false) + token (hash, TTL)
    API->>E: email with link https://api.example.com/auth/activate/{token}
    U->>E: clicks the link
    E->>API: GET /auth/activate/{token}
    API->>DB: hash(token) valid? unused? not expired?
    alt valid token
        API->>DB: is_active=true + token.used_at=now
        API->>U: HTML activation_success.html ("Go to login" button when AUTH_LOGIN_URL set)
    else invalid / expired token
        API->>U: HTML activation_error.html (HTTP 400)
    end
```

Password reset follows the same pattern: GET renders an HTML form; POST (form-encoded) consumes the token and renders success/error.

**Bundled HTML templates (shadow by dropping the same filename under `template_dir`):**

| Template | Endpoint that renders it | Jinja2 variables exposed |
|----------|--------------------------|--------------------------|
| `activation_success.html` | `GET /auth/activate/{token}` (success) | `user`, `login_url` |
| `activation_error.html` | `GET /auth/activate/{token}` (failure) | `reason`, `login_url` |
| `password_reset_form.html` | `GET /auth/password-reset/{token}` | `user`, `form_action`, `min_length`, `error`, `login_url` |
| `password_reset_success.html` | `POST /auth/password-reset/{token}` (success) | `user`, `login_url` |
| `password_reset_error.html` | `POST /auth/password-reset/{token}` (bad token) | `reason`, `login_url` |

**To override:** pass `template_dir` to `make_auth_router` and add files with the same filenames.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserAuthService, make_auth_router

from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
)
app = FastAPI()


app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        template_dir="src/templates/auth",   # optional
    ),
)
```

!!! tip "Override per language (since v0.59.0)"
    The bundled templates now live in **per-language subfolders**
    (`pt-BR/` and `en-US/`). You have two ways to override, and the SDK
    searches in this order (the first that exists wins):

    1. `template_dir/<locale>/activation_success.html` — override **just
       that language** (e.g. `src/templates/auth/pt-BR/...`).
    2. `template_dir/activation_success.html` — **flat** override, applies
       to every language (backward compatible with pre-0.59.0; keeps
       working with no changes).

    In short: if you already had flat templates, **you don't need to
    change anything**. If you want a different look per language, create
    the subfolder.

**Mode E trade-offs:**

- ✅ **Zero frontend dependency** — the backend is the single source of truth for the auth flow.
- ✅ **MVP in minutes** — no need to create SPA routes to process tokens.
- ✅ **Works in frontend-less projects** — public APIs, intranets, internal tooling.
- ⚠️ **JWT is not auto-delivered** — after activation, the user signs in manually (clicking "Go to login" and entering credentials). By design: zero token leak via URL, history, or server logs.
- ⚠️ **Requires the `[email]` extra** (Jinja2) to render the HTML pages — same dependency as the email template renderer.
- ⚠️ **No CSRF on the reset form** — the HTML form posts traditionally without a CSRF token. The reset token is one-shot + short TTL + bound to a single user, but consider plugging in `CSRFMiddleware` if attackers can predict active URLs.

The **JSON** endpoints (`POST /auth/activate/{token}`, `POST /auth/password-reset/confirm`) are still mounted — you can mix Mode E with SPA endpoints.

---

## Token delivery

*(v0.87.0+)*

By default login returns `access_token` / `refresh_token` **in the body** and the client replays them as `Authorization: Bearer <token>`. Great for mobile/API clients, but a browser SPA has to stash the token somewhere JS can reach — exposed to XSS. `AUTH_TOKEN_DELIVERY` lets you choose.

| Mode | What changes | For whom |
|------|--------------|----------|
| `bearer` *(default)* | Tokens **in the body only**. Historical, backward-compatible behaviour. | Mobile, APIs, clients that send `Authorization`. |
| `cookie` | Tokens set as **`HttpOnly`** cookies on the same paths (`/auth/login`, `/auth/refresh`, `/auth/logout`); the body returns the tokens as `null`. | Browser SPAs — the token is never visible to JS (XSS defense). |
| `both` | Bearer endpoints stay at `/auth/*` **and** a parallel cookie set is mounted at `/auth/cookie/*`. | One backend serving web (cookie) **and** mobile (bearer) at once. |

!!! danger "The cookie `Secure` flag requires HTTPS"
    With `AUTH_COOKIE_SECURE=true` (default) the browser **only** sends the cookie back over HTTPS. If the backend is served over **plain HTTP**, the cookie is dropped and the session never persists (login looks like it works but nothing stays logged in). In production, put TLS in front and keep it `true`; on local HTTP dev use `AUTH_COOKIE_SECURE=false`.

### Cookie mode

```bash
# .env
AUTH_TOKEN_DELIVERY=cookie
AUTH_COOKIE_SECURE=true          # false only on plain HTTP
AUTH_COOKIE_SAMESITE=lax         # "none" (+Secure) if the SPA is cross-site
```

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserAuthService, make_auth_router

from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
)
app = FastAPI()


app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))
```

Frontend flow — **stores no token at all**, just calls the endpoints with `credentials: "include"`:

```javascript
// login: the browser stores the HttpOnly cookies itself
await fetch("/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});

// authenticated requests: the access cookie rides along automatically
await fetch("/api/me", { credentials: "include" });

// renew the session: the refresh cookie is read on the backend, no body
await fetch("/auth/refresh", { method: "POST", credentials: "include" });

// logout: clears the cookies (and revokes the refresh family if a refresh_token_model is wired)
await fetch("/auth/logout", { method: "POST", credentials: "include" });
```

The `current_user` dependency (see [Getting the `current_user`](#getting-the-current_user-from-the-request)) now **reads the access token from the cookie** automatically whenever delivery involves cookies — the `Authorization` header still wins if present.

### Both mode

```bash
AUTH_TOKEN_DELIVERY=both
```

Mounts both sets, no route collision:

```text
# bearer (body):
POST /auth/login
POST /auth/refresh
POST /auth/logout

# cookie (HttpOnly):
POST /auth/cookie/login
POST /auth/cookie/refresh
POST /auth/cookie/logout
```

!!! info "What stays in the body"
    Cookie delivery covers the **login / refresh / logout** lifecycle. Activation (`POST /auth/activate/{token}`), signup auto-login (`AUTH_AUTO_ACTIVATE`) and `POST /auth/mfa/verify` still return the JWT pair in the body, regardless of `AUTH_TOKEN_DELIVERY`.

!!! tip "Fine-tuning the cookies: `cookie_config=`"
    The `AUTH_COOKIE_*` settings cover the common case. When you need
    per-attribute control — name, `max_age`, refresh path, `Domain` — pass an
    `AuthCookieConfig` to `make_auth_router`; it wins over the settings:

    ```python
    from tempest_fastapi_sdk import AuthCookieConfig, make_auth_router

    app.include_router(
        make_auth_router(
            auth_service,
            session_factory=db.session_dependency,
            token_delivery="cookie",
            cookie_config=AuthCookieConfig(
                access_name="session",
                refresh_name="session_refresh",
                access_max_age=900,
                refresh_max_age=1209600,
                samesite="none",
                secure=True,
                domain=".example.com",
            ),
        ),
    )
    ```

    Mirror `access_max_age` on `JWT_ACCESS_TTL_SECONDS` and `refresh_max_age` on
    `JWT_REFRESH_TTL_SECONDS`: a cookie outliving its token leaves the browser
    sending a dead credential, and the reverse logs the user out early. The
    router narrows `refresh_path` to the refresh endpoint, so the long-lived
    token never rides on an ordinary call. `http_only=True` is the whole point
    of cookie mode — do not turn it off.

    Outside `make_auth_router` the same rules apply through
    `apply_auth_cookies(response, access_token=..., refresh_token=..., config=...)`
    and `clear_auth_cookies(response, config=...)` — handy in a login route of
    your own (the OAuth callback in the [social login recipe](oauth.md), say).

!!! tip "CORS with credentials"
    For a cross-origin SPA to send/receive cookies, the backend needs `allow_credentials=True` in CORS **and** `AUTH_COOKIE_SAMESITE=none` + `AUTH_COOKIE_SECURE=true` (hence HTTPS). Same-origin (frontend served from the API's domain) works with the default `lax`.

---

## Mailhog vs smtp4dev

Both intercept local SMTP and render emails in a web UI. Relevant differences:

| Aspect | Mailhog | smtp4dev |
|--------|---------|----------|
| Docker image | `mailhog/mailhog:latest` | `rnwood/smtp4dev:latest` |
| Default SMTP port | `1025` | `2525` (configurable) |
| UI port | `8025` | `5000` |
| Image size | ~10 MB | ~120 MB (.NET) |
| Multi-account / multi-inbox | no — single mailbox | yes — filters by recipient |
| HTTP / REST API | yes (`/api/v2/messages`) | yes (built-in Swagger) |
| DKIM / SPF validation | no | yes |
| Upstream maintenance | archived in 2020, still works | active |

**Suggestion:** start with Mailhog (lighter, zero-config) and switch to smtp4dev when you need multi-inbox or DKIM inspection. For the signup → activate → reset cycle, **Mailhog is enough**.

### `docker-compose.yaml` — Mailhog

```yaml
services:
  mailhog:
    image: mailhog/mailhog:latest
    container_name: mailhog
    ports:
      - "1025:1025"  # SMTP — point SMTP_HOST here
      - "8025:8025"  # web UI
```

`SMTP_PORT=1025`, open `http://localhost:8025`.

### `docker-compose.yaml` — smtp4dev

```yaml
services:
  smtp4dev:
    image: rnwood/smtp4dev:latest
    container_name: smtp4dev
    ports:
      - "2525:25"     # SMTP — point SMTP_HOST here
      - "5000:80"     # web UI
    environment:
      - ServerOptions__HostName=smtp4dev
```

`SMTP_PORT=2525`, open `http://localhost:5000`.

!!! tip "`tempest generate --docker` already includes Mailhog"
    When the project pins the `[email]` extra, `tempest generate --docker` **auto-appends** a Mailhog service to the `docker-compose.yaml` — no manual paste and no `--with mailhog` flag. The blocks above are just a reference, or for when you prefer smtp4dev.

---

## Customizing templates

The SDK ships two bundled Jinja2 templates (`activation.html` + `password_reset.html`) — responsive HTML, inline styles, mobile-friendly. You never need to touch them for an MVP to work. When you want your own branding, drop a file with the **same name** into the `template_dir` you passed to `EmailUtils`:

```text
emails/                            # ← template_dir="emails"
├── activation.html                # overrides the SDK default
└── password_reset.html            # overrides the SDK default
```

`EmailUtils` uses a `ChoiceLoader` internally so Jinja2 looks **first** in your directory and **only falls back** to the bundled template if it can't find yours. Override one, the other, or both — no need to copy the entire template.

### Variables available in the Jinja2 context

| Variable | Type | In which templates | Example |
|----------|------|--------------------|---------|
| `user` | `UserModel` instance | both | `{{ user.email }}`, `{{ user.name }}` (when your model exposes the column) |
| `activation_url` | `str` | `activation.html` | `https://app.example.com/activate?token=aBcD...xYz` |
| `reset_url` | `str` | `password_reset.html` | `https://app.example.com/reset?token=aBcD...xYz` |
| `expires_at` | `datetime` (UTC, timezone-aware) | both | the raw value, if you want to format it yourself |
| `expires_at_str` | `str` | both | **recommended** — already formatted short, no seconds: `2026-06-21 23:25 (UTC)` |

!!! tip "Prefer `expires_at_str`"
    Use `{{ expires_at_str }}` instead of `{{ expires_at }}` — the
    bundled templates do. It's localized (per `AUTH_DEFAULT_LOCALE`) and
    drops the noisy seconds/microseconds. The raw `expires_at` is still
    there if you need a custom format.

### Example: lean `emails/activation.html`

```html
<!doctype html>
<html lang="en">
  <body style="font-family: sans-serif; max-width: 480px; margin: auto;">
    <h1>Welcome{% if user.name %}, {{ user.name }}{% endif %}!</h1>
    <p>To activate your account, click the button below:</p>
    <p>
      <a href="{{ activation_url }}"
         style="display: inline-block; padding: 12px 24px;
                background: #4f46e5; color: white;
                text-decoration: none; border-radius: 6px;">
        Activate account
      </a>
    </p>
    <p style="color: #6b7280; font-size: 12px;">
      Link valid until {{ expires_at_str }}.
      If you didn't create this account, ignore this email.
    </p>
  </body>
</html>
```

!!! note "Jinja2 only runs when there's a real email"
    In modes C (`AUTH_RETURN_TOKEN_IN_RESPONSE=true`) and D (`AUTH_AUTO_ACTIVATE=true`) the Jinja2 template is **not rendered** — the link goes out raw in the JSON, no HTML. Only modes A and B (real or intercepted SMTP) exercise the template.

---

## Security

- **Token stored as SHA-256 hash.** Plaintext leaves via email only once; the database can never reproduce the original token. A leak of the `user_tokens` table does **not** enable retroactive activation.
- **One-shot.** `used_at` is stamped on consume; replay rejected with `UnauthorizedException`.
- **TTL-bounded.** `expires_at` computed from `AUTH_ACTIVATION_TTL_SECONDS` / `AUTH_PASSWORD_RESET_TTL_SECONDS`. Expired tokens rejected.
- **Anti-enumeration.** `POST /auth/password-reset/request` always returns HTTP 202 + a generic body, regardless of whether the email exists. `POST /auth/login` raises the same `UnauthorizedException` for wrong-email vs wrong-password.
- **Password floor enforced twice.** `SignupSchema` validates on input; `UserAuthService` re-validates before hashing — defense in depth in case anyone bypasses the schema.

---

## Getting the `current_user` from the request

`make_auth_router` **issues** the JWT pair (login/activate return `access_token` + `refresh_token`). But what next? When the frontend sends `Authorization: Bearer <access_token>` to **your own** routes, you need a dependency that decodes the token and resolves the user.

Since v0.49.0, `UserAuthService` builds that dependency for you — `current_user_dependency()`. It:

1. Looks the token up in three places, in **header → cookie → query string** order (first hit wins): `Authorization: Bearer <jwt>` via `HTTPBearer`, then the cookie (`cookie_name`), and finally the query parameter (`query_param`).
2. Decodes and verifies the JWT with **the same `JWTUtils` the service signs with** — no second secret to keep in sync.
3. Pulls the `sub` (user id) from the payload, opens a session from `db=`, and returns the persisted `UserModel`.

### 1. Declare the dependency once

The service already has `user_model`, `JWTUtils` and the session — so you don't write `load_user` by hand. Group both variants in `src/api/dependencies/auth.py`:

```python
# src/api/dependencies/auth.py
from src.api.app import auth_service

get_current_user = auth_service.current_user_dependency()
get_current_user_or_none = auth_service.current_user_dependency(soft=True)
```

!!! info "Requires `db=` on `UserAuthService`"
    `current_user_dependency` needs `db=` (the `AsyncDatabaseManager` from [Minimum setup](#minimum-setup)) — without it, it raises `RuntimeError`. Because it reuses the internal `self.jwt`, the token is verified with the **same** secret that signed it — the divergent-`JWT_SECRET` footgun is gone.

    The user is loaded on the **request-scoped** session (`db.session_dependency` by default) — the very one your repositories use. So the instance comes back *attached* and you can mutate / `refresh` it without hitting `InvalidRequestError: Instance is not persistent within this Session`. If your repositories depend on a different session callable (a project-local `get_session`, say), pass **that exact callable** as `session_dependency=` — FastAPI caches a sub-dependency by callable, so a distinct wrapper opens a second session and detaches the user again.

    The same guarantee holds for `make_auth_router`'s own authenticated routes (`/auth/password-change`, `/auth/mfa/*`): they load the user on the request session as of **0.171.1**. Before that the router opened a private session and handed back an already-*detached* instance, so the write was silently dropped and the following `refresh` blew up — `/auth/password-change` answered 500 while keeping the old password.

!!! tip "Not header-only: cookie and query string count too"
    The dependency tries **header → cookie → query string** and stops at the first hit, so the single line above serves both bearer clients and cookie clients.

    - **Cookie — automatic.** With `AUTH_TOKEN_DELIVERY` set to `"cookie"` or `"both"`, `cookie_name` is **auto-derived** from `AUTH_ACCESS_COOKIE_NAME` — the same cookie the bundled `/auth/login` set. Zero extra wiring: a route guarded by this dependency already accepts that cookie. A bearer-only delivery mode leaves it `None` (header only). Pass `cookie_name="..."` to force a name.
    - **Query string — opt-in.** Never auto-derived. It exists for clients that can send neither a header **nor** a cookie — the classic case being the browser `EventSource` (SSE) cross-origin:

    ```python
    # src/api/dependencies/auth.py
    from src.api.app import auth_service

    get_current_user = auth_service.current_user_dependency(
        cookie_name="access_token",
        query_param="access_token",
    )
    ```

    Full SSE walkthrough in the [SSE recipe »](sse.md#authentication-cookie-or-query-string).

!!! warning "A token in the URL leaks"
    Query strings land in access logs, browser history and the `Referer` header. Enable `query_param` only over TLS, only with a **short-lived access token** (never a refresh token), and scrub the value from your log format. When the client shares the API's origin, prefer a session cookie (`withCredentials`).

??? note "No `UserAuthService`? Build the dependency by hand"
    If your service doesn't use the bundled flow, the `make_jwt_user_dependency` primitive accepts any `JWTUtils` + a one-argument async `user_loader`:

    ```python
    from uuid import UUID

    from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

    from src.api.app import db
    from src.core.settings import settings
    from src.db.models import UserModel
    from src.db.repositories import UserRepository

    tokens: JWTUtils = JWTUtils(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


    async def load_user(subject: str) -> UserModel:
        """Resolve the JWT subject (a UUID string) to the persisted user."""
        async with db.get_session_context() as session:
            repo: UserRepository = UserRepository(session)
            return await repo.get_by_id(UUID(subject))


    get_current_user = make_jwt_user_dependency(tokens, load_user)
    get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)
    ```

    Heads up: here `tokens` **must** use the same `JWT_SECRET` / `JWT_ALGORITHM` as login, otherwise every valid token is rejected.

### 2. Inject it on the route with `Depends`

```python
# src/api/routers/users.py
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user
from src.db.models import UserModel
from src.schemas import UserResponseSchema

router: APIRouter = APIRouter(prefix="/users", tags=["users"])


@router.get("/profile")
async def profile(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    """Return the user who owns the request's bearer token."""
    return UserResponseSchema.model_validate(current)
```

`current` **is** the `UserModel` the service resolved — typed, persisted, ready to use. Missing or invalid token → `401 UnauthorizedException` before the route body runs.

!!! tip "For a plain `/me`, write nothing"
    The bundled router has mounted `GET /auth/me` since v0.198.0, with
    `AuthUserSchema` (the columns `BaseUserModel` guarantees) as the
    response. Write your own only when the route does something **beyond**
    returning the account — aggregating counters, joining a profile from
    another table. If it is just one extra column on the same table,
    subclass the schema instead of rewriting the route:

    ```python
    from tempest_fastapi_sdk import AuthUserSchema, make_auth_router


    class UserResponseSchema(AuthUserSchema):
        name: str | None = None


    app.include_router(
        make_auth_router(
            auth_service,
            session_factory=get_db,
            me_response_model=UserResponseSchema,
        )
    )
    ```

!!! note "Your `session_factory` has to guarantee nothing"

    The HTML pages render after the commit that consumes the token, and
    SQLAlchemy's `async_sessionmaker` defaults to `expire_on_commit=True` —
    which made the page read an expired column and answer **500**
    (`MissingGreenlet`) through v0.265.0. The router now reloads the row
    **only when it expired**, checking `inspect(user).expired`, which never
    touches the database. With `AsyncDatabaseManager`'s factory
    (`expire_on_commit=False`) there is no extra query at all.

### 3. Optional auth — `soft=True`

For routes that work both authenticated **and** anonymous (e.g. a public feed that personalizes when logged in), use the `soft` variant — it returns `None` instead of raising:

```python
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user_or_none
from src.db.models import UserModel
from src.schemas import PostResponseSchema
from src.services import FeedService

feed_service = FeedService()

router = APIRouter()


@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> list[PostResponseSchema]:
    """Public feed; personalizes the ranking when a user is logged in."""
    if current is None:
        return await feed_service.public()
    return await feed_service.personalized(current.id)
```

!!! tip "Role and permission are the next step"
    When the route needs a **role** (`admin`) or **permission** (`users:write`) and not just "logged in", swap for `make_role_dependency` / `make_permission_dependency`. See the [HTTP recipe »](http.en.md) — same `JWTUtils`, same `Depends` pattern.

### 4. Imperative guards — checks inside the service / controller

The dependencies above gate the **route** (before the handler runs). But what about when you already hold the user deeper in the stack (service, controller) and just want to **assert** a condition before continuing? Since v0.50.0 the SDK ships three ready-made guards — no rewriting `if user is None: raise ...` in every service:

```python
from tempest_fastapi_sdk import (
    require_active,
    require_admin,
    require_authenticated,
)
```

| Guard | Raises when | HTTP status |
|-------|-------------|-------------|
| `require_authenticated(user)` | `user is None` | 401 `UnauthorizedException` |
| `require_active(user)` | `None`, or `not user.is_active` | 401 / 403 `ForbiddenException` |
| `require_admin(user)` | `None`, or `not user.is_admin` | 401 / 403 `ForbiddenException` |

The detail that matters: each one **returns the user already narrowed** — non-`None`, with the concrete type preserved — so the rest of the function stops seeing `| None`:

```python
from tempest_fastapi_sdk import require_admin

from src.db.models import UserModel


class ReportService:
    async def delete_all(self, current: UserModel | None) -> None:
        """Only an admin may purge reports."""
        admin: UserModel = require_admin(current)  # 401/403, or returns typed
        await self.repository.purge(by=admin.id)   # `admin` is no longer `| None`
```

It pairs directly with `current_user_dependency(soft=True)`: the route passes `UserModel | None`, and the guard decides in the service.

!!! tip "Already have `auth_service`? Use the static mirrors"
    The same guards exist as static methods on `UserAuthService` — `auth_service.require_admin(current)` — for when you already inject the service and don't want an extra import. Same semantics, same exception.

---

## Recap

- The bundled flow covers signup, activation, login, password reset and email
  change — and the concrete `UserTokenModel` is the only table your service has
  to ship.
- Password recovery and **email** recovery are different flows: one is for
  someone who forgot the password, the other for someone who lost the inbox.
- The five operating modes exist because "who renders the page" changes per
  project: pure API, backend-rendered, SPA, or a mix.
- `AUTH_TOKEN_DELIVERY` decides whether the JWT pair leaves in the body, in a
  cookie or both — and a cross-site cookie needs `SameSite=none` plus `Secure`.
- Templates and language are overridable, but the defaults work without you
  creating a single file.
- The router prefix and the template URL have to agree: mounting under `/api`
  with a template pointing at the root fails on the user's click, not at deploy
  time.

## Next steps

- **[Idempotency »](idempotency.en.md)** — protect `POST /auth/signup` from retries that would duplicate the row.
- **[MinIO/S3 Storage »](storage.en.md)** — attach avatar / profile picture during signup.
- **[Logging »](logging.en.md)** — `request_id` propagates automatically across every log line emitted during the flow.
- **[Metrics »](metrics.en.md)** — `PrometheusMiddleware` counts `/auth/*` separately with no extra config.
