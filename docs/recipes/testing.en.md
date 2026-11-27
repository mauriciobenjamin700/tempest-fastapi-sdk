# Testing

Use this page to build the suite of a service on the SDK: pytest +
pytest-asyncio + `httpx.AsyncClient`, with the app's lifespan running, a real
login and the LLM swapped for a fake.

!!! note "`from src... import` from any folder under `tests/`"
    The `pyproject.toml` that `tempest new` writes carries `pythonpath = ["."]` under `[tool.pytest.ini_options]`. Without it, pytest puts the first folder **without** an `__init__.py` above the test file on `sys.path`: `tests/repositories/test_user.py` with no `tests/repositories/__init__.py`, or `pytest tests/repositories`, fails at collection with `ModuleNotFoundError: No module named 'src'`. A project generated before 0.323.0 gets the line by hand:

    ```toml
    [tool.pytest.ini_options]
    asyncio_mode = "auto"
    pythonpath = ["."]
    testpaths = ["tests"]
    ```

## Run the app in a test with its lifespan — `conftest.py`

Use it when the test calls an HTTP route: this `conftest.py` pins the
environment, points the database at a temporary file and opens the lifespan,
which migrates and connects the database before the first request.

```python
# tests/conftest.py
"""Suite fixtures: pinned environment, database in a temporary file, lifespan running."""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

TEST_DATABASE_DIRECTORY: Path = Path(
    tempfile.mkdtemp(prefix=f"tests-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-")
)
"""One directory per process: each pytest-xdist worker migrates its own database."""

TEST_ENVIRONMENT: dict[str, str] = {
    "ENV": "test",
    "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DATABASE_DIRECTORY / 'test.db'}",
    "AUTH_AUTO_ACTIVATE": "true",
}
"""The suite's environment. Pinned before the app import, which reads the settings on load."""

os.environ.update(TEST_ENVIRONMENT)

import pytest_asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.api.app import create_app  # noqa: E402


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    """Build the app and run its lifespan: startup migration, database connected.

    A fresh app per test, so a ``dependency_overrides`` entry does not leak
    into the next test.

    Yields:
        FastAPI: The app with its lifespan open; closed on teardown.
    """
    application: FastAPI = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client that talks to the app over ASGI, no network.

    Args:
        app (FastAPI): The app with its lifespan already open.

    Yields:
        AsyncClient: The client, with a fixed ``base_url``.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Sign a user up, log in and return the ``Authorization`` header.

    Args:
        client (AsyncClient): The app's client.

    Returns:
        dict[str, str]: ``{"Authorization": "Bearer <access_token>"}``.
    """
    credentials: dict[str, str] = {
        "email": f"{uuid4().hex}@example.com",
        "password": "strong-pass-12-chars",
    }
    await client.post("/auth/signup", json=credentials)
    login = await client.post("/auth/login", json=credentials)
    return {"Authorization": f"Bearer {login.json()['access_token']}"}
```

| Piece | Why |
| --- | --- |
| `os.environ.update(...)` before the imports | The `src/core/settings.py` that `tempest new` writes builds the settings when imported, and the `AsyncDatabaseManager` is created with the `DATABASE_URL` of that moment. A variable set later never reaches the app. |
| SQLite in a **file**, not `:memory:` | The lifespan migration opens its own connection. With `:memory:` it creates the tables in a database that vanishes, and the first request fails with `sqlite3.OperationalError: no such table: users` (measured). |
| One directory per `PYTEST_XDIST_WORKER` | Each `tempest test` worker migrates its own file, and the suite never touches the project's `app.db`. |
| `application.router.lifespan_context(application)` | Runs the app's lifespan without a server. `ASGITransport` alone does not (next section). |
| `AUTH_AUTO_ACTIVATE=true` | Without it the account starts inactive and login answers `401` `invalid email or password` (measured). |
| email from `uuid4()` | The database is one per process, not one per test: a fixed email gets `409` on the second signup. |

### `ASGITransport` alone does not run the lifespan

Use this section to understand the error of building only the client.
`AsyncClient(transport=ASGITransport(app=app))` without `lifespan_context`
never enters the lifespan: the migration does not run and the database stays
empty. Measured on 2026-11-27 (httpx 0.28.1, FastAPI 0.165.0, Starlette
1.10.0), against the `tempest new --extras auth` app, with a spy on
`lifespan_context`:

