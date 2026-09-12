"""``{"col": None}`` means ``IS NULL``, and used to mean nothing at all.

The old behaviour dropped the key, so the filter vanished and the query
answered with **every** row. That is the expensive direction: a filter
that errs toward fewer rows shows up as an empty screen on the first
manual test, while this one shows up as data nobody looks at twice.

Measured in a consumer before the fix, ``{"left_at": None}`` — the
obvious spelling of "still a member" — matched people who had already
left, and the WebSocket fan-out built from it kept delivering a group's
messages to them.
"""

from __future__ import annotations

import warnings
from datetime import UTC, datetime

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    DroppedFilterWarning,
    Q,
)


class Membership(BaseModel):
    """A row with the nullable column the defect was found on."""

    __tablename__ = "membership_for_none_filter_test"

    label: Mapped[str] = mapped_column(String(16), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)


@pytest.fixture
async def repo(session: AsyncSession) -> BaseRepository[Membership]:
    """Return a repository holding one active and one departed row.

    Args:
        session (AsyncSession): The suite's session.

    Returns:
        BaseRepository[Membership]: The repository under test.
    """
    repository: BaseRepository[Membership] = BaseRepository(
        session,
        model=Membership,
    )
    await repository.add(Membership(label="active"))
    await repository.add(
        Membership(label="left", left_at=datetime(2026, 9, 11, tzinfo=UTC)),
    )
    return repository


class TestBareColumn:
    """The case the issue was filed on."""

    async def test_none_matches_only_null_rows(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        rows = await repo.list(filters={"left_at": None})

        assert [row.label for row in rows] == ["active"]

    async def test_it_used_to_return_everything(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        """Pinned as the measured regression: 2 rows where 1 was asked for."""
        everything = await repo.list()
        filtered = await repo.list(filters={"left_at": None})

        assert len(everything) == 2
        assert len(filtered) == 1

    async def test_ne_none_is_is_not_null(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        rows = await repo.list(filters={"left_at__ne": None})

        assert [row.label for row in rows] == ["left"]

    async def test_isnull_still_works_both_ways(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        """``isnull=False`` must not be read as "no filter"."""
        nulls = await repo.list(filters={"left_at__isnull": True})
        not_nulls = await repo.list(filters={"left_at__isnull": False})

        assert [row.label for row in nulls] == ["active"]
        assert [row.label for row in not_nulls] == ["left"]

    async def test_it_composes_with_another_filter(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        rows = await repo.list(filters={"left_at": None, "label": "active"})

        assert len(rows) == 1

    async def test_count_and_exists_agree_with_list(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        """Every read path shares ``_apply_filters``; none may disagree."""
        assert await repo.count(filters={"left_at": None}) == 1
        assert await repo.exists({"left_at": None}) is True


class TestQTree:
    """``Q`` shares the builder, so it had the same silent drop."""

    async def test_q_none_is_is_null(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        rows = await repo.list(where=Q(left_at=None))

        assert [row.label for row in rows] == ["active"]

    async def test_q_negation_reads_as_not_null(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        rows = await repo.list(where=~Q(left_at=None))

        assert [row.label for row in rows] == ["left"]


class TestOperatorsThatCannotExpressIt:
    """Where ``None`` has no reading, the drop stays — but announces itself."""

    async def test_gt_warns_and_drops(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        with pytest.warns(DroppedFilterWarning, match="left_at__gt"):
            rows = await repo.list(filters={"left_at__gt": None})

        assert len(rows) == 2

    async def test_in_warns_and_drops(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        with pytest.warns(DroppedFilterWarning, match="left_at__in"):
            rows = await repo.list(filters={"left_at__in": None})

        assert len(rows) == 2

    async def test_the_warning_names_the_way_out(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        with pytest.warns(DroppedFilterWarning, match="isnull"):
            await repo.list(filters={"left_at__gte": None})

    def test_it_is_silenceable_like_any_warning(self) -> None:
        assert issubclass(DroppedFilterWarning, UserWarning)


class TestRangeSugarKeepsItsMeaning:
    """``start_in`` / ``end_in`` are the two ends of a range, not values."""

    async def test_none_bound_is_not_a_null_match(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        rows = await repo.list(filters={"start_in": None, "end_in": None})

        assert len(rows) == 2

    async def test_none_bound_says_nothing(
        self,
        repo: BaseRepository[Membership],
    ) -> None:
        """The exception is deliberate, so it does not warn.

        Every other operator that cannot read ``None`` warns, which is
        what makes the silence here a claim worth pinning: the README
        documents the pair as the one place where ``None`` keeps
        meaning "no bound on this side", quietly. Without this, the
        warning could grow to cover the pair and only the prose would
        disagree.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            await repo.list(filters={"start_in": None, "end_in": None})

        assert [type(entry.message).__name__ for entry in caught] == []
