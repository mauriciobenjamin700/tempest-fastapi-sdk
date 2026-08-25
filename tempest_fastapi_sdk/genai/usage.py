"""What the AI actually cost, per user, kept in a table you can query.

:class:`~tempest_fastapi_sdk.genai.GenAIMetrics` already publishes token
counters to Prometheus. That answers "how is the fleet doing right now"; it
does not answer "which account burned the budget last month", because the
series is per-process, resets on deploy, and carries no user dimension —
adding one would make the cardinality unusable.

This is the other shape: **one row per paid call**, so the questions an
admin screen and an invoice ask become ordinary SQL.

* :class:`BaseAIUsageModel` — the abstract table; the project subclasses it
  and picks a ``__tablename__``, exactly like
  :class:`~tempest_fastapi_sdk.tasks.BaseJobModel`.
* :class:`AIUsageStore` — record a call, then aggregate: totals, per-day,
  per-service distribution, and the heaviest subjects.

Three decisions in here each cost somebody a discovery:

* **A call the provider did not price writes no row.** ``usage=None`` means
  "the response carried no usage", which is not the same as a free call. A
  zeroed row would count toward "active users" while contributing nothing,
  which is worse than not counting the call at all.
* **Price is never stored.** Cost is computed from the tokens at read time,
  so correcting a price fixes the whole history — no reprocessing, no rows
  disagreeing with each other about what a token was worth.
* **Local inference is recorded by duration, not tokens.** A model running
  on your own CPU has no token bill; what it consumes is wall-clock on
  hardware you are paying for either way. Recording zero tokens for it
  would make it invisible next to the hosted calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Float, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from sqlalchemy.sql import ColumnElement

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.genai.tokens import TokenUsage


class BaseAIUsageModel(BaseModel):
    """Abstract usage table — one row per recorded AI call.

    Inherits ``id`` / ``is_active`` / ``created_at`` / ``updated_at`` from
    :class:`~tempest_fastapi_sdk.db.model.BaseModel`, so ``created_at`` is
    when the call happened and is what every aggregation buckets on.

    Attributes:
        subject_id (UUID | None): Who the call is billed to — a user, a
            tenant, whatever the application charges. ``None`` for calls
            with no owner (a scheduled sweep, a system prompt), which is
            why the aggregations treat it as a real bucket rather than
            dropping it.
        service (str | None): Which capability spent it ("summary",
            "suggest_tasks"). ``None`` marks a row that carries duration
            instead of tokens, so the two never contaminate each other's
            sums.
        model (str | None): Which model answered, so a price change or a
            model swap stays attributable afterwards.
        input_tokens (int): Prompt tokens, as the provider reported them.
        output_tokens (int): Generated tokens.
        total_tokens (int): What the provider counts for the call — carried
            rather than summed, because cached-prefix discounts make the
            two disagree.
        duration_seconds (float | None): Wall-clock, for local inference
            that has no token bill.
        cache_hit_tokens (int | None): The slice of ``input_tokens`` the
            provider served from a cached prefix and billed cheaper — a
            subset, not an addition. Nullable rather than defaulted to
            zero on the column, because rows written before this column
            existed genuinely do not know their split, and claiming zero
            for them would price a discounted call at full rate.
    """

    __abstract__ = True

    subject_id: Mapped[UUID | None] = mapped_column(
        index=True,
        nullable=True,
        doc="Who the call is billed to; NULL for system calls.",
    )
    service: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        nullable=True,
        doc="Which capability spent it; NULL on duration-only rows.",
    )
    model: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        doc="Which model answered.",
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Prompt tokens.",
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Generated tokens.",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Tokens the provider bills for the call.",
    )
    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Wall-clock seconds, for local inference with no token bill.",
    )
    cache_hit_tokens: Mapped[int | None] = mapped_column(
        Integer,
        default=0,
        nullable=True,
        doc="Cached-prefix slice of input_tokens; NULL on pre-column rows.",
    )


def make_ai_usage_model(
    *,
    tablename: str = "ai_usage",
    class_name: str = "AIUsageModel",
) -> type[BaseAIUsageModel]:
    """Build a concrete usage model bound to ``tablename``.

    For tests and lightweight scripts; production code should subclass
    :class:`BaseAIUsageModel` by hand so Alembic picks it up statically.

    Args:
        tablename (str): The table name.
        class_name (str): The generated class name.

    Returns:
        type[BaseAIUsageModel]: The concrete model class.
    """
    return type(
        class_name,
        (BaseAIUsageModel,),
        {
            "__tablename__": tablename,
            "__module__": __name__,
            "__qualname__": class_name,
        },
    )


@dataclass(frozen=True)
class UsageTotals:
    """Aggregate consumption over a window.

    Attributes:
        input_tokens (int): Prompt tokens summed.
        output_tokens (int): Generated tokens summed.
        total_tokens (int): Billed tokens summed.
        duration_seconds (float): Local-inference seconds summed.
        calls (int): Rows counted.
        cost (float | None): Estimated spend, or ``None`` when no price is
            configured — which the interface should read as "do not show a
            cost", not as "it was free".
        cache_hit_tokens (int): Cached-prefix tokens summed — already part
            of ``input_tokens``, reported separately so a screen can show
            how much of the window the cache paid for.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    calls: int = 0
    cost: float | None = None
    cache_hit_tokens: int = 0


