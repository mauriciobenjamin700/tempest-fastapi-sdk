"""Tests for ``BaseJobModel`` + ``JobStore``.

The job table exists to answer what a queue cannot: has anything picked
this up, is it running, what did it produce, and why did it stop. These
pin the transitions, the concurrency of :meth:`JobStore.claim`, the
payload being dropped on the way out, the stale-worker readmission, and
the polling helper that must **not** hold a session between ticks.

The database is a file rather than ``:memory:`` on purpose: half of this
is about two connections seeing the same rows, and SQLAlchemy hands every
in-memory SQLite engine one shared connection.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import (
    STALE_JOB_ERROR,
    BaseJobModel,
    JobAlreadyFinishedError,
    JobNotFoundError,
    JobStatus,
    JobStore,
    make_job_model,
)
from tempest_fastapi_sdk.utils.datetime import utcnow


class _JobModel(BaseJobModel):
    """Concrete job table for these tests."""

    __tablename__ = "test_jobs"


@pytest_asyncio.fixture
async def jobs_db(tmp_path: Path) -> AsyncGenerator[AsyncDatabaseManager]:
    """A file-backed database with the job table created."""
    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
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


class TestEnqueue:
    """The row the interface reads exists before the worker runs."""

    async def test_a_new_job_is_queued(self, store: JobStore[_JobModel]) -> None:
        job = await store.enqueue("extract")

        assert job.status == JobStatus.QUEUED.value
        assert job.kind == "extract"
        assert job.attempts == 0
        assert job.started_at is None
        assert job.finished_at is None

    async def test_it_carries_params_and_payload(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Small input as JSON, large input as bytes the broker never sees."""
        job = await store.enqueue(
            "extract",
            params={"locale": "pt-BR"},
            payload=b"%PDF-1.7 ...",
        )
        reloaded = await store.get(job.id)

        assert reloaded.params == {"locale": "pt-BR"}
        assert reloaded.payload == b"%PDF-1.7 ..."

    async def test_params_default_to_an_empty_dict(
        self, store: JobStore[_JobModel]
    ) -> None:
        job = await store.enqueue("extract")

        assert (await store.get(job.id)).params == {}


class TestGet:
    async def test_missing_id_raises(self, store: JobStore[_JobModel]) -> None:
        with pytest.raises(JobNotFoundError):
            await store.get(uuid4())


