"""Tests for the F() / Q() expression wrappers on BaseRepository."""

import pytest
from sqlalchemy import Integer, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, F, Q


class Item(BaseModel):
    __tablename__ = "item_expr_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@pytest.fixture
def repo(session: AsyncSession) -> BaseRepository[Item]:
    return BaseRepository(session, model=Item)


async def _seed(repo: BaseRepository[Item]) -> None:
    await repo.add_all(
        [
            Item(name="a", status="open", stock=10, priority=1),
            Item(name="b", status="pending", stock=5, priority=8),
            Item(name="c", status="closed", stock=0, priority=3),
        ]
    )


class TestF:
    async def test_atomic_decrement(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        item = await repo.first({"name": "a"})
        assert item is not None

        await repo.bulk_update({"id": item.id}, {"stock": F("stock") - 1})

        refreshed = await repo.get_by_id(item.id)
        assert refreshed.stock == 9

    async def test_reflected_operand(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        item = await repo.first({"name": "a"})
        assert item is not None

        # 100 - stock (stock was 10) → 90
        await repo.bulk_update({"id": item.id}, {"stock": 100 - F("stock")})

        refreshed = await repo.get_by_id(item.id)
        assert refreshed.stock == 90

    async def test_column_times_column(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        item = await repo.first({"name": "b"})  # stock=5, priority=8
        assert item is not None

        await repo.bulk_update({"id": item.id}, {"stock": F("stock") * F("priority")})

        refreshed = await repo.get_by_id(item.id)
        assert refreshed.stock == 40


class TestQ:
    async def test_or(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        rows = await repo.list(where=Q(status="open") | Q(status="closed"))
        assert {r.name for r in rows} == {"a", "c"}

    async def test_and(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        rows = await repo.list(where=Q(status="pending") & Q(priority__gte=5))
        assert {r.name for r in rows} == {"b"}

    async def test_not(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        rows = await repo.list(where=~Q(status="open"))
        assert {r.name for r in rows} == {"b", "c"}

    async def test_comparison_suffix(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        rows = await repo.list(where=Q(priority__gte=3))
        assert {r.name for r in rows} == {"b", "c"}

    async def test_combined_with_filters_dict(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        # filters (stock >= 5) AND where (open OR closed) → only "a"
        rows = await repo.list(
            {"stock__gte": 5}, where=Q(status="open") | Q(status="closed")
        )
        assert {r.name for r in rows} == {"a"}

    async def test_count_and_exists_with_where(
        self, repo: BaseRepository[Item]
    ) -> None:
        await _seed(repo)
        assert await repo.count(where=Q(status="open") | Q(status="pending")) == 2
        assert await repo.exists({}, where=Q(priority__gte=8)) is True
        assert await repo.exists({}, where=Q(priority__gte=99)) is False

    async def test_empty_q_is_noop(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        rows = await repo.list(where=Q())
        assert len(rows) == 3

    async def test_delete_many_with_where(self, repo: BaseRepository[Item]) -> None:
        await _seed(repo)
        deleted = await repo.delete_many({}, where=Q(status="closed"))
        assert deleted == 1
        assert await repo.count() == 2