| Shape | Lifespan entered? | `POST /auth/signup` |
| --- | --- | --- |
| `ASGITransport` alone | no | `client.post` **raises** `sqlite3.OperationalError: no such table: users` |
| `lifespan_context` + `ASGITransport` | yes | `201` |

The error comes out of `client.post`; it does not become a `500` response:
`ASGITransport` re-raises the app's exception by default.

!!! info "What about `asgi-lifespan`?"
    The `LifespanManager` from the `asgi-lifespan` package also opens the
    lifespan, but it is not an SDK dependency. `lifespan_context` ships with
    FastAPI, which you already have.

## Test signup, login and `/me`

Use it when the service mounts `make_auth_router`: the test signs up, logs in
and calls the protected route with the token.

The example app is the `tempest new --extras auth` one with auth mounted as in
the [auth recipe](auth-flow.md): a `UserTokenModel` built by
`make_user_token_model(user_table=USER_TABLE_NAME)` (with its migration
generated), `AuthSettings` in `Settings`, the `UserAuthService` below and, in
`create_app()`,
`app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))`.

```python
# src/api/dependencies/auth.py
"""Service auth: the ``UserAuthService`` and the logged-in user dependency."""

from tempest_fastapi_sdk import UserAuthService, make_token_dependency

from src.core import settings
from src.db.models import UserModel, UserTokenModel

from .resources import db

require_token = make_token_dependency(
    settings.TOKEN_SECRET,
    allow_unauthenticated=settings.ENV != "production",
)

auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)
"""Signup, login and tokens; ``make_auth_router`` mounts the routes on top of it."""

get_current_user = auth_service.current_user_dependency()
"""``Depends(get_current_user)`` requires ``Authorization: Bearer <access_token>``."""

__all__: list[str] = ["auth_service", "get_current_user", "require_token"]
```

The test:

```python
# tests/integration/test_auth.py
from uuid import uuid4

from httpx import AsyncClient


async def test_signup_login_and_me(client: AsyncClient) -> None:
    """Scenario: new account, login with its password, ``/auth/me`` with the token.

    Expected: ``201`` on signup, ``200`` on login and the same email on ``/me``.
    """
    email: str = f"{uuid4().hex}@example.com"
    credentials: dict[str, str] = {"email": email, "password": "strong-pass-12-chars"}

    signup = await client.post("/auth/signup", json=credentials)
    assert signup.status_code == 201, signup.text

    login = await client.post("/auth/login", json=credentials)
    assert login.status_code == 200, login.text
    access_token: str = login.json()["access_token"]

    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email


async def test_me_without_token_is_401(client: AsyncClient) -> None:
    """Scenario: ``/auth/me`` with no ``Authorization`` header.

    Expected: ``401``.
    """
    response = await client.get("/auth/me")
    assert response.status_code == 401, response.text
```

A route of yours protected by `Depends(get_current_user)` is tested the same
way: the `auth_headers` fixture in `conftest.py` already returns the header,
and the next section uses it.

## Swap the LLM for a fake in a route

