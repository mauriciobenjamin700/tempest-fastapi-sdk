# Backend only: from signup to a protected route

Use to test signup → activation → login → protected route without a frontend (curl, Swagger, mobile app) and the same cycle in a test.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

You don't have a frontend yet. Maybe you are testing the API with `curl` or
through Swagger, writing a mobile app, or you want the email link to
activate the account straight on the backend. The default
`AUTH_ACTIVATION_URL_TEMPLATE` points at
`http://localhost:3000/activate?token={token}` — a frontend page that, in your
case, does not exist.

This section runs the whole cycle **with the API alone**: sign up, get the
activation token, activate, log in and call a protected route. Every output
below came from a `curl` against the app on this page.

## Step 1 — the whole app in one file

```bash
uv add "tempest-fastapi-sdk[auth,email,sqlite]" uvicorn
```

`[auth]` brings bcrypt and PyJWT, `[sqlite]` brings the `aiosqlite` driver,
and `[email]` brings the Jinja2 that the [Mode E](modes.md#mode-e-backend-only-v0320)
HTML pages render with. `uvicorn` is the server. The `Settings` class below
composes mixins — the table of all of them is in [Settings](../settings.md).

```python
# main.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI

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
    """Settings read from the environment (and from `.env`)."""


class UserModel(BaseUserModel):
    """The account table."""

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database and create the tables on startup.

    Args:
        app (FastAPI): The application being started.

    Yields:
        None: Control back to FastAPI while the app serves requests.
    """
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
app.include_router(
    make_auth_router(auth_service, session_factory=db.session_dependency),
)


@app.get("/api/profile")
async def profile(current: UserModel = Depends(get_current_user)) -> AuthUserSchema:
    """Return the account that owns the bearer token.

    Args:
        current (UserModel): The user resolved from the token.

    Returns:
        AuthUserSchema: The public view of the account.
    """
    return AuthUserSchema.model_validate(current)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

!!! note "`create_tables()` here is a demo shortcut"
    The example fits in a single file, so it creates the tables straight
    in the lifespan. In a service, the lifespan calls `sync_schema_async()`
    over the Alembic migrations: `create_tables()` adds no column to a
    table that already exists. See
    [Migrations »](../migrations.md#3-sync-the-schema-in-the-lifespan).

Three lines do this section's work:

- **`email=None`** — with no `EmailUtils`, the activation link comes back
  **in the body** of `signup` (the `activation_url` field). You don't need
  `AUTH_RETURN_TOKEN_IN_RESPONSE=true`: with `email=None` the link shows up
  anyway, and it is the only place it shows up.
- **`db.create_tables()`** — creates `users` and `user_tokens` in SQLite
  (`./app.db`, the `DATABASE_URL` default). Fine for dev; in production the
  schema comes from Alembic, as in [Concrete UserTokenModel](setup.md#concrete-usertokenmodel).
- **`current_user_dependency()`** — the dependency guarding `/api/profile`.
  Details in [Getting the `current_user`](current-user.md).

## Step 2 — the dev `.env` without a frontend

```bash
# .env
AUTH_BACKEND_LINKS=true
AUTH_ACTIVATION_URL_TEMPLATE=http://127.0.0.1:8000/auth/activate/{token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=http://127.0.0.1:8000/auth/password-reset/{token}
```

`AUTH_BACKEND_LINKS=true` turns on [Mode E](modes.md#mode-e-backend-only-v0320): the
backend mounts `GET /auth/activate/{token}`, an HTML page that activates the
account when someone opens the link. The template points at that route, so
the `activation_url` you get back is a link that works on its own.

!!! info "`127.0.0.1` only works because the link opens on your machine"
    In dev, whoever opens the link is you, on the same computer as the
    server. In production the link opens in the **user's** browser, and the
    host must be the backend's public domain — see the prefix warning in
    [Mode E](modes.md#mode-e-backend-only-v0320).

Start the server:

```bash
uv run python main.py
```

## Step 3 — sign up

```bash
curl -X POST 127.0.0.1:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}'
```

`201 Created`:

```json
{
  "user_id": "69ce003d-1384-43a6-86df-01e547a03f13",
  "activation_required": true,
  "activation_url": "http://127.0.0.1:8000/auth/activate/5wScVz_EX6rW-PGCYkaDYpv0dGjjm779J3-HbcVW6nswr_Lao7Q_ynzwicIrQzZe",
  "access_token": null,
  "refresh_token": null
}
```

The token is the last segment of `activation_url`. The account exists but is
still inactive — logging in now answers `401` with
`{"detail": "invalid email or password"}`, the same sentence as a wrong
password, so nobody learns that the email has an account.

## Step 4 — activate

Pick **one** of the two paths. The token is single-use, so the second use
answers an error.

!!! info "Single-use under concurrency too"
    Redemption is a single `UPDATE ... WHERE used_at IS NULL RETURNING`, and
    the database picks the winner. Eight simultaneous password resets with
    the same link, on separate sessions, give **one** success and seven
    `InvalidTokenException` (`tests/auth/test_consume_token_race.py`, on
    SQLite and PostgreSQL); up to 0.304.0 all eight passed on PostgreSQL.
    It holds for activation, password reset and email change. `RETURNING`
    needs PostgreSQL or SQLite 3.35+.

=== "Link (browser or `curl`)"

    Open `activation_url` in the browser, or:

    ```bash
    curl -i http://127.0.0.1:8000/auth/activate/5wScVz_EX6rW-PGCYkaDYpv0dGjjm779J3-HbcVW6nswr_Lao7Q_ynzwicIrQzZe
    ```

    `200 OK` with `content-type: text/html` and the **Account activated**
    page. It is what a Mode E user sees after clicking the email. Opening
    the same link again answers `400` with the activation-failed page.

    This page does not hand out a JWT (a token in a URL leaks into history and
    logs), so the next step is logging in.

=== "JSON (Swagger, mobile app)"

    ```bash
    curl -X POST 127.0.0.1:8000/auth/activate/5wScVz_EX6rW-PGCYkaDYpv0dGjjm779J3-HbcVW6nswr_Lao7Q_ynzwicIrQzZe
    ```

    `200 OK` with the JWT pair — the account comes out activated **and**
    logged in:

    ```json
    {
      "user_id": "69ce003d-1384-43a6-86df-01e547a03f13",
      "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
      "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…"
    }
    ```

    Repeating it answers `401` with `{"detail": "token already used"}`. This
    route exists in every mode; the `GET` one only with
    `AUTH_BACKEND_LINKS=true` — without it, opening the link in the browser
    answers `405 Method Not Allowed`.

## Step 5 — log in and call the protected route

```bash
curl -X POST 127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}'
```

`200 OK`:

```json
{
  "user_id": "69ce003d-1384-43a6-86df-01e547a03f13",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "mfa_required": false,
  "mfa_token": null
}
```

Send the `access_token` in the `Authorization` header:

```bash
curl 127.0.0.1:8000/api/profile \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…"
```

`200 OK`:

```json
{
  "id": "69ce003d-1384-43a6-86df-01e547a03f13",
  "is_active": true,
  "created_at": "2026-09-27T22:16:42.190695Z",
  "updated_at": "2026-09-27T22:16:48.642307Z",
  "email": "ana@example.com",
  "is_admin": false,
  "last_login_at": "2026-09-27T22:16:48.641560Z",
  "email_verified_at": null
}
```

`email_verified_at` is `null` because, in this mailer-less setup, the
activation link came back in the signup response: activating through it does
not prove you read the mailbox. See [What counts as a verified email](email-change.md#what-counts-as-a-verified-email).

Without the header, the same route answers `401` with
`{"detail": "Authorization token is missing or invalid"}`.

!!! tip "Through Swagger"
    Open `http://127.0.0.1:8000/docs`. `POST /auth/signup` returns the
    `activation_url`; paste the token into `POST /auth/activate/{token}`;
    copy the `access_token` from the response, click **Authorize** (the
    `HTTPBearer` scheme the dependency registers) and paste it. From then on,
    **Try it out** on `/api/profile` goes authenticated.

