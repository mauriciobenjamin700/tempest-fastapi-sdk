"""Wallet test support: models, a service factory and per-dialect fixtures.

One engine per test, on SQLite and on a real PostgreSQL. The guards at
the root of ``tests/`` import these fixtures by name, which is why they
live in a module rather than in ``conftest.py``.

The SQLite engine is a **file**, not ``:memory:``: the concurrency tests
need two connections with their own transactions, and a shared
in-memory database behind ``StaticPool`` is one connection — both
"concurrent" sessions would be the same transaction and prove nothing.
It gets :func:`~tempest_fastapi_sdk.enable_sqlite_savepoints`, which is
what :class:`~tempest_fastapi_sdk.AsyncDatabaseManager` applies to every
SQLite engine it builds and what the service's savepoint relies on.

PostgreSQL comes from ``TEST_POSTGRES_URL`` when it is set, otherwise
from a ``postgres:17-alpine`` container started once per session. The
``postgres`` parameter carries the ``docker`` marker, so the default
run (``-m "not docker"``) exercises SQLite only and ``make test-docker``
adds PostgreSQL.

Every test seeds the parent user row before writing a statement line,
so the suite holds when SQLite foreign keys are switched on.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import String, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, enable_sqlite_savepoints
from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.wallet import (
    OverdraftWalletBalanceMixin,
    WalletBalanceMixin,
    WalletRepository,
    WalletService,
    make_wallet_entry_model,
)

POSTGRES_IMAGE: str = "postgres:17-alpine"
POSTGRES_CONTAINER: str = "tempest-wallet-probe"
POSTGRES_PORT: int = 55447


class WalletUser(WalletBalanceMixin, BaseModel):
    """A user whose balance the database keeps at or above zero."""

    __tablename__ = "wallet_users"

    name: Mapped[str] = mapped_column(String(32), default="user")


class OverdraftUser(OverdraftWalletBalanceMixin, BaseModel):
    """A user whose balance a forced debit may take below zero."""

    __tablename__ = "wallet_overdraft_users"

    name: Mapped[str] = mapped_column(String(32), default="user")


class WalletOrder(BaseModel):
    """An application row with a claim column, for ``claim_once``."""

    __tablename__ = "wallet_orders"

    credited_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


WalletEntry = make_wallet_entry_model(
    user_table="wallet_users",
    tablename="wallet_entries",
    class_name="WalletEntry",
)
OverdraftEntry = make_wallet_entry_model(
    user_table="wallet_overdraft_users",
    tablename="wallet_overdraft_entries",
    class_name="OverdraftEntry",
)

TABLES = [
    WalletUser.__table__,
    OverdraftUser.__table__,
    WalletOrder.__table__,
    WalletEntry.__table__,
    OverdraftEntry.__table__,
]


def make_service(session: AsyncSession, *, overdraft: bool = False) -> WalletService:
    """Build a wallet service over one session.

    Args:
        session (AsyncSession): The session both repositories share.
        overdraft (bool): Use the overdraft user and entry tables.

    Returns:
        WalletService: The service.
    """
    user = OverdraftUser if overdraft else WalletUser
    entry = OverdraftEntry if overdraft else WalletEntry
    return WalletService(
        balances=WalletRepository(session, model=user, entry_model=entry),
        entries=BaseRepository(session, model=entry),
    )


async def seed_user(
    maker: async_sessionmaker[AsyncSession],
    wallet_cents: int = 0,
    *,
    overdraft: bool = False,
) -> UUID:
    """Insert one wallet owner and return its id.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.
        wallet_cents (int): Starting balance.
        overdraft (bool): Seed the overdraft table instead.

    Returns:
        UUID: The new user id.
    """
    model = OverdraftUser if overdraft else WalletUser
    user_id = uuid4()
    async with maker() as session:
        session.add(model(id=user_id, wallet_cents=wallet_cents))
        await session.commit()
    return user_id


def _sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """Enforce foreign keys on every SQLite connection.

    Args:
        dbapi_connection (Any): The raw driver connection.
        _record (Any): The pool record (unused).
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture(scope="session")
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


@pytest_asyncio.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.docker)],
)
async def engine(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[AsyncEngine]:
    """Yield an engine with the wallet tables, on each dialect.

    Args:
        request (pytest.FixtureRequest): Carries the dialect parameter.
        tmp_path (Path): Directory for the SQLite file.

    Yields:
        AsyncEngine: The engine; tables are dropped afterwards.
    """
    if request.param == "sqlite":
        built = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wallet.db'}")
        enable_sqlite_savepoints(built)
        event.listen(built.sync_engine, "connect", _sqlite_foreign_keys)
    else:
        built = create_async_engine(request.getfixturevalue("postgres_url"))
    async with built.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
        await connection.run_sync(BaseModel.metadata.create_all, tables=TABLES)
    yield built
    async with built.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
    await built.dispose()


@pytest.fixture
def maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory on the parametrized engine.

    Args:
        engine (AsyncEngine): The engine.

    Returns:
        async_sessionmaker[AsyncSession]: Sessions that keep loaded
        attributes after commit.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield one session on the parametrized engine.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.

    Yields:
        AsyncSession: The session.
    """
    async with maker() as opened:
        yield opened
