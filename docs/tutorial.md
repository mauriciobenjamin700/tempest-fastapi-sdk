# Tutorial — construindo a feature *Users*

Este tutorial passa pela conexão da feature **Users** usando todas as convenções do SDK. Ao final você terá:

- Um `UserModel` SQLAlchemy com colunas de auditoria + soft-delete
- Schemas Pydantic para create / update / response / filter
- Um repository, service e controller herdando das bases do SDK
- Routers com controllers injetados via `Depends`
- Subclasses de exceção de domínio serializadas pelo exception handler do SDK
- Um `GET /users` paginado e um `POST /users` protegido por JWT

!!! tip "Para os impacientes"
    Se você só quer copiar o layout, gere-o: `tempest new my-service`. A CLI entrega o mesmo esqueleto que este tutorial percorre.

!!! info "Já tem fluxo de auth pronto"
    Este tutorial mostra como **construir** signup/login com `BaseRepository` + `BaseService` + `BaseController` — é a base para qualquer feature. Para o **fluxo de auth completo** (signup + activation por email + login com JWT + reset de senha), o SDK fornece `UserAuthService` + `make_auth_router` desde v0.31.0; pule para a receita **[Auth flow »](recipes/auth-flow.md)** quando quiser usar o atalho em vez de implementar manualmente.

