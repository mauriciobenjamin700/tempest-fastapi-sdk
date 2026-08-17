"""Per-subject AI accounting: what gets a row, and what the sums say.

The interesting behaviour is in what is **not** recorded. A call the
provider did not price, and a short-circuit that never reached the model,
both look like "a call happened" to naive code — and both would inflate the
call count and the active-user count with things that never cost anything.

The database is a file rather than ``:memory:`` for the same reason as
``tests/tasks/test_jobs.py``: the store opens a session per call, and
SQLAlchemy hands every in-memory SQLite engine one shared connection.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import AIUsageStore, BaseAIUsageModel, TokenUsage
from tempest_fastapi_sdk.utils.datetime import utcnow


class _UsageModel(BaseAIUsageModel):
    """Concrete usage table for these tests."""

    __tablename__ = "test_ai_usage"


@pytest_asyncio.fixture
async def usage_db(tmp_path: Path) -> AsyncGenerator[AsyncDatabaseManager]:
    """A file-backed database with the usage table created.

    Args:
        tmp_path (Path): pytest's per-test directory.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest.fixture
def store(usage_db: AsyncDatabaseManager) -> AIUsageStore[_UsageModel]:
    """A store with prices configured.

    Args:
        usage_db (AsyncDatabaseManager): The connected manager.

    Returns:
        AIUsageStore[_UsageModel]: The store under test.
    """
    return AIUsageStore(
        usage_db,
        model=_UsageModel,
        price_input_per_1k=0.00014,
        price_output_per_1k=0.00028,
    )


@pytest.fixture
def free_store(usage_db: AsyncDatabaseManager) -> AIUsageStore[_UsageModel]:
    """A store with no prices configured.

    Args:
        usage_db (AsyncDatabaseManager): The connected manager.

    Returns:
        AIUsageStore[_UsageModel]: A store that reports no cost.
    """
    return AIUsageStore(usage_db, model=_UsageModel)


