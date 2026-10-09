"""``JobStore`` writing a result column, and getting a job back to a worker.

Issue #459 named two gaps, both of which left a row in a state nothing would
ever fix:

* ``succeed`` could not write a column the project added (an ``object_key``),
  so the worker wrote it from a session of its own first. Between those two
  transactions a cancel left a ``CANCELLED`` row pointing at a file the worker
  was about to delete. ``succeed(values=...)`` writes the column in the same
  conditional ``UPDATE`` as ``DONE``.
* ``reclaim_stale`` moved a dead worker's job back to ``QUEUED`` and returned a
  count. Nothing sends a ``QUEUED`` row to a worker, so the job sat there
  forever. It now returns the ids, and ``redispatch_queued`` resends rows
  whose send was lost.

File-backed SQLite, like ``test_jobs.py``: the race needs two real
connections, which an in-memory engine cannot give.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import String, inspect, update
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import (
    STORE_OWNED_JOB_COLUMNS,
    BaseJobModel,
    JobCancelledError,
    JobStatus,
    JobStore,
    ReclaimedJobs,
    make_job_model,
)
from tempest_fastapi_sdk.utils.datetime import utcnow

RACE_RUNS: int = 20
"""Forced interleavings per race scenario."""


class _KeyedJobModel(BaseJobModel):
    """A job table with a column of the project's own, like ``object_key``."""

    __tablename__ = "test_keyed_jobs"

    object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