!!! info "Acompanhando"
    Todo snippet é **standalone** — cole-o no caminho de arquivo mostrado no comentário. A árvore completa do projeto é o [layout obrigatório de projeto da Arquitetura →](architecture.md#layout-obrigatorio-do-projeto).

Vamos construir uma feature `Users` completa do zero, de ponta a ponta. Todo arquivo abaixo é algo que você escreve no seu projeto; os primitivos do SDK são importados.

### 1. Layout do projeto

O layout canônico que todo serviço Python distribuído contra este SDK deve adotar — `main.py` é um one-liner, `src/server.py` expõe tanto `run()` quanto o `app` importável (ou o re-exporta de `src/api/app.py`), `api/dependencies/` é **sempre um pacote** (auth + provedores factory), `controllers/` é obrigatório mesmo quando é só um pass-through fino, e `repositories/` vive **sob** `db/`.

```text
my-service/
├── main.py                       # one-liner: from src.server import run; run()
└── src/
    ├── __init__.py               # re-exporta `run` de src.server
    ├── server.py                 # uvicorn.run(...) programático + `app` no nível do módulo
    ├── core/
    │   ├── __init__.py
    │   ├── settings.py           # Settings(BaseAppSettings, mixins...)
    │   └── exceptions.py         # exceções de domínio (UserNotFoundError, ...)
    ├── db/
    │   ├── __init__.py           # re-exporta BaseModel + todo modelo
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
    │   └── user.py               # UserService — lógica de negócio
    ├── controllers/
    │   ├── __init__.py
    │   └── user.py               # UserController — orquestração (pass-through fino OK)
    └── api/
        ├── __init__.py
        ├── app.py                # create_app() — middleware, CORS, exception handlers, routers
        ├── routers/
        │   ├── __init__.py
        │   └── users.py
        └── dependencies/         # SEMPRE um pacote, nunca um módulo plano
            ├── __init__.py
            ├── auth.py           # dependências X-Token / current_user / require_role
            └── controllers.py    # factories get_<X>_controller / get_<X>_service
```

Cada `__init__.py` re-exporta todo símbolo público do seu diretório para que os consumidores sempre façam `from src.schemas import UserCreateSchema` (não `from src.schemas.user import UserCreateSchema`). Isso mantém os refactors indolores — mova arquivos sem quebrar imports.

Se o seu serviço ainda não tem controllers/services/repositories, **ainda assim distribua pacotes vazios com os nomes certos** — a uniformidade importa mais do que pular um diretório. Descarte `db/`, `utils/`, `queue/` ou `tasks/` só quando o serviço genuinamente não precisa de persistência/utilitários/mensageria.

### 2. Settings, server, factory do app & entrypoint

Quatro arquivos mapeiam em quatro responsabilidades:

| Arquivo | Responsabilidade |
| --- | --- |
| `src/core/settings.py` | `Settings(BaseAppSettings, ...mixins)` — uma fonte única de verdade para env vars. |
| `src/api/app.py` | factory `create_app()` + middleware + CORS + exception handlers + includes de router + instância `app` no nível do módulo. |
| `src/server.py` | `run()` invocando `uvicorn.run("src.api.app:app", ...)` programaticamente, mais re-exporta `app` para que runners externos (gunicorn, CLI do uvicorn) possam importá-lo. |
| `main.py` | Entry point do processo — uma única linha sob `if __name__ == "__main__":` chamando `run()`. |

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

`run_server` lê `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` de `settings` (caindo para `127.0.0.1` / `8000` / `False`) e encaminha quaisquer kwargs extras (`workers=`, `log_config=`, `ssl_*=`) literalmente para `uvicorn.run`. Veja a [receita de ponto de entrada programático do servidor](recipes/http.md#ponto-de-entrada-programatico-do-servidor).

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

Defaults de bind: `127.0.0.1` para serviços internos (o default `ServerSettings.SERVER_HOST` do SDK), `0.0.0.0` só quando o serviço é consumido por uma origem separada (ex.: um dev server de frontend). Nunca inicie o uvicorn via `subprocess.run(["uvicorn", ...])` — sempre passe por `run_server` (ou `uvicorn.run("src.api.app:app", ...)` diretamente) para que reload, tratamento de sinais e shutdown gracioso se comportem corretamente.

### 3. Modelo ORM

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

Re-exporte:

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

> **Dica:** Sempre importe os modelos em `src/db/__init__.py`. O SQLAlchemy precisa "ver" todo modelo antes de `BaseModel.metadata` ficar completa, para que o autogenerate do Alembic e o `create_tables()` funcionem corretamente.

### 4. Schemas

O padrão de nomenclatura recomendado: um schema `*Create`, `*Update`, `*Response` e `*Filter` por recurso.

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

    Inherits page/page_size/order_by/ascending/is_active from
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

### 5. Exceções de domínio

O SDK entrega `NotFoundException`, `ConflictException`, etc. genéricos. Subclasse-os por domínio para que o matching `isinstance` / `except DomainError` fique explícito. `message` / `code` / `status_code` a nível de classe são defaults aos quais o construtor recorre — você também pode sobrescrever qualquer um deles no ponto do raise sem subclassear:

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

Para códigos pontuais você não precisa de uma subclasse — passe-os ao construtor:

```python
raise NotFoundException(
    "Pedido não encontrado",
    code="ORDER_NOT_FOUND",
    details={"order_id": str(order_id)},
)
```

O exception handler do SDK ([`register_exception_handlers`](#2-settings-server-factory-do-app-entrypoint)) os serializa para:

```json
{
    "detail": "Usuário não encontrado",
    "code": "USER_NOT_FOUND",
    "details": {}
}
```

O frontend ramifica no `code`, não na mensagem (que pode estar traduzida).

### 6. Repository

Para CRUD simples você não precisa de uma subclasse nenhuma — instancie `BaseRepository` diretamente e vincule o modelo pelo construtor:

```python
# anywhere a session is in scope
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

repository = BaseRepository(session, model=UserModel)
await repository.add(
    UserModel(
        email="ana@example.com",
        name="Ana",
        password_hash="<bcrypt-hash>",
    )
)
```

Subclasse quando quiser embutir mensagens específicas de domínio, trocar a exceção de not-found, sobrescrever os métodos de mapeamento ou adicionar queries custom. A assinatura do construtor (não os atributos de classe) é o contrato:

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

O repo base te dá 20+ métodos de graça — veja a [tabela de referência](reference.md#tempest_fastapi_sdk.db.repository.BaseRepository) abaixo. Adicione queries custom em cima do mesmo `UserRepository`:

```python
# src/db/repositories/user.py  (continued)
class UserRepository(BaseRepository[UserModel]):
    # ... __init__ and mappers above ...

    # ──────── custom queries on top of the inherited bulk + read methods ────────

    async def get_by_email(self, email: str) -> UserModel:
        """Look up a user by email. Raises ``UserNotFoundError`` on miss."""
        return await self.get({"email": email})
```

O bloco destacado (sob o comentário divisor) é o que você tipicamente adiciona por projeto — tudo acima dele é o boilerplate de que a classe base já cuida.

### 7. Service

O service é onde as regras de negócio vivem. Ele chama um ou mais repositories e nunca toca em tipos de HTTP ou SQLAlchemy diretamente.

Herde de `BaseService[RepositoryT, ResponseT]`. Fazer isso te dá `get_by_id`, `get_or_none`, `list`, `paginate`, `count`, `exists` e `delete` de graça — cada um já conectado a `repository.map_to_response` (sync ou async). Sobrescreva só os métodos que precisam de lógica de domínio; adicione novos para casos de uso que a base não cobre (signup, reset de senha, etc.):

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

Os métodos que você **não** escreve — `get_by_id(user_id)`, `get_or_none(filters)`, `list(filters=None, order_by=None, ascending=True)`, `paginate(filters=None, order_by=None, page=1, page_size=20, ascending=True)`, `count(filters)`, `exists(filters)`, `delete(user_id)` — já existem na base, já aguardam um `map_to_response` async, e já retornam o `UserResponseSchema` tipado declarado no parâmetro genérico.

Quando o caso de uso precisa de um pipeline custom (joins, projeções, fan-out transacional), sobrescreva o método herdado. A assinatura continua a mesma, então o controller não percebe:

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

Mesmo quando não há orquestração a fazer, `controllers/` existe como um **pass-through fino** para que o grafo de imports fique uniforme entre os serviços. No dia em que um caso de uso precisar coordenar dois services (ou fazer fan-out para uma fila), o controller já está lá.

Herde de `BaseController[ServiceT, ResponseT]`. A base encaminha `get_by_id`, `list`, `paginate`, `count` e `delete` para o service por você — você só declara métodos que adicionam coordenação entre services ou que não existem no service (casos de uso custom como `signup`):

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

`get_by_id` / `list` / `paginate` / `count` não são redeclarados — `BaseController` já os expõe. Quando o dia da coordenação entre services chegar, sobrescreva o pass-through no lugar:

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

A assinatura do router nunca muda — só o corpo do controller cresce.

### 9. Provedores de dependência

`api/dependencies/` é **sempre um pacote**. `auth.py` hospeda as dependências de segredo compartilhado / usuário atual; `controllers.py` (ou `services.py` quando ainda não há camada de controller) hospeda os provedores factory dos quais os routers dependem. Nunca construa controllers ou services inline dentro do arquivo do router.

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

Routers recebem controllers via `Depends` do FastAPI — sem construção inline, sem lógica de negócio, sem chamadas de DB. Endpoints de negócio ficam sob `/api/<domínio>` (o prefixo é adicionado no ponto do include em `src/api/app.py`); endpoints meta (`/health`, `/tool-spec`) ficam no prefixo raiz.

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
    return await controller.signup(data)


@router.get("/{user_id}", response_model=UserResponseSchema)
async def get_user(
    user_id: UUID,
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    return await controller.get_by_id(user_id)


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
    result = await controller.paginate(
        filters=filters.get_conditions(),
        order_by=filters.order_by,
        page=filters.page,
        page_size=filters.page_size,
        ascending=filters.ascending,
    )
    return BasePaginationSchema[UserResponseSchema](**result)
```

### 11. Paginação

O contrato de paginação é imposto de ponta a ponta pelos primitivos do SDK:

- `UserFilterSchema(BasePaginationFilterSchema)` parseia `?page=&page_size=&order_by=&ascending=&is_active=&name=` da query string e expõe `.get_conditions()` retornando só os filtros de domínio (sem as chaves de paginação).
- `UserRepository.paginate(...)` roda a query com o dict de filtro + ordenação + offset/limit + contagem, retornando o dict `{items, total, page, page_size, pages}` que você embrulha em `BasePaginationSchema[UserResponseSchema]`.
- `BasePaginationSchema[UserResponseSchema]` embrulha o resultado para que o OpenAPI documente o formato da resposta corretamente.

```http
GET /api/users?page=2&page_size=20&order_by=name&ascending=true&is_active=true&name=ana
```

Retorna:

```json
{
    "items": [
        {"id": "...", "name": "Ana ...", "email": "...", ...},
        ...
    ],
    "total": 142,
    "page": 2,
    "page_size": 20,
    "pages": 8
}
```

---
