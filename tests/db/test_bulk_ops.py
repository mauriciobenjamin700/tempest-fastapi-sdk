"""Tests for the new ``BaseRepository`` bulk ops."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository


class _Item(BaseModel):
    """Test entity exercised by the bulk ops."""

    __tablename__ = "_bulk_items"

    sku: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))


class _ItemRepo(BaseRepository[_Item]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session=session, model=_Item)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


class TestBulkCreateValues:
    async def test_inserts_rows_in_one_round_trip(self, session: AsyncSession) -> None:
        repo = _ItemRepo(session)
        rows = [
            {"id": uuid4(), "sku": f"SKU-{i}", "name": f"Item {i}"} for i in range(10)
        ]
        count = await repo.bulk_create_values(rows)
        assert count == 10
        assert await repo.count() == 10

    async def test_empty_rejected(self, session: AsyncSession) -> None:
        repo = _ItemRepo(session)
        with pytest.raises(ValueError, match="at least one row"):
            await repo.bulk_create_values([])


class TestBulkUpsert:
    async def test_inserts_when_no_conflict(self, session: AsyncSession) -> None:
        repo = _ItemRepo(session)
        rows = [
            {"id": uuid4(), "sku": "A", "name": "Apple"},
            {"id": uuid4(), "sku": "B", "name": "Banana"},
        ]
        affected = await repo.bulk_upsert(rows, conflict_columns=["sku"])
        assert affected == 2
        assert await repo.count() == 2

    async def test_updates_on_conflict(self, session: AsyncSession) -> None:
        repo = _ItemRepo(session)
        a_id: UUID = uuid4()
        await repo.bulk_create_values([{"id": a_id, "sku": "A", "name": "Apple"}])

        await repo.bulk_upsert(
            [{"id": uuid4(), "sku": "A", "name": "Apricot"}],
            conflict_columns=["sku"],
            update_columns=["name"],
        )

        # SKU "A" is the same row, just with a renamed name.
        assert await repo.count() == 1
        row = await repo.get(filters={"sku": "A"})
        assert row.name == "Apricot"

    async def test_empty_rejected(self, session: AsyncSession) -> None:
        repo = _ItemRepo(session)
        with pytest.raises(ValueError, match="at least one row"):
            await repo.bulk_upsert([], conflict_columns=["sku"])
