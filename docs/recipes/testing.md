# Testing


pytest + pytest-asyncio + in-memory SQLite + FastAPI TestClient.

#### Shared fixtures

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager

from src.api.app import create_app
from src.db import BaseModel       # importing BaseModel ensures models are registered


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
    from src.api.app import db as production_db

    app.dependency_overrides[production_db.session_dependency] = db.session_dependency

    async with TestClient(app) as client:
        yield client
```

#### Repository test

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
            "/api/users",
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
        response = client.get("/api/users/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        # SDK envelope is always {detail, code, details}
        assert body["code"] == "USER_NOT_FOUND"
```

#### `tempest_fastapi_sdk.testing` helpers

`tempest_fastapi_sdk.testing` ships framework-agnostic helpers that don't require `pytest` to be importable — wrap them in `@pytest.fixture` (or any other harness) inside the consuming project's `conftest.py`. Useful when a test doesn't need a full `AsyncDatabaseManager` (no `lifespan`, no health-check probes).

| Helper | Purpose |
| --- | --- |
| `create_test_engine(url="sqlite+aiosqlite:///:memory:", **engine_kwargs)` | Build a throw-away `AsyncEngine`. |
| `create_test_session_factory(engine)` | Build a `sessionmaker` bound to the engine. |
| `init_test_metadata(engine, metadata=None)` | Create every SQLAlchemy table on the engine (defaults to `BaseModel.metadata`). |
| `drop_test_metadata(engine, metadata=None)` | Drop every table. |
| `test_database(url="sqlite+aiosqlite:///:memory:", metadata=None)` | Async context manager — yields an engine with metadata pre-created, drops everything and disposes on exit. |
| `test_session(url="sqlite+aiosqlite:///:memory:", metadata=None)` | Async context manager — yields an `AsyncSession` on top of a fresh `test_database`. |

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tempest_fastapi_sdk.testing import test_database, test_session


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Yield a fresh in-memory SQLite engine for each test."""
    async with test_database() as e:
        yield e


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a managed AsyncSession bound to the in-memory engine."""
    async with test_session() as s:
        yield s
```

Use the one-shot `test_session()` context manager for ad-hoc tests that don't need cross-fixture sharing:

```python
from tempest_fastapi_sdk.testing import test_session

from src.db.models import UserModel
from src.db.repositories import UserRepository


async def test_repo_directly() -> None:
    async with test_session() as session:
        repo = UserRepository(session)
        await repo.add(UserModel(name="Ana", email="ana@example.com", password_hash="x"))
        assert await repo.count() == 1
```

Pass `metadata=` when the project mixes the SDK `BaseModel.metadata` with a second, isolated metadata (rare — keep one `BaseModel` per service whenever possible).

