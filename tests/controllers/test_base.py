"""Tests for tempest_fastapi_sdk.controllers.BaseController."""

from uuid import uuid4

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseController,
    BaseModel,
    BaseRepository,
    BaseResponseSchema,
    BaseSchema,
    BaseService,
    NotFoundException,
)


class Gadget(BaseModel):
    __tablename__ = "gadget_for_controller_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class GadgetResponse(BaseResponseSchema):
    name: str


class GadgetUpdate(BaseSchema):
    name: str | None = None


class GadgetRepository(BaseRepository[Gadget]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=Gadget)

    def map_to_response(self, instance: Gadget) -> GadgetResponse:
        return GadgetResponse(
            id=instance.id,
            is_active=instance.is_active,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            name=instance.name,
        )


class GadgetService(BaseService[GadgetRepository, GadgetResponse]):
    pass


class GadgetController(BaseController[GadgetService, GadgetResponse]):
    pass


@pytest.fixture
def controller(session: AsyncSession) -> GadgetController:
    return GadgetController(GadgetService(GadgetRepository(session)))


class TestBaseController:
    async def test_list_passes_through(self, controller: GadgetController) -> None:
        await controller.service.repository.add(Gadget(name="alpha"))
        items = await controller.list()
        assert len(items) == 1
        assert items[0].name == "alpha"

    async def test_get_by_id_passes_through(self, controller: GadgetController) -> None:
        created = await controller.service.repository.add(Gadget(name="beta"))
        result = await controller.get_by_id(created.id)
        assert result.name == "beta"

    async def test_get_by_id_missing_raises(self, controller: GadgetController) -> None:
        with pytest.raises(NotFoundException):
            await controller.get_by_id(uuid4())

    async def test_paginate_passes_through(self, controller: GadgetController) -> None:
        await controller.service.repository.add_all(
            [Gadget(name=f"g{i}") for i in range(2)]
        )
        result = await controller.paginate(page=1, page_size=10)
        assert result["total"] == 2

    async def test_count_passes_through(self, controller: GadgetController) -> None:
        await controller.service.repository.add(Gadget(name="single"))
        assert await controller.count() == 1

    async def test_update_passes_through(self, controller: GadgetController) -> None:
        created = await controller.service.repository.add(Gadget(name="before"))
        result = await controller.update(created.id, GadgetUpdate(name="after"))
        assert result.name == "after"

    async def test_delete_passes_through(self, controller: GadgetController) -> None:
        created = await controller.service.repository.add(Gadget(name="gone"))
        await controller.delete(created.id)
        assert await controller.count() == 0
