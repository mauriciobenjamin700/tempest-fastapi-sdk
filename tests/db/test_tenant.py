"""Tests for tempest_fastapi_sdk.db.tenant.TenantScopedRepository."""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db import (
    AsyncDatabaseManager,
    BaseModel,
    TenantScopedRepository,
)
from tempest_fastapi_sdk.exceptions import NotFoundException


class _NoteModel(BaseModel):
    """Tenant-scoped business row used by the tests."""

    __tablename__ = "tenant_note"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(50), nullable=False)


class _UnscopedModel(BaseModel):
    """A model WITHOUT a tenant column, to test the guard."""

    __tablename__ = "tenant_unscoped"

    title: Mapped[str] = mapped_column(String(50), nullable=False)


TENANT_A: UUID = uuid4()
TENANT_B: UUID = uuid4()


@pytest_asyncio.fixture
async def tenant_db() -> AsyncGenerator[AsyncDatabaseManager]:
    """In-memory database with the tenant tables created."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


def _repo(session: Any, tenant_id: UUID) -> TenantScopedRepository[_NoteModel]:
    return TenantScopedRepository(session, model=_NoteModel, tenant_id=tenant_id)


class TestConstruction:
    def test_missing_tenant_column_rejected(self, tenant_db: Any) -> None:
        with pytest.raises(AttributeError):
            TenantScopedRepository(object(), model=_UnscopedModel, tenant_id=TENANT_A)


class TestWriteStamping:
    async def test_add_stamps_tenant_id(self, tenant_db: AsyncDatabaseManager) -> None:
        async with tenant_db.get_session_context() as session:
            repo = _repo(session, TENANT_A)
            note = await repo.add(_NoteModel(title="a"))
            assert note.tenant_id == TENANT_A

    async def test_add_all_stamps_each(self, tenant_db: AsyncDatabaseManager) -> None:
        async with tenant_db.get_session_context() as session:
            repo = _repo(session, TENANT_A)
            notes = await repo.add_all([_NoteModel(title="a"), _NoteModel(title="b")])
            assert all(n.tenant_id == TENANT_A for n in notes)


class TestReadScoping:
    async def test_list_only_returns_own_tenant(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        async with tenant_db.get_session_context() as session:
            await _repo(session, TENANT_A).add(_NoteModel(title="a"))
            await _repo(session, TENANT_B).add(_NoteModel(title="b"))

        async with tenant_db.get_session_context() as session:
            a_notes = await _repo(session, TENANT_A).list()
            b_notes = await _repo(session, TENANT_B).list()
            assert [n.title for n in a_notes] == ["a"]
            assert [n.title for n in b_notes] == ["b"]

    async def test_count_scoped(self, tenant_db: AsyncDatabaseManager) -> None:
        async with tenant_db.get_session_context() as session:
            await _repo(session, TENANT_A).add_all(
                [_NoteModel(title="a1"), _NoteModel(title="a2")]
            )
            await _repo(session, TENANT_B).add(_NoteModel(title="b1"))

        async with tenant_db.get_session_context() as session:
            assert await _repo(session, TENANT_A).count() == 2
            assert await _repo(session, TENANT_B).count() == 1

    async def test_get_by_id_across_tenant_raises(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        async with tenant_db.get_session_context() as session:
            note = await _repo(session, TENANT_A).add(_NoteModel(title="a"))
            note_id = note.id

        async with tenant_db.get_session_context() as session:
            # Tenant B must not see tenant A's row, even by exact id.
            with pytest.raises(NotFoundException):
                await _repo(session, TENANT_B).get_by_id(note_id)
            # Tenant A still sees it.
            found = await _repo(session, TENANT_A).get_by_id(note_id)
            assert found.title == "a"

    async def test_exists_scoped(self, tenant_db: AsyncDatabaseManager) -> None:
        async with tenant_db.get_session_context() as session:
            await _repo(session, TENANT_A).add(_NoteModel(title="a"))

        async with tenant_db.get_session_context() as session:
            assert await _repo(session, TENANT_A).exists({"title": "a"}) is True
            assert await _repo(session, TENANT_B).exists({"title": "a"}) is False


class TestDeleteScoping:
    async def test_delete_across_tenant_raises_and_keeps_row(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        async with tenant_db.get_session_context() as session:
            note = await _repo(session, TENANT_A).add(_NoteModel(title="a"))
            note_id = note.id

        async with tenant_db.get_session_context() as session:
            with pytest.raises(NotFoundException):
                await _repo(session, TENANT_B).delete(note_id)

        async with tenant_db.get_session_context() as session:
            # The row still exists for tenant A.
            assert await _repo(session, TENANT_A).get_by_id(note_id)

    async def test_delete_many_empty_filters_only_own_tenant(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        async with tenant_db.get_session_context() as session:
            await _repo(session, TENANT_A).add(_NoteModel(title="a"))
            await _repo(session, TENANT_B).add(_NoteModel(title="b"))

        async with tenant_db.get_session_context() as session:
            deleted = await _repo(session, TENANT_A).delete_many({})
            assert deleted == 1

        async with tenant_db.get_session_context() as session:
            # Tenant B's row survived.
            assert await _repo(session, TENANT_B).count() == 1

    async def test_delete_batch_scoped(self, tenant_db: AsyncDatabaseManager) -> None:
        async with tenant_db.get_session_context() as session:
            a = await _repo(session, TENANT_A).add(_NoteModel(title="a"))
            b = await _repo(session, TENANT_B).add(_NoteModel(title="b"))
            ids = [a.id, b.id]

        async with tenant_db.get_session_context() as session:
            deleted = await _repo(session, TENANT_A).delete_batch(ids)
            # Only tenant A's row is deleted even though both ids passed.
            assert deleted == 1

        async with tenant_db.get_session_context() as session:
            assert await _repo(session, TENANT_B).count() == 1
