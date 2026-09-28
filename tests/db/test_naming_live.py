"""The composite naming fix against real servers.

``test_naming.py`` pins the names SQLAlchemy computes. It cannot show
what the issue was about: that PostgreSQL refuses the legacy DDL while
SQLite accepts it, that ``parse_integrity_error`` then reports a name
two constraints shared, and that the migration from a database created
under the legacy convention works. This module runs each of those
against a live PostgreSQL in a container (marked ``docker``, run with
``make test-docker``) and, where the point is SQLite's silence, against
SQLite through ``aiosqlite``.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    Connection,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tempest_fastapi_sdk import (
    LEGACY_NAMING_CONVENTION,
    NAMING_CONVENTION,
    IntegrityViolation,
    legacy_constraint_renames,
    parse_integrity_error,
)


def _collision(convention: dict[str, str]) -> MetaData:
    """Build the issue's table: a single and a composite unique on ``title``.

    Args:
        convention (dict[str, str]): The naming convention to apply.

    Returns:
        MetaData: ``books`` with ``UniqueConstraint("title")`` and
        ``UniqueConstraint("title", "release_year")``.
    """
    metadata = MetaData(naming_convention=convention)
    Table(
        "books",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("title", String),
        Column("release_year", Integer),
        UniqueConstraint("title"),
        UniqueConstraint("title", "release_year"),
    )
    return metadata


def _catalog(convention: dict[str, str]) -> MetaData:
    """Build two composite uniques sharing a first column, plus a single one.

    Args:
        convention (dict[str, str]): The naming convention to apply.

    Returns:
        MetaData: ``catalog`` with uniques on ``isbn``,
        ``(title, release_year)`` and ``(title, author)``.
    """
    metadata = MetaData(naming_convention=convention)
    Table(
        "catalog",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("isbn", String),
        Column("title", String),
        Column("release_year", Integer),
        Column("author", String),
        UniqueConstraint("isbn"),
        UniqueConstraint("title", "release_year"),
        UniqueConstraint("title", "author"),
    )
    return metadata


def _deployed(convention: dict[str, str]) -> MetaData:
    """Build a schema a consumer could have deployed under either convention.

    No two composite items share a first column, so the legacy DDL runs
    on PostgreSQL; every composite kind is present, one of them past the
    63-character limit, and a composite foreign key references a
    composite unique.

    Args:
        convention (dict[str, str]): The naming convention to apply.

    Returns:
        MetaData: ``authors``, ``books`` and ``subscription_billing_events``.
    """
    metadata = MetaData(naming_convention=convention)
    Table(
        "authors",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer),
        Column("email", String),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("email"),
    )
    books = Table(
        "books",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("title", String),
        Column("release_year", Integer),
        Column("author", String),
        Column("tenant_id", Integer),
        Column("author_id", Integer),
        UniqueConstraint("title", "release_year"),
        ForeignKeyConstraint(
            ["tenant_id", "author_id"],
            ["authors.tenant_id", "authors.id"],
        ),
    )
    Index(None, books.c.author, books.c.title)
    Index(None, books.c.release_year)
    Table(
        "subscription_billing_events",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("customer_identifier", String),
        Column("billing_period_start", Integer),
        Column("billing_period_end", Integer),
        UniqueConstraint(
            "customer_identifier",
            "billing_period_start",
            "billing_period_end",
        ),
    )
    return metadata


async def _create(engine: AsyncEngine, metadata: MetaData) -> None:
    """Create every table of ``metadata``.

    Args:
        engine (AsyncEngine): The engine to run against.
        metadata (MetaData): The schema to create.
    """
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)


async def _violate(engine: AsyncEngine, statement: str) -> IntegrityError:
    """Run ``statement`` and return the integrity error it raises.

    Args:
        engine (AsyncEngine): The engine to run against.
        statement (str): A statement that must violate a constraint.

    Returns:
        IntegrityError: The error the server produced.

    Raises:
        AssertionError: When the statement violated nothing.
    """
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement))
    except IntegrityError as error:
        return error
    raise AssertionError(f"statement did not violate a constraint: {statement}")


def _diff(connection: Connection) -> list[Any]:
    """Return what ``--autogenerate`` sees between the database and the models.

    Args:
        connection (Connection): A sync connection (from ``run_sync``).

    Returns:
        list[Any]: Alembic's diff entries against the current convention.
    """
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, _deployed(NAMING_CONVENTION)))


SEED_CATALOG: str = (
    "INSERT INTO catalog (isbn, title, release_year, author) "
    "VALUES ('111', 'Dune', 1965, 'Herbert')"
)
SAME_ISBN: str = (
    "INSERT INTO catalog (isbn, title, release_year, author) "
    "VALUES ('111', 'Emma', 1815, 'Austen')"
)
SAME_TITLE_YEAR: str = (
    "INSERT INTO catalog (isbn, title, release_year, author) "
    "VALUES ('222', 'Dune', 1965, 'Anderson')"
)
SAME_TITLE_AUTHOR: str = (
    "INSERT INTO catalog (isbn, title, release_year, author) "
    "VALUES ('333', 'Dune', 2021, 'Herbert')"
)


IMAGE: str = "postgres:16-alpine"
CONTAINER: str = "tempest-naming-live"
PORT: int = 55438


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """Start one Postgres container for the module and yield its URL.

    Readiness is probed over TCP (``pg_isready -h 127.0.0.1``): the
    image's init step runs a temporary server on the Unix socket only,
    so a socket probe answers before the real server is up and the first
    connection is dropped.

    Yields:
        str: An async SQLAlchemy URL for the container.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")

    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_PASSWORD=probe",
            "-e",
            "POSTGRES_DB=probe",
            "-p",
            f"{PORT}:5432",
            IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    CONTAINER,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                ],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.skip("postgres never became ready")
        yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{PORT}/probe"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