@dataclass(frozen=True)
class ServiceUsage:
    """One service's share of a window.

    Attributes:
        service (str | None): The capability.
        total_tokens (int): Tokens it spent.
        share (float): Percentage of the window's tokens, to one decimal.
    """

    service: str | None
    total_tokens: int
    share: float


@dataclass(frozen=True)
class DailyUsage:
    """One day of one service.

    Attributes:
        day (date): The UTC day.
        service (str | None): The capability, or ``None`` when the query
            did not split by service.
        total_tokens (int): Tokens spent that day.
    """

    day: date
    service: str | None
    total_tokens: int


@dataclass(frozen=True)
class SubjectUsage:
    """One subject's consumption in a window.

    Attributes:
        subject_id (UUID | None): Who.
        total_tokens (int): Tokens they spent.
    """

    subject_id: UUID | None
    total_tokens: int


UsageT = TypeVar("UsageT", bound=BaseAIUsageModel)


class AIUsageStore(Generic[UsageT]):
    """Record and aggregate AI consumption.

    Every method opens its own short session, like
    :class:`~tempest_fastapi_sdk.tasks.JobStore` — the recording call runs
    inside a worker as often as inside a request.

    Example:

        >>> store = AIUsageStore(db, model=UsageModel, price_input_per_1k=0.00014)
        >>> text, usage = await gen.generate_with_usage(prompt)
        >>> await store.record(subject_id=user.id, service="summary", usage=usage)
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        model: type[UsageT],
        price_input_per_1k: float = 0.0,
        price_output_per_1k: float = 0.0,
        price_cache_hit_per_1k: float | None = None,
    ) -> None:
        """Initialize the store.

        Args:
            db (AsyncDatabaseManager): The database manager.
            model (type[UsageT]): The concrete usage model.
            price_input_per_1k (float): Cost of 1000 prompt tokens, in
                whatever currency you report. Left at ``0.0``, cost comes
                back as ``None``.
            price_output_per_1k (float): Cost of 1000 generated tokens.
            price_cache_hit_per_1k (float | None): Cost of 1000 prompt
                tokens served from a cached prefix, when the provider bills
                those cheaper. ``None`` — the default — means "no separate
                cached rate", and prices the cached slice at
                ``price_input_per_1k`` like every other prompt token.

                ``None`` rather than ``0.0`` on purpose: ``0.0`` is a price,
                and defaulting to it would make an unconfigured store report
                cached tokens as free, understating every discounted call.
                An unconfigured price should never move a number.

        Raises:
            ValueError: When a price is negative.
        """
        if price_input_per_1k < 0 or price_output_per_1k < 0:
            raise ValueError("prices must not be negative")
        if price_cache_hit_per_1k is not None and price_cache_hit_per_1k < 0:
            raise ValueError("prices must not be negative")
        self._db = db
        self._model = model
        self.price_input_per_1k = price_input_per_1k
        self.price_output_per_1k = price_output_per_1k
        self.price_cache_hit_per_1k = price_cache_hit_per_1k

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_hit_tokens: int = 0,
    ) -> float | None:
        """Price a set of token counts.

        Deliberately not stored on the row: a price correction then applies
        to the whole history at once, instead of leaving old rows priced
        with a number nobody remembers setting.

        **Not rounded.** Any fixed precision is wrong at some scale here:
        token prices live around ``0.0001`` per 1000, so rounding to cents
        reports zero for almost every single call, and even six decimals
        zeroes a one-token call — while a monthly total wants cents. The
        library therefore returns the number and leaves formatting to the
        boundary, which knows whether it is showing one call or a month.

        Args:
            input_tokens (int): Prompt tokens, cached ones **included** —
                that is how providers report the field, so subtracting them
                here would double-discount.
            output_tokens (int): Generated tokens.
            cache_hit_tokens (int): How many of ``input_tokens`` came from a
                cached prefix. Priced at ``price_cache_hit_per_1k`` when one
                is configured; otherwise it changes nothing. Clamped to
                ``input_tokens``: a provider reporting more cached tokens
                than prompt tokens is nonsense, and letting it through would
                produce a negative full-rate term.

        Returns:
            float | None: The estimate, or ``None`` when neither price is
            configured — which the interface should read as "do not show a
            cost", never as zero.
        """
        if self.price_input_per_1k == 0 and self.price_output_per_1k == 0:
            return None
        cached = min(max(cache_hit_tokens, 0), input_tokens)
        cached_rate = (
            self.price_input_per_1k
            if self.price_cache_hit_per_1k is None
            else self.price_cache_hit_per_1k
        )
        return (
            (input_tokens - cached) / 1000 * self.price_input_per_1k
            + cached / 1000 * cached_rate
            + output_tokens / 1000 * self.price_output_per_1k
        )

    async def record(
        self,
        *,
        subject_id: UUID | None,
        service: str,
        usage: TokenUsage | None,
        model: str | None = None,
    ) -> UsageT | None:
        """Record one paid call.

        Args:
            subject_id (UUID | None): Who to bill.
            service (str): Which capability spent it.
            usage (TokenUsage | None): What the provider reported.
            model (str | None): Which model answered.

        Returns:
            UsageT | None: The stored row, or ``None`` when nothing was
            recorded — either the provider reported no usage, or it
            reported zero tokens, which is what a short-circuit that never
            called the model looks like. Writing either would inflate the
            call count with calls that did not happen.
        """
        if usage is None or usage.total_tokens == 0:
            return None
        async with self._db.get_session_context() as session:
            row = self._model(
                subject_id=subject_id,
                service=service,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                cache_hit_tokens=usage.cache_hit_tokens,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def record_duration(
        self,
        *,
        subject_id: UUID | None,
        seconds: float,
        model: str | None = None,
    ) -> UsageT:
        """Record local inference, which costs time rather than tokens.

        ``service`` stays ``NULL`` so these rows never land in a token sum
        or a per-service share: they belong to a different unit.

        Args:
            subject_id (UUID | None): Who to attribute it to.
            seconds (float): Wall-clock the work took, or the media
                duration processed — pick one and stay consistent.
            model (str | None): Which local model ran.

        Returns:
            UsageT: The stored row.
        """
        async with self._db.get_session_context() as session:
            row = self._model(
                subject_id=subject_id,
                service=None,
                model=model,
                duration_seconds=seconds,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    def _window(
        self,
        since: datetime | timedelta | None,
        subject_id: UUID | None,
    ) -> list[ColumnElement[bool]]:
        """Build the WHERE terms shared by every aggregation.

        Args:
            since (datetime | timedelta | None): Lower bound; a
                ``timedelta`` is read as "this long ago", ``None`` as all
                of history.
            subject_id (UUID | None): Restrict to one subject.

        Returns:
            list[ColumnElement[bool]]: The filter terms.
        """
        terms: list[ColumnElement[bool]] = []
        if isinstance(since, timedelta):
            since = utcnow() - since
        if since is not None:
            terms.append(self._model.created_at >= since)
        if subject_id is not None:
            terms.append(self._model.subject_id == subject_id)
        return terms

    async def totals(
        self,
        since: datetime | timedelta | None = None,
        *,
        subject_id: UUID | None = None,
    ) -> UsageTotals:
        """Sum a window.

        Args:
            since (datetime | timedelta | None): Lower bound.
            subject_id (UUID | None): Restrict to one subject.

        Returns:
            UsageTotals: The sums, with ``cost`` priced on the fly. An
            empty window returns zeros, not ``None`` — "nobody used it" is
            an answer.
        """
        terms = self._window(since, subject_id)
        async with self._db.get_session_context() as session:
            row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(self._model.input_tokens), 0),
                        func.coalesce(func.sum(self._model.output_tokens), 0),
                        func.coalesce(func.sum(self._model.total_tokens), 0),
                        func.coalesce(func.sum(self._model.duration_seconds), 0.0),
                        func.count(),
                        func.coalesce(func.sum(self._model.cache_hit_tokens), 0),
                    ).where(*terms),
                )
            ).one()
        return UsageTotals(
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
            total_tokens=int(row[2]),
            duration_seconds=float(row[3]),
            calls=int(row[4]),
            cost=self.estimate_cost(
                int(row[0]),
                int(row[1]),
                cache_hit_tokens=int(row[5]),
            ),
            cache_hit_tokens=int(row[5]),
        )

    async def by_service(
        self,
        since: datetime | timedelta | None = None,
        *,
        subject_id: UUID | None = None,
    ) -> list[ServiceUsage]:
        """Split a window by capability, heaviest first.

        Duration-only rows are excluded: they carry no tokens, so including
        them would add a slice worth 0% to every chart.

        Args:
            since (datetime | timedelta | None): Lower bound.
            subject_id (UUID | None): Restrict to one subject.

        Returns:
            list[ServiceUsage]: One entry per service, with its share of
            the window's tokens. Empty when nothing matched.
        """
        terms = [*self._window(since, subject_id), self._model.service.is_not(None)]
        async with self._db.get_session_context() as session:
            rows = (
                await session.execute(
                    select(
                        self._model.service,
                        func.coalesce(func.sum(self._model.total_tokens), 0),
                    )
                    .where(*terms)
                    .group_by(self._model.service)
                    .order_by(func.sum(self._model.total_tokens).desc()),
                )
            ).all()
        total = sum(int(r[1]) for r in rows)
        return [
            ServiceUsage(
                service=r[0],
                total_tokens=int(r[1]),
                share=round(100 * int(r[1]) / total, 1) if total else 0.0,
            )
            for r in rows
        ]

    async def per_day(
        self,
        since: datetime | timedelta | None = None,
        *,
        subject_id: UUID | None = None,
        by_service: bool = True,
    ) -> list[DailyUsage]:
        """Bucket a window by UTC day, oldest first.

        Args:
            since (datetime | timedelta | None): Lower bound.
            subject_id (UUID | None): Restrict to one subject.
            by_service (bool): Split each day by capability — what a
                stacked bar chart wants. ``False`` gives one row per day
                with ``service=None``.

        Returns:
            list[DailyUsage]: The buckets, oldest first.
        """
        terms = [*self._window(since, subject_id), self._model.service.is_not(None)]
        day = func.date(self._model.created_at)
        columns: list[Any] = [day]
        if by_service:
            columns.append(self._model.service)
        columns.append(func.coalesce(func.sum(self._model.total_tokens), 0))

        async with self._db.get_session_context() as session:
            query = select(*columns).where(*terms)
            query = query.group_by(*columns[:-1]).order_by(*columns[:-1])
            rows = (await session.execute(query)).all()

        return [
            DailyUsage(
                day=_as_date(row[0]),
                service=row[1] if by_service else None,
                total_tokens=int(row[-1]),
            )
            for row in rows
        ]

    async def top_subjects(
        self,
        since: datetime | timedelta | None = None,
        *,
        limit: int = 20,
    ) -> list[SubjectUsage]:
        """Who spent the most in a window.

        Args:
            since (datetime | timedelta | None): Lower bound.
            limit (int): How many to return.

        Returns:
            list[SubjectUsage]: Subjects ordered by tokens, heaviest first.

        Raises:
            ValueError: When ``limit`` is below 1.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        terms = [*self._window(since, None), self._model.service.is_not(None)]
        async with self._db.get_session_context() as session:
            rows = (
                await session.execute(
                    select(
                        self._model.subject_id,
                        func.coalesce(func.sum(self._model.total_tokens), 0),
                    )
                    .where(*terms)
                    .group_by(self._model.subject_id)
                    .order_by(func.sum(self._model.total_tokens).desc())
                    .limit(limit),
                )
            ).all()
        return [SubjectUsage(subject_id=r[0], total_tokens=int(r[1])) for r in rows]


def _as_date(value: Any) -> date:
    """Coerce a grouped day value to a ``date``.

    ``func.date`` gives a ``date`` on PostgreSQL and a ``str`` on SQLite,
    so the caller would otherwise get a different type per backend — the
    kind of difference that passes tests on one database and fails in
    production on the other.

    Args:
        value (Any): What the driver returned.

    Returns:
        date: The day.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


__all__: list[str] = [
    "AIUsageStore",
    "BaseAIUsageModel",
    "DailyUsage",
    "ServiceUsage",
    "SubjectUsage",
    "UsageTotals",
    "make_ai_usage_model",
]
