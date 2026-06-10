"""Tests for the transactional outbox: model, save_with_outbox, relay."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db import (
    AsyncDatabaseManager,
    BaseModel,
    BaseOutboxModel,
    BaseRepository,
    OutboxRelay,
    OutboxStatus,
)


class _WidgetModel(BaseModel):
    """Business row used by the outbox tests."""

    __tablename__ = "widget"

    name: Mapped[str] = mapped_column(String(50), nullable=False)


class _OutboxModel(BaseOutboxModel):
    """Concrete outbox table for the tests."""

    __tablename__ = "outbox"


@pytest_asyncio.fixture
async def outbox_db() -> AsyncGenerator[AsyncDatabaseManager]:
    """In-memory database with the widget + outbox tables created."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


class _WidgetRepository(BaseRepository[_WidgetModel]):
    def __init__(self, session: Any) -> None:
        super().__init__(session, model=_WidgetModel)

    def map_to_schema(self, instance: _WidgetModel) -> Any:
        return instance

    def map_to_model(self, data: dict[str, Any]) -> _WidgetModel:
        return _WidgetModel(**data)

    def map_to_response(self, instance: _WidgetModel) -> Any:
        return instance


class TestNewEvent:
    def test_new_event_defaults(self) -> None:
        event = _OutboxModel.new_event("widgets.created", {"id": 1})
        assert event.topic == "widgets.created"
        assert event.payload == {"id": 1}
        assert event.status == OutboxStatus.PENDING.value
        assert event.attempts == 0
        assert event.max_attempts == 5


class TestSaveWithOutbox:
    async def test_persists_both_rows_atomically(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        async with outbox_db.get_session_context() as session:
            repo = _WidgetRepository(session)
            widget = _WidgetModel(name="gear")
            event = _OutboxModel.new_event("widgets.created", {"name": "gear"})
            saved = await repo.save_with_outbox(widget, event)
            assert saved.id is not None

        async with outbox_db.get_session_context() as session:
            widgets = (await session.execute(select(_WidgetModel))).scalars().all()
            events = (await session.execute(select(_OutboxModel))).scalars().all()
            assert len(widgets) == 1
            assert len(events) == 1
            assert events[0].status == OutboxStatus.PENDING.value


class TestOutboxRelay:
    async def test_drain_publishes_and_marks_sent(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        async with outbox_db.get_session_context() as session:
            repo = _WidgetRepository(session)
            await repo.save_with_outbox(
                _WidgetModel(name="a"),
                _OutboxModel.new_event("widgets.created", {"name": "a"}),
            )

        published: list[dict[str, Any]] = []

        async def _publish(event: BaseOutboxModel) -> None:
            published.append(event.payload)

        relay = OutboxRelay(outbox_db, model=_OutboxModel, publish=_publish)
        count = await relay.drain_once()

        assert count == 1
        assert published == [{"name": "a"}]
        async with outbox_db.get_session_context() as session:
            event = (await session.execute(select(_OutboxModel))).scalar_one()
            assert event.status == OutboxStatus.SENT.value
            assert event.sent_at is not None

    async def test_drain_empty_returns_zero(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        async def _publish(event: BaseOutboxModel) -> None:
            raise AssertionError("should not be called")

        relay = OutboxRelay(outbox_db, model=_OutboxModel, publish=_publish)
        assert await relay.drain_once() == 0

    async def test_publish_failure_reschedules_with_backoff(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        async with outbox_db.get_session_context() as session:
            repo = _WidgetRepository(session)
            await repo.save_with_outbox(
                _WidgetModel(name="b"),
                _OutboxModel.new_event("widgets.created", {"name": "b"}),
            )

        async def _failing_publish(event: BaseOutboxModel) -> None:
            raise RuntimeError("broker down")

        relay = OutboxRelay(outbox_db, model=_OutboxModel, publish=_failing_publish)
        count = await relay.drain_once()

        assert count == 0
        async with outbox_db.get_session_context() as session:
            event = (await session.execute(select(_OutboxModel))).scalar_one()
            assert event.status == OutboxStatus.PENDING.value
            assert event.attempts == 1
            assert event.last_error == "broker down"

    async def test_exhausted_attempts_marks_failed(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        async with outbox_db.get_session_context() as session:
            repo = _WidgetRepository(session)
            event = _OutboxModel.new_event(
                "widgets.created", {"name": "c"}, max_attempts=1
            )
            await repo.save_with_outbox(_WidgetModel(name="c"), event)

        async def _failing_publish(event: BaseOutboxModel) -> None:
            raise RuntimeError("nope")

        relay = OutboxRelay(outbox_db, model=_OutboxModel, publish=_failing_publish)
        await relay.drain_once()

        async with outbox_db.get_session_context() as session:
            event = (await session.execute(select(_OutboxModel))).scalar_one()
            assert event.status == OutboxStatus.FAILED.value
            assert event.attempts == 1


class TestValidation:
    def test_non_positive_batch_size_rejected(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        async def _publish(event: BaseOutboxModel) -> None:
            return None

        with pytest.raises(ValueError):
            OutboxRelay(outbox_db, model=_OutboxModel, publish=_publish, batch_size=0)
