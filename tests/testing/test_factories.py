"""Tests for the ModelFactory / seq test helpers."""

import pytest
from sqlalchemy import Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.testing import ModelFactory, seq


class Widget(BaseModel):
    __tablename__ = "factory_widget"
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@pytest.fixture
def widgets(session: AsyncSession) -> ModelFactory[Widget]:
    return ModelFactory(session, Widget, name=seq("w{n}"), count=1)


class TestModelFactory:
    def test_build_is_not_persisted(
        self, widgets: ModelFactory[Widget], session: AsyncSession
    ) -> None:
        widget = widgets.build()
        assert widget.name == "w0"
        assert widget.count == 1
        # build() constructs only — it never adds to the session.
        assert widget not in session.new

    async def test_create_persists(
        self, widgets: ModelFactory[Widget], session: AsyncSession
    ) -> None:
        created = await widgets.create()
        assert created.id is not None
        rows = (await session.execute(select(Widget))).scalars().all()
        assert [r.name for r in rows] == ["w0"]

    async def test_override_wins(self, widgets: ModelFactory[Widget]) -> None:
        widget = await widgets.create(name="custom", count=9)
        assert widget.name == "custom"
        assert widget.count == 9

    async def test_create_many_unique_via_seq(
        self, widgets: ModelFactory[Widget], session: AsyncSession
    ) -> None:
        batch = await widgets.create_many(3)
        assert {w.name for w in batch} == {"w0", "w1", "w2"}
        total = (await session.execute(select(Widget))).scalars().all()
        assert len(total) == 3

    async def test_callable_override_receives_index(
        self, widgets: ModelFactory[Widget]
    ) -> None:
        batch = await widgets.create_many(2, name=lambda i: f"x{i}")
        assert {w.name for w in batch} == {"x0", "x1"}

    def test_rejects_non_model(self, session: AsyncSession) -> None:
        with pytest.raises(TypeError, match="subclass of BaseModel"):
            ModelFactory(session, dict)  # type: ignore[type-var, arg-type]


def test_seq_formats() -> None:
    gen = seq("user{n}@x.com", start=1)
    assert gen(0) == "user1@x.com"
    assert gen(4) == "user5@x.com"
