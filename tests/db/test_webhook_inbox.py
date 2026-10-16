"""Tests for ``BaseWebhookEventModel`` + ``WebhookInbox``.

Each class pins one rule the inbox exists for: a repeated delivery records
nothing, two workers cannot both claim, the handler's effect commits with the
mark, and a failing event stops at ``max_attempts`` instead of being retried
forever.

The database is a file rather than ``:memory:``: the race tests need two
connections seeing the same rows.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import String, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db import (
    STALE_WEBHOOK_ERROR,
    AsyncDatabaseManager,
    BaseModel,
    BaseWebhookEventModel,
    WebhookEventStatusEnum,
    WebhookInbox,
    make_webhook_event_model,
)
from tempest_fastapi_sdk.utils.datetime import utcnow


class _InboxModel(BaseWebhookEventModel):
    """Concrete inbox table for these tests."""

    __tablename__ = "test_webhook_inbox_events"


class _EffectModel(BaseModel):
    """A business row the handler writes, to observe atomicity."""

    __tablename__ = "test_webhook_inbox_effects"

    external_id: Mapped[str] = mapped_column(String(255), nullable=False)


@pytest_asyncio.fixture
async def inbox_db(tmp_path: Path) -> AsyncGenerator[AsyncDatabaseManager]:
    """A file-backed database with the inbox and effect tables."""
    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'inbox.db'}")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest.fixture
def inbox(inbox_db: AsyncDatabaseManager) -> WebhookInbox[_InboxModel]:
    """An inbox with no backoff, so a retry is due immediately."""
    return WebhookInbox(
        inbox_db, model=_InboxModel, max_attempts=3, backoff_base_seconds=0
    )


async def _count(db: AsyncDatabaseManager, model: type[Any]) -> int:
    """Count rows of ``model``."""
    async with db.get_session_context() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )


async def _write_effect(event: _InboxModel, session: AsyncSession) -> None:
    """Handler that writes one business row per processed event."""
    session.add(_EffectModel(external_id=event.external_id))


async def _explode(event: _InboxModel, session: AsyncSession) -> None:
    """Handler that writes, then raises — the write must roll back."""
    session.add(_EffectModel(external_id=event.external_id))
    await session.flush()
    raise RuntimeError("downstream is down")


class TestRecord:
    """The route writes once per distinct event."""

    async def test_a_new_event_is_received(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {"a": 1})

        assert event_id is not None
        row = await inbox.get(event_id)
        assert row is not None
        assert row.status == WebhookEventStatusEnum.RECEIVED.value
        assert row.payload == {"a": 1}
        assert row.attempts == 0
        assert row.max_attempts == 3

    async def test_a_repeated_delivery_records_nothing(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        first = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        second = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})

        assert first is not None
        assert second is None
        assert await _count(inbox_db, _InboxModel) == 1

    async def test_the_same_id_from_another_provider_is_another_event(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        assert await inbox.record("revenuecat", "evt-1", "X", {}) is not None
        assert await inbox.record("stripe", "evt-1", "X", {}) is not None

    async def test_concurrent_deliveries_record_one_row(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        results = await asyncio.gather(
            *(inbox.record("revenuecat", "evt-race", "RENEWAL", {}) for _ in range(5))
        )

        assert sum(result is not None for result in results) == 1
        assert await _count(inbox_db, _InboxModel) == 1

    async def test_an_empty_external_id_is_refused(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        with pytest.raises(ValueError, match="external_id"):
            await inbox.record("revenuecat", "", "RENEWAL", {})


class TestClaim:
    """Exactly one worker gets an event."""

    async def test_two_claims_one_winner(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None

        first, second = await asyncio.gather(
            inbox.claim(event_id), inbox.claim(event_id)
        )

        winners = [row for row in (first, second) if row is not None]
        assert len(winners) == 1
        assert winners[0].status == WebhookEventStatusEnum.PROCESSING.value
        assert winners[0].attempts == 1

    async def test_two_workers_processing_the_same_id_apply_one_effect(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None

        outcomes = await asyncio.gather(
            inbox.process(event_id, _write_effect),
            inbox.process(event_id, _write_effect),
        )

        assert sorted(str(outcome) for outcome in outcomes) == ["None", "processed"]
        assert await _count(inbox_db, _EffectModel) == 1

    async def test_an_unknown_id_is_not_claimable(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        assert await inbox.claim(uuid4()) is None


class TestProcess:
    """The handler's effect and the mark commit together."""

    async def test_success_marks_processed_and_keeps_the_effect(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None

        outcome = await inbox.process(event_id, _write_effect)

        assert outcome is WebhookEventStatusEnum.PROCESSED
        row = await inbox.get(event_id)
        assert row is not None
        assert row.status == WebhookEventStatusEnum.PROCESSED.value
        assert row.processed_at is not None
        assert await _count(inbox_db, _EffectModel) == 1

    async def test_a_processed_event_is_not_processed_again(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None
        await inbox.process(event_id, _write_effect)

        assert await inbox.process(event_id, _write_effect) is None
        assert await _count(inbox_db, _EffectModel) == 1

    async def test_a_failing_handler_rolls_its_effect_back(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None

        outcome = await inbox.process(event_id, _explode)

        assert outcome is WebhookEventStatusEnum.RECEIVED
        row = await inbox.get(event_id)
        assert row is not None
        assert row.last_error == "RuntimeError: downstream is down"
        assert await _count(inbox_db, _EffectModel) == 0

    async def test_a_row_reclaimed_mid_handler_rolls_the_effect_back(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None

        async def reclaimed_meanwhile(
            event: _InboxModel, session: AsyncSession
        ) -> None:
            session.add(_EffectModel(external_id=event.external_id))
            async with inbox_db.get_session_context() as other:
                await other.execute(
                    update(_InboxModel)
                    .where(_InboxModel.id == event_id)
                    .values(status=WebhookEventStatusEnum.RECEIVED.value)
                )

        assert await inbox.process(event_id, reclaimed_meanwhile) is None
        assert await _count(inbox_db, _EffectModel) == 0


class TestAttemptBudget:
    """A poisoned event stops at ``max_attempts``."""

    async def test_an_event_failing_max_attempts_times_becomes_failed(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None

        outcomes = [await inbox.process(event_id, _explode) for _ in range(3)]

        assert outcomes == [
            WebhookEventStatusEnum.RECEIVED,
            WebhookEventStatusEnum.RECEIVED,
            WebhookEventStatusEnum.FAILED,
        ]
        row = await inbox.get(event_id)
        assert row is not None
        assert row.status == WebhookEventStatusEnum.FAILED.value
        assert row.attempts == 3

    async def test_a_failed_event_leaves_the_sweep(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None
        for _ in range(3):
            await inbox.drain_once(_explode)

        assert await inbox.due() == []
        report = await inbox.drain_once(_write_effect)
        assert report.processed == []
        assert await inbox.process(event_id, _write_effect) is None

    async def test_backoff_keeps_a_failed_attempt_out_of_the_sweep(
        self, inbox_db: AsyncDatabaseManager
    ) -> None:
        inbox = WebhookInbox(inbox_db, model=_InboxModel, backoff_base_seconds=60)
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None

        await inbox.process(event_id, _explode)

        assert await inbox.due() == []
        row = await inbox.get(event_id)
        assert row is not None
        assert row.available_at > utcnow() + timedelta(seconds=50)

    async def test_the_backoff_doubles_per_attempt(
        self, inbox_db: AsyncDatabaseManager
    ) -> None:
        inbox = WebhookInbox(inbox_db, model=_InboxModel, backoff_base_seconds=60)
        event_id = await inbox.record("revenuecat", "evt-1", "RENEWAL", {})
        assert event_id is not None
        await inbox.process(event_id, _explode)
        async with inbox_db.get_session_context() as session:
            await session.execute(
                update(_InboxModel)
                .where(_InboxModel.id == event_id)
                .values(available_at=utcnow())
            )

        await inbox.process(event_id, _explode)

        row = await inbox.get(event_id)
        assert row is not None
        assert row.available_at > utcnow() + timedelta(seconds=110)

    async def test_a_drain_reports_each_outcome(
        self, inbox: WebhookInbox[_InboxModel]
    ) -> None:
        good = await inbox.record("revenuecat", "evt-good", "RENEWAL", {})
        bad = await inbox.record("revenuecat", "evt-bad", "RENEWAL", {})

        async def handler(event: _InboxModel, session: AsyncSession) -> None:
            if event.external_id == "evt-bad":
                raise ValueError("nope")

        report = await inbox.drain_once(handler)

        assert report.processed == [good]
        assert report.retrying == [bad]


class TestReclaimStale:
    """A worker that died holding a claim does not strand the event."""

    async def _claim_long_ago(
        self, inbox: WebhookInbox[_InboxModel], db: AsyncDatabaseManager, attempts: int
    ) -> UUID:
        event_id = await inbox.record("revenuecat", f"evt-{attempts}", "RENEWAL", {})
        assert event_id is not None
        async with db.get_session_context() as session:
            await session.execute(
                update(_InboxModel)
                .where(_InboxModel.id == event_id)
                .values(
                    status=WebhookEventStatusEnum.PROCESSING.value,
                    started_at=utcnow() - timedelta(hours=1),
                    attempts=attempts,
                )
            )
        return event_id

    async def test_a_stale_claim_is_readmitted(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        event_id = await self._claim_long_ago(inbox, inbox_db, attempts=1)

        report = await inbox.reclaim_stale()

        assert report.retrying == [event_id]
        assert await inbox.due() == [event_id]

    async def test_a_stale_claim_with_no_budget_left_fails(
        self, inbox: WebhookInbox[_InboxModel], inbox_db: AsyncDatabaseManager
    ) -> None:
        event_id = await self._claim_long_ago(inbox, inbox_db, attempts=3)

        report = await inbox.reclaim_stale()

        assert report.failed == [event_id]
        row = await inbox.get(event_id)
        assert row is not None
        assert row.status == WebhookEventStatusEnum.FAILED.value
        assert row.last_error == STALE_WEBHOOK_ERROR

    async def test_reclaim_is_off_without_stale_after(
        self, inbox_db: AsyncDatabaseManager
    ) -> None:
        inbox = WebhookInbox(inbox_db, model=_InboxModel, stale_after=None)
        await self._claim_long_ago(inbox, inbox_db, attempts=1)

        report = await inbox.reclaim_stale()

        assert report.retrying == []
        assert report.failed == []


class TestConstruction:
    """Misconfiguration fails at startup, not on the first event."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_attempts": 0},
            {"backoff_base_seconds": -1},
            {"stale_after": 0},
        ],
    )
    def test_invalid_settings_are_refused(
        self, inbox_db: AsyncDatabaseManager, kwargs: dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError):
            WebhookInbox(inbox_db, model=_InboxModel, **kwargs)

    def test_the_factory_declares_the_idempotency_constraint(self) -> None:
        model = make_webhook_event_model(
            tablename="test_webhook_inbox_factory", class_name="FactoryInbox"
        )

        uniques = [
            sorted(column.name for column in constraint.columns)
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ]
        assert ["external_id", "provider"] in uniques
