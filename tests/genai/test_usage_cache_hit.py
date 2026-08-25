"""The cached-prefix slice, from the provider's payload to the priced total.

A provider that serves part of a prompt from cache bills that part at a
fraction of the normal input rate — DeepSeek's published cached rate is two
orders of magnitude below its miss rate. Dropping the field does not lose a
nice-to-have statistic: it makes every cost estimate for a repeated prompt
wrong in the expensive direction, silently, and the total looks perfectly
plausible while it does.

Two spellings exist in the same OpenAI-compatible family, so both are read.
Handling only one is worse than handling neither: it works in the provider
you tested against and quietly overcharges in the other.

The database is a file rather than ``:memory:`` for the same reason as
``tests/genai/test_usage.py``: the store opens a session per call, and
SQLAlchemy hands every in-memory SQLite engine one shared connection.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import AIUsageStore, BaseAIUsageModel, TokenUsage


class _CacheUsageModel(BaseAIUsageModel):
    """Concrete usage table for these tests."""

    __tablename__ = "test_cache_hit_usage"


@pytest_asyncio.fixture
async def usage_db(tmp_path: Path) -> AsyncGenerator[AsyncDatabaseManager]:
    """A file-backed database with the usage table created.

    Args:
        tmp_path (Path): pytest's per-test directory.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


class TestFromPayload:
    """Both spellings of the cached count, read off the wire."""

    def test_reads_the_deepseek_spelling(self) -> None:
        """DeepSeek reports a flat ``prompt_cache_hit_tokens``."""
        usage = TokenUsage.from_payload(
            {
                "prompt_tokens": 3000,
                "completion_tokens": 800,
                "total_tokens": 3800,
                "prompt_cache_hit_tokens": 2560,
            },
        )
        assert usage is not None
        assert usage.cache_hit_tokens == 2560
        assert usage.input_tokens == 3000

    def test_reads_the_openai_spelling(self) -> None:
        """OpenAI nests it under ``prompt_tokens_details.cached_tokens``."""
        usage = TokenUsage.from_payload(
            {
                "prompt_tokens": 3000,
                "completion_tokens": 800,
                "total_tokens": 3800,
                "prompt_tokens_details": {"cached_tokens": 1024},
            },
        )
        assert usage is not None
        assert usage.cache_hit_tokens == 1024

    def test_absent_means_zero_not_none(self) -> None:
        """A provider with no prompt cache reports nothing, and owes nothing.

        Zero is honest here, unlike for ``usage`` as a whole: "no tokens came
        from cache" is a fact, while "the provider said nothing about cost"
        is not a zero.
        """
        usage = TokenUsage.from_payload(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        assert usage is not None
        assert usage.cache_hit_tokens == 0

    def test_a_non_integer_is_ignored(self) -> None:
        """Garbage in the field does not become a token count."""
        usage = TokenUsage.from_payload(
            {"prompt_tokens": 10, "prompt_cache_hit_tokens": "many"},
        )
        assert usage is not None
        assert usage.cache_hit_tokens == 0


class TestTokenUsageArithmetic:
    """Summing legs of a map-reduce keeps the cached slice."""

    def test_add_sums_the_cached_slice(self) -> None:
        """A summary made of three calls reports all three cached slices."""
        total = (
            TokenUsage(1000, 200, 1200, 800)
            + TokenUsage(1000, 200, 1200, 900)
            + TokenUsage(500, 100, 600, 0)
        )
        assert total.cache_hit_tokens == 1700
        assert total.input_tokens == 2500

    def test_positional_construction_still_works(self) -> None:
        """The field is appended, so existing three-argument calls are intact."""
        usage = TokenUsage(10, 20, 30)
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
            10,
            20,
            30,
        )
        assert usage.cache_hit_tokens == 0


