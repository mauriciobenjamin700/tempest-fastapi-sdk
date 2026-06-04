# Testes

pytest + pytest-asyncio + SQLite em memória + `httpx.AsyncClient`.

!!! tip "Por que `AsyncClient` em vez de `TestClient`?"
    `fastapi.testclient.TestClient` é síncrono — não suporta `async with`. Para testar endpoints async sem dor, use `httpx.AsyncClient(transport=ASGITransport(app=app))`, que monta o app via ASGI no mesmo event-loop dos seus testes. Os exemplos abaixo seguem esse padrão.

## Fixtures compartilhadas

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel

import src.db.models  # noqa: F401 — side-effect: registers every model on BaseModel.metadata
from src.api.app import create_app


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncDatabaseManager, None]:
    """Fresh in-memory DB per test."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables(BaseModel.metadata)
    try:
        yield manager
    finally:
        await manager.drop_tables(BaseModel.metadata)
        await manager.disconnect()


@pytest_asyncio.fixture
async def session(db: AsyncDatabaseManager) -> AsyncGenerator[AsyncSession, None]:
    """Managed session bound to the in-memory DB."""
    async for s in db.session_dependency():
        yield s


@pytest_asyncio.fixture
async def client(db: AsyncDatabaseManager) -> AsyncGenerator[AsyncClient, None]:
    """ASGI-backed async client with the prod DB swapped for the in-memory one."""
    app = create_app()
    # Override the session dependency to use the test DB.
    from src.api.app import db as production_db

    app.dependency_overrides[production_db.session_dependency] = db.session_dependency

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
```

## Teste de repository

```python
# tests/repositories/test_user.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import UserNotFoundError
from src.db.models import UserModel
from src.db.repositories import UserRepository


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
                email="ana@example.com",
                name="Ana",
                hashed_password="<bcrypt-hash>",
            )
        )
        loaded = await repo.get_by_id(user.id)
        assert loaded.email == "ana@example.com"
```

!!! warning "Campos do `BaseUserModel`"
    O modelo abstrato `BaseUserModel` declara as colunas **`email`**, **`hashed_password`**, **`is_active`**, **`is_admin`**, **`name`** e **`last_login_at`**. Os campos não-default (`email` + `hashed_password`) são `nullable=False`, então omitir qualquer um deles dispara `IntegrityError` no flush. Note também que a coluna se chama **`hashed_password`** — não `password_hash`.

## Teste de endpoint

```python
# tests/api/test_users.py
from httpx import AsyncClient


class TestUsersAPI:
    async def test_signup(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "ana@example.com",
                "password": "strong-pass-12-chars",
                "name": "Ana",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert "user_id" in body
        # The activation link is only present when AUTH_RETURN_TOKEN_IN_RESPONSE=true
        # or no EmailUtils is wired — typical for the test environment.
        assert body["activation_required"] in {True, False}

    async def test_get_user_not_found(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/users/00000000-0000-0000-0000-000000000000",
        )
        assert response.status_code == 404
        body = response.json()
        # SDK envelope is always {detail, code, details}. The `code` value
        # is set by the project's UserNotFoundError subclass — use whichever
        # constant your project chose (see Tutorial §5).
        assert "code" in body
```

!!! note "O `code` na resposta de erro"
    O SDK serializa toda `AppException` no envelope `{detail, code, details}`. O valor exato de `code` depende da subclasse de domínio que **o projeto** define — `UserNotFoundError(NotFoundException, code="USER_NOT_FOUND")` é só uma convenção do tutorial. Veja o passo 5 do tutorial pra criar suas próprias subclasses.

## Helpers de `tempest_fastapi_sdk.testing`

`tempest_fastapi_sdk.testing` traz helpers agnósticos de framework que não exigem que o `pytest` seja importável — embrulhe-os em `@pytest.fixture` dentro do `conftest.py` do projeto consumidor. Úteis quando um teste não precisa de um `AsyncDatabaseManager` completo (sem `lifespan`, sem probes de health-check).

| Helper | Assinatura | Propósito |
| --- | --- | --- |
| `create_test_engine` | `(database_url="sqlite+aiosqlite:///:memory:", *, echo=False) -> AsyncEngine` | Constrói um `AsyncEngine` descartável (StaticPool quando in-memory). |
| `create_test_session_factory` | `(engine) -> async_sessionmaker[AsyncSession]` | Constrói um `sessionmaker` vinculado ao engine (`expire_on_commit=False`). |
| `init_test_metadata` | `async (engine, metadata=None) -> None` | Cria todas as tabelas (default `BaseModel.metadata`). |
| `drop_test_metadata` | `async (engine, metadata=None) -> None` | Apaga todas as tabelas. |
| `test_database` | `async (database_url=..., *, metadata=None) -> AsyncIterator[async_sessionmaker[AsyncSession]]` | Context manager — entrega uma **session factory** num DB recém-criado, apaga e descarta na saída. |
| `test_session` | `async (database_url=..., *, metadata=None) -> AsyncIterator[AsyncSession]` | Context manager — entrega **um `AsyncSession`** em cima de um `test_database` novo. |

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tempest_fastapi_sdk.testing import test_database, test_session


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Yield a session factory backed by a fresh in-memory DB per test."""
    async with test_database() as factory:
        yield factory


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a single AsyncSession backed by a fresh in-memory DB."""
    async with test_session() as s:
        yield s
```

Use o context manager `test_session()` para testes ad-hoc que não precisam de fixture compartilhada:

```python
from tempest_fastapi_sdk.testing import test_session

from src.db.models import UserModel
from src.db.repositories import UserRepository


async def test_repo_directly() -> None:
    async with test_session() as session:
        repo = UserRepository(session)
        await repo.add(
            UserModel(
                email="ana@example.com",
                name="Ana",
                hashed_password="<bcrypt-hash>",
            )
        )
        assert await repo.count() == 1
```

Passe `metadata=` quando o projeto mistura a `BaseModel.metadata` do SDK com uma segunda metadata isolada (raro — mantenha um `BaseModel` por serviço sempre que possível).
