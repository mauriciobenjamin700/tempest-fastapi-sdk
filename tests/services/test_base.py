"""Tests for tempest_fastapi_sdk.services.BaseService."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    BaseResponseSchema,
    BaseSchema,
    BaseService,
    NotFoundException,
)


class Widget(BaseModel):
    __tablename__ = "widget_for_service_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(128), nullable=True)


class WidgetResponse(BaseResponseSchema):
    name: str
    description: str | None = None


class WidgetUpdate(BaseSchema):
    name: str | None = None
    description: str | None = None


class WidgetRepository(BaseRepository[Widget]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=Widget)

    def map_to_response(self, instance: Widget) -> WidgetResponse:
        return WidgetResponse(
            id=instance.id,
            is_active=instance.is_active,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            name=instance.name,
            description=instance.description,
        )


class WidgetService(BaseService[WidgetRepository, WidgetResponse]):
    pass


@pytest.fixture
def service(session: AsyncSession) -> WidgetService:
    return WidgetService(WidgetRepository(session))


class TestBaseService:
    async def test_list_empty_returns_empty_list(self, service: WidgetService) -> None:
        assert await service.list() == []

    async def test_list_returns_mapped_responses(self, service: WidgetService) -> None:
        await service.repository.add_all(
            [Widget(name="a"), Widget(name="b")],
        )
        items = await service.list()
        assert {item.name for item in items} == {"a", "b"}
        assert all(isinstance(i, WidgetResponse) for i in items)

    async def test_get_by_id_returns_mapped_response(
        self, service: WidgetService
    ) -> None:
        created = await service.repository.add(Widget(name="alpha"))
        result = await service.get_by_id(created.id)
        assert result.name == "alpha"
        assert isinstance(result, WidgetResponse)

    async def test_get_by_id_missing_raises_not_found(
        self, service: WidgetService
    ) -> None:
        with pytest.raises(NotFoundException):
            await service.get_by_id(uuid4())

    async def test_get_or_none_returns_none_when_missing(
        self, service: WidgetService
    ) -> None:
        assert await service.get_or_none({"name": "ghost"}) is None

    async def test_paginate_maps_items(self, service: WidgetService) -> None:
        await service.repository.add_all(
            [Widget(name=f"w{i}") for i in range(3)],
        )
        result: dict[str, Any] = await service.paginate(page=1, page_size=10)
        assert result["total"] == 3
        assert all(isinstance(i, WidgetResponse) for i in result["items"])

    async def test_count(self, service: WidgetService) -> None:
        await service.repository.add(Widget(name="counted"))
        assert await service.count() == 1

    async def test_exists(self, service: WidgetService) -> None:
        await service.repository.add(Widget(name="here"))
        assert await service.exists({"name": "here"})
        assert not await service.exists({"name": "absent"})

    async def test_update_applies_fields(self, service: WidgetService) -> None:
        created = await service.repository.add(
            Widget(name="old", description="d0"),
        )
        result = await service.update(
            created.id, WidgetUpdate(name="new", description="d1")
        )
        assert isinstance(result, WidgetResponse)
        assert (result.name, result.description) == ("new", "d1")

    async def test_update_is_partial(self, service: WidgetService) -> None:
        """Unset fields are left untouched (PATCH semantics)."""
        created = await service.repository.add(
            Widget(name="keep", description="keep-me"),
        )
        result = await service.update(created.id, WidgetUpdate(name="renamed"))
        assert result.name == "renamed"
        assert result.description == "keep-me"

    async def test_update_missing_raises_not_found(
        self, service: WidgetService
    ) -> None:
        with pytest.raises(NotFoundException):
            await service.update(uuid4(), WidgetUpdate(name="x"))

    async def test_delete(self, service: WidgetService) -> None:
        created = await service.repository.add(Widget(name="bye"))
        await service.delete(created.id)
        with pytest.raises(NotFoundException):
            await service.get_by_id(created.id)


class AsyncWidgetRepository(BaseRepository[Widget]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=Widget)

    async def map_to_response(self, instance: Widget) -> WidgetResponse:
        return WidgetResponse(
            id=instance.id,
            is_active=instance.is_active,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            name=instance.name,
            description=instance.description,
        )


class AsyncWidgetService(BaseService[AsyncWidgetRepository, WidgetResponse]):
    pass


@pytest.fixture
def async_service(session: AsyncSession) -> AsyncWidgetService:
    return AsyncWidgetService(AsyncWidgetRepository(session))


class TestBaseServiceAsyncMapToResponse:
    """BaseService must also support repositories whose
    ``map_to_response`` is a coroutine, awaiting it transparently."""

    async def test_get_by_id_awaits_async_mapper(
        self, async_service: AsyncWidgetService
    ) -> None:
        created = await async_service.repository.add(Widget(name="async-alpha"))
        result = await async_service.get_by_id(created.id)
        assert isinstance(result, WidgetResponse)
        assert result.name == "async-alpha"

    async def test_get_or_none_awaits_async_mapper(
        self, async_service: AsyncWidgetService
    ) -> None:
        created = await async_service.repository.add(Widget(name="async-beta"))
        result = await async_service.get_or_none({"name": "async-beta"})
        assert result is not None
        assert result.id == created.id

    async def test_list_awaits_async_mapper(
        self, async_service: AsyncWidgetService
    ) -> None:
        await async_service.repository.add_all(
            [Widget(name="async-x"), Widget(name="async-y")],
        )
        items = await async_service.list()
        assert {item.name for item in items} == {"async-x", "async-y"}
        assert all(isinstance(i, WidgetResponse) for i in items)

    async def test_paginate_awaits_async_mapper(
        self, async_service: AsyncWidgetService
    ) -> None:
        await async_service.repository.add_all(
            [Widget(name=f"async-w{i}") for i in range(3)],
        )
        result: dict[str, Any] = await async_service.paginate(page=1, page_size=10)
        assert result["total"] == 3
        assert all(isinstance(i, WidgetResponse) for i in result["items"])