## What about the mobile app?

The app has no web page to receive the link, but it can receive a **deep
link**. The template is a `.format()` string with no scheme validation, so
it takes your app's scheme:

```bash
# .env
AUTH_ACTIVATION_URL_TEMPLATE=meuapp://ativar?token={token}
```

`signup` then returns
`"activation_url": "meuapp://ativar?token=f8vX7Qa…"`. The app opens from
that link, reads `token` from the query string and calls
`POST /auth/activate/{token}` — the JSON path from step 4, which already
returns the JWT pair. `AUTH_BACKEND_LINKS` can stay off here: the app
handles the link, not the backend.

## The same cycle in a test, with the email captured

In a test you want the production path — the link **in the email**, not in
the body. `FakeEmailUtils` is an `EmailUtils` that keeps each message in
`outbox` instead of opening an SMTP connection:

```python
# tests/test_auth_flow.py
import re
from collections.abc import AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    AuthUserSchema,
    BaseUserModel,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils


class UserModel(BaseUserModel):
    """The account table."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")


@pytest.fixture
async def mailer() -> FakeEmailUtils:
    """Return a mailer whose messages land in `outbox`.

    Returns:
        FakeEmailUtils: The fake mailer.
    """
    return FakeEmailUtils()


@pytest.fixture
async def client(mailer: FakeEmailUtils) -> AsyncIterator[AsyncClient]:
    """Serve the auth router over an in-memory SQLite database.

    Args:
        mailer (FakeEmailUtils): Where the activation email lands.

    Yields:
        AsyncClient: A client bound to the app, no socket involved.
    """
    db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    service: UserAuthService = UserAuthService(
        db=db,
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=AuthSettings(
            AUTH_BACKEND_LINKS=True,
            AUTH_ACTIVATION_URL_TEMPLATE="http://test/auth/activate/{token}",
        ),
        jwt_settings=JWTSettings(),
        email=mailer,
    )
    get_current_user = service.current_user_dependency()
    app: FastAPI = FastAPI()
    app.include_router(make_auth_router(service, session_factory=db.session_dependency))

    @app.get("/api/profile")
    async def profile(current: UserModel = Depends(get_current_user)) -> AuthUserSchema:
        """Return the account that owns the bearer token.

        Args:
            current (UserModel): The user resolved from the token.

        Returns:
            AuthUserSchema: The public view of the account.
        """
        return AuthUserSchema.model_validate(current)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as opened:
        yield opened
    await db.disconnect()


async def test_signup_activate_login_profile(
    client: AsyncClient,
    mailer: FakeEmailUtils,
) -> None:
    """Walk the account from signup to a protected route.

    Args:
        client (AsyncClient): The app client.
        mailer (FakeEmailUtils): The captured outbox.
    """
    credentials: dict[str, str] = {"email": "ana@example.com", "password": "senha-forte-123"}

    signup: Response = await client.post("/auth/signup", json=credentials)
    assert signup.status_code == 201
    assert signup.json()["activation_url"] is None

    match = re.search(r"http://test/auth/activate/\S+", mailer.outbox[0].body)
    assert match is not None
    link: str = match.group(0)

    page: Response = await client.get(link)
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]

    login: Response = await client.post("/auth/login", json=credentials)
    assert login.status_code == 200
    token: str = login.json()["access_token"]

    profile: Response = await client.get(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile.status_code == 200
    assert profile.json()["is_active"] is True
```

Note the `assert signup.json()["activation_url"] is None`: with an
`EmailUtils` configured, the link **leaves the body** and goes only to the
email — that is what proves the test exercises the production path. The
`async` fixtures need `asyncio_mode = "auto"` under
`[tool.pytest.ini_options]` in `pyproject.toml` (with `pytest-asyncio`
installed).

!!! note "Installed only `[auth]`?"
    The JSON path works with `[auth,sqlite]`. With `AUTH_BACKEND_LINKS=true`
    and without the `[email]` Jinja2, `make_auth_router` refuses to start and
    names the missing extra — before, the router started, and the first click
    on the link activated the account and answered `500`.

**Recap.**

- With no frontend, the link comes in the `signup` body while `email=None`;
  with real email, only in the email.
- `POST /auth/activate/{token}` activates and already returns the JWT pair;
  `GET` on the same path is the Mode E HTML page, which asks for a login
  afterwards.
- `AUTH_ACTIVATION_URL_TEMPLATE` points at whoever will open the link: the
  frontend, the backend (Mode E) or the app (deep link).
- The `access_token` goes in `Authorization: Bearer …` on any route guarded
  by `current_user_dependency()`.
