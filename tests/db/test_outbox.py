"""Tests for the transactional outbox: model, save_with_outbox, relay."""

import asyncio
import functools
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

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


class _BrokerLike:
    """Stand-in with the shape of ``MessageBroker.publish(channel, message)``."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    async def publish(self, channel: Any, message: Any, **options: Any) -> None:
        self.calls.append((channel, message))


class TestPublishContract:
    """``OutboxRelay`` calls ``publish(event)`` with one argument.

    A broker's bound ``publish(channel, message)`` passed raw used to be
    accepted, then raised ``TypeError`` on every drain — swallowed by the
    retry path, so each event ended ``FAILED`` without being published.
    """

    def test_raw_two_argument_publish_is_refused_at_construction(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        broker = _BrokerLike()
        with pytest.raises(TypeError, match=r"publish\(event\)") as info:
            OutboxRelay(outbox_db, model=_OutboxModel, publish=broker.publish)
        assert "event.topic, event.payload" in str(info.value)

    def test_non_callable_is_refused(self, outbox_db: AsyncDatabaseManager) -> None:
        with pytest.raises(TypeError, match="callable"):
            OutboxRelay(outbox_db, model=_OutboxModel, publish="nope")  # type: ignore[arg-type]

    def test_one_argument_shapes_are_accepted(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        broker = _BrokerLike()

        async def wrapper(event: BaseOutboxModel) -> None:
            await broker.publish(event.topic, event.payload)

        class _Callable:
            async def __call__(self, event: BaseOutboxModel) -> None:
                return None

        async def two(topic: str, event: BaseOutboxModel) -> None:
            return None

        accepted: list[Any] = [
            wrapper,
            lambda e: broker.publish(e.topic, e.payload),
            functools.partial(two, "orders"),
            _Callable(),
            AsyncMock(),
        ]
        for publish in accepted:
            OutboxRelay(outbox_db, model=_OutboxModel, publish=publish)

    @pytest.mark.asyncio
    async def test_wrapped_broker_publishes_topic_then_payload(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        broker = _BrokerLike()

        async def publish(event: BaseOutboxModel) -> None:
            await broker.publish(event.topic, event.payload)

        async with outbox_db.get_session_context() as session:
            session.add(_OutboxModel.new_event("orders.paid", {"order_id": "1"}))
            await session.commit()

        relay = OutboxRelay(outbox_db, model=_OutboxModel, publish=publish)
        assert await relay.drain_once() == 1
        assert broker.calls == [("orders.paid", {"order_id": "1"})]


class TestAtLeastOnce:
    """Delivery is at-least-once: a published event can be published again."""

    @pytest.mark.asyncio
    async def test_drain_cut_short_after_publish_republishes_the_event(
        self, outbox_db: AsyncDatabaseManager
    ) -> None:
        """The ``SENT`` flip commits at the end of the batch, after publishing.

        A drain interrupted between the two — here a ``CancelledError`` on
        the second event, standing in for a worker killed mid-batch — rolls
        the flip back, so the first event, already handed to the broker,
        is still ``PENDING`` and the next drain publishes it again.
        """
        async with outbox_db.get_session_context() as session:
            session.add(_OutboxModel.new_event("a", {"n": 1}))
            await session.commit()
            session.add(_OutboxModel.new_event("b", {"n": 2}))
            await session.commit()

        seen: list[str] = []
        cancel_on: set[str] = {"b"}

        async def publish(event: BaseOutboxModel) -> None:
            seen.append(event.topic)
            if event.topic in cancel_on:
                raise asyncio.CancelledError

        relay = OutboxRelay(outbox_db, model=_OutboxModel, publish=publish)
        with pytest.raises(asyncio.CancelledError):
            await relay.drain_once()
        cancel_on.clear()
        assert await relay.drain_once() == 2
        assert seen == ["a", "b", "a", "b"]
