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

## The suite in parallel — `tempest test --fast`

With a throw-away database per test, the tests already share no state.
Running them serially is paying for one core while the rest of the machine
sits idle. `--fast` spreads the suite across the cores with
[pytest-xdist](https://pytest-xdist.readthedocs.io/).

First, what the suite needs — the `[tests]` extra brings `pytest`,
`pytest-asyncio` and `pytest-xdist`:

```bash
uv add --dev "tempest-fastapi-sdk[tests]"
```

Then, the suite:

```bash
tempest test --fast              # -n auto: one worker per core
tempest test --fast -w 4         # four workers
tempest test tests/api --fast    # the target is still forwarded
tempest check --fast             # the full gate, with the test step in parallel
```

Underneath it becomes `pytest -n <workers> -p no:cacheprovider [target]`,
and pytest's exit code comes back untranslated. The pytest cache is off
because several workers writing `.pytest_cache` at once is a race that buys
nothing — and that cache is what `--lf` / `--ff` read, so those two remain a
serial-run affair.

!!! info "`--fast` comes from `tempest-cli` 0.4.0"
    The `test` and `check` commands belong to
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), which the SDK
    mounts on its own CLI. The SDK's `[cli]` extra declares `tempest-cli>=0.4.0`,
    so the flag comes with it. With `tempest-cli` 0.3.0 pinned separately in your project,
    `tempest test --fast` exits with `No such option: --fast` (exit 2).

### Without pytest-xdist

Before running anything, `--fast` asks **the very interpreter that will run
pytest** whether it can import `xdist`. When it cannot, the message names the
package and the extra, and the exit code is `127`, with no traceback —
instead of the `unrecognized arguments: -n` pytest would give on its own:

```console
$ tempest test --fast
error: --fast needs pytest-xdist, which is not installed in the environment pytest runs in (.venv/bin/pytest). Install it with 'uv add --dev pytest-xdist' — or a bundle that carries it, 'uv add --dev "tempest-cli[tools]"' or 'uv add --dev "tempest-fastapi-sdk[tests]"' — and retry, or drop --fast to run the suite serially.
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
    - Use `httpx.AsyncClient` + `ASGITransport`, never the synchronous `TestClient`.
    - The `db` fixture builds an in-memory SQLite per test with `create_tables()` / `drop_tables()` — **no arguments**, they use `BaseModel.metadata` internally.
    - `dependency_overrides` swaps the production database for the test one on the `client`.
    - The `tempest_fastapi_sdk.testing` helpers (`make_test_database` / `make_test_session`) give ready-made fixtures when you don't need a full `AsyncDatabaseManager`.
    - `tempest test --fast` runs the suite in parallel with the `[tests]` extra; a test that fails only there is checked by running it alone.

**Next step:** see the [database recipe](database.md) for the `BaseRepository` and migration patterns these tests exercise.

## Recap

- The suite is pytest + pytest-asyncio + in-memory SQLite +
  `httpx.AsyncClient`: a throw-away database per test, never touching the
  production one.
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
  goes in place of the real provider.
- `tempest test --fast` (and `tempest check --fast`) spreads the suite across
  the cores with the pytest-xdist of the `[tests]` extra; a test that fails
  only in parallel is an isolation problem until it runs alone and fails too.
