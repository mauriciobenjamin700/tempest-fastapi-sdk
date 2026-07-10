"""Tests for BaseRepository lifecycle signals."""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, RepositorySignal, on_signal
from tempest_fastapi_sdk.db import connect, disconnect
from tempest_fastapi_sdk.db.signals import clear_signals


class Widget(BaseModel):
    __tablename__ = "widget_signal"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture(autouse=True)
def _clear_signals() -> Iterator[None]:
    """Reset the global signal registry around every test."""
    clear_signals()
    yield
    clear_signals()


@pytest.fixture
def repo(session: AsyncSession) -> BaseRepository[Widget]:
    return BaseRepository(session, model=Widget)


class TestSaveSignals:
    async def test_add_fires_pre_then_post_save(
        self, repo: BaseRepository[Widget]
    ) -> None:
        events: list[tuple[str, str]] = []
        connect(
            Widget,
            RepositorySignal.PRE_SAVE,
            lambda w: events.append(("pre", w.name)),
        )
        connect(
            Widget,
            RepositorySignal.POST_SAVE,
            lambda w: events.append(("post", w.name)),
        )

        await repo.add(Widget(name="gear"))

        assert events == [("pre", "gear"), ("post", "gear")]

    async def test_update_fires_save_signals(
        self, repo: BaseRepository[Widget]
    ) -> None:
        widget = await repo.add(Widget(name="gear"))
        fired: list[str] = []
        connect(Widget, RepositorySignal.POST_SAVE, lambda w: fired.append(w.name))

        widget.name = "cog"
        await repo.update(widget)

        assert fired == ["cog"]

    async def test_soft_delete_fires_save_not_delete(
        self, repo: BaseRepository[Widget]
    ) -> None:
        widget = await repo.add(Widget(name="gear"))
        saved: list[bool] = []
        deleted: list[str] = []
        connect(Widget, RepositorySignal.POST_SAVE, lambda w: saved.append(w.is_active))
        connect(Widget, RepositorySignal.PRE_DELETE, lambda w: deleted.append(w.name))

        await repo.soft_delete(widget.id)

        assert saved == [False]
        assert deleted == []

    async def test_pre_save_handler_can_veto(
        self, repo: BaseRepository[Widget]
    ) -> None:
        def veto(_w: Any) -> None:
            raise ValueError("nope")

        connect(Widget, RepositorySignal.PRE_SAVE, veto)

        with pytest.raises(ValueError, match="nope"):
            await repo.add(Widget(name="gear"))

        # The rollback means nothing was persisted.
        assert await repo.count() == 0

    async def test_async_handler_is_awaited(self, repo: BaseRepository[Widget]) -> None:
        fired: list[str] = []

        @on_signal(Widget, RepositorySignal.POST_SAVE)
        async def _record(w: Widget) -> None:
            fired.append(w.name)

        await repo.add(Widget(name="gear"))

        assert fired == ["gear"]

    async def test_handler_on_base_class_fires_via_mro(
        self, repo: BaseRepository[Widget]
    ) -> None:
        fired: list[str] = []
        # Registered on BaseModel, not Widget — MRO resolution applies it.
        connect(BaseModel, RepositorySignal.POST_SAVE, lambda w: fired.append(w.name))

        await repo.add(Widget(name="gear"))

        assert fired == ["gear"]


class TestDeleteSignals:
    async def test_delete_fires_pre_and_post_with_readable_row(
        self, repo: BaseRepository[Widget]
    ) -> None:
        widget = await repo.add(Widget(name="gear"))
        seen: list[tuple[str, str]] = []
        connect(
            Widget,
            RepositorySignal.PRE_DELETE,
            lambda w: seen.append(("pre", w.name)),
        )
        # POST_DELETE reads an attribute after commit — proves the row was
        # detached so its columns survive the session's expire-on-commit.
        connect(
            Widget,
            RepositorySignal.POST_DELETE,
            lambda w: seen.append(("post", w.name)),
        )

        await repo.delete(widget.id)

        assert seen == [("pre", "gear"), ("post", "gear")]
        assert await repo.count() == 0

    async def test_delete_missing_raises_before_signals(
        self, repo: BaseRepository[Widget]
    ) -> None:
        widget = await repo.add(Widget(name="gear"))
        fired: list[str] = []
        connect(Widget, RepositorySignal.PRE_DELETE, lambda w: fired.append(w.name))
        await repo.delete(widget.id)
        fired.clear()

        from tempest_fastapi_sdk import NotFoundException

        with pytest.raises(NotFoundException):
            await repo.delete(widget.id)
        assert fired == []


class TestBulkBypassesSignals:
    async def test_bulk_update_does_not_fire(
        self, repo: BaseRepository[Widget]
    ) -> None:
        await repo.add(Widget(name="gear"))
        fired: list[str] = []
        connect(Widget, RepositorySignal.POST_SAVE, lambda w: fired.append(w.name))

        await repo.bulk_update({"name": "gear"}, {"name": "cog"})

        assert fired == []

    async def test_delete_many_does_not_fire(
        self, repo: BaseRepository[Widget]
    ) -> None:
        await repo.add(Widget(name="gear"))
        fired: list[str] = []
        connect(Widget, RepositorySignal.POST_DELETE, lambda w: fired.append(w.name))

        await repo.delete_many({"name": "gear"})

        assert fired == []


class TestRegistryManagement:
    async def test_disconnect_stops_delivery(
        self, repo: BaseRepository[Widget]
    ) -> None:
        fired: list[str] = []

        def handler(w: Widget) -> None:
            fired.append(w.name)

        connect(Widget, RepositorySignal.POST_SAVE, handler)
        disconnect(Widget, RepositorySignal.POST_SAVE, handler)

        await repo.add(Widget(name="gear"))

        assert fired == []
