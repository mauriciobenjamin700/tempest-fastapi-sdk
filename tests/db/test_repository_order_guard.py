"""``orderable_columns`` and ``max_page_size`` on ``BaseRepository``.

Issue #295: the repository accepted any mapped column in ``order_by`` and
its refusal published ``details["allowed"]`` with **every** mapped column —
``hashed_password`` included. The repository is the second line behind the
filter schema, for callers that reach it without one.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from sqlalchemy import Integer, String, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    OrderByNotAllowedException,
    PageSizeTooLargeException,
    ValidationException,
)


class Account(BaseModel):
    __tablename__ = "account_for_order_guard_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    wallet: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GuardedAccountRepository(BaseRepository[Account]):
    orderable_columns: ClassVar[frozenset[str] | None] = frozenset(
        {"created_at", "name"},
    )
    max_page_size: ClassVar[int | None] = 50

    def __init__(self, session: AsyncSession, **kwargs: Any) -> None:
        super().__init__(session, model=Account, **kwargs)


ALL_COLUMNS: frozenset[str] = frozenset(inspect(Account).columns.keys())


async def _seed(session: AsyncSession) -> None:
    repo = BaseRepository(session, model=Account)
    await repo.add_all(
        [
            Account(name="bruna", hashed_password="h2", wallet=10),
            Account(name="ana", hashed_password="h1", wallet=99),
        ],
    )


class TestUndeclared:
    async def test_any_mapped_column_still_sorts(self, session: AsyncSession) -> None:
        await _seed(session)
        repo = BaseRepository(session, model=Account)
        page = await repo.paginate(order_by="wallet", ascending=False)
        assert [a.name for a in page["items"]] == ["ana", "bruna"]

    async def test_refusal_omits_allowed(self, session: AsyncSession) -> None:
        repo = BaseRepository(session, model=Account)
        with pytest.raises(OrderByNotAllowedException) as exc:
            await repo.paginate(order_by="ghost")
        assert exc.value.details == {"order_by": "ghost"}
        assert isinstance(exc.value, ValidationException)
        assert exc.value.field == "order_by"

    async def test_refusal_never_names_a_mapped_column(
        self, session: AsyncSession
    ) -> None:
        repo = BaseRepository(session, model=Account)
        with pytest.raises(OrderByNotAllowedException) as exc:
            await repo.paginate(order_by="ghost")
        serialized = repr(exc.value.details) + exc.value.message
        for column in ALL_COLUMNS:
            assert f"'{column}'" not in serialized

    async def test_no_page_size_ceiling_by_default(self, session: AsyncSession) -> None:
        repo = BaseRepository(session, model=Account)
        page = await repo.paginate(page_size=10_000)
        assert page["page_size"] == 10_000


class TestDeclaredOnTheClass:
    async def test_declared_column_sorts(self, session: AsyncSession) -> None:
        await _seed(session)
        page = await GuardedAccountRepository(session).paginate(order_by="name")
        assert [a.name for a in page["items"]] == ["ana", "bruna"]

    @pytest.mark.parametrize(
        "column",
        [*sorted(ALL_COLUMNS - {"created_at", "name"}), "ghost", "metadata"],
    )
    async def test_refusal_lists_only_the_declared_set(
        self, session: AsyncSession, column: str
    ) -> None:
        repo = GuardedAccountRepository(session)
        with pytest.raises(OrderByNotAllowedException) as exc:
            await repo.paginate(order_by=column)
        assert exc.value.details["allowed"] == ["created_at", "name"]
        assert set(exc.value.details["allowed"]) <= {"created_at", "name"}

    async def test_cursor_paginate_respects_the_set(
        self, session: AsyncSession
    ) -> None:
        repo = GuardedAccountRepository(session)
        with pytest.raises(OrderByNotAllowedException) as exc:
            await repo.cursor_paginate(order_by="hashed_password")
        assert exc.value.details["allowed"] == ["created_at", "name"]
        page = await repo.cursor_paginate(order_by="name")
        assert page["items"] == []

    async def test_changes_since_respects_the_set(self, session: AsyncSession) -> None:
        with pytest.raises(OrderByNotAllowedException):
            await GuardedAccountRepository(session).changes_since(None)

    async def test_page_size_ceiling(self, session: AsyncSession) -> None:
        repo = GuardedAccountRepository(session)
        assert (await repo.paginate(page_size=50))["page_size"] == 50
        with pytest.raises(PageSizeTooLargeException) as exc:
            await repo.paginate(page_size=51)
        assert exc.value.code == "PAGE_SIZE_TOO_LARGE"
        assert exc.value.status_code == 422
        assert exc.value.field == "page_size"
        assert exc.value.details == {"page_size": 51, "max_page_size": 50}

    async def test_cursor_limit_ceiling(self, session: AsyncSession) -> None:
        with pytest.raises(PageSizeTooLargeException):
            await GuardedAccountRepository(session).cursor_paginate(limit=51)


class TestDeclaredOnTheConstructor:
    async def test_constructor_overrides_the_class(self, session: AsyncSession) -> None:
        repo = GuardedAccountRepository(
            session,
            orderable_columns=["wallet"],
            max_page_size=5,
        )
        await repo.paginate(order_by="wallet", page_size=5)
        with pytest.raises(OrderByNotAllowedException) as exc:
            await repo.paginate(order_by="name", page_size=5)
        assert exc.value.details["allowed"] == ["wallet"]
        with pytest.raises(PageSizeTooLargeException):
            await repo.paginate(page_size=6)

    async def test_plain_repository_takes_the_kwargs(
        self, session: AsyncSession
    ) -> None:
        repo = BaseRepository(session, model=Account, orderable_columns={"name"})
        with pytest.raises(OrderByNotAllowedException) as exc:
            await repo.paginate(order_by="wallet")
        assert exc.value.details["allowed"] == ["name"]

    def test_unknown_declared_column_fails_at_construction(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="nme"):
            BaseRepository(session, model=Account, orderable_columns={"nme"})

    def test_non_positive_ceiling_fails_at_construction(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="max_page_size"):
            BaseRepository(session, model=Account, max_page_size=0)
