# Endpoints

Look up every endpoint with payload and behaviour: closed system, admin-created accounts, extra signup fields and attempt limits.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

| Method | Path | Body / Output | Behavior |
|--------|------|---------------|----------|
| POST | `/auth/signup` | `SignupSchema` → `SignupResponseSchema` | Creates user. Not mounted when `AUTH_SIGNUP_ENABLED=false` (v0.272.0+). Emits email (modes A/B) **or** returns the link in the body (mode C). With `AUTH_AUTO_ACTIVATE=True`, the user is born active and the JWT pair returns immediately (mode D). |
| POST | `/auth/activate/{token}` | — → `ActivationResponseSchema` | Consumes token + sets `is_active=True` + issues JWT pair. A **freshly signed-up** account has no factor, so nothing changes; but if the token came from `/auth/activation/request` for a deactivated account that had MFA enrolled, it answers the challenge (`mfa_required=true` + `mfa_token`) instead of the pair. |
| POST | `/auth/activation/request` *(v0.297.0+)* | `ActivationRequestSchema` → `ActivationResendResponseSchema` | **Unauthenticated.** Re-issues the activation link for an account that never activated. Always 202 + generic body. |
| POST | `/auth/login` | `LoginSchema` → `LoginResponseSchema` | Email + password → JWT pair. Generic errors (no account enumeration). |
| GET | `/auth/me` *(v0.198.0+)* | — → `AuthUserSchema` | **Authenticated.** Returns the account owning the bearer token. Never serializes the password hash: the handler returns the whole model and the `response_model` filters. Swap in your own schema with `me_response_model=` to expose extra columns. |
| POST | `/auth/password-reset/request` | `PasswordResetRequestSchema` → `PasswordResetResponseSchema` | Always HTTP 202 + generic body. Link via email (A/B) or body (C). |
| POST | `/auth/password-reset/confirm` | `PasswordResetConfirmSchema` → `LoginResponseSchema` | Consumes token + writes new password + revokes sessions + issues JWT pair. With MFA enrolled on the account, it answers the challenge (`mfa_required=true` + `mfa_token`) instead of the pair — finish at `/auth/mfa/verify`. |
| POST | `/auth/password-change` | `PasswordChangeSchema` → `LoginResponseSchema` | **Authenticated** (bearer token). Change your own password: confirm the current password (and the `code` on an MFA account) + write the new one. No email token. |
| POST | `/auth/email-change/request` *(v0.92.0+)* | `EmailChangeRequestSchema` → `EmailChangeResponseSchema` | **Authenticated.** Confirm the current password + email a confirmation link to the **new** address. Always 202. |
| POST | `/auth/email-change/confirm` *(v0.92.0+)* | `EmailChangeConfirmSchema` → `EmailChangeResponseSchema` | Consume the token + apply the new email + (optionally) notify the old address. |
| POST | `/auth/email-verify/request` *(v0.92.0+)* | — → `EmailChangeResponseSchema` | **Authenticated.** Re-send a verification link to the **current** email. |
| POST | `/auth/email-verify/confirm` *(v0.92.0+)* | `EmailChangeConfirmSchema` → `EmailChangeResponseSchema` | Consume the token + mark the account active. |
| POST | `/auth/email-recovery/request` *(v0.92.0+, opt-in)* | `EmailRecoveryRequestSchema` → `EmailChangeResponseSchema` | **Unauthenticated.** Recover an account whose mailbox is lost: password (+ MFA if enrolled). Only mounted with `AUTH_EMAIL_RECOVERY_ENABLED=True`. Always 202. |
| GET | `/auth/oauth/{provider}/login` *(v0.273.0+)* | — → `302` | Mints the `state`, writes it to an `HttpOnly` cookie and redirects to the provider. Mounted only with `AUTH_OAUTH_ENABLED=true`. See the [social-login recipe](../oauth.en.md). |
| GET | `/auth/oauth/{provider}/callback` *(v0.273.0+)* | — → `LoginResponseSchema` | Checks the `state`, trades the `code`, resolves `(provider, subject)` in the database and issues **the same JWT pair** as `/auth/login` — `typ`, opaque refresh, rotation and `/auth/logout` included. An account with TOTP gets the challenge (`mfa_required=true` + `mfa_token`), as at `/auth/login` *(v0.314.0+)*. |
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
      `current_password`. No email or reset token involved. Answers
      **200**: every session is revoked and the caller gets a fresh pair
      (or, on an MFA account without `code`, the login challenge).