class TestRecording:
    """What earns a row."""

    async def test_a_priced_call_is_recorded(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """The ordinary path stores what the provider reported."""
        subject = uuid4()

        row = await store.record(
            subject_id=subject,
            service="summary",
            usage=TokenUsage(100, 50, 150),
            model="deepseek-chat",
        )

        assert row is not None
        assert row.subject_id == subject
        assert row.service == "summary"
        assert row.model == "deepseek-chat"
        assert (row.input_tokens, row.output_tokens, row.total_tokens) == (100, 50, 150)

    async def test_no_usage_writes_no_row(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """ "The provider did not say" is not "the call was free".

        A zeroed row would count toward the call count and the active-user
        count while contributing nothing, which is worse than not counting
        the call.
        """
        assert await store.record(subject_id=uuid4(), service="s", usage=None) is None
        assert (await store.totals()).calls == 0

    async def test_zero_tokens_writes_no_row(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """Zero tokens is what a short-circuit looks like.

        A service that returns early — nothing to summarize — never reached
        the model, so there is no call to bill.
        """
        row = await store.record(
            subject_id=uuid4(),
            service="summary",
            usage=TokenUsage(0, 0, 0),
        )

        assert row is None
        assert (await store.totals()).calls == 0

    async def test_duration_is_recorded_without_tokens(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """Local inference costs wall-clock, not tokens."""
        row = await store.record_duration(
            subject_id=uuid4(),
            seconds=754.0,
            model="whisper-large-v3-turbo",
        )

        assert row.duration_seconds == 754.0
        assert row.total_tokens == 0
        assert row.service is None

    async def test_a_system_call_has_no_subject(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """Work with no owner is still worth counting."""
        row = await store.record(
            subject_id=None,
            service="sweep",
            usage=TokenUsage(10, 5, 15),
        )

        assert row is not None
        assert row.subject_id is None


class TestTotals:
    """Summing a window."""

    async def test_empty_window_is_zeros_not_none(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """ "Nobody used it" is an answer, not a missing one."""
        totals = await store.totals()

        assert totals.calls == 0
        assert totals.total_tokens == 0
        assert totals.duration_seconds == 0.0

    async def test_totals_sum_every_column(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """Tokens and duration are summed side by side, not mixed."""
        subject = uuid4()
        await store.record(
            subject_id=subject, service="summary", usage=TokenUsage(100, 50, 150)
        )
        await store.record(
            subject_id=subject, service="tasks", usage=TokenUsage(20, 10, 30)
        )
        await store.record_duration(subject_id=subject, seconds=60.0)

        totals = await store.totals()

        assert totals.input_tokens == 120
        assert totals.output_tokens == 60
        assert totals.total_tokens == 180
        assert totals.duration_seconds == 60.0
        assert totals.calls == 3

    async def test_totals_can_be_scoped_to_one_subject(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """Per-user accounting is the point of the table."""
        mine, theirs = uuid4(), uuid4()
        await store.record(
            subject_id=mine, service="summary", usage=TokenUsage(100, 50, 150)
        )
        await store.record(
            subject_id=theirs, service="summary", usage=TokenUsage(999, 999, 1998)
        )

        totals = await store.totals(subject_id=mine)

        assert totals.total_tokens == 150

    async def test_a_window_excludes_older_rows(
        self, store: AIUsageStore[_UsageModel], usage_db: AsyncDatabaseManager
    ) -> None:
        """The lower bound is applied, so "last 7 days" means that."""
        subject = uuid4()
        old = await store.record(
            subject_id=subject, service="summary", usage=TokenUsage(1, 1, 2)
        )
        assert old is not None
        async with usage_db.get_session_context() as session:
            await session.execute(
                update(_UsageModel)
                .where(_UsageModel.id == old.id)
                .values(created_at=utcnow() - timedelta(days=30)),
            )
            await session.commit()
        await store.record(
            subject_id=subject, service="summary", usage=TokenUsage(10, 5, 15)
        )

        recent = await store.totals(timedelta(days=7))

        assert recent.total_tokens == 15


class TestCost:
    """Pricing, which is never stored."""

    async def test_cost_is_computed_from_tokens(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """1000 in + 1000 out at the configured prices."""
        await store.record(
            subject_id=uuid4(),
            service="summary",
            usage=TokenUsage(1000, 1000, 2000),
        )

        totals = await store.totals()

        assert totals.cost == pytest.approx(0.00042)

    def test_a_single_call_does_not_round_to_nothing(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """The library rounds nowhere, because every precision is wrong somewhere.

        Token prices live around 0.0001 per 1000, so rounding to cents
        reports zero for nearly every single call, and even six decimals
        zeroes a one-token call — while a monthly total wants cents.
        Formatting belongs at the boundary that knows which it is showing.
        """
        assert store.estimate_cost(1000, 1000) == pytest.approx(0.00042)
        assert store.estimate_cost(1, 1) == pytest.approx(4.2e-07, rel=1e-9)
        assert store.estimate_cost(1, 1) > 0

    async def test_no_price_means_no_cost_not_zero(
        self, free_store: AIUsageStore[_UsageModel]
    ) -> None:
        """``None`` tells the interface to hide the cost, not to show 0.

        Showing a confident 0.00 for an unconfigured price is worse than
        showing nothing.
        """
        await free_store.record(
            subject_id=uuid4(),
            service="summary",
            usage=TokenUsage(1000, 1000, 2000),
        )

        assert (await free_store.totals()).cost is None

    async def test_changing_the_price_reprices_history(
        self, usage_db: AsyncDatabaseManager
    ) -> None:
        """A price correction applies to rows already written.

        This is why the price is not a column: the alternative leaves old
        rows priced with a number nobody remembers setting.
        """
        cheap = AIUsageStore(
            usage_db, model=_UsageModel, price_input_per_1k=0.001, price_output_per_1k=0
        )
        await cheap.record(
            subject_id=uuid4(), service="summary", usage=TokenUsage(1000, 0, 1000)
        )
        assert (await cheap.totals()).cost == pytest.approx(0.001)

        dearer = AIUsageStore(
            usage_db, model=_UsageModel, price_input_per_1k=0.002, price_output_per_1k=0
        )

        assert (await dearer.totals()).cost == pytest.approx(0.002)

    def test_negative_price_is_refused(self, usage_db: AsyncDatabaseManager) -> None:
        """A negative price would make spending look like income."""
        with pytest.raises(ValueError, match="negative"):
            AIUsageStore(usage_db, model=_UsageModel, price_input_per_1k=-1)


class TestBreakdowns:
    """The shapes an admin screen draws."""

    async def test_by_service_shares_add_up(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """Heaviest first, with each slice's percentage."""
        subject = uuid4()
        await store.record(
            subject_id=subject, service="summary", usage=TokenUsage(0, 0, 750)
        )
        await store.record(
            subject_id=subject, service="tasks", usage=TokenUsage(0, 0, 250)
        )

        rows = await store.by_service()

        assert [(r.service, r.total_tokens, r.share) for r in rows] == [
            ("summary", 750, 75.0),
            ("tasks", 250, 25.0),
        ]

    async def test_by_service_ignores_duration_rows(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """A token chart must not grow a 0% slice for local inference."""
        subject = uuid4()
        await store.record(
            subject_id=subject, service="summary", usage=TokenUsage(0, 0, 100)
        )
        await store.record_duration(subject_id=subject, seconds=999.0)

        rows = await store.by_service()

        assert [r.service for r in rows] == ["summary"]

    async def test_per_day_buckets_by_service(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """What a stacked bar chart reads."""
        subject = uuid4()
        await store.record(
            subject_id=subject, service="summary", usage=TokenUsage(0, 0, 10)
        )
        await store.record(
            subject_id=subject, service="tasks", usage=TokenUsage(0, 0, 20)
        )

        rows = await store.per_day()

        assert {(r.service, r.total_tokens) for r in rows} == {
            ("summary", 10),
            ("tasks", 20),
        }
        assert all(r.day == utcnow().date() for r in rows)

    async def test_per_day_without_service_split(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """One row per day when the caller does not want the split."""
        subject = uuid4()
        await store.record(
            subject_id=subject, service="summary", usage=TokenUsage(0, 0, 10)
        )
        await store.record(
            subject_id=subject, service="tasks", usage=TokenUsage(0, 0, 20)
        )

        rows = await store.per_day(by_service=False)

        assert len(rows) == 1
        assert rows[0].total_tokens == 30
        assert rows[0].service is None

    async def test_top_subjects_is_ordered_by_spend(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """ "Who is burning the budget" is the question this answers."""
        light, heavy = uuid4(), uuid4()
        await store.record(
            subject_id=light, service="summary", usage=TokenUsage(0, 0, 10)
        )
        await store.record(
            subject_id=heavy, service="summary", usage=TokenUsage(0, 0, 900)
        )

        rows = await store.top_subjects()

        assert [r.subject_id for r in rows] == [heavy, light]

    async def test_top_subjects_respects_the_limit(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """A dashboard asks for a top N, not the whole table."""
        for tokens in (10, 20, 30):
            await store.record(
                subject_id=uuid4(), service="s", usage=TokenUsage(0, 0, tokens)
            )

        assert len(await store.top_subjects(limit=2)) == 2

    async def test_zero_limit_is_refused(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """A limit below one would silently answer nothing."""
        with pytest.raises(ValueError, match="limit"):
            await store.top_subjects(limit=0)

    async def test_empty_breakdowns_are_empty_lists(
        self, store: AIUsageStore[_UsageModel]
    ) -> None:
        """Nothing matched is a successful query returning ``[]``."""
        assert await store.by_service() == []
        assert await store.per_day() == []
        assert await store.top_subjects() == []
