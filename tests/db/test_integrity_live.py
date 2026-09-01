"""``parse_integrity_error`` against real servers, not captured strings.

``test_integrity.py`` feeds the parser the exact messages a probe run
captured, so a regex regression fails on every checkout. It cannot
answer the question those fixtures depend on: whether the servers still
say that. A driver or a server release rewording a sentence would leave
that file green and every consumer's error handler falling back to
``UNKNOWN``.

This module makes the same five assertions against a live Postgres in a
container and a live SQLite through ``aiosqlite``, so the day a wording
changes it fails here — in this repo, on `make test-docker` — instead of
in a service's 409 handler.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tempest_fastapi_sdk import IntegrityViolation, parse_integrity_error

DDL_POSTGRES: tuple[str, ...] = (
    "DROP TABLE IF EXISTS orders, users CASCADE",
    """CREATE TABLE users (
        id serial PRIMARY KEY,
        email text NOT NULL UNIQUE,
        nickname text,
        age int CHECK (age >= 18),
        CONSTRAINT users_name_pair_key UNIQUE (nickname, age)
    )""",
    """CREATE TABLE orders (
        id serial PRIMARY KEY,
        user_id int NOT NULL REFERENCES users(id)
    )""",
    "INSERT INTO users (email, nickname, age) VALUES ('a@x.com', 'ann', 30)",
)

DDL_SQLITE: tuple[str, ...] = (
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        nickname TEXT,
        age INTEGER CHECK (age >= 18),
        CONSTRAINT users_name_pair_key UNIQUE (nickname, age)
    )""",
    """CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id)
    )""",
    "INSERT INTO users (email, nickname, age) VALUES ('a@x.com', 'ann', 30)",
)

DUPLICATE = "INSERT INTO users (email, nickname, age) VALUES ('a@x.com', 'b', 40)"
COMPOSITE = "INSERT INTO users (email, nickname, age) VALUES ('c@x.com', 'ann', 30)"
NULL_EMAIL = "INSERT INTO users (email, nickname, age) VALUES (NULL, 'd', 20)"
BAD_AGE = "INSERT INTO users (email, nickname, age) VALUES ('e@x.com', 'e', 5)"
ORPHAN = "INSERT INTO orders (user_id) VALUES (9999)"


async def _violate(engine: AsyncEngine, statement: str) -> IntegrityError:
    """Run ``statement`` and return the integrity error it raises.

    Args:
        engine (AsyncEngine): The engine to run against.
        statement (str): A statement that must violate a constraint.

    Returns:
        IntegrityError: The error the server produced.

    Raises:
        AssertionError: When the statement did not violate anything,
            which would make every assertion below vacuous.
    """
    try:
        async with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                await connection.execute(text("PRAGMA foreign_keys=ON"))
            await connection.execute(text(statement))
    except IntegrityError as error:
        return error
    raise AssertionError(f"statement did not violate a constraint: {statement}")


async def _seed(engine: AsyncEngine, ddl: tuple[str, ...]) -> None:
    """Create the probe schema and its one existing row.

    Args:
        engine (AsyncEngine): The engine to run against.
        ddl (tuple[str, ...]): Statements to run in order.
    """
    async with engine.begin() as connection:
        for statement in ddl:
            await connection.execute(text(statement))


class _SharedAssertions:
    """The five violations, asserted the same way on both dialects."""

    async def assert_unique(self, engine: AsyncEngine) -> None:
        """Assert a single-column unique violation names its column.

        Args:
            engine (AsyncEngine): The engine under test.
        """
        failure = parse_integrity_error(await _violate(engine, DUPLICATE))
        assert failure.kind is IntegrityViolation.UNIQUE
        assert failure.column == "email"

    async def assert_composite(self, engine: AsyncEngine) -> None:
        """Assert a composite unique names every column, in order.

        Args:
            engine (AsyncEngine): The engine under test.
        """
        failure = parse_integrity_error(await _violate(engine, COMPOSITE))
        assert failure.kind is IntegrityViolation.UNIQUE
        assert failure.columns == ("nickname", "age")
        assert failure.column is None

    async def assert_not_null(self, engine: AsyncEngine) -> None:
        """Assert a not-null violation names the offending column.

        Args:
            engine (AsyncEngine): The engine under test.
        """
        failure = parse_integrity_error(await _violate(engine, NULL_EMAIL))
        assert failure.kind is IntegrityViolation.NOT_NULL
        assert failure.column == "email"
        assert failure.table == "users"

    async def assert_check(self, engine: AsyncEngine) -> None:
        """Assert a check violation is classified as one.

        Args:
            engine (AsyncEngine): The engine under test.
        """
        failure = parse_integrity_error(await _violate(engine, BAD_AGE))
        assert failure.kind is IntegrityViolation.CHECK
        assert failure.constraint is not None

    async def assert_foreign_key(self, engine: AsyncEngine) -> None:
        """Assert a foreign-key violation is classified as one.

        Args:
            engine (AsyncEngine): The engine under test.
        """
        failure = parse_integrity_error(await _violate(engine, ORPHAN))
        assert failure.kind is IntegrityViolation.FOREIGN_KEY