class TestClaim:
    """What separates 'nobody picked it up' from 'the worker is busy'."""

    async def test_claim_moves_it_to_running(self, store: JobStore[_JobModel]) -> None:
        job = await store.enqueue("extract", payload=b"input")

        claimed = await store.claim(job.id)

        assert claimed is not None
        assert claimed.status == JobStatus.RUNNING.value
        assert claimed.started_at is not None
        assert claimed.attempts == 1

    async def test_the_payload_comes_back_with_the_claim(
        self, store: JobStore[_JobModel]
    ) -> None:
        """The worker gets its input without a second round trip."""
        job = await store.enqueue("extract", payload=b"input")

        claimed = await store.claim(job.id)

        assert claimed is not None
        assert claimed.payload == b"input"

    async def test_claiming_twice_returns_none(
        self, store: JobStore[_JobModel]
    ) -> None:
        job = await store.enqueue("extract")
        await store.claim(job.id)

        assert await store.claim(job.id) is None

    async def test_claiming_a_missing_job_returns_none(
        self, store: JobStore[_JobModel]
    ) -> None:
        assert await store.claim(uuid4()) is None

    async def test_two_workers_racing_over_two_connections(
        self, jobs_db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        """Exactly one claim wins when the contenders are separate engines.

        Two ``AsyncDatabaseManager`` instances on one file means two real
        connections and two independent transactions — the condition a
        single shared in-memory connection cannot reproduce. A barrier
        releases both at the same moment, because letting the event loop
        decide made the contention appear only sometimes.
        """
        other = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
        await other.connect()
        try:
            first: JobStore[_JobModel] = JobStore(jobs_db, model=_JobModel)
            second: JobStore[_JobModel] = JobStore(other, model=_JobModel)
            job = await first.enqueue("extract")
            start = asyncio.Barrier(2)

            async def _claim(store: JobStore[_JobModel]) -> _JobModel | None:
                """Wait for the other contender, then claim.

                Args:
                    store (JobStore[_JobModel]): The contender's store.

                Returns:
                    _JobModel | None: Whatever the claim answered.
                """
                await start.wait()
                return await store.claim(job.id)

            results = await asyncio.gather(_claim(first), _claim(second))

            assert sum(1 for r in results if r is not None) == 1
            assert (await first.get(job.id)).attempts == 1
        finally:
            await other.disconnect()

    async def test_the_naive_claim_is_the_one_that_breaks(
        self, jobs_db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        """Reading the status and then writing it fails under contention.

        Without this the test above proves nothing — it would pass just
        as happily against an implementation with no conditional update.

        What the naive shape does here is **not** what one expects.
        Measured under the same barrier: it does not hand the job to both
        contenders, it raises ``sqlite3.OperationalError: database is
        locked``, because each session reads first and writes later and
        lock promotion is precisely what ``busy_timeout`` cannot wait
        out. (A backend with MVCC readers would give both the job
        instead.) Either way it is broken; the conditional ``UPDATE`` is
        what turns losing into a quiet ``None``.
        """
        other = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
        await other.connect()
        start = asyncio.Barrier(2)

        async def _naive_claim(db: AsyncDatabaseManager, job_id: object) -> bool:
            """Claim by reading the status and then writing it.

            Args:
                db (AsyncDatabaseManager): Which connection claims.
                job_id (object): The job to claim.

            Returns:
                bool: Whether this caller believes it won.
            """
            async with db.get_session_context() as session:
                current = (
                    await session.execute(
                        select(_JobModel).where(_JobModel.id == job_id),
                    )
                ).scalar_one()
                await start.wait()
                if current.status != JobStatus.QUEUED.value:
                    return False
                await session.execute(
                    update(_JobModel)
                    .where(_JobModel.id == job_id)
                    .values(status=JobStatus.RUNNING.value),
                )
                return True

        try:
            store: JobStore[_JobModel] = JobStore(jobs_db, model=_JobModel)
            job = await store.enqueue("extract")

            outcomes = await asyncio.gather(
                _naive_claim(jobs_db, job.id),
                _naive_claim(other, job.id),
                return_exceptions=True,
            )
        finally:
            await other.disconnect()

        failures = [o for o in outcomes if isinstance(o, OperationalError)]
        assert len(failures) == 1
        assert "database is locked" in str(failures[0])


class TestFinishing:
    """Terminal states, and the payload that must not survive them."""

    async def test_succeed_records_the_result(self, store: JobStore[_JobModel]) -> None:
        job = await store.enqueue("extract", payload=b"input")
        await store.claim(job.id)
        result_id = uuid4()

        done = await store.succeed(job.id, result_id=result_id)

        assert done.status == JobStatus.DONE.value
        assert done.result_id == result_id
        assert done.finished_at is not None
        assert done.error is None

    async def test_succeed_drops_the_payload(self, store: JobStore[_JobModel]) -> None:
        """Otherwise the finished-jobs table becomes a pile of documents."""
        job = await store.enqueue("extract", payload=b"%PDF-1.7 ...")
        await store.claim(job.id)

        await store.succeed(job.id)

        assert (await store.get(job.id)).payload is None

    async def test_fail_records_the_reason_and_drops_the_payload(
        self, store: JobStore[_JobModel]
    ) -> None:
        job = await store.enqueue("extract", payload=b"input")
        await store.claim(job.id)

        failed = await store.fail(job.id, "O arquivo nao e um PDF valido.")

        assert failed.status == JobStatus.FAILED.value
        assert failed.error == "O arquivo nao e um PDF valido."
        assert failed.payload is None

    async def test_a_queued_job_can_fail_without_being_claimed(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Rejecting work before starting it is a real outcome."""
        job = await store.enqueue("extract")

        failed = await store.fail(job.id, "Formato nao suportado.")

        assert failed.status == JobStatus.FAILED.value

    async def test_finishing_twice_raises(self, store: JobStore[_JobModel]) -> None:
        """Two workers believing they own the job must not be silent."""
        job = await store.enqueue("extract")
        await store.succeed(job.id)

        with pytest.raises(JobAlreadyFinishedError):
            await store.fail(job.id, "late")

    async def test_finishing_a_missing_job_raises_not_found(
        self, store: JobStore[_JobModel]
    ) -> None:
        with pytest.raises(JobNotFoundError):
            await store.succeed(uuid4())

    async def test_is_terminal_reflects_the_status(
        self, store: JobStore[_JobModel]
    ) -> None:
        job = await store.enqueue("extract")
        assert job.is_terminal is False

        assert (await store.succeed(job.id)).is_terminal is True


class TestListRecent:
    async def test_newest_first(self, store: JobStore[_JobModel]) -> None:
        await store.enqueue("a")
        await asyncio.sleep(0.01)
        second = await store.enqueue("b")

        listed = await store.list_recent()

        assert listed[0].id == second.id

    async def test_filters_by_kind_and_status(self, store: JobStore[_JobModel]) -> None:
        await store.enqueue("extract")
        other = await store.enqueue("render")
        await store.claim(other.id)

        assert len(await store.list_recent(kind="render")) == 1
        assert len(await store.list_recent(status=JobStatus.RUNNING)) == 1
        assert len(await store.list_recent(status="queued")) == 1

    async def test_no_matches_is_an_empty_list(
        self, store: JobStore[_JobModel]
    ) -> None:
        """ "Nothing yet" is a successful answer, never a not-found."""
        assert await store.list_recent(kind="nothing-like-this") == []

    async def test_limit_is_applied(self, store: JobStore[_JobModel]) -> None:
        for _ in range(3):
            await store.enqueue("extract")

        assert len(await store.list_recent(limit=2)) == 2

    async def test_a_non_positive_limit_raises(
        self, store: JobStore[_JobModel]
    ) -> None:
        with pytest.raises(ValueError, match="limit must be positive"):
            await store.list_recent(limit=0)


class TestReclaimStale:
    """The job whose worker died is the one a queue cannot see."""

    @staticmethod
    async def _age_the_claim(
        db: AsyncDatabaseManager, job_id: object, *, minutes: int
    ) -> None:
        """Backdate a running job's ``started_at``.

        Args:
            db (AsyncDatabaseManager): The database manager.
            job_id (object): The job to backdate.
            minutes (int): How far into the past to move it.
        """
        async with db.get_session_context() as session:
            await session.execute(
                update(_JobModel)
                .where(_JobModel.id == job_id)
                .values(started_at=utcnow() - timedelta(minutes=minutes)),
            )

    async def test_a_stalled_job_goes_back_to_the_queue(
        self, store: JobStore[_JobModel], jobs_db: AsyncDatabaseManager
    ) -> None:
        job = await store.enqueue("extract")
        await store.claim(job.id)
        await self._age_the_claim(jobs_db, job.id, minutes=10)

        assert await store.reclaim_stale() == 1

        reclaimed = await store.get(job.id)
        assert reclaimed.status == JobStatus.QUEUED.value
        assert reclaimed.started_at is None

    async def test_a_fresh_running_job_is_left_alone(
        self, store: JobStore[_JobModel]
    ) -> None:
        job = await store.enqueue("extract")
        await store.claim(job.id)

        assert await store.reclaim_stale() == 0
        assert (await store.get(job.id)).status == JobStatus.RUNNING.value

    async def test_the_attempt_budget_ends_the_loop(
        self, store: JobStore[_JobModel], jobs_db: AsyncDatabaseManager
    ) -> None:
        """A job that kills its worker must not be readmitted forever."""
        job = await store.enqueue("extract", payload=b"poison", max_attempts=2)
        for _ in range(2):
            await store.claim(job.id)
            await self._age_the_claim(jobs_db, job.id, minutes=10)
            await store.reclaim_stale()

        final = await store.get(job.id)
        assert final.status == JobStatus.FAILED.value
        assert final.attempts == 2
        assert final.payload is None
        assert final.error is not None
        assert final.error == STALE_JOB_ERROR

    async def test_without_stale_after_it_refuses(
        self, jobs_db: AsyncDatabaseManager
    ) -> None:
        """No threshold means no guess about what counts as dead."""
        store: JobStore[_JobModel] = JobStore(jobs_db, model=_JobModel)

        with pytest.raises(RuntimeError, match="stale_after"):
            await store.reclaim_stale()

    async def test_a_non_positive_stale_after_raises(
        self, jobs_db: AsyncDatabaseManager
    ) -> None:
        with pytest.raises(ValueError, match="stale_after must be positive"):
            JobStore(jobs_db, model=_JobModel, stale_after=0.0)

    async def test_a_timedelta_is_accepted(self, jobs_db: AsyncDatabaseManager) -> None:
        store: JobStore[_JobModel] = JobStore(
            jobs_db,
            model=_JobModel,
            stale_after=timedelta(minutes=5),
        )

        assert store.stale_after == timedelta(minutes=5)


class TestWatch:
    """The "is it done yet?" poll every application writes by hand."""

    async def test_an_already_finished_job_yields_once(
        self, store: JobStore[_JobModel]
    ) -> None:
        job = await store.enqueue("extract")
        await store.succeed(job.id)

        seen = [j.status async for j in store.watch(job.id, interval=0.01)]

        assert seen == [JobStatus.DONE.value]

    async def test_it_follows_the_job_to_a_terminal_status(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Every yielded value is a change, and the last one is terminal."""
        job = await store.enqueue("extract")

        async def _work() -> None:
            await asyncio.sleep(0.05)
            await store.claim(job.id)
            await asyncio.sleep(0.05)
            await store.succeed(job.id)

        worker = asyncio.create_task(_work())
        seen = [j.status async for j in store.watch(job.id, interval=0.01)]
        await worker

        assert seen[0] == JobStatus.QUEUED.value
        assert seen[-1] == JobStatus.DONE.value
        assert all(a != b for a, b in pairwise(seen))

    async def test_it_gives_up_on_timeout(self, store: JobStore[_JobModel]) -> None:
        job = await store.enqueue("extract")

        with pytest.raises(TimeoutError):
            async for _ in store.watch(job.id, interval=0.01, timeout=0.05):
                pass

    async def test_a_missing_job_raises(self, store: JobStore[_JobModel]) -> None:
        with pytest.raises(JobNotFoundError):
            async for _ in store.watch(uuid4(), interval=0.01):
                pass

    async def test_a_non_positive_interval_raises(
        self, store: JobStore[_JobModel]
    ) -> None:
        with pytest.raises(ValueError, match="interval must be positive"):
            async for _ in store.watch(uuid4(), interval=0.0):
                pass

    async def test_the_poll_never_blocks_a_writer_on_another_connection(
        self, store: JobStore[_JobModel], jobs_db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        """The session is opened and closed per tick, not held across them.

        A watcher that kept its transaction open would hold a read lock
        for the whole poll; the writer here lives on a **separate
        engine**, so it would be the one to fail. It finishing at all is
        the assertion.
        """
        job = await store.enqueue("extract")
        writer_db = AsyncDatabaseManager(
            f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
            sqlite_busy_timeout=2.0,
        )
        await writer_db.connect()
        writer: JobStore[_JobModel] = JobStore(writer_db, model=_JobModel)
        try:

            async def _finish_from_the_other_connection() -> None:
                await asyncio.sleep(0.05)
                await writer.succeed(job.id)

            task = asyncio.create_task(_finish_from_the_other_connection())
            seen = [j.status async for j in store.watch(job.id, interval=0.01)]
            await task
        finally:
            await writer_db.disconnect()

        assert seen[-1] == JobStatus.DONE.value


class TestMakeJobModel:
    def test_it_builds_a_concrete_table(self) -> None:
        model = make_job_model(tablename="scratch_jobs", class_name="ScratchJob")

        assert model.__tablename__ == "scratch_jobs"
        assert model.__name__ == "ScratchJob"
        assert issubclass(model, BaseJobModel)