class TestSQLiteLive:
    """SQLite accepts the legacy DDL, which is how the defect hid in tests."""

    @pytest_asyncio.fixture
    async def engine(self) -> AsyncIterator[AsyncEngine]:
        """Yield an engine on a fresh in-memory database.

        Yields:
            AsyncEngine: The engine, disposed afterwards.
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        yield engine
        await engine.dispose()

    async def test_legacy_collision_is_accepted(self, engine: AsyncEngine) -> None:
        await _create(engine, _collision(LEGACY_NAMING_CONVENTION))

        async with engine.connect() as connection:
            sql = (
                await connection.execute(
                    text("SELECT sql FROM sqlite_master WHERE name = 'books'"),
                )
            ).scalar_one()

        assert sql.count("CONSTRAINT uq_books_title UNIQUE") == 2

    async def test_each_unique_reports_its_columns(self, engine: AsyncEngine) -> None:
        await _create(engine, _catalog(NAMING_CONVENTION))
        async with engine.begin() as connection:
            await connection.execute(text(SEED_CATALOG))

        isbn = parse_integrity_error(await _violate(engine, SAME_ISBN))
        year = parse_integrity_error(await _violate(engine, SAME_TITLE_YEAR))
        author = parse_integrity_error(await _violate(engine, SAME_TITLE_AUTHOR))

        assert isbn.columns == ("isbn",)
        assert year.columns == ("title", "release_year")
        assert author.columns == ("title", "author")
        assert {isbn.constraint, year.constraint, author.constraint} == {None}


@pytest.mark.docker
class TestPostgresLive:
    """The dialect that refused the legacy DDL."""

    @pytest_asyncio.fixture
    async def engine(self, postgres_url: str) -> AsyncIterator[AsyncEngine]:
        """Yield an engine on an empty ``public`` schema.

        Args:
            postgres_url (str): URL from the container fixture.

        Yields:
            AsyncEngine: The engine, disposed afterwards.
        """
        engine = create_async_engine(postgres_url)
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
        yield engine
        await engine.dispose()

    async def _names(self, engine: AsyncEngine) -> set[str]:
        """Return every constraint and index name in ``public``.

        Args:
            engine (AsyncEngine): The engine to read.

        Returns:
            set[str]: Constraint names from ``pg_constraint`` and index
            names from ``pg_indexes``.
        """
        async with engine.connect() as connection:
            constraints = await connection.execute(
                text(
                    "SELECT conname FROM pg_constraint c JOIN pg_namespace n "
                    "ON n.oid = c.connamespace WHERE n.nspname = 'public'",
                ),
            )
            indexes = await connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"),
            )
            return {row[0] for row in constraints} | {row[0] for row in indexes}

    async def test_legacy_collision_is_refused(self, engine: AsyncEngine) -> None:
        """The error the issue reported, reproduced."""
        with pytest.raises(
            DBAPIError, match='relation "uq_books_title" already exists'
        ):
            await _create(engine, _collision(LEGACY_NAMING_CONVENTION))

    async def test_current_convention_creates_both(self, engine: AsyncEngine) -> None:
        await _create(engine, _collision(NAMING_CONVENTION))

        names = await self._names(engine)

        assert {"uq_books_title", "uq_books_title_release_year"} <= names

    async def test_each_unique_is_reported_by_its_own_name(
        self,
        engine: AsyncEngine,
    ) -> None:
        await _create(engine, _catalog(NAMING_CONVENTION))
        async with engine.begin() as connection:
            await connection.execute(text(SEED_CATALOG))

        isbn = parse_integrity_error(await _violate(engine, SAME_ISBN))
        year = parse_integrity_error(await _violate(engine, SAME_TITLE_YEAR))
        author = parse_integrity_error(await _violate(engine, SAME_TITLE_AUTHOR))

        assert {isbn.kind, year.kind, author.kind} == {IntegrityViolation.UNIQUE}
        assert (isbn.constraint, isbn.columns) == ("uq_catalog_isbn", ("isbn",))
        assert (year.constraint, year.columns) == (
            "uq_catalog_title_release_year",
            ("title", "release_year"),
        )
        assert (author.constraint, author.columns) == (
            "uq_catalog_title_author",
            ("title", "author"),
        )

    async def test_autogenerate_on_a_legacy_database(self, engine: AsyncEngine) -> None:
        """Uniques and indexes drift as drop + create; the foreign key is invisible."""
        await _create(engine, _deployed(LEGACY_NAMING_CONVENTION))

        async with engine.connect() as connection:
            diff = await connection.run_sync(_diff)

        entries = sorted((entry[0], str(entry[1].name)) for entry in diff)
        assert entries == [
            ("add_constraint", "uq_authors_tenant_id_id"),
            ("add_constraint", "uq_books_title_release_year"),
            (
                "add_constraint",
                "uq_subscription_billing_events_customer_identifier_"
                "billing_period_start_billing_period_end",
            ),
            ("add_index", "ix_books_author_books_title"),
            ("remove_constraint", "uq_authors_tenant_id"),
            ("remove_constraint", "uq_books_title"),
            ("remove_constraint", "uq_subscription_billing_events_customer_identifier"),
            ("remove_index", "ix_books_author"),
        ]
        assert "fk_books_tenant_id_authors" in await self._names(engine)

    async def test_autogenerated_drop_fails_under_a_foreign_key(
        self,
        engine: AsyncEngine,
    ) -> None:
        """The drop + create ``--autogenerate`` renders, run as rendered."""
        await _create(engine, _deployed(LEGACY_NAMING_CONVENTION))

        def drop_and_create(connection: Connection) -> None:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("authors") as batch_op:
                batch_op.drop_constraint("uq_authors_tenant_id", type_="unique")
                batch_op.create_unique_constraint(
                    "uq_authors_tenant_id_id",
                    ["tenant_id", "id"],
                )

        with pytest.raises(DBAPIError, match="other objects depend on it"):
            async with engine.begin() as connection:
                await connection.run_sync(drop_and_create)

    async def test_rename_statements_leave_no_drift(self, engine: AsyncEngine) -> None:
        await _create(engine, _deployed(LEGACY_NAMING_CONVENTION))
        renames = legacy_constraint_renames(_deployed(NAMING_CONVENTION))

        async with engine.begin() as connection:
            for rename in renames:
                await connection.execute(text(rename.statement(connection.dialect)))

        async with engine.connect() as connection:
            assert await connection.run_sync(_diff) == []
        names = await self._names(engine)
        assert "fk_books_tenant_id_author_id_authors" in names
        assert "uq_subscription_billing_events_customer_identifier_bill_f06e" in names
        assert "uq_authors_email" in names
        assert "ix_books_release_year" in names
        assert not {rename.old_name for rename in renames} & names

    async def test_inverse_statements_restore_the_legacy_names(
        self,
        engine: AsyncEngine,
    ) -> None:
        await _create(engine, _deployed(NAMING_CONVENTION))
        renames = legacy_constraint_renames(_deployed(NAMING_CONVENTION))

        async with engine.begin() as connection:
            for rename in renames:
                statement = rename.inverse().statement(connection.dialect)
                await connection.execute(text(statement))

        assert {rename.old_name for rename in renames} <= await self._names(engine)