class TestSQLiteLive(_SharedAssertions):
    """SQLite runs everywhere, so these need no marker."""

    @pytest_asyncio.fixture
    async def engine(self) -> AsyncIterator[AsyncEngine]:
        """Yield an engine on a fresh shared in-memory database.

        Yields:
            AsyncEngine: The engine, disposed afterwards.
        """
        url = "sqlite+aiosqlite:///file:integrity?mode=memory&cache=shared&uri=true"
        engine = create_async_engine(url)
        await _seed(engine, DDL_SQLITE)
        yield engine
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE orders"))
            await connection.execute(text("DROP TABLE users"))
        await engine.dispose()

    async def test_unique(self, engine: AsyncEngine) -> None:
        await self.assert_unique(engine)

    async def test_unique_composite(self, engine: AsyncEngine) -> None:
        await self.assert_composite(engine)

    async def test_not_null(self, engine: AsyncEngine) -> None:
        await self.assert_not_null(engine)

    async def test_check(self, engine: AsyncEngine) -> None:
        await self.assert_check(engine)

    async def test_foreign_key_carries_only_the_kind(
        self,
        engine: AsyncEngine,
    ) -> None:
        """SQLite says exactly ``FOREIGN KEY constraint failed``.

        Pinned as a measured limit, not a defect: there is no column to
        report, so a caller that needs one has to know it already.
        """
        failure = parse_integrity_error(await _violate(engine, ORPHAN))

        assert failure.kind is IntegrityViolation.FOREIGN_KEY
        assert failure.columns == ()
        assert failure.table is None


@pytest.mark.docker
class TestPostgresLive(_SharedAssertions):
    """The dialect whose wording the fixtures were captured from."""

    IMAGE: str = "postgres:16-alpine"
    CONTAINER: str = "tempest-integrity-probe"
    PORT: int = 55433

    @pytest.fixture
    def postgres_url(self) -> Iterator[str]:
        """Start a Postgres container and yield its URL.

        Yields:
            str: An async SQLAlchemy URL for the container.
        """
        if shutil.which("docker") is None:
            pytest.skip("docker CLI not installed")
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            pytest.skip("docker daemon not reachable")

        subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)
        started = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                self.CONTAINER,
                "-e",
                "POSTGRES_PASSWORD=probe",
                "-e",
                "POSTGRES_DB=probe",
                "-p",
                f"{self.PORT}:5432",
                self.IMAGE,
            ],
            capture_output=True,
            text=True,
        )
        if started.returncode != 0:
            pytest.skip(f"could not start {self.IMAGE}: {started.stderr.strip()}")
        try:
            for _ in range(60):
                ready = subprocess.run(
                    ["docker", "exec", self.CONTAINER, "pg_isready", "-U", "postgres"],
                    capture_output=True,
                )
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                pytest.skip("postgres never became ready")
            yield (f"postgresql+asyncpg://postgres:probe@127.0.0.1:{self.PORT}/probe")
        finally:
            subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)

    @pytest_asyncio.fixture
    async def engine(self, postgres_url: str) -> AsyncIterator[AsyncEngine]:
        """Yield a seeded engine against the container.

        Args:
            postgres_url (str): URL from the container fixture.

        Yields:
            AsyncEngine: The engine, disposed afterwards.
        """
        engine = create_async_engine(postgres_url)
        await _seed(engine, DDL_POSTGRES)
        yield engine
        await engine.dispose()

    async def test_unique(self, engine: AsyncEngine) -> None:
        await self.assert_unique(engine)

    async def test_unique_composite(self, engine: AsyncEngine) -> None:
        await self.assert_composite(engine)

    async def test_not_null(self, engine: AsyncEngine) -> None:
        await self.assert_not_null(engine)

    async def test_check(self, engine: AsyncEngine) -> None:
        await self.assert_check(engine)

    async def test_foreign_key(self, engine: AsyncEngine) -> None:
        await self.assert_foreign_key(engine)

    async def test_unique_names_the_constraint(self, engine: AsyncEngine) -> None:
        """Postgres names it; SQLite does not. Pinned so the split stays real."""
        failure = parse_integrity_error(await _violate(engine, DUPLICATE))

        assert failure.constraint == "users_email_key"