Use it when the route calls `chat_structured` or `generate_structured`:
[`FakeStructuredBackend`](fakes.md#structured-output-fakestructuredbackend)
comes in through `dependency_overrides` and returns the instance the test
chose, with no model and no network.

The route receives the generator through `Depends` and never builds it
inline — that is what lets the test swap it:

```python
# src/api/dependencies/genai.py
"""The service's LLM generator, handed out through ``Depends``."""

from tempest_fastapi_sdk.genai import OllamaGenerator

generator: OllamaGenerator = OllamaGenerator(model="gpt-oss:20b")
"""One generator per process; the test swaps ``get_generator``, not this object."""


def get_generator() -> OllamaGenerator:
    """Return the process-wide generator.

    Returns:
        OllamaGenerator: The shared generator.
    """
    return generator
```

```python
# src/api/routers/extraction.py
"""``POST /api/invoices/extract`` — the model reads the invoice and returns its fields."""

from fastapi import Depends
from pydantic import BaseModel
from tempest_fastapi_sdk import TempestAPIRouter
from tempest_fastapi_sdk.genai import OllamaGenerator

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.genai import get_generator
from src.db.models import UserModel


class InvoiceSchema(BaseModel):
    """The fields the model fills."""

    number: str
    total_cents: int


class DocumentSchema(BaseModel):
    """The invoice text the client sends."""

    text: str


router: TempestAPIRouter = TempestAPIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/extract")
async def extract_invoice(
    payload: DocumentSchema,
    user: UserModel = Depends(get_current_user),
    generator: OllamaGenerator = Depends(get_generator),
) -> InvoiceSchema:
    """Extract the number and the total of an invoice.

    Args:
        payload (DocumentSchema): The invoice text.
        user (UserModel): The caller; the route requires login.
        generator (OllamaGenerator): The generator, swappable via ``dependency_overrides``.

    Returns:
        InvoiceSchema: The fields the model filled.
    """
    return await generator.chat_structured(
        [
            {"role": "system", "content": "Extract the number and the total of the invoice."},
            {"role": "user", "content": payload.text},
        ],
        InvoiceSchema,
        think="low",
    )
```

The test queues the reply, swaps the dependency and checks what the route
sent to the model:

```python
# tests/integration/test_extraction.py
from fastapi import FastAPI
from httpx import AsyncClient
from tempest_fastapi_sdk.testing.fakes import FakeStructuredBackend

from src.api.dependencies.genai import get_generator
from src.api.routers.extraction import InvoiceSchema


async def test_extract_returns_what_the_model_filled(
    app: FastAPI,
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Scenario: the extraction route with the generator swapped for the fake.

    Expected: the response JSON is the queued instance, and the fake
    recorded the document and the ``think`` the route sent.
    """
    fake: FakeStructuredBackend = FakeStructuredBackend()
    fake.queue(InvoiceSchema(number="42", total_cents=1990))
    app.dependency_overrides[get_generator] = lambda: fake

    response = await client.post(
        "/api/invoices/extract",
        json={"text": "Invoice 42, total $19.90"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"number": "42", "total_cents": 1990}
    assert fake.prompts == ["Invoice 42, total $19.90"]
    assert fake.requests[0].schema is InvoiceSchema
    assert fake.requests[0].think == "low"
```

The `conftest.py` and the two test files above ran together in a
`tempest new --extras auth` project with these pieces, on 2026-11-27:
`3 passed` serially and with `-n 2`, both under `-W error`.

## Test a repository against an in-memory database

Use it when the test talks to the repository directly, with no HTTP and no
lifespan: one in-memory `AsyncDatabaseManager` per test, with the tables
created from the metadata.

```python
# tests/repositories/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager

import src.db.models  # noqa: F401 — side-effect: registers every model on BaseModel.metadata


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
```

Here `:memory:` works: the tables come from `create_tables()` on the same
connection, not from a migration.

### The repository test

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
    The abstract `BaseUserModel` declares **`email`**, **`hashed_password`**, **`is_admin`**, **`last_login_at`** and **`email_verified_at`**, and inherits **`id`**, **`is_active`**, **`created_at`** and **`updated_at`** from `BaseModel`. The **`name`** column used in the examples is **not** part of `BaseUserModel` — it's added by the project's own `UserModel`. The non-default fields (`email` + `hashed_password`) are `nullable=False`, so omitting either one raises `IntegrityError` on flush. Also note: the column is **`hashed_password`** — not `password_hash`.

## Check a route's error envelope

Use it when the route raises an `AppException`: the test uses the `client`
from `conftest.py` and checks the envelope's `code`.

```python
# tests/api/test_users.py
from httpx import AsyncClient


class TestUsersAPI:
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
| `create_test_engine` | `(database_url="sqlite+aiosqlite:///:memory:", *, echo=False, foreign_keys=True) -> AsyncEngine` | Build a throwaway `AsyncEngine` (StaticPool when in-memory); on SQLite it checks foreign keys and applies the savepoint fix the manager applies. |
| `create_test_session_factory` | `(engine) -> async_sessionmaker[AsyncSession]` | Build a sessionmaker bound to the engine (`expire_on_commit=False`). |
| `init_test_metadata` | `async (engine, metadata=None) -> None` | Create every table (defaults to `BaseModel.metadata`). |
| `drop_test_metadata` | `async (engine, metadata=None) -> None` | Drop every table. |
| `make_test_database` | `async (database_url=..., *, metadata=None) -> AsyncIterator[async_sessionmaker[AsyncSession]]` | Async context manager — yields a **session factory** with metadata pre-created, drops + disposes on exit. |
| `make_test_session` | `async (database_url=..., *, metadata=None) -> AsyncIterator[AsyncSession]` | Async context manager — yields **one `AsyncSession`** on top of a fresh `make_test_database`. |

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tempest_fastapi_sdk.testing import make_test_database, make_test_session


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Yield a session factory backed by a fresh in-memory DB per test."""
    async with make_test_database() as factory:
        yield factory


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a single AsyncSession backed by a fresh in-memory DB."""
    async with make_test_session() as s:
        yield s
```

Use the `make_test_session()` context manager for ad-hoc tests that don't need a shared fixture:

```python
from tempest_fastapi_sdk.testing import make_test_session

from src.db.models import UserModel
from src.db.repositories import UserRepository


async def test_repo_directly() -> None:
    async with make_test_session() as session:
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

!!! warning "`test_session` and `test_database` became `make_test_session` and `make_test_database`"
    pytest collects as a test **every imported function** in a test module
    whose name starts with `test`. With the old names, a
    `from tempest_fastapi_sdk.testing import test_session` gave the suite a
    phantom item, `test_x.py::test_session`, that "passed" without testing
    anything and emitted `PytestReturnNotNoneWarning` — a real failure for
    anyone running with `-W error` or `filterwarnings = ["error"]`.

    The old names stay importable as deprecated aliases: every call emits a
    `DeprecationWarning`, and both carry `__test__ = False`, so pytest no
    longer collects them. Switch the import to the new name; the alias goes
    away in a future release.

### The test engine checks foreign keys

`create_test_engine` — and with it `make_test_database` and `make_test_session` —
builds the SQLite engine with the same configuration as
`AsyncDatabaseManager`, minus WAL:

- **foreign keys checked** (`PRAGMA foreign_keys=ON` on every connection):
  an orphan child raises `IntegrityError` and `ON DELETE CASCADE` deletes the
  children, as on PostgreSQL;
- **real savepoints**: a `begin_nested()` that exits cleanly does not commit
  the outer transaction (before, `RELEASE SAVEPOINT` became the final commit
  on SQLite).

The practical consequence: **a test that writes a child seeds the parent
first.** A loose `user_id=uuid4()`, which used to pass on SQLite and would
fail on PostgreSQL, now fails here too:

```python
import pytest
from sqlalchemy import ForeignKey
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tempest_fastapi_sdk.testing import make_test_session


class Base(DeclarativeBase):
    """Declarative base for this example only."""


class Org(Base):
    """Organization: the parent row."""

    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(primary_key=True)


class Member(Base):
    """Member of an organization."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))


async def test_member_needs_an_existing_org() -> None:
    async with make_test_session(metadata=Base.metadata) as session:
        session.add(Member(id=1, org_id=999))
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            await session.commit()


async def test_member_of_a_seeded_org() -> None:
    async with make_test_session(metadata=Base.metadata) as session:
        session.add(Org(id=1))
        await session.flush()
        session.add(Member(id=1, org_id=1))
        await session.commit()
        assert await session.get(Member, 1) is not None
```

The `flush()` after the parent matters when the two models have no
`relationship()`: without it, SQLAlchemy's unit of work sends the INSERTs in
`add` order, and the child's may go first.

To turn it off — a fixture that reproduces a legacy database with an orphan
on purpose:

```python
from sqlalchemy.ext.asyncio import AsyncEngine

from tempest_fastapi_sdk.testing import create_test_engine

engine: AsyncEngine = create_test_engine(foreign_keys=False)
```

!!! note "SQLite only"
    `foreign_keys` only applies to SQLite. With another backend's URL the
    engine gets no listener at all — PostgreSQL always checks the key.

### Every schema field documented — `assert_schema_metadata`

`assert_schema_metadata(module)` fails listing each exported field without
`title`, `description` and `examples` (a field holding another schema needs
only the first two). The `tempest new` project already ships the test:

```python
# tests/test_schemas.py
import src.schemas
from tempest_fastapi_sdk.testing import assert_schema_metadata


def test_exported_schemas_carry_metadata() -> None:
    """Cenário: the schemas ``src.schemas`` exports.

    Esperado: every field carries ``title``, ``description`` and ``examples``.
    """
    assert_schema_metadata(src.schemas)
```

`schema_metadata_findings(module)` returns the findings without raising, and
`tempest lint-schemas` prints the same list on the command line — what
counts as a leaf field, inheritance and the real output are in the
[CLI recipe](cli/lint-schemas.md).

## Model factories — `ModelFactory` + `seq`

Constructing instances with every required field in each test is
repetitive. `ModelFactory` binds the model + default values to the
session; `build()` returns a loose instance, `create()` persists it
(add + flush + refresh) and `create_many(n)` makes several. Per-call
overrides win over the defaults.

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk.testing import ModelFactory, seq

from src.db.models import UserModel


# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

users = ModelFactory(
    session,
    UserModel,
    email=seq("user{n}@example.com"),  # unique per row
    hashed_password="x",
    is_admin=False,
)


async def main() -> None:
    """Run this example."""
    alice = await users.create(is_admin=True)  # one row, one field changed
    team = await users.create_many(5)  # five rows, unique emails
    draft = users.build(email="temp@x.com")  # unsaved instance


asyncio.run(main())
```

**No magic**: the factory never guesses a required field's value — you
declare the defaults. A **callable** default (or override) receives the
row index (an incrementing int) and becomes a per-row generator; `seq(...)`
is the shortcut for the `"{n}"` case. It uses `flush` (not `commit`), so
rows are visible within the test's transaction and rollback stays with
the fixture.

Pass `metadata=` when your project mixes the SDK's `BaseModel.metadata` with a second isolated metadata (rare — keep one `BaseModel` per service whenever possible).

## The suite in parallel — the `tempest test` default

With a throw-away database per test, the tests already share no state.
Running them serially is paying for one core while the rest of the machine
sits idle. So `tempest test` and `tempest check` spread the suite across the
cores with [pytest-xdist](https://pytest-xdist.readthedocs.io/) **by
default**.

First, what the suite needs — the `[tests]` extra brings `pytest`,
`pytest-asyncio` and `pytest-xdist` (the `tempest new` project already
declares `pytest-xdist` in its `dev` group):

```bash
uv add --dev "tempest-fastapi-sdk[tests]"
```

!!! info "The `tempest` gates need the `[cli]` extra"
    `lint`, `fix`, `format`, `fmt-check`, `type`, `test`, `check` and
    `pr-prompt` come from `[cli]`; with only `[cli-ops]` (the server one),
    they exit with code 2 pointing at `[cli]`. Install it with
    `uv add --dev "tempest-fastapi-sdk[cli]"` — details in
    [Installation → CLI](../installation.md#cli).

Then, the suite:

```bash
tempest test                     # -n auto: one worker per core
tempest test -w 4                # four workers
tempest test tests/api           # the target is still forwarded
tempest test --serial            # serially: for --lf / --ff or a debugger
tempest check                    # the full gate, with the test step in parallel
```

Underneath it becomes `pytest -n <workers> -p no:cacheprovider [target]`,
and pytest's exit code comes back untranslated. The pytest cache is off
because several workers writing `.pytest_cache` at once is a race that buys
nothing — and that cache is what `--lf` / `--ff` read, so those two need
`--serial`. `--workers` with `--serial` is a usage error (exit `2`).

!!! info "Parallel by default since `tempest-cli` 0.6.0"
    The `test` and `check` commands belong to
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), which the SDK
    mounts on its own CLI, and the `[cli]` extra declares
    `tempest-cli>=0.7.0`. Up to 0.5, parallel needed `--fast`; the flag is
    still accepted and now means "require pytest-xdist".

!!! warning "One database per worker"
    Every test that builds the app runs its lifespan, which migrates the
    `DATABASE_URL` database. Two workers migrating the **same** SQLite file
    failed with `table alembic_version already exists`. The
    `tests/conftest.py` `tempest new` writes points each worker's
    `DATABASE_URL` at a file of its own, in a temporary directory — and, as a
    bonus, the suite never touches the project's `app.db`.

### Without pytest-xdist

The default asks **the very interpreter that will run pytest** whether it
can import `xdist`. When it cannot, it prints one line and runs serially
(measured output):

```console
$ tempest test
note: pytest-xdist is not installed in the environment pytest runs in (.venv/bin/pytest), so the suite runs serially. Install it with 'uv add --dev pytest-xdist' — or a bundle that carries it, 'uv add --dev "tempest-cli[tools]"' or 'uv add --dev "tempest-fastapi-sdk[tests]"' — to run it in parallel.
```

An explicit `--fast` requires the plugin: without it, the message names the
package and the exit code is `127`, with nothing run:

```console
$ tempest test --fast
error: --fast needs pytest-xdist, which is not installed in the environment pytest runs in (.venv/bin/pytest). Install it with 'uv add --dev pytest-xdist' — or a bundle that carries it, 'uv add --dev "tempest-cli[tools]"' or 'uv add --dev "tempest-fastapi-sdk[tests]"' — and retry, or pass --serial to run the suite serially.
```

### When to use it

- **Use it** on the suite that gates the merge — locally, before the push,
  and in CI. The gain grows with the suite: a service with 2678 tests on a
  12-core machine went from ~5 min to 1 min 28 s
  ([#328](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/328)).
  This SDK's own suite, at 10 218 tests during the v0.302.0 development
  cycle (the number grows every release), on a machine with 6 physical cores
  and 12 threads, went from 2127 s serial to 411 s with `--fast` — about
  5.2x, measured over one run of each.
- **`auto` counts physical cores when `psutil` is installed** (pytest-xdist's
  rule): on the machine above, `auto` started 6 workers. Without `psutil` it
  counts logical CPUs. To use the threads, `-w logical`.
- **Skip it** to run one file or one test: starting the workers costs more
  than the test.

### A test that fails only in parallel

Parallelism exposes tests that depend on order or on an idle machine: two
tests writing the same file, the same port, a module-level global, or a fixed
`sleep` waiting for something that takes longer once every core is busy.
Before treating the failure as a regression, run the test **alone and
serially**:

```bash
tempest test "tests/test_scheduler.py::test_lease_expires"
```

- **It passes alone**: the defect is the test's isolation, not the change
  under review. Fix the test — a file under `tmp_path`, a free port, wait on a
  condition instead of a `sleep`.
- **It fails alone too**: it is a real regression.

!!! warning "The extra carries an inherited cap: `pytest<10`"
    `[tests]` declares no upper bound, but it inherits the ones in the
    `requires-dist` of what it brings: `pytest-asyncio` 1.4.0 requires
    `pytest<10,>=8.4`, and `pytest` itself requires `pluggy<2`. Every service
    here already depends on `pytest-asyncio`, so the cap is not new — it is
    now written down. `pytest-xdist` 3.8.0 (`execnet>=2.1`, `pytest>=7.0.0`)
    brings no cap.

!!! check "Recap"
    - A route test opens the lifespan with `app.router.lifespan_context(app)` and talks to the app through `ASGITransport`; the transport alone does not run the lifespan.
    - The environment is pinned before the app import, and the database is a temporary file per worker, not `:memory:`.
    - `dependency_overrides` swaps the LLM generator for `FakeStructuredBackend` in a route.
    - The `db` fixture builds an in-memory SQLite per test with `create_tables()` / `drop_tables()` — **no arguments**, they use `BaseModel.metadata` internally — for a repository test with no HTTP.
    - The `tempest_fastapi_sdk.testing` helpers (`make_test_database` / `make_test_session`) give ready-made fixtures when you don't need a full `AsyncDatabaseManager`.
    - `tempest test` runs the suite in parallel by default (`--serial` to turn it off), with one database per worker; a test that fails only there is checked by running it alone.

**Next step:** see the [database recipe](database.md) for the `BaseRepository` and migration patterns these tests exercise.

## Recap

- The suite is pytest + pytest-asyncio + `httpx.AsyncClient`, never touching
  the production database: a route test runs the lifespan over SQLite in a
  temporary file, a repository test uses in-memory SQLite.
- Signup → login → `/auth/me` runs through the real `make_auth_router`; the
  `auth_headers` fixture hands the header to your protected routes.
- Shared fixtures live in your project's `conftest.py` — the SDK ships the
  helpers, not the fixtures, so production runtimes never need `pytest` to be
  importable.
- `create_test_engine`, `make_test_database` and `make_test_session` cover the case where
  you do not want a whole `AsyncDatabaseManager` (no `lifespan`, no health
  probes) — and they check foreign keys, so the test seeds the parent before
  the child.
- `ModelFactory` + `seq` remove the required-field boilerplate: declare the
  defaults once, override per test, and the row index reaches the callable so a
  unique column stays unique across `create_many`.
- An endpoint test boots the app with `AsyncClient` and swaps dependencies
  through `dependency_overrides` — the same seam where a [fake »](fakes.md)
  goes in place of the real provider, such as `FakeStructuredBackend` in place
  of `OllamaGenerator`.
- `tempest test` (and `tempest check`) spreads the suite across the cores
  with the pytest-xdist of the `[tests]` extra, by default; a test that fails
  only in parallel is an isolation problem until it runs alone and fails too.
