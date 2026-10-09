"""Pin the ``filters`` contract that every docstring spells out (#443, #465).

Each ``filters: dict[str, Any]`` parameter in the repository, service,
controller, tenant and admin layers documents the same behavior: column
name to value, ANDed; a list is ``IN``; ``None`` is ``IS NULL``; a
``<column>__<op>`` key applies an operator; and a key the model has no
column for, or an unknown operator, raises
:class:`~tempest_fastapi_sdk.UnknownFilterKeyException` before any
statement runs. These tests are the measurement behind those sentences —
when one of them changes, the docstrings have to change with it.

Until #465 the unknown key was dropped in silence: the same
``{"usr_id": 1234}`` made ``list`` / ``count`` return every row and
``bulk_update`` / ``delete_many`` change every row in the table (3/3 here,
pinned by the previous version of this file).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseController,
    BaseModel,
    BaseRepository,
    BaseService,
    TenantScopedRepository,
    UnknownFilterKeyException,
    register_exception_handlers,
)
from tempest_fastapi_sdk.db import Q


class FilterProbe(BaseModel):
    """A row with the columns the docstring example names."""

    __tablename__ = "filter_contract_probe"

    label: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class TenantProbe(BaseModel):
    """A tenant-scoped row for the tenant repository case."""

    __tablename__ = "filter_contract_tenant_probe"

    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)


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


class TestUnknownKeyIsRefused:
    """A key the model cannot resolve raises instead of widening the query."""

    @pytest.mark.parametrize(
        "key",
        ["usr_id", "user_id__bogus", "usr_id__gte", "usr_id__isnull", "metadata"],
    )
    async def test_every_read_refuses(
        self,
        repo: BaseRepository[FilterProbe],
        key: str,
    ) -> None:
        """Typo, unknown suffix, typo under a real suffix, non-SQL attribute.

        ``metadata`` exists on every model class — it is the SQLAlchemy
        ``MetaData`` — but has no SQL reading, so it counts as unknown.
        """
        reads: list[Callable[[], Awaitable[object]]] = [
            lambda: repo.list(filters={key: 1}),
            lambda: repo.count(filters={key: 1}),
            lambda: repo.exists({key: 1}),
            lambda: repo.first(filters={key: 1}),
            lambda: repo.get_or_none({key: 1}),
            lambda: repo.get({key: 1}),
            lambda: repo.paginate(filters={key: 1}),
            lambda: repo.cursor_paginate(filters={key: 1}),
            lambda: repo.list(where=Q(**{key: 1})),
            lambda: repo.list(where=~Q(**{key: 1})),
        ]
        for read in reads:
            with pytest.raises(UnknownFilterKeyException) as exc:
                await read()
            assert exc.value.code == "UNKNOWN_FILTER_KEY"
            assert exc.value.status_code == 422
            assert exc.value.details == {"filter": key}
            assert exc.value.filter_key == key

    async def test_delete_many_deletes_nothing(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """The acceptance case of #465: the typo no longer empties the table."""
        with pytest.raises(UnknownFilterKeyException):
            await repo.delete_many({"usr_id": 1})
        assert await repo.count() == 3

    async def test_bulk_update_changes_nothing(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """A typo next to a valid key still refuses the whole write."""
        with pytest.raises(UnknownFilterKeyException):
            await repo.bulk_update({"usr_id": 1234}, {"status": "void"})
        with pytest.raises(UnknownFilterKeyException):
            await repo.bulk_update(
                {"status": "open", "user_id__bogus": 1},
                {"status": "void"},
            )
        assert await repo.count(filters={"status": "void"}) == 0

    async def test_update_returning_changes_nothing(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """The read-back write refuses the same way."""
        with pytest.raises(UnknownFilterKeyException):
            await repo.update_returning(
                {"usr_id": 1234},
                {"status": "void"},
                returning=["label"],
            )
        assert await repo.count(filters={"status": "void"}) == 0

    async def test_known_keys_still_resolve(
        self,
        repo: BaseRepository[FilterProbe],
    ) -> None:
        """Operator suffixes and the range sugar keep working."""
        assert await repo.count(filters={"user_id__isnull": False}) == 3
        assert await repo.count(filters={"label__in": ["a", "b"]}) == 2
        assert await repo.count(filters={"status__icontains": "PE"}) == 2
        assert await repo.count(filters={"start_in": date(2000, 1, 1)}) == 3


class TestLayersPassTheRefusalThrough:
    """Service, controller and tenant repository raise the same exception."""

    async def test_service_and_controller(self, session: AsyncSession) -> None:
        """Neither layer swallows the refusal or rewrites it.

        Args:
            session (AsyncSession): The suite's session.
        """
        repository: BaseRepository[FilterProbe] = BaseRepository(
            session,
            model=FilterProbe,
        )
        service: BaseService[Any, Any, Any] = BaseService(repository)
        controller: BaseController[Any, Any, Any] = BaseController(service)
        calls: list[Callable[[], Awaitable[object]]] = [
            lambda: service.list(filters={"usr_id": 1}),
            lambda: service.count({"usr_id": 1}),
            lambda: service.exists({"usr_id": 1}),
            lambda: service.paginate(filters={"usr_id": 1}),
            lambda: controller.list(filters={"usr_id": 1}),
            lambda: controller.count({"usr_id": 1}),
            lambda: controller.paginate(filters={"usr_id": 1}),
        ]
        for call in calls:
            with pytest.raises(UnknownFilterKeyException):
                await call()

    async def test_tenant_repository(self, session: AsyncSession) -> None:
        """The tenant key is merged in, the caller's typo still raises.

        Args:
            session (AsyncSession): The suite's session.
        """
        repository: TenantScopedRepository[TenantProbe] = TenantScopedRepository(
            session,
            model=TenantProbe,
            tenant_id=uuid4(),
        )
        assert await repository.count() == 0
        with pytest.raises(UnknownFilterKeyException):
            await repository.delete_many({"usr_id": 1})
        with pytest.raises(UnknownFilterKeyException):
            await repository.list(filters={"usr_id": 1})


class TestHttpShape:
    """Through the SDK exception handlers the refusal is a 422 body."""

    def test_handler_renders_422(self) -> None:
        """``code`` and ``details`` reach the client unchanged."""
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/probe")
        async def probe() -> None:
            """Raise what a repository raises on ``{"usr_id": 1}``."""
            raise UnknownFilterKeyException("usr_id")

        response = TestClient(app).get("/probe")
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "UNKNOWN_FILTER_KEY"
        assert body["details"] == {"filter": "usr_id"}