## Closed system — no registration door *(v0.272.0+)*

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
    account opened through a flow with an activation link still needs it,
    and that endpoint is what consumes the emailed token. Nothing beyond
    `POST /auth/signup` moves. An account opened from the panel with
    `admin_create_user` is born active — see the next section.

**Recap.** `AUTH_SIGNUP_ENABLED=false` closes the public door without
touching any other endpoint; `allow_signup=` overrides per router; and the
route leaves the OpenAPI schema along with it, which the manual filter never
delivered.

## Account created by the administrator, no e-mail *(v0.364.0+)*

Closing sign-up solves half of a closed system. The other half is the
panel: the administrator opens the account with an e-mail and a password,
and sometimes replaces someone's password. Nobody receives an e-mail on that
path — which is why it cannot go through the link flows.

Without a mailer in production (an unset `ENV` counts as production),
`signup` and `request_password_reset` answer `503 INTEGRATION_DISABLED`: the
account whose activation link never arrives is not created, and the reset
nobody receives is not issued. That is the right rule for the user, and it
is why composing `signup` + `activate` or `request_password_reset` +
`confirm_password_reset` for the panel breaks.

`UserAuthService` has the two panel methods, with no token and no e-mail:

```python
# main.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    AuthUserSchema,
    BaseUserModel,
    DatabaseSettings,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)


class Settings(DatabaseSettings, JWTSettings, AuthSettings):
    """Settings read from the environment (and the `.env`)."""


class UserModel(BaseUserModel):
    """The accounts table."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

settings: Settings = Settings()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)
get_current_user = auth_service.current_user_dependency()


class AccountCreateSchema(BaseModel):
    """What the administrator fills in to open an account."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=1)
    name: str | None = None


class PasswordSetSchema(BaseModel):
    """The new password the administrator sets."""

    new_password: str = Field(min_length=1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database and create the tables on startup.

    Args:
        app (FastAPI): The application starting up.

    Yields:
        None: Hands control back to FastAPI while the app serves.
    """
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        allow_signup=False,
    ),
)


@app.post("/api/admin/accounts", status_code=201)
async def create_account(
    payload: AccountCreateSchema,
    current: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(db.session_dependency),
) -> AuthUserSchema:
    """Open an already active account with the password the administrator chose.

    Args:
        payload (AccountCreateSchema): E-mail, password and name of the account.
        current (UserModel): The caller; must be an administrator.
        session (AsyncSession): The request session.

    Returns:
        AuthUserSchema: The public view of the created account.
    """
    auth_service.require_admin(current)
    user = await auth_service.admin_create_user(
        session,
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )
    await session.commit()
    return AuthUserSchema.model_validate(user)


@app.put("/api/admin/accounts/{user_id}/password", status_code=204)
async def set_account_password(
    user_id: UUID,
    payload: PasswordSetSchema,
    current: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(db.session_dependency),
) -> None:
    """Replace an account's password and end its sessions.

    Args:
        user_id (UUID): The account whose password changes.
        payload (PasswordSetSchema): The new password.
        current (UserModel): The caller; must be an administrator.
        session (AsyncSession): The request session.
    """
    auth_service.require_admin(current)
    await auth_service.admin_set_password(
        session,
        user_id=user_id,
        new_password=payload.new_password,
    )
    await session.commit()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

!!! note "`create_tables()` here is a demo shortcut"
    The example fits in a single file, so it creates the tables straight
    in the lifespan. In a service, the lifespan calls `sync_schema_async()`
    over the Alembic migrations: `create_tables()` adds no column to a
    table that already exists. See
    [Migrations »](../migrations.md#3-sync-the-schema-in-the-lifespan).

What each route does:

- **`admin_create_user`** creates the account **already active**. The path is
  `signup`'s: normalized e-mail, the same password policy, the same
  protected columns in `fields=`, and the same `ConflictException` (`409`)
  for a taken e-mail. `is_admin=True` is its own parameter, because
  `fields=` refuses the column. `email_verified_at` stays empty: the
  administrator typed the address, and nobody proved the mailbox.
- **`admin_set_password`** replaces the password under the reset's policy
  and, by default, **revokes the account's sessions and passkeys**, like
  `confirm_password_reset`: someone else chose the password, so the old
  access ends. `revoke_sessions=False` and `revoke_passkeys=False` are the
  explicit opt-out. An unknown id is a `404`.
- Both only `flush`; the route owns the `commit`.

Running the app above without `ENV` and with `email=None`, with an
administrator account already logged in:

```console
POST /api/admin/accounts {"email": "Maria@Example.com", ...}  -> 201, "email": "maria@example.com", "is_active": true
POST /api/admin/accounts {"email": "maria@example.com", ...}  -> 409
PUT  /api/admin/accounts/<id>/password                       -> 204
POST /auth/login with the new password                        -> 200
POST /auth/signup                                             -> 404
```

!!! warning "Authorization belongs to the route"
    The methods trust their caller. Guard the route with
    `auth_service.require_admin(current)` (or the panel's session), as in the
    example — without it, any logged-in account opens accounts and replaces
    passwords.

**Recap.** An account born in the panel uses `admin_create_user`; a password
replaced by the administrator uses `admin_set_password`. Neither depends on a
mailer, and both keep the password policy, the e-mail conflict and the
session revocation of the link flows.

## An account born with more than email and password *(v0.278.0+)*

Almost no product has an account that is only `email`, `password` and `name`.
There is the phone, the tax document, the flag that separates a customer from a
producer. Until now the way out was to unmount `POST /auth/signup` and write the
route by hand — and that hand-written route had to re-derive the password
policy, the 409 on a duplicate email, the activation branch and the JWT pair.
Four things the bundled route already gets right, and that start drifting one
release at a time.

One argument covers the common case: **`signup_schema`** replaces the request
body, and every new field on it that is also a column of your `UserModel` is
set on the row **before** the insert *(v0.305.0+)*. For what is not a column —
a terms checkbox, a derived value — there is **`on_signup`**.

```python
# src/api/app.py

