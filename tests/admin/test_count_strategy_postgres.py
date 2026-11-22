"""``count_strategy="estimate"`` on a real PostgreSQL.

The estimate reads ``pg_class.reltuples``; only PostgreSQL has it, so the
behaviour that matters — no ``COUNT`` statement while the list shows the
whole table, the exact count back under a search, a ``row_scope`` or a table
never analyzed — is measured here on the statements the engine actually
sends. PostgreSQL comes from ``TEST_POSTGRES_URL`` when set, otherwise from
a ``postgres:16-alpine`` container started once per module on its own name
and port. Every test carries the ``docker`` marker (``make test-docker``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ColumnElement, String, event, text, true
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.testing import admin_login

pytestmark = pytest.mark.docker

POSTGRES_IMAGE: str = "postgres:16-alpine"
POSTGRES_CONTAINER: str = "tempest-admin-count-estimate"
POSTGRES_PORT: int = 55804
SECRET = "p" * 48


class PgCountUser(BaseUserModel):
    """Operator of the PostgreSQL count tests."""

    __tablename__ = "admin_pg_count_users"


class PgCountItem(BaseModel):
    """Table analyzed before the list is read."""

    __tablename__ = "admin_pg_count_items"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)


class PgFreshItem(BaseModel):
    """Table never analyzed: ``reltuples`` stays ``-1``."""

    __tablename__ = "admin_pg_fresh_items"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """Yield a PostgreSQL URL: ``TEST_POSTGRES_URL`` or a fresh container.

    Yields:
        str: An async SQLAlchemy URL.
    """
    configured = os.environ.get("TEST_POSTGRES_URL")
    if configured:
        yield configured
        return
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", POSTGRES_CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            POSTGRES_CONTAINER,
            "-e",
            "POSTGRES_PASSWORD=probe",
            "-e",
            "POSTGRES_DB=probe",
            "-p",
            f"{POSTGRES_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {POSTGRES_IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", POSTGRES_CONTAINER, "pg_isready", "-U", "postgres"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.skip("postgres never became ready")
        time.sleep(1)
        yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{POSTGRES_PORT}/probe"
    finally:
        subprocess.run(["docker", "rm", "-f", POSTGRES_CONTAINER], capture_output=True)


@dataclass
class PgWorld:
    """The database and the statements the admin sent to it."""

    db: AsyncDatabaseManager
    statements: list[str] = field(default_factory=list)

    def counts(self) -> list[str]:
        """Return the captured statements that run ``count(``.

        Returns:
            list[str]: The counting statements.
        """
        return [sql for sql in self.statements if "count(" in sql.lower()]

    def estimates(self) -> list[str]:
        """Return the captured statements that read ``pg_class``.

        Returns:
            list[str]: The estimate lookups.
        """
        return [sql for sql in self.statements if "pg_class" in sql]


@pytest.fixture
async def world(postgres_url: str) -> AsyncIterator[PgWorld]:
    """Create the tables, seed 40 analyzed rows and 5 fresh ones.

    Args:
        postgres_url (str): The database URL.

    Yields:
        PgWorld: The database with statement capture attached.
    """
    db = AsyncDatabaseManager(postgres_url)
    await db.connect()
    await db.drop_tables()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = PgCountUser(email="op@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add_all(
            [
                PgCountItem(name=f"Item{n}", company="acme" if n % 2 else "globex")
                for n in range(40)
            ]
        )
        session.add_all([PgFreshItem(name=f"Fresh{n}") for n in range(5)])
        await session.commit()
        await session.execute(text(f"ANALYZE {PgCountItem.__tablename__}"))
        await session.commit()
    pg_world = PgWorld(db=db)

    def capture(
        conn: Any, cursor: Any, statement: str, *args: Any, **kwargs: Any
    ) -> None:
        """Record every statement sent to the database.

        Args:
            conn (Any): The connection.
            cursor (Any): The DBAPI cursor.
            statement (str): The SQL.
            *args (Any): Unused.
            **kwargs (Any): Unused.
        """
        pg_world.statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", capture)
    yield pg_world
    event.remove(db.engine.sync_engine, "before_cursor_execute", capture)
    await db.drop_tables()
    await db.disconnect()


def _acme_only(user: Any, admin: Any) -> ColumnElement[bool]:
    """Scope the items to one company.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.

    Returns:
        ColumnElement[bool]: ``company == "acme"``.
    """
    return PgCountItem.company == "acme"


def _every_row(user: Any, admin: Any) -> ColumnElement[bool]:
    """Scope that says "every row" explicitly.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.

    Returns:
        ColumnElement[bool]: ``true()``.
    """
    return true()


async def _get(world: PgWorld, admins: list[AdminModel[Any]], path: str) -> str:
    """Mount ``admins``, sign in and return the page at ``path``.

    Only the statements of that last request stay captured.

    Args:
        world (PgWorld): The world.
        admins (list[AdminModel[Any]]): What to register.
        path (str): The URL to read.

    Returns:
        str: The page body.
    """
    site = AdminSite(title="PgCount")
    for admin in admins:
        site.register(admin)
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=world.db,
            auth_backend=UserModelAuthBackend(PgCountUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        world.statements.clear()
        page = await client.get(path)
        assert page.status_code == 200, page.text
        return page.text


ITEMS = f"/admin/m/{PgCountItem.__tablename__}/"
FRESH = f"/admin/m/{PgFreshItem.__tablename__}/"


@pytest.mark.asyncio
async def test_estimate_reads_reltuples_and_runs_no_count(world: PgWorld) -> None:
    page = await _get(
        world,
        [AdminModel(PgCountItem, count_strategy="estimate", page_size=10)],
        ITEMS,
    )
    assert world.counts() == []
    assert len(world.estimates()) == 1
    assert "~40 records" in page
    assert "Page 1 of ~4" in page


@pytest.mark.asyncio
async def test_exact_runs_count(world: PgWorld) -> None:
    page = await _get(world, [AdminModel(PgCountItem, page_size=10)], ITEMS)
    assert len(world.counts()) == 1
    assert world.estimates() == []
    assert "40 records" in page
    assert "~40" not in page


@pytest.mark.asyncio
async def test_never_analyzed_table_counts_exactly(world: PgWorld) -> None:
    async with world.db.get_session_context() as session:
        reltuples = (
            await session.execute(
                text("SELECT reltuples FROM pg_class WHERE relname = :name"),
                {"name": PgFreshItem.__tablename__},
            )
        ).scalar()
    assert reltuples == -1
    page = await _get(
        world, [AdminModel(PgFreshItem, count_strategy="estimate")], FRESH
    )
    assert len(world.estimates()) == 1
    assert len(world.counts()) == 1
    assert "5 records" in page
    assert "~5" not in page


@pytest.mark.asyncio
async def test_search_counts_exactly(world: PgWorld) -> None:
    page = await _get(
        world,
        [
            AdminModel(
                PgCountItem,
                search_fields=["name"],
                count_strategy="estimate",
                page_size=10,
            )
        ],
        f"{ITEMS}?q=Item1",
    )
    assert world.estimates() == []
    assert len(world.counts()) == 1
    assert "11 records" in page


@pytest.mark.asyncio
async def test_row_scope_counts_exactly_unless_it_is_true(world: PgWorld) -> None:
    scoped = await _get(
        world,
        [
            AdminModel(
                PgCountItem,
                count_strategy="estimate",
                row_scope=_acme_only,
                page_size=10,
            )
        ],
        ITEMS,
    )
    assert world.estimates() == []
    assert len(world.counts()) == 1
    assert "20 records" in scoped
    unrestricted = await _get(
        world,
        [
            AdminModel(
                PgCountItem,
                count_strategy="estimate",
                row_scope=_every_row,
                page_size=10,
            )
        ],
        ITEMS,
    )
    assert world.counts() == []
    assert "~40 records" in unrestricted


@pytest.mark.asyncio
async def test_dashboard_card_uses_the_estimate(world: PgWorld) -> None:
    page = await _get(
        world,
        [AdminModel(PgCountItem, count_strategy="estimate")],
        "/admin/",
    )
    assert world.counts() == []
    assert "~40" in page
