"""Tests for ``PhasePlan`` + ``ProgressTracker`` and the row they write to.

A progress bar is a claim about work, so these pin the two ways the claim
can be a lie: a number that moves backwards, and a phase that reports
itself finished before it is. The interpolation is checked against an
injected clock rather than by sleeping, so the arithmetic is what is
under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import (
    BaseJobModel,
    JobStatus,
    JobStore,
    Phase,
    PhasePlan,
    ProgressTracker,
    StageInterruptedError,
)


class _JobModel(BaseJobModel):
    """Concrete job table for these tests."""

    __tablename__ = "test_progress_jobs"


@pytest_asyncio.fixture
async def jobs_db(tmp_path: Path) -> AsyncGenerator[AsyncDatabaseManager]:
    """A file-backed database with the job table created."""
    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'progress.db'}")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest.fixture
def store(jobs_db: AsyncDatabaseManager) -> JobStore[_JobModel]:
    """A store over the test job table."""
    return JobStore(jobs_db, model=_JobModel, stale_after=60.0)


@pytest.fixture
def plan() -> PhasePlan:
    """Three phases whose weights are the measured medians."""
    return PhasePlan.from_seconds(
        {"pdf": 1.0, "table": 30.0, "reading": 19.0},
        per_kilochar={"table": 0.5},
    )


class _Sink:
    """A progress sink that records instead of writing to a database."""

    def __init__(self, *, cancel_after: int | None = None) -> None:
        """Build the sink.

        Args:
            cancel_after (int | None): Report the job cancelled once this
                many checks have been answered.
        """
        self.writes: list[tuple[float, str | None]] = []
        self.checks: int = 0
        self._cancel_after: int | None = cancel_after

    async def report_progress(
        self,
        job_id: UUID,
        *,
        progress: float,
        stage: str | None = None,
    ) -> bool:
        """Record a write.

        Args:
            job_id (UUID): Ignored; there is one job here.
            progress (float): What the tracker wanted to write.
            stage (str | None): The phase it named.

        Returns:
            bool: Always ``True``.
        """
        self.writes.append((progress, stage))
        return True

    async def is_cancelled(self, job_id: UUID) -> bool:
        """Answer the tracker's cancellation check.

        Args:
            job_id (UUID): Ignored; there is one job here.

        Returns:
            bool: ``True`` once the configured number of checks passed.
        """
        self.checks += 1
        return self._cancel_after is not None and self.checks >= self._cancel_after


class _Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now: float = 0.0

    def __call__(self) -> float:
        """Read the clock.

        Returns:
            float: The current fake seconds.
        """
        return self.now


class TestPhasePlan:
    """Weights become bounds, and durations become the number in between."""

    def test_weights_normalise_into_contiguous_bounds(self, plan: PhasePlan) -> None:
        assert plan.names == ("pdf", "table", "reading")
        assert plan.bounds("pdf")[0] == 0.0
        assert plan.bounds("pdf")[1] == pytest.approx(plan.bounds("table")[0])
        assert plan.bounds("table")[1] == pytest.approx(plan.bounds("reading")[0])
        assert plan.bounds("reading")[1] == pytest.approx(1.0)

    def test_a_phase_starting_sits_on_its_floor(self, plan: PhasePlan) -> None:
        assert plan.fraction("table", elapsed=0.0) == pytest.approx(
            plan.bounds("table")[0],
        )

    def test_interpolation_stops_short_of_the_phase_ceiling(
        self,
        plan: PhasePlan,
    ) -> None:
        floor, ceiling = plan.bounds("table")
        overdue = plan.fraction("table", elapsed=10_000.0)
        assert overdue < ceiling
        assert overdue == pytest.approx(floor + (ceiling - floor) * 0.95)

    def test_input_size_stretches_the_expectation(self, plan: PhasePlan) -> None:
        assert plan.expected("table") == pytest.approx(30.0)
        assert plan.expected("table", size=40_000) == pytest.approx(50.0)
        halfway = plan.fraction("table", elapsed=25.0, size=40_000)
        assert halfway < plan.fraction("table", elapsed=25.0)

    def test_a_real_count_beats_the_interpolation(self, plan: PhasePlan) -> None:
        floor, ceiling = plan.bounds("pdf")
        assert plan.fraction("pdf", done=1.0) == pytest.approx(ceiling)
        assert plan.fraction("pdf", done=0.5) == pytest.approx(
            floor + (ceiling - floor) / 2,
        )

    def test_an_unmeasured_phase_does_not_move(self) -> None:
        plan = PhasePlan([Phase("wait", weight=1.0)])
        assert plan.fraction("wait", elapsed=600.0) == 0.0

    @pytest.mark.parametrize(
        "phases",
        [
            [],
            [Phase("a", weight=1.0), Phase("a", weight=1.0)],
            [Phase("a", weight=0.0)],
        ],
    )
    def test_a_plan_that_cannot_be_drawn_is_refused(
        self,
        phases: list[Phase],
    ) -> None:
        with pytest.raises(ValueError):
            PhasePlan(phases)


class TestProgressTracker:
    """The tracker writes the phase it is in, and stops when told."""

    async def test_entering_a_phase_writes_its_floor(self, plan: PhasePlan) -> None:
        sink = _Sink()
        tracker = ProgressTracker(sink, uuid4(), plan=plan, interval=0.01)
        await tracker.enter("table")
        assert sink.writes == [(plan.bounds("table")[0], "table")]

    async def test_a_running_phase_is_ticked_while_it_waits(
        self,
        plan: PhasePlan,
    ) -> None:
        sink = _Sink()
        clock = _Clock()
        tracker = ProgressTracker(
            sink,
            uuid4(),
            plan=plan,
            interval=0.01,
            clock=clock,
        )

        async def work() -> str:
            """Take long enough to be ticked at least twice."""
            for _ in range(4):
                clock.now += 5.0
                await asyncio.sleep(0.02)
            return "done"

        assert await tracker.run("table", work(), size=10_000) == "done"
        written = [value for value, _stage in sink.writes]
        assert written == sorted(written)
        assert written[-1] > written[0]
        assert all(stage == "table" for _value, stage in sink.writes)

    async def test_a_cancelled_job_stops_the_phase(self, plan: PhasePlan) -> None:
        sink = _Sink(cancel_after=1)

        async def work() -> str:
            """Outlast the cancellation."""
            await asyncio.sleep(5.0)
            return "never"

        tracker = ProgressTracker(sink, uuid4(), plan=plan, interval=0.01)
        with pytest.raises(StageInterruptedError):
            await tracker.run("reading", work())


class TestReportProgress:
    """The column the tracker writes, and what the store refuses to write."""

    async def test_progress_moves_forward_on_a_running_job(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        job = await store.enqueue("read")
        await store.claim(job.id)
        assert await store.report_progress(job.id, progress=0.4, stage="table")
        reloaded = await store.get(job.id)
        assert reloaded.progress == pytest.approx(0.4)
        assert reloaded.stage == "table"

    async def test_a_late_tick_cannot_rewind_the_bar(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        job = await store.enqueue("read")
        await store.claim(job.id)
        await store.report_progress(job.id, progress=0.6)
        assert not await store.report_progress(job.id, progress=0.3)
        assert (await store.get(job.id)).progress == pytest.approx(0.6)

    async def test_a_queued_job_has_nothing_to_report(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        job = await store.enqueue("read")
        assert not await store.report_progress(job.id, progress=0.5)
        assert (await store.get(job.id)).progress == pytest.approx(0.0)

    async def test_a_cancelled_job_is_not_repainted(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        job = await store.enqueue("read")
        await store.claim(job.id)
        await store.cancel(job.id)
        assert not await store.report_progress(job.id, progress=0.9)

    async def test_finishing_fills_the_bar(self, store: JobStore[_JobModel]) -> None:
        job = await store.enqueue("read")
        await store.claim(job.id)
        await store.report_progress(job.id, progress=0.4)
        done = await store.succeed(job.id)
        assert done.progress == pytest.approx(1.0)

    async def test_a_failed_job_keeps_the_bar_where_it_stopped(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        job = await store.enqueue("read")
        await store.claim(job.id)
        await store.report_progress(job.id, progress=0.4)
        stopped = await store.fail(job.id, "the model gave up")
        assert stopped.progress == pytest.approx(0.4)


class TestListingAndWatching:
    """What a screen showing work in flight asks for."""

    async def test_several_statuses_come_back_in_one_query(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        queued = await store.enqueue("read")
        running = await store.enqueue("read")
        await store.claim(running.id)
        finished = await store.enqueue("read")
        await store.claim(finished.id)
        await store.succeed(finished.id)
        in_flight = await store.list_recent(
            statuses=(JobStatus.QUEUED, JobStatus.RUNNING),
        )
        assert {job.id for job in in_flight} == {queued.id, running.id}

    async def test_asking_two_ways_at_once_is_refused(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        with pytest.raises(ValueError, match="not both"):
            await store.list_recent(
                status=JobStatus.QUEUED,
                statuses=(JobStatus.RUNNING,),
            )

    async def test_watching_progress_yields_between_status_changes(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        job = await store.enqueue("read")
        await store.claim(job.id)

        async def advance() -> None:
            """Move the bar twice, then finish."""
            for value in (0.3, 0.6):
                await asyncio.sleep(0.02)
                await store.report_progress(job.id, progress=value)
            await asyncio.sleep(0.02)
            await store.succeed(job.id)

        mover = asyncio.create_task(advance())
        seen = [
            snapshot.progress
            async for snapshot in store.watch(
                job.id,
                interval=0.01,
                emit_on=("status", "progress"),
            )
        ]
        await mover
        assert seen == sorted(seen)
        assert len(seen) >= 3
        assert seen[-1] == pytest.approx(1.0)

    async def test_watching_a_column_that_does_not_exist_is_refused(
        self,
        store: JobStore[_JobModel],
    ) -> None:
        job = await store.enqueue("read")
        with pytest.raises(ValueError, match="no column"):
            async for _snapshot in store.watch(job.id, emit_on=("nonsense",)):
                pass