from fastapi import FastAPI
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthUserSchema,
    SignupSchema,
    UserAuthService,
    ValidationException,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel


class ProducerSignupSchema(SignupSchema):
    """This product's signup: email, password, name — and the rest."""

    phone: str = Field(max_length=20)
    is_producer: bool = Field(default=False)
    accept_terms: bool = Field(default=False)


class ProducerMeSchema(AuthUserSchema):
    """What ``GET /auth/me`` returns: the account and this product's columns."""

    phone: str
    is_producer: bool


async def require_terms(
    session: AsyncSession,
    user: UserModel,
    payload: ProducerSignupSchema,
) -> None:
    """Refuse a signup that did not accept the terms.

    Args:
        session (AsyncSession): The transaction the row was inserted in.
        user (UserModel): The freshly created instance.
        payload (ProducerSignupSchema): The validated body.

    Raises:
        ValidationException: When ``accept_terms`` is false.
    """
    if not payload.accept_terms:
        raise ValidationException(message="accept the terms to sign up")


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
        on_signup=require_terms,
        me_response_model=ProducerMeSchema,
    ),
)
```

Here `phone` and `is_producer` are columns of `UserModel` (`phone` is
`NOT NULL` and `UNIQUE`), so they reach the row with no hook at all.
`accept_terms` is not a column: it stays only on the `payload` `on_signup`
receives. And `me_response_model=ProducerMeSchema` makes `GET /auth/me`
return both new columns next to the ones `AuthUserSchema` already carries —
still without `hashed_password`, which neither schema declares.

With `AUTH_AUTO_ACTIVATE=true` in `.env` the account is born active and signup already returns the JWT pair. Under the default (`false`) the same call answers `"activation_required":true` with `access_token` and `refresh_token` set to `null`, and the pair only comes out of `POST /auth/activate/{token}`:

```console
$ curl -s -X POST localhost:8000/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"ana@example.com","password":"strong-pass-12","phone":"5511999999999","is_producer":true,"accept_terms":true}'
{"user_id":"0e5cf2fc-…","activation_required":false,"activation_url":null,"access_token":"eyJ…","refresh_token":"eyJ…"}
```

A second signup with the same phone answers `409`, with the column in
`details` and in `field` — not a `500` from an `IntegrityError`:

```console
$ curl -s -X POST localhost:8000/auth/signup \
    -H "Content-Type: application/json" \
    -d '{"email":"bia@example.com","password":"strong-pass-12","phone":"5511999999999","accept_terms":true}'
{"detail":"phone already in use","code":"CONFLICT","details":{"columns":["phone"],"constraint":null},"field":"phone"}
```

`constraint` is `null` on SQLite, which does not name a unique constraint;
PostgreSQL reports the name (`users_phone_key`, for instance). The `detail`
above is from an app without a catalog; with `register_exception_handlers(app,
catalog=default_message_catalog())` it comes out translated (`Conflito de
recurso` under pt-BR), and `code`, `details` and `field` do not change.

!!! warning "A protected column never comes from the body"
    `id`, `email`, `hashed_password`, `is_active`, `is_admin`,
    `email_verified_at` and the `MFAMixin` columns (`totp_secret`,
    `totp_enabled_at`, `totp_last_step`), and the bookkeeping columns the SDK
    keeps — `last_login_at` (written by login), `created_at` / `updated_at`
    (the database clock), `deleted_at` (`SoftDeleteMixin`: a row born
    deleted hides from every query) and `created_by` / `updated_by`
    (`AuditMixin`: the sign-up would claim another author) — the
    `SIGNUP_PROTECTED_FIELDS` — belong to the SDK, and every column your model declares in
    `__secret_columns__` joins them (`service.signup_protected_fields()`
    returns the set for your model). A `signup_schema` with an `is_admin`
    field promotes nobody, and one with `totp_enabled_at` turns on no
    factor: the route drops those names before writing. Calling the service
    directly, `signup(fields={"is_admin": True})` raises `ValueError`.

Annotate the hook with **your** classes — the concrete model and the concrete
schema — as above: that is what makes `payload.accept_terms` type-check on
your side.

The schema **must** subclass `SignupSchema` — the route reads `email`,
`password` and `name` off it by name, so a body without them fails
`make_auth_router` at wiring time, not on the first request. The new fields
reach `/openapi.json` too: a generated client sees `phone` and `is_producer`
without anyone hand-writing a schema.

!!! tip "If your service overrides `signup`"
    Declare `fields: Mapping[str, Any] | None = None` on the override and pass
    it on to `super().signup(...)`: that is how the `signup_schema` columns
    reach the row before the insert. An override without `fields` keeps
    working — the route calls it without that argument, as before 0.305.0, and
    logs, when the router is mounted, which columns are left for `on_signup`
    to fill.

??? info "No router: `signup(fields=...)`"
    The same path lives on the service, for whoever builds their own route:

    ```python
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk import UserAuthService


    async def create_driver(
        service: UserAuthService,
        session: AsyncSession,
        email: str,
        password: str,
        phone: str,
    ) -> None:
        """Create the driver with the required phone already in the insert.

        Args:
            service (UserAuthService): The auth service.
            session (AsyncSession): The request session.
            email (str): Account email.
            password (str): Plaintext password.
            phone (str): Phone, a ``NOT NULL`` unique column.
        """
        await service.signup(
            session,
            email=email,
            password=password,
            fields={"phone": phone},
        )
        await session.commit()
    ```

    A key that is not a column of the model is a `ValueError` before anything
    is written. A forgotten `NOT NULL` column is a `ValueError` too, naming the
    column — a wiring defect, not a client error.

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
visible in OpenAPI — and its fields that are columns land on the row before the
insert. `on_signup` is for the rest, inside the insert's transaction. Nothing
else has to leave the SDK for your service.


## Login and signup attempt limits

Without a limit, `POST /auth/login` answers `401` to every wrong password
forever — brute force is only a matter of patience — and `POST /auth/signup`
creates accounts with no ceiling. The router ships both brakes, shaped like
`mfa_throttle`:

| Parameter | Key | Counts | Default |
| --- | --- | --- | --- |
| `login_throttle` | normalized e-mail (SHA-256) | every attempt, **cleared by a right login** | on: 5 per 900 s, in memory |
| `login_ip_throttle` | client IP | wrong credentials only; a right login does **not** clear it | off |
| `signup_throttle` | client IP | every attempt, right or not | off |

```python
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AttemptThrottle,
    AuthSettings,
    BaseUserModel,
    InMemoryThrottleBackend,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)


