"""Event sourcing test support: events, tables and per-dialect databases.

The SQL store is exercised through
:class:`~tempest_fastapi_sdk.AsyncDatabaseManager` — the object a service
hands it — so SQLite gets the savepoint, WAL and busy-timeout setup the
manager applies in production. The SQLite database is a **file**: the
concurrency tests need two connections with their own transactions.

PostgreSQL comes from ``TEST_POSTGRES_URL`` when set, otherwise from a
``postgres:16-alpine`` container started once per session. The ``postgres``
parameter carries the ``docker`` marker, so the default run exercises
SQLite and ``make test-docker`` adds PostgreSQL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import pytest_asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel
from tempest_fastapi_sdk.db.outbox import BaseOutboxModel
from tempest_fastapi_sdk.eventsourcing import (
    Aggregate,
    BaseEventModel,
    BaseEventSnapshotModel,
    BaseEventStreamModel,
    BaseProjectionCheckpointModel,
    DomainEvent,
    EventRegistry,
    SqlEventStore,
)

POSTGRES_IMAGE: str = "postgres:16-alpine"
POSTGRES_CONTAINER: str = "tempest-eventsourcing-pg"
POSTGRES_PORT: int = 55461


class OrderPlaced(DomainEvent):
    """An order was placed."""

    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class OrderShipped(DomainEvent):
    """An order was shipped."""

    event_type: ClassVar[str] = "order.shipped"

    order_id: str
    carrier: str = "post"


class OrderNoted(DomainEvent):
    """A note was attached; registered only by some tests."""

    event_type: ClassVar[str] = "order.noted"

    text: str


def make_registry() -> EventRegistry:
    """Return a registry with the two order events.

    Returns:
        EventRegistry: ``order.placed`` and ``order.shipped``.
    """
    return EventRegistry(OrderPlaced, OrderShipped)


class EsEventModel(BaseEventModel):
    """Event log table for the tests."""

    __tablename__ = "es_test_events"


class EsStreamModel(BaseEventStreamModel):
    """Stream table for the tests."""

    __tablename__ = "es_test_streams"


class EsSnapshotModel(BaseEventSnapshotModel):
    """Snapshot table for the tests."""

    __tablename__ = "es_test_snapshots"


class EsCheckpointModel(BaseProjectionCheckpointModel):
    """Projection checkpoint table for the tests."""

    __tablename__ = "es_test_checkpoints"


class EsOutboxModel(BaseOutboxModel):
    """Outbox table for the tests."""

    __tablename__ = "es_test_outbox"


TABLES: list[Any] = [
    EsEventModel.__table__,
    EsStreamModel.__table__,
    EsSnapshotModel.__table__,
    EsCheckpointModel.__table__,
    EsOutboxModel.__table__,
]


class Order(Aggregate):
    """A tiny aggregate: placed once, shipped once."""

    def __init__(self, aggregate_id: str) -> None:
        """Create an empty order.

        Args:
            aggregate_id (str): The order id.
        """
        super().__init__(aggregate_id)
        self.total_cents: int = 0
        self.shipped: bool = False
        self.applied: int = 0

    def place(self, total_cents: int) -> None:
        """Place the order.

        Args:
            total_cents (int): The total.
        """
        self.record(OrderPlaced(order_id=self.aggregate_id, total_cents=total_cents))

    def ship(self) -> None:
        """Ship the order."""
        self.record(OrderShipped(order_id=self.aggregate_id))

    def apply(self, event: DomainEvent) -> None:
        """Fold one event.

        Args:
            event (DomainEvent): The event.
        """
        self.applied += 1
        if isinstance(event, OrderPlaced):
            self.total_cents += event.total_cents
        elif isinstance(event, OrderShipped):
            self.shipped = True

    def snapshot(self) -> dict[str, Any] | None:
        """Return the state.

        Returns:
            dict[str, Any] | None: The state.
        """
        return {"total_cents": self.total_cents, "shipped": self.shipped}

    def restore(self, state: dict[str, Any]) -> None:
        """Restore the state.

        Args:
            state (dict[str, Any]): The saved state.
        """
        self.total_cents = int(state["total_cents"])
        self.shipped = bool(state["shipped"])


def make_store(db: AsyncDatabaseManager, *, outbox: bool = False) -> SqlEventStore:
    """Build a SQL store over the test tables.

    Args:
        db (AsyncDatabaseManager): The database.
        outbox (bool): Also write to the outbox table.

    Returns:
        SqlEventStore: The store.
    """
    return SqlEventStore(
        db,
        registry=make_registry(),
        event_model=EsEventModel,
        stream_model=EsStreamModel,
        snapshot_model=EsSnapshotModel,
        outbox_model=EsOutboxModel if outbox else None,
    )


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
async def db(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a connected manager with fresh event tables, on each dialect.

    Args:
        request (pytest.FixtureRequest): Carries the dialect parameter.
        tmp_path (Path): Directory for the SQLite file.

    Yields:
        AsyncDatabaseManager: The manager; tables are dropped afterwards.
    """
    if request.param == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path / 'events.db'}"
    else:
        url = request.getfixturevalue("postgres_url")
    manager = AsyncDatabaseManager(url)
    await manager.connect()
    async with manager.engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
        await connection.run_sync(BaseModel.metadata.create_all, tables=TABLES)
    try:
        yield manager
    finally:
        async with manager.engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
        await manager.disconnect()
