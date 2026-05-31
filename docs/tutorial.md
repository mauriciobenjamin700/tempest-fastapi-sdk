# Tutorial — building the *Users* feature

This tutorial walks through wiring the **Users** feature using every SDK convention. By the end you'll have:

- A SQLAlchemy `UserModel` with audit + soft-delete columns
- Pydantic schemas for create / update / response / filter
- A repository, service and controller subclassing the SDK bases
- Routers with `Depends`-injected controllers
- Domain exception subclasses serialized by the SDK's exception handler
- A paginated `GET /users` and a JWT-protected `POST /users`

!!! tip "Tom for the impatient"
    If you only want to copy the layout, scaffold it: `tempest new my-service`. The CLI ships the same skeleton this tutorial walks through.

!!! info "Following along"
    Every snippet is **standalone** — paste it into the file path shown in the comment. The full project tree is the [mandatory project layout from Architecture →](architecture.md#mandatory-project-layout).

We'll build a complete `Users` feature from scratch, end to end. Every file below is something you write in your project; SDK primitives are imported.

### 1. Project layout

The canonical layout every Python service shipped against this SDK should adopt — `main.py` is a one-liner, `src/server.py` exposes both `run()` and the importable `app` (or re-exports it from `src/api/app.py`), `api/dependencies/` is **always a package** (auth + factory providers), `controllers/` is mandatory even when it's only a thin pass-through, and `repositories/` lives **under** `db/`.

```text
my-service/
├── main.py                       # one-liner: from src.server import run; run()
└── src/
    ├── __init__.py               # re-exports `run` from src.server
    ├── server.py                 # programmatic uvicorn.run(...) + module-level `app`
    ├── core/
    │   ├── __init__.py
    │   ├── settings.py           # Settings(BaseAppSettings, mixins...)
    │   └── exceptions.py         # domain exceptions (UserNotFoundError, ...)
    ├── db/
    │   ├── __init__.py           # re-exports BaseModel + every model
    │   ├── models/
    │   │   ├── __init__.py
    │   │   └── user.py           # UserModel(BaseModel)
    │   └── repositories/
    │       ├── __init__.py
    │       └── user.py           # UserRepository(BaseRepository[UserModel])
    ├── schemas/
    │   ├── __init__.py
    │   └── user.py               # UserCreate/Update/Response/Filter
    ├── services/
    │   ├── __init__.py
    │   └── user.py               # UserService — business logic
    ├── controllers/
    │   ├── __init__.py
    │   └── user.py               # UserController — orchestration (thin pass-through OK)
    └── api/
        ├── __init__.py
        ├── app.py                # create_app() — middleware, CORS, exception handlers, routers
        ├── routers/
        │   ├── __init__.py
        │   └── users.py
        └── dependencies/         # ALWAYS a package, never a flat module
            ├── __init__.py
            ├── auth.py           # X-Token / current_user / require_role dependencies
            └── controllers.py    # get_<X>_controller / get_<X>_service factories
```

Each `__init__.py` re-exports every public symbol from its directory so consumers always do `from src.schemas import UserCreateSchema` (not `from src.schemas.user import UserCreateSchema`). This keeps refactors painless — move files around without breaking imports.

If your service has no controllers/services/repositories yet, **still ship empty packages with the right names** — uniformity matters more than skipping a directory. Drop `db/`, `utils/`, `queue/` or `tasks/` only when the service genuinely doesn't need persistence/utilities/messaging.

### 2. Settings, server, app factory & entry point

Four files map onto four responsibilities:

| File | Responsibility |
| --- | --- |
| `src/core/settings.py` | `Settings(BaseAppSettings, ...mixins)` — one source of truth for env vars. |
| `src/api/app.py` | `create_app()` factory + middleware + CORS + exception handlers + router includes + module-level `app` instance. |
| `src/server.py` | `run()` invoking `uvicorn.run("src.api.app:app", ...)` programmatically, plus re-exports `app` so external runners (gunicorn, uvicorn CLI) can import it. |
| `main.py` | Process entry point — a single line under `if __name__ == "__main__":` calling `run()`. |

```python
# src/core/settings.py
from tempest_fastapi_sdk import BaseAppSettings, DatabaseSettings, ServerSettings


class Settings(ServerSettings, DatabaseSettings, BaseAppSettings):
    """All environment-driven configuration lives here.

    BaseAppSettings ships `env_file=.env`, `extra=ignore`,
    `case_sensitive=True`, `frozen=True` and `str_strip_whitespace=True`.
    ServerSettings adds SERVER_HOST/PORT/RELOAD, DatabaseSettings adds
    DATABASE_URL/ECHO/POOL_*.
    """

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
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RequestIDMiddleware,
    make_health_router,
    register_exception_handlers,
)

from src.api.routers import users
from src.core.settings import settings


db = AsyncDatabaseManager(settings.DATABASE_URL)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Connect on startup, dispose on shutdown."""
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    app = FastAPI(title="my-service", version="0.1.0", lifespan=lifespan)

    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)

    # Meta endpoints sit at the root prefix.
    app.include_router(make_health_router(db=db, version="0.1.0"))

    # Business endpoints sit under /api/<domain>.
    app.include_router(users.router, prefix="/api")
    return app


app = create_app()
```

```python
# src/server.py
from tempest_fastapi_sdk import run_server

from src.api.app import app  # noqa: F401 — re-exported for external runners
from src.core.settings import settings


def run() -> None:
    """Start the API server programmatically."""
    run_server("src.api.app:app", settings=settings)


__all__: list[str] = ["app", "run"]
```

`run_server` reads `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` from `settings` (falling back to `127.0.0.1` / `8000` / `False`) and forwards any extra kwargs (`workers=`, `log_config=`, `ssl_*=`) verbatim to `uvicorn.run`. See the [Programmatic server entry point recipe](recipes/http.md#programmatic-server-entry-point).

```python
# src/__init__.py
from src.server import run

__all__: list[str] = ["run"]
```

```python
# main.py
from src.server import run

if __name__ == "__main__":
    run()
```

Bind defaults: `127.0.0.1` for internal services (the SDK's `ServerSettings.SERVER_HOST` default), `0.0.0.0` only when the service is consumed by a separate origin (e.g. a frontend dev server). Never start uvicorn via `subprocess.run(["uvicorn", ...])` — always go through `run_server` (or `uvicorn.run("src.api.app:app", ...)` directly) so reload, signal handling and graceful shutdown behave correctly.

### 3. ORM model

```python
# src/db/models/user.py
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
# src/db/models/__init__.py
from src.db.models.user import UserModel

__all__: list[str] = ["UserModel"]
```

```python
# src/db/__init__.py
from src.db.models import UserModel
from tempest_fastapi_sdk import BaseModel

__all__: list[str] = ["BaseModel", "UserModel"]
```

> **Tip:** Always import models in `src/db/__init__.py`. SQLAlchemy needs to "see" every model before `BaseModel.metadata` is complete, so Alembic autogenerate and `create_tables()` work correctly.

### 4. Schemas

The recommended naming pattern: one `*Create`, `*Update`, `*Response` and `*Filter` schema per resource.

```python
# src/schemas/user.py
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
# src/schemas/__init__.py
from src.schemas.user import (
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

The SDK ships generic `NotFoundException`, `ConflictException`, etc. Subclass them per domain so the `isinstance` / `except DomainError` matching stays explicit. Class-level `message` / `code` / `status_code` are defaults the constructor falls back to — you can also override any of them at the raise site without subclassing:

```python
# src/core/exceptions.py
from tempest_fastapi_sdk import ConflictException, NotFoundException


class UserNotFoundError(NotFoundException):
    """Subclass kept only for ``except UserNotFoundError`` matching."""

    message: str = "Usuário não encontrado"
    code: str = "USER_NOT_FOUND"


class UserEmailAlreadyTakenError(ConflictException):
    message: str = "Já existe um usuário com esse e-mail"
    code: str = "USER_EMAIL_TAKEN"
```

For one-off codes you don't need a subclass — pass them to the constructor:

```python
raise NotFoundException(
    "Pedido não encontrado",
    code="ORDER_NOT_FOUND",
    details={"order_id": str(order_id)},
)
```

The SDK's exception handler ([`register_exception_handlers`](#2-settings-server-app-factory-entry-point)) serializes them to:

```json
{
    "detail": "Usuário não encontrado",
    "code": "USER_NOT_FOUND",
    "details": {}
}
```

The frontend branches on `code`, not on the (potentially translated) message.

### 6. Repository

For plain CRUD you don't need a subclass at all — instantiate `BaseRepository` directly and bind the model via the constructor:

```python
# anywhere a session is in scope
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

repository = BaseRepository(session, model=UserModel)
await repository.add(UserModel(email="ana@example.com"))
```

Subclass when you want to bake in domain-specific messages, swap the not-found exception, override the mapper methods or add custom queries. The constructor signature (not class attributes) is the contract:

```python
# src/db/repositories/user.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.core.exceptions import UserNotFoundError
from src.db.models import UserModel
from src.schemas import UserResponseSchema


class UserRepository(BaseRepository[UserModel]):
    """Data-access layer for users."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            model=UserModel,
            not_found_exception=UserNotFoundError,
            not_found_message="Usuário não encontrado",
            create_conflict_message="Já existe um usuário com esse e-mail",
            update_conflict_message="Conflito ao atualizar usuário",
        )

    def map_to_schema(self, instance: UserModel) -> UserResponseSchema:
        return UserResponseSchema.model_validate(instance)

    def map_to_response(self, instance: UserModel) -> UserResponseSchema:
        return self.map_to_schema(instance)
```

The base repo gives you 17 methods for free — see the [reference table](reference.md#tempest_fastapi_sdk.db.repository.BaseRepository) below. Add custom queries on top of the same `UserRepository`:

```python
# src/db/repositories/user.py  (continued)
class UserRepository(BaseRepository[UserModel]):
    # ... __init__ and mappers above ...

    # ──────── custom queries on top of the 17 inherited methods ────────

    async def get_by_email(self, email: str) -> UserModel:
        """Look up a user by email. Raises ``UserNotFoundError`` on miss."""
        return await self.get({"email": email})
```

The highlighted block (under the divider comment) is what you typically add per project — everything above it is the boilerplate the base class already takes care of.

### 7. Service

The service is where business rules live. It calls one or more repositories and never touches HTTP or SQLAlchemy types directly.

Inherit from `BaseService[RepositoryT, ResponseT]`. Doing so gives you `get_by_id`, `get_or_none`, `list`, `paginate`, `count`, `exists` and `delete` for free — every one is already wired to `repository.map_to_response` (sync or async). Override only the methods that need domain logic; add new ones for use cases the base doesn't cover (signup, password reset, etc.):

```python
# src/services/user.py
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseService, PasswordUtils

from src.core.exceptions import UserEmailAlreadyTakenError
from src.db.repositories import UserRepository
from src.schemas import UserCreateSchema, UserResponseSchema, UserUpdateSchema


class UserService(BaseService[UserRepository, UserResponseSchema]):
    """Business logic for the user domain.

    Inherits the canonical read-path methods (``get_by_id`` / ``list`` /
    ``paginate`` / ``count`` / ``exists`` / ``delete``) from
    :class:`BaseService` and adds the write-path methods that need
    domain rules (uniqueness check, password hashing, mass-assignment
    guard).
    """

    def __init__(
        self,
        repository: UserRepository,
        *,
        passwords: PasswordUtils,
    ) -> None:
        """Initialize the service.

        Args:
            repository (UserRepository): User-domain repository.
            passwords (PasswordUtils): Shared bcrypt helper.
        """
        super().__init__(repository)
        self.passwords: PasswordUtils = passwords

    # ──────── overrides: domain rules live here ────────

    async def signup(self, data: UserCreateSchema) -> UserResponseSchema:
        """Create a new user, enforcing email uniqueness + hashing the password."""
        if await self.repository.exists({"email": data.email}):
            raise UserEmailAlreadyTakenError()
        instance = self.repository.map_to_model(
            {
                **data.to_dict(exclude=["password"]),
                "password_hash": self.passwords.hash(data.password),
            },
        )
        instance = await self.repository.add(instance)
        return self.repository.map_to_response(instance)

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
    ) -> UserResponseSchema:
        """Apply a partial update, whitelisting the columns that may change."""
        instance = await self.repository.get_by_id(user_id)
        instance.update_from_dict(
            data.to_dict(),
            allowed_fields={"name", "email"},   # prevents mass-assignment
        )
        instance = await self.repository.update(instance)
        return self.repository.map_to_response(instance)

    async def soft_delete(self, user_id: UUID) -> None:
        """Flip ``is_active=False`` instead of hard-deleting."""
        await self.repository.soft_delete(user_id)
```

The methods you do **not** write — `get_by_id(user_id)`, `list(filters)`, `paginate(filters, page, page_size, order_by, ascending)`, `count(filters)`, `exists(filters)`, `delete(user_id)` — already exist on the base, already await an async `map_to_response`, and already return the typed `UserResponseSchema` declared in the generic parameter.

When the use case needs a custom pipeline (joins, projections, transactional fan-out), override the inherited method. The signature stays the same so the controller doesn't notice:

```python
class UserService(BaseService[UserRepository, UserResponseSchema]):
    # ... __init__ and overrides above ...

    async def list(  # override the inherited pass-through
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
    ) -> list[UserResponseSchema]:
        """List active users only — domain rule baked into the base method."""
        merged: dict[str, Any] = {**(filters or {}), "is_active": True}
        return await super().list(filters=merged, order_by=order_by, ascending=ascending)
```

### 8. Controller

Even when there's no orchestration to do, `controllers/` exists as a **thin pass-through** so the import graph stays uniform across services. The day a use case needs to coordinate two services (or fan out to a queue), the controller is already there.

Inherit from `BaseController[ServiceT, ResponseT]`. The base forwards `get_by_id`, `list`, `paginate`, `count` and `delete` to the service for you — you only declare methods that add cross-service coordination or that don't exist on the service (custom use cases like `signup`):

```python
# src/controllers/user.py
from uuid import UUID

from tempest_fastapi_sdk import BaseController

from src.schemas import UserCreateSchema, UserResponseSchema, UserUpdateSchema
from src.services.user import UserService


class UserController(BaseController[UserService, UserResponseSchema]):
    """Orchestrate user use cases.

    Today every method is a thin pass-through to ``UserService``. As
    soon as a use case needs to coordinate more than one service —
    e.g. signup also sends a welcome email and enqueues a CRM sync —
    the orchestration lives here, not in the router and not in the
    service.
    """

    # ──────── new methods for use cases the base doesn't cover ────────

    async def signup(self, data: UserCreateSchema) -> UserResponseSchema:
        """Create a user and (eventually) trigger downstream side effects."""
        return await self.service.signup(data)

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
    ) -> UserResponseSchema:
        """Domain-specific partial update — distinct from the base ``delete``."""
        return await self.service.update(user_id, data)

    async def soft_delete(self, user_id: UUID) -> None:
        """Soft-delete instead of the inherited hard ``delete``."""
        await self.service.soft_delete(user_id)
```

`get_by_id` / `list` / `paginate` / `count` are not redeclared — `BaseController` already exposes them. When the cross-service coordination day arrives, override the pass-through in place:

```python
class UserController(BaseController[UserService, UserResponseSchema]):
    # ... methods above ...

    async def signup(self, data: UserCreateSchema) -> UserResponseSchema:
        """Create the user, send a welcome email, enqueue the CRM sync."""
        user = await self.service.signup(data)
        await self.emails.send_welcome(user)            # second dependency
        await self.tasks.enqueue("crm.user.created", {"id": str(user.id)})
        return user
```

The router signature never changes — only the controller's body grows.

### 9. Dependency providers

`api/dependencies/` is **always a package**. `auth.py` hosts the shared-secret / current-user dependencies; `controllers.py` (or `services.py` when there is no controller layer yet) hosts the factory providers the routers depend on. Never construct controllers or services inline inside the router file.

```python
# src/api/dependencies/controllers.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import PasswordUtils

from src.api.app import db
from src.controllers.user import UserController
from src.db.repositories import UserRepository
from src.services.user import UserService


# Stateless utilities — instantiate once per process.
_passwords: PasswordUtils = PasswordUtils()


def get_user_controller(
    session: AsyncSession = Depends(db.session_dependency),
) -> UserController:
    """Wire repository → service → controller for a single request."""
    repository = UserRepository(session)
    service = UserService(repository=repository, passwords=_passwords)
    return UserController(service=service)
```

```python
# src/api/dependencies/__init__.py
from src.api.dependencies.controllers import get_user_controller

__all__: list[str] = ["get_user_controller"]
```

### 10. Router

Routers receive controllers via FastAPI `Depends` — no inline construction, no business logic, no DB calls. Business endpoints sit under `/api/<domain>` (the prefix is added at the include site in `src/api/app.py`); meta endpoints (`/health`, `/tool-spec`) stay at the root prefix.

```python
# src/api/routers/users.py
from uuid import UUID

from fastapi import APIRouter, Depends, status

from tempest_fastapi_sdk import BasePaginationSchema

from src.api.dependencies import get_user_controller
from src.controllers.user import UserController
from src.schemas import (
    UserCreateSchema,
    UserFilterSchema,
    UserResponseSchema,
    UserUpdateSchema,
)


router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreateSchema,
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    return await controller.create(data)


@router.get("/{user_id}", response_model=UserResponseSchema)
async def get_user(
    user_id: UUID,
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    return await controller.get(user_id)


@router.patch("/{user_id}", response_model=UserResponseSchema)
async def update_user(
    user_id: UUID,
    data: UserUpdateSchema,
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    return await controller.update(user_id, data)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    controller: UserController = Depends(get_user_controller),
) -> None:
    await controller.soft_delete(user_id)


@router.get("", response_model=BasePaginationSchema[UserResponseSchema])
async def list_users(
    filters: UserFilterSchema = Depends(),
    controller: UserController = Depends(get_user_controller),
) -> BasePaginationSchema[UserResponseSchema]:
    return await controller.list_paginated(filters)
```

### 11. Pagination

The pagination contract is enforced end-to-end by SDK primitives:

- `UserFilterSchema(BasePaginationFilterSchema)` parses `?page=&size=&order_by=&ascending=&is_active=&name=` from the query string and exposes `.get_conditions()` returning only the domain-level filters (without pagination keys).
- `UserRepository.paginate(...)` runs the query with the filter dict + ordering + offset/limit + count, returning `{items, total, page, size, pages}`.
- `BasePaginationSchema[UserResponseSchema]` wraps the result so OpenAPI documents the response shape correctly.

```http
GET /api/users?page=2&size=20&order_by=name&ascending=true&is_active=true&name=ana
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

