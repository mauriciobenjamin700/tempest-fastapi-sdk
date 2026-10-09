"""Pin the ``filters`` contract that every docstring now spells out (#443).

Each ``filters: dict[str, Any]`` parameter in the repository, service,
controller, tenant and admin layers documents the same behavior: column
name to value, ANDed; a list is ``IN``; ``None`` is ``IS NULL``; a
``<column>__<op>`` key applies an operator; and a key the model has no
column for is ignored without error. These tests are the measurement
behind those sentences — when one of them changes, the docstrings have
to change with it.
"""

from __future__ import annotations

import warnings

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository


class FilterProbe(BaseModel):
    """A row with the columns the docstring example names."""

    __tablename__ = "filter_contract_probe"

    label: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


@pytest.fixture
async def repo(session: AsyncSession) -> BaseRepository[FilterProbe]:
    """Return a repository holding three rows that split on every key.

    Args:
        session (AsyncSession): The suite's session.

    Returns:
        BaseRepository[FilterProbe]: The repository under test.
    """
    repository: BaseRepository[FilterProbe] = BaseRepository(
        session,
        model=FilterProbe,
    )
    await repository.add(
        FilterProbe(label="a", user_id=1234, is_active=True, status="open"),
    )
    await repository.add(
        FilterProbe(label="b", user_id=1234, is_active=False, status="paid"),
    )
    await repository.add(
        FilterProbe(label="c", user_id=99, is_active=True, status="open"),
    )
    return repository


async def _labels(
    repo: BaseRepository[FilterProbe],
    filters: dict[str, object],
) -> list[str]:
    """Return the sorted labels the filter selects.

    Args:
        repo (BaseRepository[FilterProbe]): The repository under test.
        filters (dict[str, object]): The mapping to apply.

    Returns:
        list[str]: The labels of the matching rows.
    """
    return sorted(row.label for row in await repo.list(filters=dict(filters)))


class TestDocumentedExample:
    """The example every docstring carries does what the prose says."""

    async def test_pairs_are_anded(self, repo: BaseRepository[FilterProbe]) -> None:
        """``{"user_id": 1234, "is_active": True}`` needs both to hold."""
        assert await _labels(repo, {"user_id": 1234, "is_active": True}) == ["a"]

    async def test_list_is_in(self, repo: BaseRepository[FilterProbe]) -> None:
        """A list value is membership, not equality with the list."""
        assert await _labels(repo, {"user_id": [1234, 99]}) == ["a", "b", "c"]

    async def test_operator_suffix(self, repo: BaseRepository[FilterProbe]) -> None:
        """``<column>__<op>`` applies the operator."""
        assert await _labels(repo, {"user_id__gte": 100}) == ["a", "b"]

    async def test_plain_string_is_case_sensitive(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """Equality on a string column keeps case, as the class docstring says."""
        assert await _labels(repo, {"status": "OPEN"}) == []
        assert await _labels(repo, {"status": "open"}) == ["a", "c"]


class TestUnknownKeyIsIgnored:
    """A key the model has no column for drops out of the query silently."""

    async def test_read_matches_every_row(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """A misspelled key returns every row, with no warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert await _labels(repo, {"usr_id": 1234}) == ["a", "b", "c"]
            assert await repo.count(filters={"usr_id": 1234}) == 3

    async def test_unknown_operator_matches_every_row(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """An unknown suffix is dropped the same way."""
        assert await _labels(repo, {"user_id__bogus": 1}) == ["a", "b", "c"]

    async def test_write_reaches_every_row(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """The write docstrings warn about this: the typo widens the write."""
        assert await repo.bulk_update({"usr_id": 1234}, {"status": "void"}) == 3
        assert await repo.delete_many({"usr_id": 1234}) == 3
        assert await repo.count() == 0
