"""Tests for tempest_fastapi_sdk.db.explain.

Capture, filtering and reporting run for real against SQLite. The
PostgreSQL-only parsing (``FORMAT JSON`` costs and measured times) is
tested against a recorded payload, since there is no server here — and
the write-safety rule, which is the one that could corrupt data if it
regressed, is tested by counting rows rather than by inspecting SQL.
"""

import pytest
from sqlalchemy import String, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.db.explain import (
    POSTGRESQL_ANALYZE_PREFIX,
    POSTGRESQL_PLAN_PREFIX,
    SQLITE_PLAN_PREFIX,
    ExplainDetail,
    ExplainReport,
    QueryPlan,
    _postgresql_plan,
    _prefix_for,
    explain_queries,
)

PG_PAYLOAD: list[dict[str, object]] = [
    {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "note_for_explain_test",
            "Total Cost": 431.25,
            "Plan Rows": 20,
            "Actual Total Time": 12.75,
            "Actual Rows": 1904,
        },
    },
]


class Note(BaseModel):
    __tablename__ = "note_for_explain_test"

    title: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture
def notes(session: AsyncSession) -> BaseRepository[Note]:
    return BaseRepository(session, model=Note)


class TestCapture:
    async def test_captures_one_plan_per_statement(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        async with explain_queries(session) as report:
            await notes.list()
            await notes.list(filters={"title": "x"})
        assert len(report) == 2

    async def test_report_is_empty_until_the_block_exits(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        """Explaining during the block would perturb what is measured."""
        async with explain_queries(session) as report:
            await notes.list()
            assert len(report) == 0
        assert len(report) == 1

    async def test_nothing_is_captured_after_the_block(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        async with explain_queries(session) as report:
            await notes.list()
        await notes.list()
        assert len(report) == 1

    async def test_plans_survive_an_exception(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        """The erroring query is exactly the one you want the plan for."""
        with pytest.raises(RuntimeError):
            async with explain_queries(session) as report:
                await notes.list()
                raise RuntimeError("boom")
        assert len(report) == 1

    async def test_another_session_is_not_captured(
        self, notes: BaseRepository[Note], db: object, session: AsyncSession
    ) -> None:
        async with (
            explain_queries(session) as report,
            db.get_session_context() as other,  # type: ignore[attr-defined]
        ):
            other_repo: BaseRepository[Note] = BaseRepository(other, model=Note)
            await other_repo.list()
        assert len(report) == 0


class TestSqliteOutput:
    async def test_detail_is_plan_only(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        """SQLite reports neither cost nor timing; say so, don't invent."""
        async with explain_queries(session) as report:
            await notes.list()
        plan = report.plans[0]
        assert plan.detail is ExplainDetail.PLAN_ONLY
        assert plan.total_cost is None
        assert plan.duration_ms is None

    async def test_plan_text_describes_the_scan(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        async with explain_queries(session) as report:
            await notes.list()
        assert "note_for_explain_test" in report.plans[0].plan_text

    async def test_summary_mentions_the_detail_level(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        async with explain_queries(session) as report:
            await notes.list()
        assert "[plan_only]" in report.plans[0].summary()


class TestWritesAreNeverReExecuted:
    async def test_an_insert_is_not_performed_twice(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        """The defect that would make this tool destructive."""
        async with explain_queries(session):
            await notes.add(Note(title="only once"))
        assert await notes.count() == 1

    async def test_an_update_is_not_applied_twice(
        self, notes: BaseRepository[Note], session: AsyncSession
    ) -> None:
        created = await notes.add(Note(title="before"))
        async with explain_queries(session):
            await notes.bulk_update({"id": created.id}, {"title": "after"})
        rows = await notes.list()
        assert [row.title for row in rows] == ["after"]

    def test_a_select_is_analyzed_on_postgresql(self) -> None:
        prefix, detail = _prefix_for(select(Note), dialect="postgresql", analyze=True)
        assert prefix == POSTGRESQL_ANALYZE_PREFIX
        assert detail is ExplainDetail.MEASURED

    def test_a_write_is_never_analyzed_on_postgresql(self) -> None:
        prefix, detail = _prefix_for(
            Note.__table__.insert(), dialect="postgresql", analyze=True
        )
        assert prefix == POSTGRESQL_PLAN_PREFIX
        assert detail is ExplainDetail.ESTIMATED

    def test_raw_text_is_classified_by_its_keyword(self) -> None:
        read, _ = _prefix_for(text("  SELECT 1"), dialect="postgresql", analyze=True)
        write, _ = _prefix_for(
            text("DELETE FROM t"), dialect="postgresql", analyze=True
        )
        assert read == POSTGRESQL_ANALYZE_PREFIX
        assert write == POSTGRESQL_PLAN_PREFIX

    def test_analyze_false_keeps_everything_to_estimates(self) -> None:
        prefix, detail = _prefix_for(select(Note), dialect="postgresql", analyze=False)
        assert prefix == POSTGRESQL_PLAN_PREFIX
        assert detail is ExplainDetail.ESTIMATED

    def test_sqlite_never_executes_what_it_explains(self) -> None:
        prefix, _ = _prefix_for(select(Note), dialect="sqlite", analyze=True)
        assert prefix == SQLITE_PLAN_PREFIX

    def test_an_unsupported_backend_yields_no_prefix(self) -> None:
        prefix, _ = _prefix_for(select(Note), dialect="mysql", analyze=True)
        assert prefix is None


class TestPostgresParsing:
    def test_reads_cost_time_and_rows(self) -> None:
        plan = _postgresql_plan("SELECT 1", [(PG_PAYLOAD,)], ExplainDetail.MEASURED)
        assert plan.total_cost == 431.25
        assert plan.duration_ms == 12.75
        assert plan.rows == 1904

    def test_accepts_json_delivered_as_text(self) -> None:
        """Some drivers hand back the JSON already decoded, some do not."""
        import json

        plan = _postgresql_plan(
            "SELECT 1", [(json.dumps(PG_PAYLOAD),)], ExplainDetail.MEASURED
        )
        assert plan.total_cost == 431.25

    def test_estimated_rows_are_used_when_nothing_was_measured(self) -> None:
        payload = [{"Plan": {"Total Cost": 10.0, "Plan Rows": 7}}]
        plan = _postgresql_plan("SELECT 1", [(payload,)], ExplainDetail.ESTIMATED)
        assert plan.rows == 7
        assert plan.duration_ms is None

    def test_missing_metrics_stay_none(self) -> None:
        """Defaulting to zero would read as 'free'."""
        plan = _postgresql_plan("SELECT 1", [([{"Plan": {}}],)], ExplainDetail.MEASURED)
        assert plan.total_cost is None
        assert plan.duration_ms is None
        assert plan.rows is None

    def test_empty_output_does_not_raise(self) -> None:
        plan = _postgresql_plan("SELECT 1", [], ExplainDetail.MEASURED)
        assert plan.total_cost is None


class TestReportAggregates:
    def _report(self) -> ExplainReport:
        """Build a report with a mix of timed and untimed plans.

        Returns:
            ExplainReport: The fixture report.
        """
        return ExplainReport(
            backend="postgresql",
            plans=[
                QueryPlan(
                    sql="a",
                    detail=ExplainDetail.MEASURED,
                    plan_text="",
                    duration_ms=3.0,
                    total_cost=10.0,
                ),
                QueryPlan(
                    sql="b",
                    detail=ExplainDetail.MEASURED,
                    plan_text="",
                    duration_ms=9.0,
                    total_cost=2.0,
                ),
            ],
        )

    def test_slowest_prefers_measured_time(self) -> None:
        slowest = self._report().slowest
        assert slowest is not None
        assert slowest.sql == "b"

    def test_slowest_falls_back_to_cost(self) -> None:
        """On a backend that times nothing, cost still ranks the plans."""
        report = ExplainReport(
            plans=[
                QueryPlan(
                    sql="a",
                    detail=ExplainDetail.ESTIMATED,
                    plan_text="",
                    total_cost=1.0,
                ),
                QueryPlan(
                    sql="b",
                    detail=ExplainDetail.ESTIMATED,
                    plan_text="",
                    total_cost=5.0,
                ),
            ],
        )
        slowest = report.slowest
        assert slowest is not None
        assert slowest.sql == "b"

    def test_slowest_is_none_without_metrics(self) -> None:
        report = ExplainReport(
            plans=[QueryPlan(sql="a", detail=ExplainDetail.PLAN_ONLY, plan_text="")],
        )
        assert report.slowest is None

    def test_total_duration_sums_the_timed_plans(self) -> None:
        assert self._report().total_duration_ms == 12.0

    def test_total_duration_is_none_without_timings(self) -> None:
        report = ExplainReport(
            plans=[QueryPlan(sql="a", detail=ExplainDetail.PLAN_ONLY, plan_text="")],
        )
        assert report.total_duration_ms is None

    def test_report_renders_one_line_per_plan(self) -> None:
        assert len(self._report().report().splitlines()) == 2

    def test_empty_report_says_so(self) -> None:
        assert ExplainReport().report() == "no statements captured"

    def test_long_sql_is_truncated_in_the_summary(self) -> None:
        plan = QueryPlan(
            sql="SELECT " + "x" * 300,
            detail=ExplainDetail.PLAN_ONLY,
            plan_text="",
        )
        assert plan.summary().endswith("...")


class TestRepositorySugar:
    async def test_explain_binds_the_repository_session(
        self, notes: BaseRepository[Note]
    ) -> None:
        async with notes.explain() as report:
            await notes.list()
        assert len(report) == 1
