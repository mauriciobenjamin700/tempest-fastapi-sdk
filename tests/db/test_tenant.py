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


async def _seed(tenant_db: AsyncDatabaseManager) -> tuple[UUID, UUID]:
    """Insert one note per tenant and return their ids.

    Args:
        tenant_db (AsyncDatabaseManager): The database fixture.

    Returns:
        tuple[UUID, UUID]: Tenant A's note id and tenant B's note id.
    """
    async with tenant_db.get_session_context() as session:
        a = await _repo(session, TENANT_A).add(_NoteModel(title="a-doc"))
        b = await _repo(session, TENANT_B).add(_NoteModel(title="b-doc"))
        return a.id, b.id


class TestInheritedReadsScoped:
    """Inherited read paths that bypassed the scope before the fix."""

    async def test_search_without_filters_is_scoped(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """A term-only search returns only the bound tenant's rows."""
        await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            rows = await _repo(session, TENANT_A).search("doc", fields=["title"])
            assert [r.title for r in rows] == ["a-doc"]

    async def test_full_text_search_without_filters_is_scoped(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """The full-text fallback is scoped as well."""
        await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            rows = await _repo(session, TENANT_A).full_text_search(
                "doc", fields=["title"]
            )
            assert [r.title for r in rows] == ["a-doc"]

    async def test_caller_tenant_filter_cannot_widen_scope(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """A caller filter naming another tenant matches nothing."""
        _, b_id = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            repo = _repo(session, TENANT_A)
            assert await repo.list({"tenant_id": TENANT_B}) == []
            assert await repo.list({"tenant_id__in": [TENANT_A, TENANT_B]}) != []
            assert await repo.count({"tenant_id": TENANT_B}) == 0
            assert await repo.get_or_none({"id": b_id, "tenant_id": TENANT_B}) is None
            assert await repo.exists({"tenant_id": TENANT_B}) is False
            assert await repo.first({"tenant_id": TENANT_B}) is None
            assert (await repo.paginate({"tenant_id": TENANT_B}))["items"] == []
            assert (
                await repo.search(
                    "doc", fields=["title"], filters={"tenant_id": TENANT_B}
                )
                == []
            )

    async def test_delete_many_with_foreign_tenant_filter_deletes_nothing(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """A tenant filter cannot redirect ``delete_many`` to another tenant."""
        await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            assert (
                await _repo(session, TENANT_A).delete_many({"tenant_id": TENANT_B}) == 0
            )
        async with tenant_db.get_session_context() as session:
            assert await _repo(session, TENANT_B).count() == 1


class TestBulkWritesScoped:
    """Bulk statements that bypass the unit of work stay inside the tenant."""

    async def test_bulk_create_values_stamps_tenant(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """Rows are stamped with the bound tenant, overriding a foreign value."""
        await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            await _repo(session, TENANT_A).bulk_create_values(
                [
                    {"id": uuid4(), "title": "planted", "tenant_id": TENANT_B},
                    {"id": uuid4(), "title": "no-tenant-key"},
                ]
            )
        async with tenant_db.get_session_context() as session:
            b_titles = sorted(n.title for n in await _repo(session, TENANT_B).list())
            a_titles = sorted(n.title for n in await _repo(session, TENANT_A).list())
            assert b_titles == ["b-doc"]
            assert a_titles == ["a-doc", "no-tenant-key", "planted"]

    async def test_bulk_upsert_does_not_touch_other_tenant_row(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """A conflict on another tenant's id updates nothing."""
        _, b_id = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            await _repo(session, TENANT_A).bulk_upsert(
                [{"id": b_id, "title": "overwritten", "tenant_id": TENANT_A}],
                conflict_columns=["id"],
            )
        async with tenant_db.get_session_context() as session:
            b_rows = await _repo(session, TENANT_B).list()
            assert [(n.id, n.title) for n in b_rows] == [(b_id, "b-doc")]
            assert await _repo(session, TENANT_A).get_or_none({"id": b_id}) is None

    async def test_bulk_upsert_updates_own_row(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """A conflict on the tenant's own row still updates it."""
        a_id, _ = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            await _repo(session, TENANT_A).bulk_upsert(
                [{"id": a_id, "title": "renamed"}],
                conflict_columns=["id"],
            )
        async with tenant_db.get_session_context() as session:
            note = await _repo(session, TENANT_A).get_by_id(a_id)
            assert note.title == "renamed"

    async def test_bulk_update_cannot_move_rows_to_other_tenant(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """``values`` that re-tenant a row are refused."""
        a_id, _ = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            repo = _repo(session, TENANT_A)
            with pytest.raises(ValueError):
                await repo.bulk_update({"id": a_id}, {"tenant_id": TENANT_B})
            with pytest.raises(ValueError):
                await repo.update_returning(
                    {"id": a_id}, {"tenant_id": TENANT_B}, returning=["id"]
                )
        async with tenant_db.get_session_context() as session:
            assert await _repo(session, TENANT_B).count() == 1

    async def test_bulk_update_foreign_filter_matches_nothing(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """A tenant filter cannot point ``bulk_update`` at another tenant."""
        _, b_id = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            affected = await _repo(session, TENANT_A).bulk_update(
                {"id": b_id, "tenant_id": TENANT_B}, {"title": "x"}
            )
            assert affected == 0
        async with tenant_db.get_session_context() as session:
            assert (await _repo(session, TENANT_B).get_by_id(b_id)).title == "b-doc"


class TestInstanceWritesScoped:
    """Instance-based writes refuse rows outside the bound tenant."""

    async def test_update_refuses_moving_own_row(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """Reassigning the tenant attribute and calling ``update`` is refused."""
        a_id, _ = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            repo = _repo(session, TENANT_A)
            note = await repo.get_by_id(a_id)
            note.tenant_id = TENANT_B
            with pytest.raises(ValueError):
                await repo.update(note)
        async with tenant_db.get_session_context() as session:
            assert (await _repo(session, TENANT_A).get_by_id(a_id)).title == "a-doc"

    async def test_update_refuses_foreign_instance(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """An instance loaded through another tenant's repository is refused."""
        _, b_id = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            note = await _repo(session, TENANT_B).get_by_id(b_id)
            note.title = "hijacked"
            with pytest.raises(NotFoundException):
                await _repo(session, TENANT_A).update(note)
        async with tenant_db.get_session_context() as session:
            assert (await _repo(session, TENANT_B).get_by_id(b_id)).title == "b-doc"

    async def test_update_own_instance_still_works(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """The check lets the tenant's own writes through."""
        a_id, _ = await _seed(tenant_db)
        async with tenant_db.get_session_context() as session:
            repo = _repo(session, TENANT_A)
            note = await repo.get_by_id(a_id)
            note.title = "edited"
            await repo.update(note)
            await repo.soft_delete(a_id)
        async with tenant_db.get_session_context() as session:
            note = await _repo(session, TENANT_A).get_by_id(a_id)
            assert (note.title, note.is_active) == ("edited", False)

    async def test_save_with_outbox_stamps_tenant(
        self, tenant_db: AsyncDatabaseManager
    ) -> None:
        """``save_with_outbox`` stamps the tenant like ``add``."""
        async with tenant_db.get_session_context() as session:
            note = _NoteModel(title="outboxed", tenant_id=TENANT_B)
            saved = await _repo(session, TENANT_A).save_with_outbox(
                note, _NoteModel(title="event-row", tenant_id=TENANT_A)
            )
            assert saved.tenant_id == TENANT_A