class TestEstimateCost:
    """Where the cached rate does and does not move the number."""

    def test_a_cached_slice_is_cheaper(self, usage_db: AsyncDatabaseManager) -> None:
        """The discount applies to the cached tokens and nothing else."""
        store: AIUsageStore[_CacheUsageModel] = AIUsageStore(
            usage_db,
            model=_CacheUsageModel,
            price_input_per_1k=0.00014,
            price_output_per_1k=0.00028,
            price_cache_hit_per_1k=0.0000028,
        )
        full = store.estimate_cost(1000, 0)
        discounted = store.estimate_cost(1000, 0, cache_hit_tokens=1000)
        assert full == pytest.approx(0.00014)
        assert discounted == pytest.approx(0.0000028)

    def test_an_unconfigured_cached_rate_changes_nothing(
        self,
        usage_db: AsyncDatabaseManager,
    ) -> None:
        """The guard on defaulting the cached price to ``0.0``.

        A ``0.0`` default reads as a configured price of zero, which would
        make the cached slice free and understate every discounted call. The
        default has to leave the estimate exactly where it was.
        """
        store: AIUsageStore[_CacheUsageModel] = AIUsageStore(
            usage_db,
            model=_CacheUsageModel,
            price_input_per_1k=0.00014,
            price_output_per_1k=0.00028,
        )
        assert store.price_cache_hit_per_1k is None
        assert store.estimate_cost(1000, 0, cache_hit_tokens=1000) == pytest.approx(
            store.estimate_cost(1000, 0),
        )

    def test_more_cached_than_prompt_is_clamped(
        self,
        usage_db: AsyncDatabaseManager,
    ) -> None:
        """A nonsensical report must not produce a negative full-rate term."""
        store: AIUsageStore[_CacheUsageModel] = AIUsageStore(
            usage_db,
            model=_CacheUsageModel,
            price_input_per_1k=0.00014,
            price_output_per_1k=0.0,
            price_cache_hit_per_1k=0.0000028,
        )
        cost = store.estimate_cost(100, 0, cache_hit_tokens=10_000)
        assert cost == pytest.approx(100 / 1000 * 0.0000028)

    def test_a_negative_cached_price_is_refused(
        self,
        usage_db: AsyncDatabaseManager,
    ) -> None:
        """Negative prices are refused at construction, like the other two."""
        with pytest.raises(ValueError, match="negative"):
            AIUsageStore(
                usage_db,
                model=_CacheUsageModel,
                price_cache_hit_per_1k=-1.0,
            )


class TestRecordAndTotals:
    """The field survives the round trip through the table."""

    async def test_record_stores_the_cached_slice(
        self,
        usage_db: AsyncDatabaseManager,
    ) -> None:
        """What the provider reported is what the row holds."""
        store: AIUsageStore[_CacheUsageModel] = AIUsageStore(
            usage_db,
            model=_CacheUsageModel,
        )
        row = await store.record(
            subject_id=uuid4(),
            service="summary",
            usage=TokenUsage(3000, 800, 3800, 2560),
        )
        assert row is not None
        assert row.cache_hit_tokens == 2560

    async def test_totals_sum_and_price_the_cached_slice(
        self,
        usage_db: AsyncDatabaseManager,
    ) -> None:
        """The window reports the cached tokens, and bills them cheaper.

        Two calls, 2000 prompt tokens each, 1500 of each served from cache.
        At the discounted rate the window costs strictly less than the same
        window priced without the discount — which is the whole point.
        """
        store: AIUsageStore[_CacheUsageModel] = AIUsageStore(
            usage_db,
            model=_CacheUsageModel,
            price_input_per_1k=0.00014,
            price_output_per_1k=0.00028,
            price_cache_hit_per_1k=0.0000028,
        )
        owner = uuid4()
        for _ in range(2):
            await store.record(
                subject_id=owner,
                service="summary",
                usage=TokenUsage(2000, 400, 2400, 1500),
            )

        totals = await store.totals()
        assert totals.input_tokens == 4000
        assert totals.cache_hit_tokens == 3000
        assert totals.cost is not None

        undiscounted = 4000 / 1000 * 0.00014 + 800 / 1000 * 0.00028
        expected = (
            1000 / 1000 * 0.00014 + 3000 / 1000 * 0.0000028 + 800 / 1000 * 0.00028
        )
        assert totals.cost == pytest.approx(expected)
        assert totals.cost < undiscounted

    async def test_an_empty_window_reports_zero(
        self,
        usage_db: AsyncDatabaseManager,
    ) -> None:
        """No rows must read as zero, not blow up on a ``NULL`` sum.

        ``SUM`` over no rows is ``NULL``, and the totals are built by
        casting each column to ``int`` — so without the ``COALESCE`` a
        fresh install's cost screen raises on its first render, which is
        exactly when nobody is watching the logs.
        """
        store: AIUsageStore[_CacheUsageModel] = AIUsageStore(
            usage_db,
            model=_CacheUsageModel,
        )
        totals = await store.totals()
        assert totals.cache_hit_tokens == 0
        assert totals.calls == 0

    async def test_a_duration_row_carries_no_cached_slice(
        self,
        usage_db: AsyncDatabaseManager,
    ) -> None:
        """Local inference has no cached slice, and must not invent one.

        ``record_duration`` writes no token column at all. Were it ever to
        start filling this one, the cached total would grow with work that
        never touched a provider cache.
        """
        store: AIUsageStore[_CacheUsageModel] = AIUsageStore(
            usage_db,
            model=_CacheUsageModel,
        )
        owner = uuid4()
        await store.record_duration(subject_id=owner, seconds=42.0)
        await store.record(
            subject_id=owner,
            service="summary",
            usage=TokenUsage(100, 20, 120, 64),
        )

        totals = await store.totals()
        assert totals.cache_hit_tokens == 64
        assert totals.duration_seconds == pytest.approx(42.0)
