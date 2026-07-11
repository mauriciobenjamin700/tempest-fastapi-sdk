# Testing

You'll wire up an async test suite — pytest + pytest-asyncio + in-memory SQLite + `httpx.AsyncClient` — swapping the production database for a throwaway one on every test.

pytest + pytest-asyncio + in-memory SQLite + `httpx.AsyncClient`.

!!! tip "Why `AsyncClient` instead of `TestClient`?"
    `fastapi.testclient.TestClient` is synchronous — it does not support `async with`. To test async endpoints painlessly, use `httpx.AsyncClient(transport=ASGITransport(app=app))`, which mounts the app over ASGI in the same event-loop as your tests. The examples below follow that pattern.

## Shared fixtures

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager

import src.db.models  # noqa: F401 — side-effect: registers every model on BaseModel.metadata
from src.api.app import create_app


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

## Repository test

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

!!! warning "`BaseUserModel` columns"
    The abstract `BaseUserModel` declares **`email`**, **`hashed_password`**, **`is_admin`** and **`last_login_at`**, and inherits **`id`**, **`is_active`**, **`created_at`** and **`updated_at`** from `BaseModel`. The **`name`** column used in the examples is **not** part of `BaseUserModel` — it's added by the project's own `UserModel`. The non-default fields (`email` + `hashed_password`) are `nullable=False`, so omitting either one raises `IntegrityError` on flush. Also note: the column is **`hashed_password`** — not `password_hash`.

## Endpoint test

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
        # is set by your project's UserNotFoundError subclass — use whichever
        # constant your project chose (see Tutorial §5).
        assert "code" in body
```

!!! note "About the `code` field on the error envelope"
    The SDK serializes every `AppException` as `{detail, code, details}`. The exact `code` value depends on the domain subclass **your project** defines — `UserNotFoundError(NotFoundException, code="USER_NOT_FOUND")` is just a tutorial convention. See tutorial §5 to create your own subclasses.

## Helpers from `tempest_fastapi_sdk.testing`

`tempest_fastapi_sdk.testing` provides framework-agnostic helpers that don't require `pytest` to be importable — wrap them in `@pytest.fixture` inside the consuming project's `conftest.py`. Useful when a test doesn't need a full `AsyncDatabaseManager` (no lifespan, no health-check probes).

| Helper | Signature | Purpose |
| --- | --- | --- |
| `create_test_engine` | `(database_url="sqlite+aiosqlite:///:memory:", *, echo=False) -> AsyncEngine` | Build a throwaway `AsyncEngine` (StaticPool when in-memory). |
| `create_test_session_factory` | `(engine) -> async_sessionmaker[AsyncSession]` | Build a sessionmaker bound to the engine (`expire_on_commit=False`). |
| `init_test_metadata` | `async (engine, metadata=None) -> None` | Create every table (defaults to `BaseModel.metadata`). |
| `drop_test_metadata` | `async (engine, metadata=None) -> None` | Drop every table. |
| `test_database` | `async (database_url=..., *, metadata=None) -> AsyncIterator[async_sessionmaker[AsyncSession]]` | Async context manager — yields a **session factory** with metadata pre-created, drops + disposes on exit. |
| `test_session` | `async (database_url=..., *, metadata=None) -> AsyncIterator[AsyncSession]` | Async context manager — yields **one `AsyncSession`** on top of a fresh `test_database`. |

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

Use the `test_session()` context manager for ad-hoc tests that don't need a shared fixture:

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

## Model factories — `ModelFactory` + `seq`

Constructing instances with every required field in each test is
repetitive. `ModelFactory` binds the model + default values to the
session; `build()` returns a loose instance, `create()` persists it
(add + flush + refresh) and `create_many(n)` makes several. Per-call
overrides win over the defaults.

```python
from tempest_fastapi_sdk.testing import ModelFactory, seq

users = ModelFactory(
    session,
    UserModel,
    email=seq("user{n}@example.com"),   # unique per row
    hashed_password="x",
    is_admin=False,
)

alice = await users.create(is_admin=True)     # one row, one field changed
team = await users.create_many(5)             # five rows, unique emails
draft = users.build(email="temp@x.com")        # unsaved instance
```

**No magic**: the factory never guesses a required field's value — you
declare the defaults. A **callable** default (or override) receives the
row index (an incrementing int) and becomes a per-row generator; `seq(...)`
is the shortcut for the `"{n}"` case. It uses `flush` (not `commit`), so
rows are visible within the test's transaction and rollback stays with
the fixture.

Pass `metadata=` when your project mixes the SDK's `BaseModel.metadata` with a second isolated metadata (rare — keep one `BaseModel` per service whenever possible).

!!! check "Recap"
    - Use `httpx.AsyncClient` + `ASGITransport`, never the synchronous `TestClient`.
    - The `db` fixture builds an in-memory SQLite per test with `create_tables()` / `drop_tables()` — **no arguments**, they use `BaseModel.metadata` internally.
    - `dependency_overrides` swaps the production database for the test one on the `client`.
    - The `tempest_fastapi_sdk.testing` helpers (`test_database` / `test_session`) give ready-made fixtures when you don't need a full `AsyncDatabaseManager`.

**Next step:** see the [database recipe](database.md) for the `BaseRepository` and migration patterns these tests exercise.