class UserModel(BaseUserModel):
    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")


async def main() -> None:
    db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    service: UserAuthService = UserAuthService(
        db=db,
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=AuthSettings(AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(),
        email=None,
    )
    app: FastAPI = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            service,
            session_factory=db.session_dependency,
            login_ip_throttle=AttemptThrottle(
                InMemoryThrottleBackend(), max_attempts=20, window_seconds=900
            ),
            signup_throttle=AttemptThrottle(
                InMemoryThrottleBackend(), max_attempts=10, window_seconds=3600
            ),
            trusted_ip_header="x-real-ip",
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/signup",
            json={"email": "ana@example.com", "password": "senha-forte-123"},
        )
        for password in ["senha-errada-123"] * 5 + ["senha-forte-123"]:
            response: Response = await client.post(
                "/auth/login",
                json={"email": "ana@example.com", "password": password},
            )
            print(response.status_code, response.headers.get("retry-after"))
        print(response.json())
    await db.disconnect()


asyncio.run(main())
```

Output:

```text
401 None
401 None
401 None
401 None
401 None
429 900
{'detail': 'too many login attempts, try again later', 'code': 'LOGIN_THROTTLED', 'details': {'retry_after_seconds': 900}}
```

The sixth attempt gets `429` **with the right password**: the budget is
reserved before the password is checked, so a burst of parallel guesses does
not all pass a "not blocked yet" read together. A right login before the limit
clears the e-mail's count.

!!! check "The 429 does not reveal whether the e-mail exists"
    The e-mail budget is spent the same way for an address without an account,
    and `detail` is the same text in both cases. Whoever probes the login
    learns they guessed too often, not which e-mails are registered.

!!! warning "Why the per-IP limits ship off"
    Behind a proxy, the IP the app sees is the **proxy's** — without
    `trusted_ip_header`, every client lands in the same bucket, and one per-IP
    budget locks everyone out of login (or signup) at once. Turn
    `login_ip_throttle` and `signup_throttle` on together with the
    `trusted_ip_header` your edge overwrites (`"x-real-ip"`), never with
    `X-Forwarded-For`.

Four more things worth knowing:

- **With more than one worker, pass a shared backend.** The default counts in
  memory, per process; `AttemptThrottle(redis, max_attempts=5,
  window_seconds=900)` shares the counter across replicas.
- **`login_throttle=False` turns off** the per-e-mail limit, for a service that
  already limits login in another layer.
- **Locking per e-mail has a cost**: whoever knows someone's e-mail can spend
  its budget and keep the owner out for one window. That is the trade every
  per-account lock makes; the short window is what keeps it acceptable.
- **The rest of the router is unchanged.** `/auth/me`, `/auth/refresh` and the
  other routes go through none of these limits, and the OpenAPI schema only
  declares the `429` on the login routes and `/auth/signup` while the matching
  limit is on.

**Recap.** Login comes protected per e-mail with no work from you; the per-IP
limits you turn on once you know where the real IP comes from.
