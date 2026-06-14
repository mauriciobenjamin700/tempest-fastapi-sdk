"""Tests for the per-entity audit trail."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db import (
    AsyncDatabaseManager,
    AuditAction,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
    diff_snapshots,
    snapshot_model,
)


class _GadgetModel(BaseModel):
    """Business row used by the audit tests."""

    __tablename__ = "gadget"

    name: Mapped[str] = mapped_column(String(50), nullable=False)


class _AuditLogModel(BaseAuditLogModel):
    """Concrete audit-log table for the tests."""

    __tablename__ = "audit_log"


class _GadgetRepository(BaseRepository[_GadgetModel]):
    def __init__(self, session: Any) -> None:
        super().__init__(session, model=_GadgetModel, audit_model=_AuditLogModel)


class _UnauditedRepository(BaseRepository[_GadgetModel]):
    def __init__(self, session: Any) -> None:
        super().__init__(session, model=_GadgetModel)


@pytest_asyncio.fixture
async def audit_db() -> AsyncGenerator[AsyncDatabaseManager]:
    """In-memory database with the gadget + audit_log tables created."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


# --------------------------------------------------------------------------- #
# snapshot / diff helpers                                                     #
# --------------------------------------------------------------------------- #


def test_snapshot_model_is_jsonable() -> None:
    """A snapshot turns UUIDs into strings and includes every column."""
    gadget = _GadgetModel(name="a")
    snap = snapshot_model(gadget)
    assert snap["name"] == "a"
    assert "id" in snap and "is_active" in snap


def test_diff_snapshots_reports_changes() -> None:
    """The diff lists only changed fields, union of both key sets."""
    diff = diff_snapshots({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    assert diff == {
        "b": {"before": 2, "after": 3},
        "c": {"before": None, "after": 4},
    }


# --------------------------------------------------------------------------- #
# Entry classmethods                                                          #
# --------------------------------------------------------------------------- #


def test_for_create_snapshots_after() -> None:
    """A create entry stores the new row under ``after``."""
    gadget = _GadgetModel(name="new")
    entry = _AuditLogModel.for_create(gadget, actor="alice")
    assert entry.action == AuditAction.CREATE.value
    assert entry.actor == "alice"
    assert entry.changes["after"]["name"] == "new"
    assert entry.entity == "_GadgetModel"


def test_for_delete_snapshots_before() -> None:
    """A delete entry stores the removed row under ``before``."""
    gadget = _GadgetModel(name="gone")
    entry = _AuditLogModel.for_delete(gadget)
    assert entry.action == AuditAction.DELETE.value
    assert entry.changes["before"]["name"] == "gone"


# --------------------------------------------------------------------------- #
# Repository hook                                                             #
# --------------------------------------------------------------------------- #


async def test_add_audited_writes_both_rows(
    audit_db: AsyncDatabaseManager,
) -> None:
    """add_audited persists the business row and a create audit entry."""
    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await repo.add_audited(_GadgetModel(name="widget-1"), actor="bob")
        assert gadget.id is not None

    async with audit_db.get_session_context() as session:
        rows = (await session.execute(select(_AuditLogModel))).scalars().all()
        assert len(rows) == 1
        entry = rows[0]
        assert entry.action == AuditAction.CREATE.value
        assert entry.actor == "bob"
        assert entry.entity_id == str(gadget.id)
        assert entry.changes["after"]["name"] == "widget-1"


async def test_update_audited_records_diff(
    audit_db: AsyncDatabaseManager,
) -> None:
    """update_audited stores only the changed fields."""
    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await repo.add_audited(_GadgetModel(name="before"))

    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await session.get(_GadgetModel, gadget.id)
        assert gadget is not None
        before = repo.snapshot(gadget)
        gadget.name = "after"
        await repo.update_audited(gadget, before, actor="carol")

    async with audit_db.get_session_context() as session:
        rows = (
            (
                await session.execute(
                    select(_AuditLogModel).where(
                        _AuditLogModel.action == AuditAction.UPDATE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].changes["name"] == {"before": "before", "after": "after"}
        assert rows[0].actor == "carol"


async def test_delete_audited_removes_row_and_logs(
    audit_db: AsyncDatabaseManager,
) -> None:
    """delete_audited deletes the row and logs the before-snapshot."""
    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await repo.add_audited(_GadgetModel(name="doomed"))

    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await session.get(_GadgetModel, gadget.id)
        assert gadget is not None
        await repo.delete_audited(gadget, actor="dave")

    async with audit_db.get_session_context() as session:
        assert await session.get(_GadgetModel, gadget.id) is None
        rows = (
            (
                await session.execute(
                    select(_AuditLogModel).where(
                        _AuditLogModel.action == AuditAction.DELETE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].changes["before"]["name"] == "doomed"
        assert rows[0].actor == "dave"


async def test_audit_without_model_raises(
    audit_db: AsyncDatabaseManager,
) -> None:
    """A repository built without audit_model refuses to record."""
    async with audit_db.get_session_context() as session:
        repo = _UnauditedRepository(session)
        with pytest.raises(RuntimeError, match="without an audit_model"):
            await repo.add_audited(_GadgetModel(name="x"))