@pytest_asyncio.fixture
async def keyed_db(tmp_path: Path) -> AsyncGenerator[AsyncDatabaseManager]:
    """A file-backed database with the keyed job table created.

    Args:
        tmp_path (Path): Pytest's scratch directory.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest.fixture
def store(keyed_db: AsyncDatabaseManager) -> JobStore[_KeyedJobModel]:
    """A store over the keyed job table.

    Args:
        keyed_db (AsyncDatabaseManager): The database manager.

    Returns:
        JobStore[_KeyedJobModel]: A store that reclaims after 60 seconds.
    """
    return JobStore(keyed_db, model=_KeyedJobModel, stale_after=60.0)


async def _backdate(
    db: AsyncDatabaseManager, job_id: UUID, *, minutes: int, column: str
) -> None:
    """Move a timestamp column of one job into the past.

    Args:
        db (AsyncDatabaseManager): The database manager.
        job_id (UUID): The job to backdate.
        minutes (int): How far into the past.
        column (str): ``started_at`` or ``updated_at``.
    """
    async with db.get_session_context() as session:
        await session.execute(
            update(_KeyedJobModel)
            .where(_KeyedJobModel.id == job_id)
            .values({column: utcnow() - timedelta(minutes=minutes)}),
        )


class TestSucceedValues:
    """The result column lands with ``DONE``, or not at all."""

    async def test_values_are_written_with_the_transition(
        self, store: JobStore[_KeyedJobModel]
    ) -> None:
        job = await store.enqueue("export")
        await store.claim(job.id)

        done = await store.succeed(job.id, values={"object_key": "exports/a.zip"})

        assert done.status == JobStatus.DONE.value
        assert done.object_key == "exports/a.zip"
        assert (await store.get(job.id)).object_key == "exports/a.zip"

    async def test_a_cancelled_job_does_not_receive_the_value(
        self, store: JobStore[_KeyedJobModel]
    ) -> None:
        job = await store.enqueue("export")
        await store.claim(job.id)
        await store.cancel(job.id, reason="user")

        with pytest.raises(JobCancelledError):
            await store.succeed(job.id, values={"object_key": "exports/a.zip"})

        row = await store.get(job.id)
        assert row.status == JobStatus.CANCELLED.value
        assert row.object_key is None

    async def test_an_unknown_column_is_refused_before_any_write(
        self, store: JobStore[_KeyedJobModel]
    ) -> None:
        job = await store.enqueue("export")
        await store.claim(job.id)

        with pytest.raises(ValueError, match="no column of _KeyedJobModel: nope"):
            await store.succeed(job.id, values={"nope": 1})

        assert (await store.get(job.id)).status == JobStatus.RUNNING.value

    @pytest.mark.parametrize("column", ["status", "finished_at", "result_id"])
    async def test_a_store_owned_column_is_refused(
        self, store: JobStore[_KeyedJobModel], column: str
    ) -> None:
        job = await store.enqueue("export")
        await store.claim(job.id)

        with pytest.raises(ValueError, match=f"JobStore controls: {column}"):
            await store.succeed(job.id, values={column: None})

        assert (await store.get(job.id)).status == JobStatus.RUNNING.value

    def test_store_owned_columns_are_exactly_the_base_model_columns(self) -> None:
        """A column added to ``BaseJobModel`` must be classified here.

        Without this the constant drifts silently: the new column would be
        writable through ``values`` and could contradict the transition.
        """
        model = make_job_model(tablename="pinned_jobs", class_name="PinnedJobModel")

        assert set(inspect(model).column_attrs.keys()) == STORE_OWNED_JOB_COLUMNS

    async def test_succeed_racing_cancel_never_leaves_a_cancelled_row_with_a_key(
        self, keyed_db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        """One statement means there is no window for the cancel to land in.

        ``succeed`` and ``cancel`` run on two connections released by a
        barrier, ``RACE_RUNS`` times. Whichever wins, the row is consistent:
        ``DONE`` with the key, or ``CANCELLED`` without it.
        """
        other = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
        await other.connect()
        worker: JobStore[_KeyedJobModel] = JobStore(keyed_db, model=_KeyedJobModel)
        screen: JobStore[_KeyedJobModel] = JobStore(other, model=_KeyedJobModel)
        outcomes: set[str] = set()
        try:
            for _ in range(RACE_RUNS):
                job = await worker.enqueue("export")
                await worker.claim(job.id)
                start = asyncio.Barrier(2)

                async def _succeed(
                    job_id: UUID = job.id, barrier: asyncio.Barrier = start
                ) -> None:
                    """Close the job with its key once both sides are ready.

                    Args:
                        job_id (UUID): The job to close.
                        barrier (asyncio.Barrier): This run's barrier.
                    """
                    await barrier.wait()
                    try:
                        await worker.succeed(job_id, values={"object_key": "k"})
                    except JobCancelledError:
                        return

                async def _cancel(
                    job_id: UUID = job.id, barrier: asyncio.Barrier = start
                ) -> None:
                    """Cancel the job once both sides are ready.

                    Args:
                        job_id (UUID): The job to cancel.
                        barrier (asyncio.Barrier): This run's barrier.
                    """
                    await barrier.wait()
                    await screen.cancel(job_id, reason="user")

                await asyncio.gather(_succeed(), _cancel())
                row = await worker.get(job.id)
                outcomes.add(row.status)
                if row.status == JobStatus.CANCELLED.value:
                    assert row.object_key is None
                else:
                    assert row.status == JobStatus.DONE.value
                    assert row.object_key == "k"
        finally:
            await other.disconnect()
        assert outcomes <= {JobStatus.DONE.value, JobStatus.CANCELLED.value}

    async def test_the_two_transaction_workaround_is_the_one_that_breaks(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        """The shape #459 reported, with the cancel forced into its window.

        Without this the tests above could pass against a ``succeed`` that
        ignored ``values`` and a caller writing the column itself.
        """
        job = await store.enqueue("export")
        await store.claim(job.id)

        async with keyed_db.get_session_context() as session:
            await session.execute(
                update(_KeyedJobModel)
                .where(_KeyedJobModel.id == job.id)
                .values(object_key="exports/a.zip"),
            )
        await store.cancel(job.id, reason="user")
        with pytest.raises(JobCancelledError):
            await store.succeed(job.id)

        row = await store.get(job.id)
        assert row.status == JobStatus.CANCELLED.value
        assert row.object_key == "exports/a.zip"


class TestReclaimIds:
    """The ids are what let the caller get the job back to a worker."""

    async def test_the_spent_budget_is_reported_as_failed(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        job = await store.enqueue("export", max_attempts=1)
        await store.claim(job.id)
        await _backdate(keyed_db, job.id, minutes=10, column="started_at")

        reclaimed = await store.reclaim_stale()

        assert reclaimed == ReclaimedJobs(requeued=[], failed=[job.id])
        assert len(reclaimed) == 1

    async def test_a_reclaimed_job_reaches_dispatch_and_finishes(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        job = await store.enqueue("export")
        await store.claim(job.id)
        await _backdate(keyed_db, job.id, minutes=10, column="started_at")

        async def _worker(job_id: UUID) -> None:
            """Run the job the way the task body would.

            Args:
                job_id (UUID): The job to run.
            """
            claimed = await store.claim(job_id)
            assert claimed is not None
            await store.succeed(job_id, values={"object_key": "exports/b.zip"})

        reclaimed = await store.reclaim_stale()
        for job_id in reclaimed.requeued:
            await _worker(job_id)

        row = await store.get(job.id)
        assert row.status == JobStatus.DONE.value
        assert row.attempts == 2
        assert row.object_key == "exports/b.zip"


class TestRedispatchQueued:
    """A ``QUEUED`` row whose send was lost goes back to a worker."""

    async def test_a_lost_send_is_redispatched_and_finishes(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        job = await store.enqueue("export")
        await _backdate(keyed_db, job.id, minutes=30, column="updated_at")
        sent: list[UUID] = []

        async def _dispatch(row: _KeyedJobModel) -> None:
            """Stand in for ``task.enqueue`` plus the worker running it.

            Args:
                row (_KeyedJobModel): The job handed over.
            """
            sent.append(row.id)
            await store.claim(row.id)
            await store.succeed(row.id)

        resent = await store.redispatch_queued(_dispatch, older_than=600.0)

        assert resent == [job.id]
        assert sent == [job.id]
        assert (await store.get(job.id)).status == JobStatus.DONE.value

    async def test_a_resent_row_waits_a_full_window_before_the_next_send(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        job = await store.enqueue("export")
        await _backdate(keyed_db, job.id, minutes=30, column="updated_at")
        sent: list[UUID] = []

        async def _dispatch(row: _KeyedJobModel) -> None:
            """Record the send; the message is lost again.

            Args:
                row (_KeyedJobModel): The job handed over.
            """
            sent.append(row.id)

        assert await store.redispatch_queued(_dispatch, older_than=600.0) == [job.id]
        assert await store.redispatch_queued(_dispatch, older_than=600.0) == []
        assert sent == [job.id]

    async def test_fresh_and_running_rows_are_left_alone(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        await store.enqueue("export")
        running = await store.enqueue("export")
        await store.claim(running.id)
        await _backdate(keyed_db, running.id, minutes=30, column="updated_at")
        sent: list[UUID] = []

        async def _dispatch(row: _KeyedJobModel) -> None:
            """Record the send.

            Args:
                row (_KeyedJobModel): The job handed over.
            """
            sent.append(row.id)

        assert await store.redispatch_queued(_dispatch, older_than=600.0) == []
        assert sent == []

    async def test_a_failing_dispatch_leaves_the_rest_for_the_next_sweep(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        first = await store.enqueue("export")
        second = await store.enqueue("export")
        await _backdate(keyed_db, first.id, minutes=40, column="updated_at")
        await _backdate(keyed_db, second.id, minutes=30, column="updated_at")

        async def _broken(row: _KeyedJobModel) -> None:
            """Fail like a broker that is down.

            Args:
                row (_KeyedJobModel): The job handed over.

            Raises:
                ConnectionError: Always.
            """
            raise ConnectionError(f"broker down for {row.id}")

        with pytest.raises(ConnectionError):
            await store.redispatch_queued(_broken, older_than=600.0)

        sent: list[UUID] = []

        async def _dispatch(row: _KeyedJobModel) -> None:
            """Record the send.

            Args:
                row (_KeyedJobModel): The job handed over.
            """
            sent.append(row.id)

        assert await store.redispatch_queued(_dispatch, older_than=600.0) == [
            first.id,
            second.id,
        ]
        assert sent == [first.id, second.id]

    async def test_limit_bounds_one_sweep_oldest_first(
        self, store: JobStore[_KeyedJobModel], keyed_db: AsyncDatabaseManager
    ) -> None:
        older = await store.enqueue("export")
        newer = await store.enqueue("export")
        await _backdate(keyed_db, older.id, minutes=40, column="updated_at")
        await _backdate(keyed_db, newer.id, minutes=30, column="updated_at")

        async def _dispatch(row: _KeyedJobModel) -> None:
            """Accept the send.

            Args:
                row (_KeyedJobModel): The job handed over.
            """

        assert await store.redispatch_queued(
            _dispatch, older_than=timedelta(minutes=10), limit=1
        ) == [older.id]

    @pytest.mark.parametrize(
        ("older_than", "limit", "message"),
        [
            (0.0, 10, "older_than must be positive"),
            (60.0, 0, "limit must be positive"),
        ],
    )
    async def test_non_positive_arguments_are_refused(
        self,
        store: JobStore[_KeyedJobModel],
        older_than: float,
        limit: int,
        message: str,
    ) -> None:
        async def _dispatch(row: _KeyedJobModel) -> None:
            """Never called.

            Args:
                row (_KeyedJobModel): The job handed over.
            """

        with pytest.raises(ValueError, match=message):
            await store.redispatch_queued(_dispatch, older_than=older_than, limit=limit)
