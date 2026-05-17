# tempest-fastapi-sdk

Shared FastAPI/SQLAlchemy/Pydantic building blocks used across Tempest projects: base schemas, ORM model, async repository, pagination, settings, exceptions, Alembic helper, FastStream/TaskIQ broker managers, Redis cache, Server-Sent Events, Web Push and the utility classes (`PasswordUtils`, `JWTUtils`, `EmailUtils`, `UploadUtils`, `MetricsUtils`, `LogUtils`).

The goal is to start every new backend with the same opinionated foundation already in place — no copy-pasting `BaseModel`, no rewriting the same CRUD repository, no re-inventing the exception envelope.

---

## Table of contents

- [Install](#install)
  - [Optional extras](#optional-extras)
- [What's inside](#whats-inside)
- [Architecture overview](#architecture-overview)
- [Tutorial — building the *Users* feature](#tutorial--building-the-users-feature)
  - [1. Project layout](#1-project-layout)
  - [2. Settings & app entry point](#2-settings--app-entry-point)
  - [3. ORM model](#3-orm-model)
  - [4. Schemas](#4-schemas)
  - [5. Domain exceptions](#5-domain-exceptions)
  - [6. Repository](#6-repository)
  - [7. Service](#7-service)
  - [8. Router](#8-router)
  - [9. Pagination](#9-pagination)
- [Recipes](#recipes)
  - [Authentication](#authentication-recipe)
  - [File uploads](#file-uploads-recipe)
  - [Transactional email](#transactional-email-recipe)
  - [Alembic migrations](#alembic-migrations-recipe)
  - [BR document & phone validation](#br-document--phone-validation-recipe)
  - [Testing](#testing-recipe)
  - [Application bootstrap (`create_app`)](#application-bootstrap-recipe)
  - [Structured logging & request IDs](#structured-logging--request-ids-recipe)
  - [Settings mixins composition](#settings-mixins-composition-recipe)
  - [Controllers & services layering](#controllers--services-layering-recipe)
  - [Audit & soft-delete mixins](#audit--soft-delete-mixins-recipe)
  - [Cursor pagination](#cursor-pagination-recipe)
  - [Redis cache (`AsyncRedisManager`)](#redis-cache-recipe)
  - [Server-Sent Events (SSE)](#server-sent-events-recipe)
  - [Web Push notifications](#web-push-notifications-recipe)
  - [Message queues — FastStream (`AsyncBrokerManager`)](#message-queues--faststream-recipe)
  - [Background tasks — TaskIQ (`AsyncTaskBrokerManager`)](#background-tasks--taskiq-recipe)
  - [System metrics (`MetricsUtils`)](#system-metrics-recipe)
- [Reference](#reference)
- [Conventions](#conventions)
- [Development](#development)
- [Release](#release)
- [License](#license)

---

## Install

```bash
pip install tempest-fastapi-sdk
```

Via `pyproject.toml`:

```toml
dependencies = [
    "tempest-fastapi-sdk>=0.7.1",
]
```

Requires Python `>=3.11`.

### Optional extras

Feature-rich helpers pull in third-party dependencies that you only need when you actually use the helper. Pick the extras the service consumes:

| Extra | Pulls in | Unlocks |
| --- | --- | --- |
| `[auth]` | `bcrypt`, `PyJWT` | `PasswordUtils`, `JWTUtils` |
| `[email]` | `aiosmtplib` | `EmailUtils` |
| `[upload]` | `aiofiles`, `python-multipart` | `UploadUtils` |
| `[cache]` | `redis` | `AsyncRedisManager` |
| `[webpush]` | `pywebpush`, `cryptography` | `WebPushDispatcher` |
| `[metrics]` | `psutil`, `nvidia-ml-py` | `MetricsUtils` |
| `[queue]` | `faststream[rabbit]` | `AsyncBrokerManager` (FastStream) |
| `[tasks]` | `taskiq`, `taskiq-aio-pika` | `AsyncTaskBrokerManager` (TaskIQ) |
| `[all]` | everything above | every helper |

```bash
pip install "tempest-fastapi-sdk[auth,upload]"   # only what the service uses
pip install "tempest-fastapi-sdk[all]"           # or pull everything
```

Since `0.7.1` every optional dependency is imported lazily at first instantiation, so `import tempest_fastapi_sdk` works with any subset of extras — instantiating a helper whose extra is missing raises `ImportError` with a clear hint pointing at the right one.

---

## What's inside

| Module | Exports |
| --- | --- |
| `tempest_fastapi_sdk.schemas` | `BaseSchema`, `BaseResponseSchema`, `BasePaginationFilterSchema`, `BasePaginationSchema[T]`, `CursorPaginationFilterSchema`, `CursorPaginationSchema`, `encode_cursor`, `decode_cursor` |
| `tempest_fastapi_sdk.db` | `BaseModel`, `BaseRepository[ModelType]`, `AsyncDatabaseManager`, `AlembicHelper`, `NAMING_CONVENTION`, `AuditMixin`, `SoftDeleteMixin` |
| `tempest_fastapi_sdk.exceptions` | `AppException`, `NotFoundException`, `ConflictException`, `ValidationException`, `UnauthorizedException`, `ForbiddenException`, `InvalidTokenException`, `ExpiredTokenException`, `FileTooLargeException`, `InvalidFileTypeException` |
| `tempest_fastapi_sdk.settings` | `BaseAppSettings`, `ServerSettings`, `DatabaseSettings`, `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `CORSSettings` |
| `tempest_fastapi_sdk.api` | `register_exception_handlers`, `app_exception_handler`, `apply_cors`, `make_health_router`, `make_token_dependency`, `require_x_token`, `RequestIDMiddleware`, `HealthCheck` |
| `tempest_fastapi_sdk.controllers` | `BaseController` |
| `tempest_fastapi_sdk.services` | `BaseService` |
| `tempest_fastapi_sdk.core` | `configure_logging`, `JSONFormatter`, `get_request_id`/`set_request_id`/`clear_request_id`, `request_id_ctx` |
| `tempest_fastapi_sdk.sse` | `EventStream`, `ServerSentEvent`, `sse_response` |
| `tempest_fastapi_sdk.cache` *(extra: `[cache]`)* | `AsyncRedisManager` |
| `tempest_fastapi_sdk.webpush` *(extra: `[webpush]`)* | `WebPushDispatcher`, `WebPushError`, `WebPushGoneError`, `WebPushSubscriptionSchema`, `WebPushKeysSchema`, `WebPushPayloadSchema` |
| `tempest_fastapi_sdk.queue` *(extra: `[queue]`)* | `AsyncBrokerManager` (FastStream lifecycle wrapper) |
| `tempest_fastapi_sdk.tasks` *(extra: `[tasks]`)* | `AsyncTaskBrokerManager` (TaskIQ lifecycle wrapper) |
| `tempest_fastapi_sdk.utils` | `to_utc`, `utcnow`, `modify_dict`, `LogUtils`, `PasswordUtils` *(extra: `[auth]`)*, `JWTUtils` *(extra: `[auth]`)*, `EmailUtils` *(extra: `[email]`)*, `UploadUtils` *(extra: `[upload]`)*, `MetricsUtils`/`CPUMetrics`/`MemoryMetrics`/`DiskMetrics`/`GPUMetrics`/`SystemMetrics` *(extra: `[metrics]`)*, BR regex helpers (`CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR`, `is_valid_*`, `normalize_*`, `only_digits`, `*_PATTERN`) |

Core primitives are re-exported from `tempest_fastapi_sdk` at the top level — `from tempest_fastapi_sdk import BaseModel, BaseRepository, AppException` always works. The extras-gated managers in `tempest_fastapi_sdk.cache`, `tempest_fastapi_sdk.queue` and `tempest_fastapi_sdk.tasks` must be imported from their own submodule (`from tempest_fastapi_sdk.queue import AsyncBrokerManager`).

---

## Architecture overview

The SDK assumes a layered architecture where each layer has a single, narrow responsibility:

```text
HTTP request
    │
    ▼
┌─────────────┐    receive HTTP, validate input via schemas,
│   Router    │    call service, return response schema.
└──────┬──────┘    No business logic, no DB access.
       │
       ▼
┌─────────────┐    orchestrate use case across one or more services;
│ Controller  │    handle cross-service coordination only.
└──────┬──────┘    Optional — skip for simple CRUD.
       │
       ▼
┌─────────────┐    business rules, validation beyond Pydantic,
│   Service   │    domain decisions. Calls one or more repositories.
└──────┬──────┘    No HTTP types, no SQLAlchemy types.
       │
       ▼
┌─────────────┐    raw data access via SQLAlchemy. CRUD, filters,
│ Repository  │    pagination. Translates between ORM and schemas
└──────┬──────┘    via map_to_* methods. No business decisions.
       │
       ▼
┌─────────────┐    SQLAlchemy AsyncSession on top of asyncpg/aiosqlite.
│  Database   │
└─────────────┘
```

The SDK ships **`BaseModel`**, **`BaseRepository`**, **`BaseSchema`** and the exception/settings primitives. Routers, services and controllers are your code — the SDK gives you the conventions so they all look the same across projects.

---

## Tutorial — building the *Users* feature

We'll build a complete `Users` feature from scratch, end to end. Every file below is something you write in your project; SDK primitives are imported.

### 1. Project layout

```text
app/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── settings.py       # Settings(BaseAppSettings)
│   └── exceptions.py     # domain exceptions (UserNotFoundError, ...)
├── db/
│   ├── __init__.py       # re-exports BaseModel + every model
│   └── models/
│       ├── __init__.py
│       └── user.py       # UserModel(BaseModel)
├── schemas/
│   ├── __init__.py
│   └── user.py           # UserCreate/Update/Response/Filter
├── repositories/
│   ├── __init__.py
│   └── user.py           # UserRepository(BaseRepository[UserModel])
├── services/
│   ├── __init__.py
│   └── user.py           # UserService
├── api/
│   ├── __init__.py
│   ├── factory.py        # create_app()
│   └── routers/
│       ├── __init__.py
│       └── users.py
└── main.py               # uvicorn entry point
```

`__init__.py` re-exports every public symbol from its directory so consumers always do `from app.schemas import UserCreateSchema` (not `from app.schemas.user import UserCreateSchema`). This keeps refactors painless — move files around without breaking imports.

### 2. Settings & app entry point

```python
# app/core/settings.py
from tempest_fastapi_sdk import BaseAppSettings


class Settings(BaseAppSettings):
    """All environment-driven configuration lives here.

    BaseAppSettings already ships `env_file=.env`, `extra=ignore`,
    `case_sensitive=True`, `frozen=True` and `str_strip_whitespace=True`.
    """

    DB_URL: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_HOURS: int = 1

    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_ADDR: str = "noreply@example.com"

    UPLOAD_DIR: str = "./var/uploads"


settings = Settings()
```

```python
# app/api/factory.py
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    register_exception_handlers,
)

from app.api.routers import users
from app.core.settings import settings


db = AsyncDatabaseManager(settings.DB_URL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect on startup, dispose on shutdown."""
    await db.connect()
    yield
    await db.disconnect()


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    app = FastAPI(title="My API", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)

    app.include_router(users.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, bool]:
        return {"db": await db.health_check()}

    return app


app = create_app()
```

```python
# app/main.py
import uvicorn

from app.api.factory import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 3. ORM model

```python
# app/db/models/user.py
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserModel(BaseModel):
    """One row per registered user.

    Inherits from BaseModel, so it automatically gets:
    - id (UUID v4, cross-DB portable via sqlalchemy.Uuid)
    - is_active (bool, soft-delete flag)
    - created_at, updated_at (timezone-aware TIMESTAMP, set by Python AND
      the DB so the instance attribute is populated right after flush)
    - __tablename__ = "user" (auto: class name without "Model" suffix,
      snake-cased; override by assigning __tablename__ explicitly)
    - __eq__/__hash__ by (type, id) so the same row across sessions
      compares equal
    - to_dict(exclude, include, remove_none) and
      update_from_dict(data, allowed_fields) helpers
    """

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
```

Re-export it:

```python
# app/db/models/__init__.py
from app.db.models.user import UserModel

__all__: list[str] = ["UserModel"]
```

```python
# app/db/__init__.py
from app.db.models import UserModel
from tempest_fastapi_sdk import BaseModel

__all__: list[str] = ["BaseModel", "UserModel"]
```

> **Tip:** Always import models in `app/db/__init__.py`. SQLAlchemy needs to "see" every model before `BaseModel.metadata` is complete, so Alembic autogenerate and `create_tables()` work correctly.

### 4. Schemas

The recommended naming pattern: one `*Create`, `*Update`, `*Response` and `*Filter` schema per resource.

```python
# app/schemas/user.py
from pydantic import EmailStr, Field

from tempest_fastapi_sdk import (
    BasePaginationFilterSchema,
    BaseResponseSchema,
    BaseSchema,
)


class UserCreateSchema(BaseSchema):
    """Payload for POST /users."""

    name: str = Field(min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserUpdateSchema(BaseSchema):
    """Partial payload for PATCH /users/{id}. Every field optional."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    email: EmailStr | None = None


class UserResponseSchema(BaseResponseSchema):
    """Outbound representation.

    Inherits id/is_active/created_at/updated_at from BaseResponseSchema
    (timestamps already normalized to UTC by the field validator).
    """

    name: str
    email: EmailStr


class UserFilterSchema(BasePaginationFilterSchema):
    """Query-string filters for GET /users.

    Inherits page/size/order_by/ascending/is_active from
    BasePaginationFilterSchema. Add domain-level filters below.
    """

    name: str | None = None              # ILIKE %name% search
    email: EmailStr | None = None        # exact-match filter
```

```python
# app/schemas/__init__.py
from app.schemas.user import (
    UserCreateSchema,
    UserFilterSchema,
    UserResponseSchema,
    UserUpdateSchema,
)

__all__: list[str] = [
    "UserCreateSchema",
    "UserFilterSchema",
    "UserResponseSchema",
    "UserUpdateSchema",
]
```

### 5. Domain exceptions

The SDK ships generic `NotFoundException`, `ConflictException`, etc. Subclass them per domain so error responses have a useful `code` field:

```python
# app/core/exceptions.py
from typing import ClassVar

from tempest_fastapi_sdk import ConflictException, NotFoundException


class UserNotFoundError(NotFoundException):
    message: str = "Usuário não encontrado"
    code: ClassVar[str] = "USER_NOT_FOUND"


class UserEmailAlreadyTakenError(ConflictException):
    message: str = "Já existe um usuário com esse e-mail"
    code: ClassVar[str] = "USER_EMAIL_TAKEN"
```

The SDK's exception handler ([`register_exception_handlers`](#2-settings--app-entry-point)) serializes them to:

```json
{
    "detail": "Usuário não encontrado",
    "code": "USER_NOT_FOUND",
    "details": {}
}
```

The frontend branches on `code`, not on the (potentially translated) message.

### 6. Repository

```python
# app/repositories/user.py
from typing import ClassVar

from tempest_fastapi_sdk import AppException, BaseRepository

from app.core.exceptions import UserNotFoundError
from app.db.models import UserModel
from app.schemas import UserResponseSchema


class UserRepository(BaseRepository[UserModel]):
    """Data-access layer for users."""

    model: type[UserModel] = UserModel
    not_found_exception: ClassVar[type[AppException]] = UserNotFoundError

    def map_to_schema(self, instance: UserModel) -> UserResponseSchema:
        return UserResponseSchema.model_validate(instance)

    def map_to_response(self, instance: UserModel) -> UserResponseSchema:
        return self.map_to_schema(instance)
```

Per-domain error messages (optional but recommended in real apps):

```python
class UserRepository(BaseRepository[UserModel]):
    model: type[UserModel] = UserModel
    not_found_exception: ClassVar[type[AppException]] = UserNotFoundError

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            not_found_message="Usuário não encontrado",
            create_conflict_message="Já existe um usuário com esse e-mail",
            update_conflict_message="Conflito ao atualizar usuário",
        )
```

The base repo gives you 17 methods for free — see the [reference table](#baserepository-methods) below. Add custom methods on top:

```python
async def get_by_email(self, email: str) -> UserModel:
    """Look up a user by email. Raises UserNotFoundError on miss."""
    return await self.get({"email": email})
```

### 7. Service

The service is where business rules live. It calls one or more repositories and never touches HTTP or SQLAlchemy types directly.

```python
# app/services/user.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BasePaginationSchema,
    PasswordUtils,
)

from app.core.exceptions import UserEmailAlreadyTakenError
from app.repositories.user import UserRepository
from app.schemas import (
    UserCreateSchema,
    UserFilterSchema,
    UserResponseSchema,
    UserUpdateSchema,
)


class UserService:
    def __init__(
        self,
        session: AsyncSession,
        passwords: PasswordUtils,
    ) -> None:
        self.repo = UserRepository(session)
        self.passwords = passwords

    async def create(self, data: UserCreateSchema) -> UserResponseSchema:
        if await self.repo.exists({"email": data.email}):
            raise UserEmailAlreadyTakenError()
        user = self.repo.map_to_model(
            {
                **data.to_dict(exclude=["password"]),
                "password_hash": self.passwords.hash(data.password),
            }
        )
        user = await self.repo.add(user)
        return self.repo.map_to_response(user)

    async def get(self, user_id: UUID) -> UserResponseSchema:
        user = await self.repo.get_by_id(user_id)
        return self.repo.map_to_response(user)

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
    ) -> UserResponseSchema:
        user = await self.repo.get_by_id(user_id)
        user.update_from_dict(
            data.to_dict(),
            allowed_fields={"name", "email"},   # prevents mass-assignment
        )
        user = await self.repo.update(user)
        return self.repo.map_to_response(user)

    async def soft_delete(self, user_id: UUID) -> None:
        await self.repo.soft_delete(user_id)

    async def list_paginated(
        self,
        filters: UserFilterSchema,
    ) -> BasePaginationSchema[UserResponseSchema]:
        page = await self.repo.paginate(
            filters=filters.get_conditions(),
            page=filters.page,
            page_size=filters.size,
            order_by=filters.order_by,
            ascending=filters.ascending,
        )
        return BasePaginationSchema[UserResponseSchema](
            items=[self.repo.map_to_response(u) for u in page["items"]],
            total=page["total"],
            page=page["page"],
            size=page["size"],
            pages=page["pages"],
        )
```

### 8. Router

```python
# app/api/routers/users.py
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BasePaginationSchema, PasswordUtils

from app.api.factory import db
from app.core.settings import settings
from app.schemas import (
    UserCreateSchema,
    UserFilterSchema,
    UserResponseSchema,
    UserUpdateSchema,
)
from app.services.user import UserService


router = APIRouter(prefix="/users", tags=["users"])

# Single PasswordUtils instance per process — bcrypt is stateless.
_passwords = PasswordUtils()


def get_service(
    session: AsyncSession = Depends(db.session_dependency),
) -> UserService:
    return UserService(session, _passwords)


@router.post(
    "",
    response_model=UserResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreateSchema,
    service: UserService = Depends(get_service),
) -> UserResponseSchema:
    return await service.create(data)


@router.get("/{user_id}", response_model=UserResponseSchema)
async def get_user(
    user_id: UUID,
    service: UserService = Depends(get_service),
) -> UserResponseSchema:
    return await service.get(user_id)


@router.patch("/{user_id}", response_model=UserResponseSchema)
async def update_user(
    user_id: UUID,
    data: UserUpdateSchema,
    service: UserService = Depends(get_service),
) -> UserResponseSchema:
    return await service.update(user_id, data)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    service: UserService = Depends(get_service),
) -> None:
    await service.soft_delete(user_id)


@router.get("", response_model=BasePaginationSchema[UserResponseSchema])
async def list_users(
    filters: UserFilterSchema = Depends(),
    service: UserService = Depends(get_service),
) -> BasePaginationSchema[UserResponseSchema]:
    return await service.list_paginated(filters)
```

### 9. Pagination

The pagination contract is enforced end-to-end by SDK primitives:

- `UserFilterSchema(BasePaginationFilterSchema)` parses `?page=&size=&order_by=&ascending=&is_active=&name=` from the query string and exposes `.get_conditions()` returning only the domain-level filters (without pagination keys).
- `UserRepository.paginate(...)` runs the query with the filter dict + ordering + offset/limit + count, returning `{items, total, page, size, pages}`.
- `BasePaginationSchema[UserResponseSchema]` wraps the result so OpenAPI documents the response shape correctly.

```http
GET /users?page=2&size=20&order_by=name&ascending=true&is_active=true&name=ana
```

Returns:

```json
{
    "items": [
        {"id": "...", "name": "Ana ...", "email": "...", ...},
        ...
    ],
    "total": 142,
    "page": 2,
    "size": 20,
    "pages": 8
}
```

---

## Recipes

### Authentication recipe

End-to-end signup + login + protected route using `PasswordUtils` and `JWTUtils`. Requires the `[auth]` extra.

#### Wire the utility singletons

```python
# app/core/security.py
from datetime import timedelta

from tempest_fastapi_sdk import JWTUtils, PasswordUtils

from app.core.settings import settings


passwords = PasswordUtils(rounds=12)

tokens = JWTUtils(
    secret=settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
    default_ttl=timedelta(hours=settings.JWT_TTL_HOURS),
    issuer="my-app",
)
```

#### Signup

Reuse the `UserService.create` defined in the tutorial — it already hashes the password.

#### Login

```python
# app/schemas/auth.py
from pydantic import EmailStr

from tempest_fastapi_sdk import BaseSchema


class LoginSchema(BaseSchema):
    email: EmailStr
    password: str


class TokenResponseSchema(BaseSchema):
    access_token: str
    token_type: str = "bearer"
```

```python
# app/services/auth.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import JWTUtils, PasswordUtils, UnauthorizedException

from app.repositories.user import UserRepository
from app.schemas.auth import LoginSchema, TokenResponseSchema


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        passwords: PasswordUtils,
        tokens: JWTUtils,
    ) -> None:
        self.repo = UserRepository(session)
        self.passwords = passwords
        self.tokens = tokens

    async def login(self, data: LoginSchema) -> TokenResponseSchema:
        user = await self.repo.get_or_none({"email": data.email})
        if user is None or not self.passwords.verify(
            data.password, user.password_hash
        ):
            # Same error for both cases — don't leak which one failed.
            raise UnauthorizedException(message="E-mail ou senha inválidos")
        token = self.tokens.encode({"sub": str(user.id)})
        return TokenResponseSchema(access_token=token)
```

```python
# app/api/routers/auth.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.factory import db
from app.core.security import passwords, tokens
from app.schemas.auth import LoginSchema, TokenResponseSchema
from app.services.auth import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(
    session: AsyncSession = Depends(db.session_dependency),
) -> AuthService:
    return AuthService(session, passwords, tokens)


@router.post("/login", response_model=TokenResponseSchema)
async def login(
    data: LoginSchema,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponseSchema:
    return await service.login(data)
```

#### Protect a route — JWT dependency

```python
# app/api/dependencies/auth.py
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import UnauthorizedException

from app.api.factory import db
from app.core.security import tokens
from app.db.models import UserModel
from app.repositories.user import UserRepository


bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(db.session_dependency),
) -> UserModel:
    if credentials is None:
        raise UnauthorizedException(message="Token ausente")
    payload = tokens.decode(credentials.credentials)
    # `tokens.decode` raises InvalidTokenException / ExpiredTokenException
    # already — both serialize to 401 with proper `code`.
    user_id = UUID(payload["sub"])
    repo = UserRepository(session)
    return await repo.get_by_id(user_id)
```

```python
# Use in any route
@router.get("/me", response_model=UserResponseSchema)
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current)
```

#### Soft auth (optional user)

For endpoints that work both authenticated and anonymous, use `decode_or_none`:

```python
async def get_current_user_or_none(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(db.session_dependency),
) -> UserModel | None:
    if credentials is None:
        return None
    payload = tokens.decode_or_none(credentials.credentials)
    if payload is None:
        return None
    repo = UserRepository(session)
    return await repo.get_or_none({"id": UUID(payload["sub"])})
```

---

### File uploads recipe

Avatar endpoint with validation + cleanup. Requires the `[upload]` extra.

```python
# app/core/storage.py
from tempest_fastapi_sdk import UploadUtils

from app.core.settings import settings


avatar_storage = UploadUtils(
    upload_dir=f"{settings.UPLOAD_DIR}/avatars",
    max_size_bytes=5 * 1024 * 1024,            # 5 MiB
    allowed_extensions={"png", "jpg", "jpeg", "webp"},
    allowed_mimetypes={"image/png", "image/jpeg", "image/webp"},
)
```

```python
# app/api/routers/users.py (extension)
from fastapi import UploadFile

from app.core.storage import avatar_storage


@router.post("/{user_id}/avatar", response_model=UserResponseSchema)
async def upload_avatar(
    user_id: UUID,
    file: UploadFile,
    current: UserModel = Depends(get_current_user),
    service: UserService = Depends(get_service),
) -> UserResponseSchema:
    if current.id != user_id:
        raise ForbiddenException(message="Só pode editar o próprio avatar")
    path = await avatar_storage.save(file, subdir=str(user_id))
    return await service.set_avatar(user_id, str(path))
```

Inside the service:

```python
async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
    user = await self.repo.get_by_id(user_id)
    # Delete previous file when replacing.
    if user.avatar_path and user.avatar_path != path:
        avatar_storage.delete(user.avatar_path)
    user.avatar_path = path
    user = await self.repo.update(user)
    return self.repo.map_to_response(user)
```

`UploadUtils.save()` raises `FileTooLargeException` (413) or `InvalidFileTypeException` (415) on rejection — the SDK's exception handler already returns the right status code with a `code` field on the response.

#### Serving the file back

Local-disk uploads are best served by an upstream (nginx / Caddy) so FastAPI doesn't stream bytes. For dev:

```python
from fastapi.staticfiles import StaticFiles

app.mount(
    "/static/uploads",
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="uploads",
)
```

Construct the public URL in the response schema:

```python
class UserResponseSchema(BaseResponseSchema):
    name: str
    email: EmailStr
    avatar_url: str | None = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _absolute_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # avatar_path stored as relative path → public URL
        return f"/static/uploads/{value}"
```

---

### Transactional email recipe

Password reset flow using `EmailUtils` + a short-lived JWT. Requires the `[email]` extra.

```python
# app/core/mailer.py
from tempest_fastapi_sdk import EmailUtils

from app.core.settings import settings


mailer = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    from_addr=settings.SMTP_FROM_ADDR,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    use_starttls=True,
)
```

```python
# app/services/password_reset.py
from datetime import timedelta

from tempest_fastapi_sdk import EmailUtils, JWTUtils, NotFoundException

from app.repositories.user import UserRepository


class PasswordResetService:
    def __init__(
        self,
        repo: UserRepository,
        tokens: JWTUtils,
        mailer: EmailUtils,
    ) -> None:
        self.repo = repo
        self.tokens = tokens
        self.mailer = mailer

    async def request_reset(self, email: str) -> None:
        """Send a password-reset link to `email`.

        Always returns silently — don't reveal whether the email
        is registered or not (avoids account enumeration).
        """
        user = await self.repo.get_or_none({"email": email})
        if user is None:
            return
        token = self.tokens.encode(
            {"sub": str(user.id), "purpose": "password_reset"},
            ttl=timedelta(minutes=15),
        )
        reset_url = f"https://my-app.com/reset-password?token={token}"
        await self.mailer.send(
            to=user.email,
            subject="Reset your password",
            body=f"Click here to reset your password: {reset_url}",
            html=f'<p>Click <a href="{reset_url}">here</a> to reset.</p>',
        )

    async def consume_reset(
        self,
        token: str,
        new_password: str,
        passwords: PasswordUtils,
    ) -> None:
        # `decode` raises InvalidTokenException / ExpiredTokenException
        # (both 401). Caught by the SDK handler.
        payload = self.tokens.decode(token)
        if payload.get("purpose") != "password_reset":
            raise InvalidTokenException()
        user = await self.repo.get_by_id(UUID(payload["sub"]))
        user.password_hash = passwords.hash(new_password)
        await self.repo.update(user)
```

---

### Alembic migrations recipe

Full workflow: bootstrap → first migration → apply → CI gate.

#### Bootstrap once per project

```python
# scripts/alembic_init.py
from tempest_fastapi_sdk import AlembicHelper

from app.core.settings import settings

helper = AlembicHelper(config_path="alembic.ini", db_url=settings.DB_URL)
helper.init(
    directory="alembic",
    metadata_module="app.db",        # exposes BaseModel
    metadata_attr="BaseModel",
    db_url=settings.DB_URL,
)
```

Run once: `uv run python scripts/alembic_init.py`.

This creates:

```text
alembic.ini                 # SDK-curated config (UTC timezone, date-prefixed file template)
alembic/
├── env.py                  # SDK template (already wires target_metadata, compare_type, batch mode)
├── script.py.mako
└── versions/
```

#### Author migrations

```python
# scripts/make_migration.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from app.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DB_URL)
helper.revision(
    message=sys.argv[1],
    autogenerate=True,
)
```

```bash
uv run python scripts/make_migration.py "add users table"
```

Generated file lands at `alembic/versions/2026_05_16_1432-ae12cd34_add_users_table.py` — the date prefix means files sort chronologically and merge conflicts are obvious.

#### Apply on startup

```python
# app/api/factory.py — extend lifespan
import asyncio

from tempest_fastapi_sdk import AlembicHelper


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Run pending migrations before serving traffic.
    helper = AlembicHelper("alembic.ini", db_url=settings.DB_URL)
    await asyncio.to_thread(helper.upgrade)

    await db.connect()
    yield
    await db.disconnect()
```

#### CI gate — schema must match models

```python
# scripts/check_migrations.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from app.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DB_URL)
if not helper.check():
    print("Schema drift detected — run make_migration.py and commit.")
    sys.exit(1)
print("Schema is in sync.")
```

```yaml
# .github/workflows/ci.yml
- name: Check migrations are in sync
  run: uv run python scripts/check_migrations.py
```

---

### BR document & phone validation recipe

`tempest_fastapi_sdk.utils.regex` ships ready-to-use regex patterns, validators, normalizers and Pydantic types for the identity/contact fields that show up in almost every Brazilian API. No extra required — pure stdlib + Pydantic (already a core dependency).

| Symbol | Kind | Purpose |
| --- | --- | --- |
| `CPF_PATTERN`, `CNPJ_PATTERN`, `CPF_CNPJ_PATTERN`, `PHONE_BR_PATTERN` | `re.Pattern[str]` | Compiled regex (masked or raw input). |
| `is_valid_cpf`, `is_valid_cnpj`, `is_valid_cpf_cnpj` | `(str) -> bool` | Format match **+** check-digit math. All-same-digit sequences rejected. |
| `is_valid_phone_br` | `(str) -> bool` | BR phone shape: optional `+55`, optional DDD, optional 9th digit. |
| `normalize_cpf`, `normalize_cnpj`, `normalize_cpf_cnpj`, `normalize_phone_br` | `(str) -> str` | Strip mask to digits-only; raise `ValueError` if invalid. |
| `only_digits` | `(str) -> str` | Strip every non-digit character. |
| `CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR` | `Annotated[str, AfterValidator(...)]` | Drop-in Pydantic field types — validate + normalize automatically. |

#### Schema usage

```python
from pydantic import EmailStr, Field

from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CPF, CPFOrCNPJ, PhoneBR


class CustomerCreateSchema(BaseSchema):
    """Payload for POST /customers.

    `document` accepts CPF or CNPJ in masked or raw form and is
    stored digits-only after validation. `phone` is normalized the
    same way. Invalid values surface as a Pydantic `ValidationError`
    (HTTP 422 via the SDK exception handler).
    """

    name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    document: CPFOrCNPJ
    phone: PhoneBR
```

Valid input:

```json
{
    "name": "Ana",
    "email": "ana@example.com",
    "document": "529.982.247-25",
    "phone": "+55 (11) 98888-7777"
}
```

After validation:

```python
CustomerCreateSchema(...).document  # "52998224725"
CustomerCreateSchema(...).phone     # "5511988887777"
```

#### Manual validation (services, controllers, queue handlers)

```python
from tempest_fastapi_sdk.utils import (
    is_valid_cpf_cnpj,
    normalize_cpf_cnpj,
    only_digits,
)

if not is_valid_cpf_cnpj(raw_document):
    raise ValidationException(message="Documento inválido")

document_digits = normalize_cpf_cnpj(raw_document)
```

#### Filtering by stored digits

The normalizers strip masks before saving, so repository filters and unique constraints all work on the canonical digits-only form:

```python
await repo.get({"document": normalize_cpf_cnpj(query)})
```

---

### Testing recipe

pytest + pytest-asyncio + in-memory SQLite + FastAPI TestClient.

#### Shared fixtures

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager

from app.api.factory import create_app
from app.db import BaseModel       # importing BaseModel ensures models are registered


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncDatabaseManager, None]:
    """Fresh in-memory DB per test."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest_asyncio.fixture
async def session(db: AsyncDatabaseManager) -> AsyncGenerator[AsyncSession, None]:
    """Managed session bound to the in-memory DB."""
    async with db.get_session_context() as session:
        yield session


@pytest_asyncio.fixture
async def client(db: AsyncDatabaseManager) -> AsyncGenerator[TestClient, None]:
    """FastAPI TestClient with the SDK manager overridden to use SQLite."""
    app = create_app()
    # Override the session dependency to use the test DB.
    from app.api.factory import db as production_db

    app.dependency_overrides[production_db.session_dependency] = db.session_dependency

    async with TestClient(app) as client:
        yield client
```

#### Repository test

```python
# tests/repositories/test_user.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UserNotFoundError
from app.db.models import UserModel
from app.repositories.user import UserRepository


class TestUserRepository:
    async def test_get_by_email_raises_when_missing(
        self, session: AsyncSession
    ) -> None:
        repo = UserRepository(session)
        with pytest.raises(UserNotFoundError):
            await repo.get({"email": "ghost@example.com"})

    async def test_add_and_get(self, session: AsyncSession) -> None:
        repo = UserRepository(session)
        user = await repo.add(
            UserModel(
                name="Ana", email="ana@example.com", password_hash="x"
            )
        )
        loaded = await repo.get_by_id(user.id)
        assert loaded.name == "Ana"
```

#### Endpoint test

```python
# tests/api/test_users.py
from fastapi.testclient import TestClient


class TestUsersAPI:
    def test_create_user(self, client: TestClient) -> None:
        response = client.post(
            "/users",
            json={
                "name": "Ana",
                "email": "ana@example.com",
                "password": "hunter22",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "ana@example.com"
        assert "password" not in body
        assert "password_hash" not in body

    def test_get_user_not_found(self, client: TestClient) -> None:
        response = client.get("/users/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        # SDK envelope is always {detail, code, details}
        assert body["code"] == "USER_NOT_FOUND"
```

The SDK also ships `tempest_fastapi_sdk.testing` helpers (`create_test_engine`, `create_test_session_factory`, `init_test_metadata`, `drop_test_metadata`, `test_database`, `test_session`) for tests that don't need a full `AsyncDatabaseManager`:

```python
from tempest_fastapi_sdk.testing import test_session

async def test_repo_directly() -> None:
    async with test_session() as session:
        repo = UserRepository(session)
        await repo.add(UserModel(name="Ana", email="ana@example.com", password_hash="x"))
        assert await repo.count() == 1
```

### Application bootstrap recipe

The SDK ships every piece of an `app.py` factory: exception handlers, CORS, request-ID middleware, the health router and a shared-secret token dependency. A full bootstrap looks like this:

```python
# app/api/factory.py
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RequestIDMiddleware,
    apply_cors,
    configure_logging,
    make_health_router,
    make_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from app.core.settings import settings


configure_logging(level=settings.LOG_LEVEL, json_output=settings.LOG_JSON)

db = AsyncDatabaseManager(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
)
redis = AsyncRedisManager(settings.REDIS_URL)
require_token = make_token_dependency(settings.TOKEN_SECRET)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    await redis.connect()
    try:
        yield
    finally:
        await redis.disconnect()
        await db.disconnect()


def create_app() -> FastAPI:
    app = FastAPI(
        title="my-service",
        version=settings.VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    apply_cors(app, settings)
    register_exception_handlers(app)

    app.include_router(
        make_health_router(
            db=db,
            checks={"redis": redis.health_check},
            version=settings.VERSION,
        ),
    )

    from app.api.routers import users

    app.include_router(users.router, prefix="/api", dependencies=[Depends(require_token)])
    return app
```

Key points:

- `RequestIDMiddleware` reads/writes `X-Request-ID` and seeds `request_id_ctx` so every log line emitted during the request carries the correlation ID.
- `apply_cors(app, settings)` reads `CORSSettings` defaults; pass keyword overrides for one-off changes.
- `register_exception_handlers(app)` wires every `AppException` subclass to the canonical `{detail, code, details}` envelope.
- `make_health_router(db=db, checks={"redis": redis.health_check}, version=...)` mounts `GET /health/liveness` and `GET /health/readiness` (returns `503` when any check fails).
- `make_token_dependency(secret)` returns an async dependency that validates `X-Token` with `hmac.compare_digest`; pass empty string to disable in dev.

### Structured logging & request IDs recipe

`configure_logging` installs a JSON handler on the root logger that emits one-line JSON records carrying the active request ID. `LogUtils` is a thin facade that adds level methods accepting structured `**fields`.

```python
from tempest_fastapi_sdk import LogUtils, configure_logging
from tempest_fastapi_sdk.core import get_request_id

# Imperative — call once during bootstrap.
configure_logging(level="INFO", json_output=True)

# Facade — handy for service-wide singletons.
log = LogUtils("app.users", level="INFO")
log.info("user_created", user_id=str(user.id), email=user.email)
log.warning("login_throttled", ip="1.2.3.4", attempts=5)

try:
    risky()
except RuntimeError:
    log.exception("risky_failed", op="reconcile")  # appends traceback

# Surface the correlation ID outside the log line if needed.
request_id = get_request_id()
```

JSON output (single line — formatted here for readability):

```json
{
  "timestamp": "2026-05-16T20:14:33.412+00:00Z",
  "level": "INFO",
  "logger": "app.users",
  "message": "user_created",
  "request_id": "d83e4b0c-7c2f-4bd6-aaa1-7d4f6cf5e5e9",
  "user_id": "9c1a5b2d-...",
  "email": "ana@example.com"
}
```

The middleware accepts a custom header name (`RequestIDMiddleware(app, header_name="X-Correlation-ID")`); the same header is echoed back on every response.

### Settings mixins composition recipe

`BaseAppSettings` is the configured `pydantic-settings` base. The SDK also exposes composable mixins for the most common dependencies; pick the ones the service needs and put `BaseAppSettings` at the **end** of the MRO so its `model_config` wins.

```python
# app/core/settings.py
from pydantic import Field

from tempest_fastapi_sdk import (
    BaseAppSettings,
    CORSSettings,
    DatabaseSettings,
    JWTSettings,
    RabbitMQSettings,
    RedisSettings,
    ServerSettings,
)


class Settings(
    ServerSettings,
    DatabaseSettings,
    RedisSettings,
    RabbitMQSettings,
    JWTSettings,
    CORSSettings,
    BaseAppSettings,
):
    """Service-wide settings."""

    VERSION: str = Field(default="0.0.0")
    TOKEN_SECRET: str = Field(default="")


settings = Settings()
```

Each mixin advertises its own env vars (`SERVER_*` lives on `ServerSettings`, `DATABASE_*` on `DatabaseSettings`, `REDIS_*` on `RedisSettings`, `RABBITMQ_*` on `RabbitMQSettings`, `JWT_*` on `JWTSettings`, `CORS_*` on `CORSSettings`). Skip the mixins the service doesn't use; mix them in and the `.env` keys are picked up automatically.

### Controllers & services layering recipe

`BaseService[RepositoryT, ResponseT]` and `BaseController[ServiceT, ResponseT]` are generic skeletons matching the SDK layering (router → controller → service → repository). They expose pass-through CRUD methods so simple endpoints can subclass them without overriding anything; you override only methods that need orchestration.

```python
# app/services/user_service.py
from uuid import UUID

from tempest_fastapi_sdk import BaseService

from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.utils.security import password_utils


class UserService(BaseService[UserRepository, UserResponse]):
    """Business logic for the user feature."""

    async def signup(self, data: UserCreate) -> UserResponse:
        # Business logic — hash the password, then delegate to the repo.
        instance = self.repository.map_to_model(
            {
                "name": data.name,
                "email": data.email,
                "password_hash": password_utils.hash(data.password),
            },
        )
        created = await self.repository.add(instance)
        return self.repository.map_to_response(created)


# app/controllers/user_controller.py
from tempest_fastapi_sdk import BaseController

from app.schemas.user import UserCreate, UserResponse
from app.services.user_service import UserService


class UserController(BaseController[UserService, UserResponse]):
    """Thin orchestration over UserService."""

    async def signup(self, data: UserCreate) -> UserResponse:
        # Pass-through today; the controller is the seam to add
        # cross-service coordination later (audit log, outbox event,
        # downstream notification, etc.) without touching the router.
        return await self.service.signup(data)


# app/api/dependencies/controllers.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.factory import db
from app.controllers.user_controller import UserController
from app.repositories.user import UserRepository
from app.services.user_service import UserService


def get_user_controller(
    session: AsyncSession = Depends(db.session_dependency),
) -> UserController:
    return UserController(UserService(UserRepository(session)))


# app/api/routers/users.py
from fastapi import APIRouter, Depends, status

from app.api.dependencies.controllers import get_user_controller
from app.controllers.user_controller import UserController
from app.schemas.user import UserCreate, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreate,
    controller: UserController = Depends(get_user_controller),
) -> UserResponse:
    return await controller.signup(data)
```

Keep controllers present even when they only pass through — the import graph stays uniform across services, so adding cross-cutting policy later doesn't change the router signature.

### Audit & soft-delete mixins recipe

`SoftDeleteMixin` adds a `deleted_at` timestamp column with `mark_deleted()` / `mark_restored()` / `is_deleted` helpers. `AuditMixin` adds `created_by` / `updated_by` UUID columns with `stamp_created_by(user_id)` / `stamp_updated_by(user_id)` helpers. Mix them in alongside `BaseModel`:

```python
# app/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AuditMixin, BaseModel, SoftDeleteMixin


class UserModel(BaseModel, SoftDeleteMixin, AuditMixin):
    """Users — soft-deletable and audited."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

Filtering is the caller's responsibility — the mixin doesn't install a global filter. Hide soft-deleted rows from list endpoints by passing `deleted_at=None` (or filtering in your repository subclass):

```python
async def list_alive(self) -> list[UserResponse]:
    instances = await self.repository.list(filters={"deleted_at": None})
    return [self.repository.map_to_response(i) for i in instances]
```

Stamping audit columns belongs to the service layer where the current user is in scope:

```python
async def update(self, user_id: UUID, data: UserUpdate, *, actor_id: UUID) -> UserResponse:
    instance = await self.repository.get_by_id(user_id)
    instance.update_from_dict(data.model_dump(exclude_unset=True))
    instance.stamp_updated_by(actor_id)
    updated = await self.repository.update(instance)
    return self.repository.map_to_response(updated)
```

Use the mixin's helpers (`mark_deleted` / `mark_restored`) when you want the `deleted_at` semantics; use `BaseRepository.soft_delete(id)` when the existing `is_active` flag is enough.

### Cursor pagination recipe

Cursor pagination scales better than offset pagination on big tables (no `COUNT(*)`, stable under concurrent inserts) at the cost of losing random-access. The SDK provides `CursorPaginationFilterSchema`, `CursorPaginationSchema[T]` and the opaque `encode_cursor` / `decode_cursor` helpers.

```python
# app/schemas/user.py
from tempest_fastapi_sdk import CursorPaginationFilterSchema, CursorPaginationSchema

from app.schemas.user import UserResponse


class UserCursorFilter(CursorPaginationFilterSchema):
    name: str | None = None  # ILIKE %value% via repository convention


UserCursorPage = CursorPaginationSchema[UserResponse]
```

Repository helper (cursor over `created_at` + `id` tie-break):

```python
# app/repositories/user.py
from sqlalchemy import asc, desc

from tempest_fastapi_sdk import BaseRepository, decode_cursor, encode_cursor

from app.db.models.user import UserModel
from app.schemas.user import UserResponse


class UserRepository(BaseRepository[UserModel]):
    model = UserModel

    async def cursor_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        ascending: bool,
        filters: dict[str, Any] | None = None,
    ) -> UserCursorPage:
        query = select(UserModel)
        if filters:
            query = self._apply_filters(query, filters)

        order = asc if ascending else desc
        query = query.order_by(order(UserModel.created_at), order(UserModel.id))

        if cursor is not None:
            state = decode_cursor(cursor)
            cmp = (UserModel.created_at, UserModel.id) > (state["value"], state["id"])
            query = query.where(cmp if ascending else ~cmp)

        query = query.limit(limit + 1)  # peek one ahead to set has_more
        result = await self.session.execute(query)
        rows = list(result.unique().scalars().all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            encode_cursor(
                {"id": str(rows[-1].id), "value": rows[-1].created_at.isoformat()},
            )
            if has_more and rows
            else None
        )
        return UserCursorPage(
            items=[self.map_to_response(r) for r in rows],
            next_cursor=next_cursor,
            has_more=has_more,
            limit=limit,
        )
```

Router:

```python
@router.get("/", response_model=UserCursorPage)
async def list_users(
    f: UserCursorFilter = Depends(),
    controller: UserController = Depends(get_user_controller),
) -> UserCursorPage:
    return await controller.service.repository.cursor_page(
        cursor=f.cursor,
        limit=f.limit,
        ascending=f.ascending,
        filters=f.get_conditions(),
    )
```

The cursor is opaque base64-url-safe JSON — clients never inspect it; they pass back the value verbatim until `next_cursor` becomes `null`.

### Redis cache recipe

`AsyncRedisManager` wraps `redis.asyncio` with the same connect/disconnect/health-check surface as `AsyncDatabaseManager`. Install with `[cache]`.

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager

cache = AsyncRedisManager(settings.REDIS_URL, decode_responses=True)

# Lifespan
await cache.connect()
...
await cache.disconnect()

# Direct use
await cache.client.set("user:123:name", "Ana", ex=300)
name = await cache.client.get("user:123:name")

# FastAPI dependency — yields the live client.
from fastapi import Depends
from redis.asyncio import Redis


@router.get("/cached")
async def cached_endpoint(
    redis: Redis = Depends(cache.client_dependency),
) -> dict[str, str]:
    value = await redis.get("greeting") or "hello"
    return {"value": value}
```

Wire the health check on the canonical router with `make_health_router(checks={"redis": cache.health_check})` so readiness probes fail when Redis is down.

### Server-Sent Events recipe

`EventStream` is an in-memory async queue feeding one SSE HTTP connection. `ServerSentEvent` encodes one frame; `sse_response` wraps the byte stream in a Starlette `StreamingResponse` with SSE-friendly headers.

```python
# app/api/routers/events.py
import asyncio

from fastapi import APIRouter

from tempest_fastapi_sdk import EventStream, sse_response

router = APIRouter()


@router.get("/events")
async def events() -> "StreamingResponse":  # forward-declared by Starlette
    stream = EventStream(heartbeat_seconds=15.0)

    async def producer() -> None:
        for n in range(1, 4):
            await stream.publish({"n": n}, event="counter", id=str(n))
            await asyncio.sleep(1)
        await stream.close()

    asyncio.create_task(producer())
    return sse_response(stream.stream())
```

Browser side:

```javascript
const es = new EventSource("/events");
es.addEventListener("counter", (e) => console.log("got", JSON.parse(e.data)));
```

`heartbeat_seconds` emits a `: keepalive` SSE comment when idle so load-balancers don't close long-lived connections. `ServerSentEvent.data` accepts strings, bytes or any JSON-serializable Python object — non-strings are JSON-encoded automatically. Pass `retry=` to hint the browser at the reconnect delay (milliseconds).

### Web Push notifications recipe

`WebPushDispatcher` wraps the synchronous `pywebpush` library in `asyncio.to_thread` and surfaces the two errors the application cares about: `WebPushGoneError` (HTTP 404/410 — delete the subscription) and `WebPushError` (everything else). Install with `[webpush]`.

```python
# app/services/notifications.py
from tempest_fastapi_sdk import (
    WebPushDispatcher,
    WebPushGoneError,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)


dispatcher = WebPushDispatcher(
    settings.VAPID_PRIVATE_KEY,
    vapid_subject="mailto:ops@example.com",
    ttl_seconds=60,
)


async def notify_order_paid(
    subscription: WebPushSubscriptionSchema,
    order_id: str,
) -> None:
    payload = WebPushPayloadSchema(
        title="Pagamento confirmado",
        body=f"Pedido {order_id} aprovado.",
        icon="/static/icons/order.png",
        data={"orderId": order_id, "url": f"/orders/{order_id}"},
    )
    try:
        await dispatcher.send(subscription, payload)
    except WebPushGoneError:
        # Prune the subscription from your store.
        await subscriptions_repo.delete_by_endpoint(subscription.endpoint)


async def broadcast(subs: list[WebPushSubscriptionSchema], payload: WebPushPayloadSchema) -> None:
    gone = await dispatcher.send_many(subs, payload)
    if gone:
        await subscriptions_repo.delete_by_endpoints(gone)
```

`WebPushSubscriptionSchema` round-trips the exact JSON `PushSubscription.toJSON()` emits in the browser (it aliases `expiration_time` ↔ `expirationTime`), so you can store inbound subscriptions verbatim and replay them on dispatch.

### Message queues — FastStream recipe

`AsyncBrokerManager` wraps any FastStream broker (RabbitMQ, Kafka, NATS, Redis Streams) with a uniform connect/disconnect/health-check surface. The broker instance is injected so the SDK doesn't pin a single transport.

Install with `[queue]` (pulls `faststream[rabbit]`). Pick the matching FastStream extra for other transports.

```python
# app/queue/__init__.py
from faststream.rabbit import RabbitBroker
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import AsyncBrokerManager

from app.core.settings import settings


broker = RabbitBroker(settings.RABBITMQ_URL)
queue = AsyncBrokerManager(broker)


class OrderMessage(BaseModel):
    order_id: str
    user_id: str


@broker.subscriber("orders.paid")
async def handle_order_paid(msg: OrderMessage) -> None:
    await mark_order_paid(msg.order_id, msg.user_id)


# app/api/factory.py lifespan
await queue.connect()
...
await queue.disconnect()


# Publish from anywhere in the application
await queue.publish(OrderMessage(order_id="abc", user_id="x"), queue="orders.paid")
```

The manager exposes:

- `connect()` / `disconnect()` — idempotent; safe to call from FastAPI lifespan.
- `publish(message, *args, **kwargs)` — passthrough to `broker.publish` with a `RuntimeError` guard when the broker isn't started.
- `lifespan()` — async context manager handling start/stop, handy for short scripts.
- `broker_dependency` — FastAPI `Depends` that yields the live broker.
- `health_check()` / `is_connected` — true while the broker is started.

Wire it on the health router with `make_health_router(checks={"queue": queue.health_check})`.

### Background tasks — TaskIQ recipe

`AsyncTaskBrokerManager` wraps any TaskIQ broker (AioPika for RabbitMQ, Redis, in-memory for tests). Install with `[tasks]` (pulls `taskiq` + `taskiq-aio-pika`).

```python
# app/tasks/__init__.py
from taskiq_aio_pika import AioPikaBroker

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager

from app.core.settings import settings


tasks = AsyncTaskBrokerManager(AioPikaBroker(settings.RABBITMQ_URL))


@tasks.task
async def send_welcome_email(to: str, name: str) -> None:
    await email_utils.send(
        to=to,
        subject="Bem-vindo!",
        body=f"Olá, {name} — sua conta foi criada.",
    )


# app/api/factory.py lifespan
await tasks.connect()
...
await tasks.disconnect()


# Enqueue from a request handler
await send_welcome_email.kiq(to=user.email, name=user.name)
```

`register_task(callable, task_name=..., **kwargs)` registers a function without decorator syntax — useful when wiring third-party callables that you can't decorate at definition time. For tests, swap the broker for `taskiq.InMemoryBroker()` so kicked tasks execute synchronously.

The same lifespan guard rails as the queue manager apply: `connect()`/`disconnect()`/`lifespan()`/`broker_dependency`/`health_check()`/`is_connected`.

### System metrics recipe

`MetricsUtils` collects CPU, memory, disk and NVIDIA GPU usage via `psutil` + `pynvml`. Every method has a sync and an async variant (the async wrapper runs the same code via `asyncio.to_thread`). GPU sampling gracefully degrades to `[]` when `pynvml` or NVIDIA drivers are missing.

Install with `[metrics]`.

```python
from tempest_fastapi_sdk import MetricsUtils

# Synchronous, blocking call
snapshot = MetricsUtils.snapshot(disk_paths=["/", "/data"], cpu_interval=0.1)
print(snapshot.cpu.percent, snapshot.memory.percent)
for disk in snapshot.disks:
    print(disk.path, disk.percent)
for gpu in snapshot.gpus:
    print(gpu.name, gpu.utilization_percent, gpu.memory_used_bytes)

# Async — runs every collector concurrently via asyncio.gather
snapshot = await MetricsUtils.snapshot_async(disk_paths=["/"])


@router.get("/metrics")
async def metrics() -> dict[str, Any]:
    snap = await MetricsUtils.snapshot_async()
    return snap.to_dict()
```

Individual collectors are also available: `MetricsUtils.cpu(interval=...)`, `MetricsUtils.memory()`, `MetricsUtils.disk(path)`, `MetricsUtils.disks(paths)`, `MetricsUtils.gpus()` — and their `*_async` variants. Each returns a typed dataclass (`CPUMetrics`, `MemoryMetrics`, `DiskMetrics`, `GPUMetrics`, `SystemMetrics`) with a `to_dict()` helper for JSON serialization.

---

## Reference

### BaseRepository methods

| Method | Purpose | Raises on miss |
| --- | --- | --- |
| `get(filters, for_update=False)` | Single record matching filters | ✅ `not_found_exception` |
| `get_or_none(filters, for_update=False)` | Single record or `None` | — |
| `get_by_id(id, for_update=False)` | Shortcut for `get({"id": id})` | ✅ `not_found_exception` |
| `exists(filters)` | `bool` without loading the row | — |
| `first(filters, order_by, ascending)` | First match ordered | — |
| `list(filters, order_by, ascending)` | All rows; `[]` when empty | — |
| `paginate(filters, order_by, page, page_size, ascending, query=)` | `dict` with `items, total, page, size, pages` | — |
| `count(filters)` | Row count | — |
| `add(model)` | Insert single | `ConflictException` on integrity |
| `add_all(models)` | Insert batch | `ConflictException` on integrity |
| `update(model)` | Commit mutated instance | `ConflictException` on integrity |
| `update_many(models)` | Commit batch | `ConflictException` on integrity |
| `bulk_update(filters, values)` | `UPDATE ... WHERE` mass mutation; rejects empty filters | `ConflictException` on integrity |
| `delete(id)` | Hard delete by PK | ✅ `not_found_exception` |
| `delete_many(filters)` | Hard delete by filter | — |
| `delete_batch(ids)` | Hard delete several PKs | — |
| `soft_delete(id)` | Flip `is_active=False`; returns row | ✅ `not_found_exception` |
| `restore(id)` | Flip `is_active=True`; returns row | ✅ `not_found_exception` |
| `map_to_schema(instance)` | Override in subclass | `NotImplementedError` |
| `map_to_response(instance)` | Override in subclass | `NotImplementedError` |
| `map_to_model(data)` | Default: `self.model(**data)` | — |

### Filter dict conventions

The dict passed to `get` / `list` / `paginate` / `count` / `exists` / `delete_many` / `bulk_update` understands these patterns out of the box:

| Filter shape | Generated SQL |
| --- | --- |
| `{"col": value}` | `col = value` |
| `{"col": [a, b]}` | `col IN (a, b)` |
| `{"col": True}` / `{"col": False}` | `col IS TRUE` / `col IS FALSE` |
| `{"name": "ana"}` (string field literally named `name`) | `name ILIKE '%ana%'` |
| `{"col": date(2024, 1, 1)}` (date value) | `date(col) = '2024-01-01'` |
| `{"start_in": date(...)}` | `date(date_col_or_created_at) >= ...` |
| `{"end_in": date(...)}` | `date(date_col_or_created_at) <= ...` |
| `{"col": None}` | filter is skipped (omit-when-None semantics) |

Pass the dict from `BasePaginationFilterSchema.get_conditions()` for query-string-driven filters.

### Error envelope

Every `AppException` (and any subclass) is serialized by `register_exception_handlers` into:

```json
{
    "detail": "Human-readable message",
    "code": "MACHINE_READABLE_CODE",
    "details": {"any": "structured context"}
}
```

| Exception | Default `status_code` | Default `code` |
| --- | --- | --- |
| `AppException` | 500 | `INTERNAL_SERVER_ERROR` |
| `NotFoundException` | 404 | `NOT_FOUND` |
| `ConflictException` | 409 | `CONFLICT` |
| `ValidationException` | 422 | `VALIDATION_ERROR` |
| `UnauthorizedException` | 401 | `UNAUTHORIZED` |
| `InvalidTokenException` | 401 | `INVALID_TOKEN` |
| `ExpiredTokenException` | 401 | `TOKEN_EXPIRED` |
| `ForbiddenException` | 403 | `FORBIDDEN` |
| `FileTooLargeException` | 413 | `FILE_TOO_LARGE` |
| `InvalidFileTypeException` | 415 | `INVALID_FILE_TYPE` |

Subclasses set `message`/`code`/`status_code` as class attributes; instances can override `message` and attach a `details` dict at construction time.

---

## Conventions

- **Layered architecture**: routers → controllers → services → repositories. Never skip layers.
- **Async-first**: every I/O method is `async`. Use SQLAlchemy 2.0 patterns (`select`, not `session.query()`).
- **Collections return `[]`**: never raise on empty results. Only single-resource lookups raise `NotFoundException`.
- **Soft delete by default**: `is_active=False` instead of physical delete when applicable.
- **UTC everywhere**: timestamps normalized via `to_utc`; `BaseResponseSchema` enforces this on field validators.
- **Double quotes**: enforced by `ruff format`.
- **Full typing**: every parameter, return value and class attribute is typed. `Any` is explicit, never implicit.
- **Docstrings in English**: Google-style covering description / args / returns / raises.
- **Frontend branches on `code`**, not on the (translatable) message.

---

## Development

```bash
make install     # uv sync --all-extras
make test        # pytest with coverage
make lint        # ruff check .
make fmt         # ruff format .
make type        # mypy --strict
make check       # lint + fmt-check + type + test (every gate)
make ci          # check + build + smoke install in a clean venv (mirrors GitHub Actions)
make build       # uv build → dist/
make clean       # remove caches and build artifacts
```

Run `make` (or `make help`) to list every target. The Makefile is just thin wrappers around `uv` — direct invocations still work too:

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy tempest_fastapi_sdk
uv build
```

The CI gate (`.github/workflows/ci.yml`) runs the equivalent of `make ci` on every push and pull request against `main`. The wheel-build smoke step installs the freshly built artifact into a clean Python 3.13 virtualenv and imports the top-level surface to guard against the empty-wheel / missing-package-data class of bugs.

---

## Release

Releases are published to [PyPI](https://pypi.org/project/tempest-fastapi-sdk/) automatically when a `v*.*.*` tag is pushed.

The pipeline (`.github/workflows/release-pypi.yml`) does three things:

1. **Version sanity check.** The tag (`v0.1.0`), `pyproject.toml`'s `version` field and `tempest_fastapi_sdk.__version__` must all match. Mismatched releases abort before any artifact is built.
2. **Build + verify.** Run the full validation suite (tests + lint + mypy), build sdist/wheel with `uv build`, check metadata with `twine check`, and verify the wheel actually contains the package files + the bundled `env.py.template` Alembic template.
3. **Publish via Trusted Publishing.** Uses OIDC against PyPI — no long-lived API tokens stored in repo secrets.

### Cutting a release

```bash
make release VERSION=0.2.0
```

This single target:

1. Refuses to run if the working tree is dirty.
2. Bumps `pyproject.toml` and `tempest_fastapi_sdk/__init__.py` to the requested version.
3. Runs `make check` (lint + format + mypy + tests) so a broken commit never gets tagged.
4. Commits the bump as `chore: release v0.2.0` and creates the `v0.2.0` tag locally.
5. Prints the two `git push` commands you still need to run — pushing is left manual on purpose so you can review the commit one last time.

```bash
# Review then push
git show v0.2.0
git push origin main
git push origin v0.2.0
```

GitHub Actions picks up the tag, runs the release pipeline and publishes the artifacts to PyPI.

Manual flow (no Makefile) — same result:

```bash
$EDITOR pyproject.toml                              # version = "0.2.0"
$EDITOR tempest_fastapi_sdk/__init__.py             # __version__ = "0.2.0"
git commit -am "chore: release v0.2.0"
git tag v0.2.0
git push origin main v0.2.0
```

### One-time PyPI Trusted Publishing setup

PyPI needs to know which workflow is allowed to publish on the project's behalf. Done once per project:

1. Create the project on [PyPI](https://pypi.org) (name: `tempest-fastapi-sdk`). For brand-new projects, register a placeholder release manually first or use [`pending` publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) so the first OIDC-driven upload can create it.
2. On the project's PyPI settings page, add a **Trusted Publisher** pointing to:
   - **Owner**: `mauriciobenjamin700`
   - **Repository**: `tempest-fastapi-sdk`
   - **Workflow filename**: `release-pypi.yml`
   - **Environment**: `pypi` (must match `release-pypi.yml`'s `environment.name`)
3. In the GitHub repository, create an environment named `pypi` (Settings → Environments → New environment). Optionally restrict deployments to tags matching `v*.*.*` for an extra safety net.

After this, every `v*.*.*` tag triggers a publish. No PYPI tokens are stored anywhere.

---

## License

MIT
