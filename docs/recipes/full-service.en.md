---
description: "Use to build a whole running service: settings from mixins, migration at startup, signup and login, a protected upload and an end-to-end test."
---

# Complete service: settings, migrations, auth, upload and a test

Use this page when you want **a whole service that runs**, with the pieces
almost every backend needs wired together: settings composed from the SDK
mixins, the schema migrated at startup, signup and login, uploads for
logged-in users only, and an end-to-end test. Each piece has its own recipe;
here it shows up where it fits.

| File | What it does | Recipe for the piece |
| --- | --- | --- |
| `src/core/settings.py` | composes the settings mixins | [Settings](settings.md) |
| `src/db/models.py` | users table and tokens table | [Auth flow](auth-flow/setup.md#concrete-usertokenmodel) |
| `src/db/migrations.py` | `sync_schema_async()` in the lifespan | [Migrations](migrations.md#3-sync-the-schema-in-the-lifespan) |
| `src/api/dependencies/resources.py` | database, `UserAuthService`, `UploadUtils` | [Uploads](uploads.md#quick-reference) |
| `src/api/routers/files.py` | upload guarded by `current_user_dependency()` | [Uploads — protected route](uploads.md#upload-for-logged-in-users-only) |
| `src/api/app.py` | lifespan + health, auth and upload routers | [Testing](testing.md) |
| `tests/` | signup → login → `/auth/me` → upload | [Testing](testing.md) |

Everything on this page ran with `tempest-fastapi-sdk` 0.374.0 from PyPI,
Python 3.12, on 2026-11-27.

## Install the dependencies

Create the project and install only the extras it uses.

```bash
uv init --bare --name servico servico
cd servico
uv add "tempest-fastapi-sdk[auth,cli-ops,sqlite,upload]" uvicorn
uv add --dev pytest pytest-asyncio ruff
```

| Extra | What for |
| --- | --- |
| `[auth]` | bcrypt + PyJWT for `UserAuthService` |
| `[cli-ops]` | the `tempest db` command (without the quality gate) |
| `[sqlite]` | the `aiosqlite` driver |
| `[upload]` | `aiofiles` + `python-multipart` for `UploadUtils` |

`ruff` in the dev group is what the generated `alembic.ini`'s
`post_write_hooks` call to format each revision. Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
pythonpath = ["."]
```

## See the project tree

These are all the files; `alembic/` and `alembic.ini` come from `tempest db init`.

```text
servico/
├── .env
├── main.py
├── pyproject.toml
├── alembic.ini
├── alembic/
├── src/
│   ├── __init__.py
│   ├── server.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── migrations.py
│   │   └── models.py
│   └── api/
│       ├── __init__.py
│       ├── app.py
│       ├── dependencies/
│       │   ├── __init__.py
│       │   └── resources.py
│       └── routers/
│           ├── __init__.py
│           └── files.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_service.py
```

`src/__init__.py`, `src/api/__init__.py` and `tests/__init__.py` stay empty.

## Compose the settings

Inherit the mixins instead of declaring `DATABASE_URL`, `JWT_SECRET` or
`UPLOAD_DIR` by hand: each mixin brings the field, the default and the
`*_kwargs()` that builds the client.

```python
# src/core/settings.py
from tempest_fastapi_sdk import (
    AuthSettings,
    DatabaseSettings,
    EnvironmentSettings,
    JWTSettings,
    ServerSettings,
    UploadSettings,
)


class Settings(
    EnvironmentSettings,
    ServerSettings,
    DatabaseSettings,
    JWTSettings,
    AuthSettings,
    UploadSettings,
):
    """Service configuration, composed from the SDK mixins."""


settings: Settings = Settings()
```

```python
# src/core/__init__.py
from src.core.settings import Settings as Settings
from src.core.settings import settings as settings

__all__: list[str] = ["Settings", "settings"]
```

```bash
# .env
ENV=development
JWT_SECRET=troque-por-um-segredo-de-32-caracteres-ou-mais
AUTH_AUTO_ACTIVATE=true
UPLOAD_DIR=./var/uploads
UPLOAD_MAX_SIZE_BYTES=5242880
UPLOAD_ALLOWED_EXTENSIONS=[".png", ".jpg", ".pdf"]
```

| Mixin | Variables this page uses |
| --- | --- |
| `EnvironmentSettings` | `ENV` (required; `production` refuses to boot with development values, `test` drops the `.env`) |
| `ServerSettings` | `SERVER_HOST` (default `127.0.0.1`), `SERVER_PORT` (default `8000`) |
| `DatabaseSettings` | `DATABASE_URL` (default `sqlite+aiosqlite:///./app.db`) |
| `JWTSettings` | `JWT_SECRET` |
| `AuthSettings` | `AUTH_AUTO_ACTIVATE` (`true` skips the activation email) |
| `UploadSettings` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_ALLOWED_EXTENSIONS` |

!!! tip "No email in development"
    `AUTH_AUTO_ACTIVATE=true` makes the account active at `signup`, so login
    works without SMTP. For the flow with an activation email, see
    [Auth flow](auth-flow/backend-only.md).

## Declare the user models

Use this when you want the SDK's signup: `BaseUserModel` is the accounts table
and `make_user_token_model` the activation and reset tokens table.

```python
# src/db/models.py
from tempest_fastapi_sdk import BaseModel as BaseModel
from tempest_fastapi_sdk import BaseUserModel, BaseUserTokenModel, make_user_token_model


class UserModel(BaseUserModel):
    """User account: email, password hash and activation flags."""

    __tablename__ = "users"


UserTokenModel: type[BaseUserTokenModel] = make_user_token_model(user_table="users")

__all__: list[str] = ["BaseModel", "UserModel", "UserTokenModel"]
```

The `BaseModel` re-exported here is what the generated `alembic/env.py`
imports (`from src.db.models import BaseModel`) for autogenerate.

## Migrate the schema at startup

Use `sync_schema_async()` in the lifespan so the service boots with the
database at head, whether it is empty, already migrated or older than Alembic.

```python
# src/db/migrations.py
from pathlib import Path

from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcomeEnum

ALEMBIC_INI_PATH: Path = Path(__file__).resolve().parents[2] / "alembic.ini"


async def run_migrations(database_url: str) -> SchemaSyncOutcomeEnum:
    """Bring the database schema to the migrations head.

    Args:
        database_url (str): URL of the database the service uses.

    Returns:
        SchemaSyncOutcomeEnum: Which path ran.
    """
    return await AlembicHelper(
        str(ALEMBIC_INI_PATH),
        db_url=database_url,
    ).sync_schema_async()
```

```python
# src/db/__init__.py
from src.db.migrations import run_migrations as run_migrations
from src.db.models import UserModel as UserModel
from src.db.models import UserTokenModel as UserTokenModel

__all__: list[str] = ["UserModel", "UserTokenModel", "run_migrations"]
```

Generate the Alembic environment and the first revision:

```bash
uv run tempest db init
uv run tempest db revision -m "initial"
```

The generated revision creates `users` and `user_tokens`. Without it,
`sync_schema_async()` returns `NO_MIGRATIONS` and creates no table at all; the
three paths are in [Migrations](migrations.md#3-sync-the-schema-in-the-lifespan).

## Build the shared resources

Build the database, auth and uploads **once per process**, in
`api/dependencies/`, and inject them through `Depends`.

```python
# src/api/dependencies/resources.py
from collections.abc import Callable, Coroutine
from typing import Any

from tempest_fastapi_sdk import AsyncDatabaseManager, UploadUtils, UserAuthService

from src.core import settings
from src.db import UserModel, UserTokenModel

db: AsyncDatabaseManager = AsyncDatabaseManager(**settings.database_kwargs())
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)
get_current_user: Callable[..., Coroutine[Any, Any, Any]] = (
    auth_service.current_user_dependency()
)
uploads: UploadUtils = UploadUtils(**settings.upload_kwargs())


def get_uploads() -> UploadUtils:
    """Return the process-wide `UploadUtils` instance.

    Returns:
        UploadUtils: The utility configured from settings.
    """
    return uploads
```

```python
# src/api/dependencies/__init__.py
from src.api.dependencies.resources import auth_service as auth_service
from src.api.dependencies.resources import db as db
from src.api.dependencies.resources import get_current_user as get_current_user
from src.api.dependencies.resources import get_uploads as get_uploads

__all__: list[str] = ["auth_service", "db", "get_current_user", "get_uploads"]
```

- `email=None`: without `EmailUtils` the service sends no email; with
  `AUTH_AUTO_ACTIVATE=true` the account is born active.
- `current_user_dependency()` verifies the bearer token with the same secret
  the `auth_service` signs with, and returns the `UserModel` that owns it.
- `UploadUtils(**settings.upload_kwargs())` reads `UPLOAD_DIR`, the size
  ceiling and the allowlists from `.env`.

## Protect the upload route

Ask for the user through `Depends(get_current_user)`: without a valid token the
route answers `401` before reading the file.

```python
# src/api/routers/files.py
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel
from tempest_fastapi_sdk import UploadUtils

from src.api.dependencies import get_current_user, get_uploads
from src.db import UserModel

router: APIRouter = APIRouter(prefix="/api/files", tags=["files"])


class FileUploadResponseSchema(BaseModel):
    """Where the file was stored."""

    key: str


@router.post("", status_code=201)
async def upload_file(
    file: UploadFile,
    current: Annotated[UserModel, Depends(get_current_user)],
    uploads: Annotated[UploadUtils, Depends(get_uploads)],
) -> FileUploadResponseSchema:
    """Store the file in the logged-in user's folder.

    Args:
        file (UploadFile): The multipart form file.
        current (UserModel): The bearer token's owner.
        uploads (UploadUtils): The process-wide upload utility.

    Returns:
        FileUploadResponseSchema: The key relative to `UPLOAD_DIR`.
    """
    key = await uploads.save(file, subdir=str(current.id))
    return FileUploadResponseSchema(key=str(key))
```

```python
# src/api/routers/__init__.py
from src.api.routers.files import router as files_router

__all__: list[str] = ["files_router"]
```

`subdir=str(current.id)` stores each file in its owner's folder:
`var/uploads/<user_id>/<uuid>.png`.

## Build the app with a lifespan

The lifespan migrates the schema before opening the database;
`register_exception_handlers` turns the SDK's exceptions into the statuses and
the `{detail, code, details}` envelope.

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tempest_fastapi_sdk import (
    make_auth_router,
    make_health_router,
    register_exception_handlers,
)

from src.api.dependencies import auth_service, db
from src.api.routers import files_router
from src.core import settings
from src.db import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Migrate the schema, open the database and close it on shutdown.

    Args:
        app (FastAPI): The application starting up.

    Yields:
        None: Hands control back to FastAPI while the app serves.
    """
    await run_migrations(settings.DATABASE_URL)
    await db.connect()
    yield
    await db.disconnect()


def create_app() -> FastAPI:
    """Build the app with health, auth and upload.

    Returns:
        FastAPI: The application, ready for uvicorn.
    """
    app: FastAPI = FastAPI(title="servico", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(make_health_router(db=db))
    app.include_router(
        make_auth_router(auth_service, session_factory=db.session_dependency),
    )
    app.include_router(files_router)
    return app


app: FastAPI = create_app()
```

`make_auth_router` mounts `/auth/signup`, `/auth/login`, `/auth/me`,
`/auth/refresh` and the rest of the flow; `make_health_router` mounts
`/health/liveness` and `/health/readiness`.

## Run the server

Run uvicorn through `run_server`, which reads host and port from
`ServerSettings`.

```python
# src/server.py
from tempest_fastapi_sdk import run_server

from src.core import settings


def run() -> None:
    """Start uvicorn with host and port from settings."""
    run_server("src.api.app:app", settings=settings)
```

```python
# main.py
from src.server import run

if __name__ == "__main__":
    run()
```

```bash
SERVER_PORT=8765 uv run python main.py
```

## Call the API with curl

With the server up, this is the whole cycle; the output below is from a real
run (tokens cut).

```bash
B=http://127.0.0.1:8765
curl -s -X POST $B/auth/signup -H 'Content-Type: application/json' \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}'
TOKEN=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -s $B/auth/me -H "Authorization: Bearer $TOKEN"
curl -s -X POST $B/api/files -H "Authorization: Bearer $TOKEN" -F 'file=@foto.png;type=image/png'
curl -s -X POST $B/api/files -F 'file=@foto.png;type=image/png'
curl -s -X POST $B/api/files -H "Authorization: Bearer $TOKEN" -F 'file=@foto.png;filename=a.zip;type=application/zip'
curl -s $B/health/readiness
```

| Call | Status | Body |
| --- | --- | --- |
| `POST /auth/signup` | `201` | `{"user_id": "...", "activation_required": false, ...}` |
| `POST /auth/login` | `200` | `{"user_id": "...", "access_token": "eyJ...", ...}` |
| `GET /auth/me` | `200` | `{"id": "...", "is_active": true, "email": "ana@example.com", ...}` |
| `POST /api/files` with token | `201` | `{"key": "<user_id>/<uuid>.png"}` |
| `POST /api/files` without token | `401` | `{"detail": "Authorization token is missing or invalid", "code": "UNAUTHORIZED", "details": {}}` |
| `POST /api/files` with `.zip` | `415` | `{"detail": "Invalid file type", "code": "INVALID_FILE_TYPE", "details": {"extension": "zip", "allowed": ["jpg", "pdf", "png"]}}` |
| `GET /health/readiness` | `200` | `{"status": "ready", "checks": {"database": true}}` |

After the first start the database holds `alembic_version`, `users` and
`user_tokens`; restarting the server boots without error and the same login
answers `200`.

## Test end to end

Use the production app with its lifespan running: the test exercises the real
migration, auth and upload, on a temporary database and directory.

```python
# tests/conftest.py
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

TEST_DIR: Path = Path(tempfile.mkdtemp(prefix="servico-test-"))
TEST_ENVIRONMENT: dict[str, str] = {
    "ENV": "test",
    "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DIR / 'test.db'}",
    "JWT_SECRET": "segredo-de-teste-com-32-caracteres-ok",
    "AUTH_AUTO_ACTIVATE": "true",
    "UPLOAD_DIR": str(TEST_DIR / "uploads"),
    "UPLOAD_MAX_SIZE_BYTES": "1024",
    "UPLOAD_ALLOWED_EXTENSIONS": '[".png"]',
}
os.environ.update(TEST_ENVIRONMENT)

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Run the lifespan (migration + database) and yield a socketless client.

    Yields:
        AsyncClient: Client bound to the production app.
    """
    async with app.router.lifespan_context(app):
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as opened:
            yield opened
```

```python
# tests/test_service.py
from httpx import AsyncClient, Response

PNG: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


async def test_signup_login_me_upload(client: AsyncClient) -> None:
    """Take an account from signup to a protected upload.

    Args:
        client (AsyncClient): The app's client.
    """
    credentials: dict[str, str] = {"email": "ana@example.com", "password": "senha-forte-123"}

    signup: Response = await client.post("/auth/signup", json=credentials)
    assert signup.status_code == 201

    login: Response = await client.post("/auth/login", json=credentials)
    assert login.status_code == 200
    headers: dict[str, str] = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me: Response = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "ana@example.com"

    upload: Response = await client.post(
        "/api/files",
        headers=headers,
        files={"file": ("foto.png", PNG, "image/png")},
    )
    assert upload.status_code == 201
    assert upload.json()["key"].startswith(f"{me.json()['id']}/")

    anonymous: Response = await client.post(
        "/api/files",
        files={"file": ("foto.png", PNG, "image/png")},
    )
    assert anonymous.status_code == 401

    wrong_type: Response = await client.post(
        "/api/files",
        headers=headers,
        files={"file": ("pacote.zip", b"PK", "application/zip")},
    )
    assert wrong_type.status_code == 415
    assert wrong_type.json()["code"] == "INVALID_FILE_TYPE"

    too_large: Response = await client.post(
        "/api/files",
        headers=headers,
        files={"file": ("grande.png", PNG + b"\x00" * 2048, "image/png")},
    )
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "FILE_TOO_LARGE"
```

```bash
uv run pytest -v
```

```text
tests/test_service.py::test_signup_login_me_upload PASSED                [100%]

============================== 1 passed in 0.42s ===============================
```

- `ENV=test` in the process environment makes `EnvironmentSettings` **ignore
  the `.env`**: the suite does not inherit the development database or secret.
- The variables go into `os.environ` **before** `from src.api.app import app`,
  because `settings`, `db` and `uploads` are built at import time.
- The database is a temporary SQLite file, not `:memory:`: the migration runs
  in a thread (`sync_schema_async`) with its own connection, and an in-memory
  database is not shared across connections. With
  `sqlite+aiosqlite:///:memory:` the `signup` fails with
  `no such table: users`.

## Recap

- Settings: inherit the mixins (`EnvironmentSettings`, `ServerSettings`,
  `DatabaseSettings`, `JWTSettings`, `AuthSettings`, `UploadSettings`) and use
  the `*_kwargs()` to build the clients.
- Database: `tempest db init` + `tempest db revision -m "initial"` once; in the
  lifespan, `sync_schema_async()` before `db.connect()`.
- Auth: `UserAuthService` + `make_auth_router` give signup, login and
  `/auth/me`; `current_user_dependency()` guards your own routes.
- Upload: `UploadUtils(**settings.upload_kwargs())` injected through
  `Depends`; `415`, `413` and `401` come ready with
  `register_exception_handlers`.
- Test: the production app under `lifespan_context`, with the environment
  pinned to `ENV=test` before the import.
