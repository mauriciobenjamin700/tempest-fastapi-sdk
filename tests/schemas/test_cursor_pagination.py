"""Tests for cursor pagination schemas + repository support."""

from uuid import uuid4

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    CursorPaginationFilterSchema,
    CursorPaginationSchema,
    decode_cursor,
    encode_cursor,
)


class Note(BaseModel):
    __tablename__ = "note_for_cursor_test"
    title: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture
def repo(session: AsyncSession) -> BaseRepository[Note]:
    return BaseRepository(session, model=Note)


class TestCursorEncoding:
    def test_round_trip(self) -> None:
        payload = {"value": "abc", "id": str(uuid4())}
        cursor = encode_cursor(payload)
        decoded = decode_cursor(cursor)
        assert decoded == payload

    def test_decode_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            decode_cursor("not-a-cursor")

    def test_decode_rejects_non_object(self) -> None:
        import base64

        bogus = base64.urlsafe_b64encode(b'"string"').rstrip(b"=").decode("ascii")
        with pytest.raises(ValueError):
            decode_cursor(bogus)


class TestCursorPaginationFilterSchema:
    def test_defaults(self) -> None:
        filt = CursorPaginationFilterSchema()
        assert filt.cursor is None
        assert filt.limit == 20
        assert filt.order_by == "created_at"
        assert filt.ascending is False

    def test_get_conditions_strips_pagination(self) -> None:
        filt = CursorPaginationFilterSchema(
            cursor="x",
            limit=5,
            order_by="title",
            ascending=True,
        )
        assert filt.get_conditions() == {}


class TestCursorPaginate:
    async def test_first_page_no_cursor(self, repo: BaseRepository[Note]) -> None:
        await repo.add_all([Note(title=f"n{i}") for i in range(5)])
        result = await repo.cursor_paginate(limit=2, order_by="title", ascending=True)
        assert len(result["items"]) == 2
        assert result["has_more"] is True
        assert result["next_cursor"] is not None
        assert result["limit"] == 2

    async def test_walks_through_pages(self, repo: BaseRepository[Note]) -> None:
        await repo.add_all([Note(title=f"n{i:02d}") for i in range(7)])
        seen_titles: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            page = await repo.cursor_paginate(
                cursor=cursor,
                limit=3,
                order_by="title",
                ascending=True,
            )
            seen_titles.extend(n.title for n in page["items"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert seen_titles == [f"n{i:02d}" for i in range(7)]

    async def test_last_page_has_no_next_cursor(
        self, repo: BaseRepository[Note]
    ) -> None:
        await repo.add_all([Note(title=f"n{i}") for i in range(3)])
        result = await repo.cursor_paginate(limit=10, order_by="title", ascending=True)
        assert result["has_more"] is False
        assert result["next_cursor"] is None
        assert len(result["items"]) == 3

    async def test_invalid_order_by_raises(self, repo: BaseRepository[Note]) -> None:
        with pytest.raises(ValueError):
            await repo.cursor_paginate(order_by="ghost_column")

    async def test_descending_order(self, repo: BaseRepository[Note]) -> None:
        await repo.add_all([Note(title=f"n{i}") for i in range(3)])
        result = await repo.cursor_paginate(limit=10, order_by="title", ascending=False)
        titles = [n.title for n in result["items"]]
        assert titles == sorted(titles, reverse=True)


class TestCursorPaginationSchema:
    def test_envelope_round_trip(self) -> None:
        envelope = CursorPaginationSchema[CursorPaginationFilterSchema](
            items=[],
            next_cursor="abc",
            has_more=True,
            limit=10,
        )
        assert envelope.next_cursor == "abc"
        assert envelope.has_more is True
