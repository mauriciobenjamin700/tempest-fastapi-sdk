"""Cancelling a job: what the request side writes, what the worker reads.

The two sides never meet. The request writes ``CANCELLED`` and answers; the
worker reads that status at checkpoints and gives up. So the assertions come
in two shapes: what :meth:`JobStore.cancel` does to the row, and what
:meth:`JobStore.is_cancelled` reports back to a worker that is mid-flight.

The database is a file rather than ``:memory:`` for the same reason as
``test_jobs.py``: half of this is about one connection seeing what another
committed, and SQLAlchemy hands every in-memory SQLite engine one shared
connection.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import (
    BaseJobModel,
    JobCancelledError,
    JobStatus,
    JobStore,
    StageInterruptedError,
    run_cancellable,
)


class _JobModel(BaseJobModel):
    """Concrete job table for these tests."""

    __tablename__ = "cancel_test_jobs"


@pytest_asyncio.fixture
async def jobs_db(tmp_path: Path) -> AsyncGenerator[AsyncDatabaseManager]:
    """A file-backed database with the job table created.

    Args:
        tmp_path (Path): pytest's per-test directory.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    manager = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'cancel.db'}")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest.fixture
def store(jobs_db: AsyncDatabaseManager) -> JobStore[_JobModel]:
    """A store over the test job table.

    Args:
        jobs_db (AsyncDatabaseManager): The connected manager.

    Returns:
        JobStore[_JobModel]: The store under test.
    """
    return JobStore(jobs_db, model=_JobModel)


class TestCancel:
    """What the request side writes."""

    async def test_a_queued_job_can_be_cancelled(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Cancelling before a worker picks it up is the common case."""
        job = await store.enqueue("summarize")

        cancelled = await store.cancel(job.id)

        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLED.value
        assert cancelled.finished_at is not None

    async def test_a_running_job_can_be_cancelled(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Cancelling mid-flight is the case the worker has to cooperate with."""
        job = await store.enqueue("summarize")
        await store.claim(job.id)

        cancelled = await store.cancel(job.id)

        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLED.value

    async def test_the_reason_is_stored(self, store: JobStore[_JobModel]) -> None:
        """The screen can say who stopped it, not just that it stopped."""
        job = await store.enqueue("summarize")

        cancelled = await store.cancel(job.id, reason="cancelled by the user")

        assert cancelled is not None
        assert cancelled.error == "cancelled by the user"

    async def test_the_payload_is_dropped(self, store: JobStore[_JobModel]) -> None:
        """A cancelled job is finished, so it stops carrying its document."""
        job = await store.enqueue("summarize", payload=b"a long transcript ...")

        cancelled = await store.cancel(job.id)

        assert cancelled is not None
        assert cancelled.payload is None

    async def test_cancelling_a_done_job_answers_none(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Nothing to stop is not an error — a click that lost a race."""
        job = await store.enqueue("summarize")
        await store.claim(job.id)
        await store.succeed(job.id)

        assert await store.cancel(job.id) is None

    async def test_cancelling_twice_answers_none(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Idempotent: a double-click must not raise."""
        job = await store.enqueue("summarize")
        assert await store.cancel(job.id) is not None

        assert await store.cancel(job.id) is None

    async def test_cancelling_an_unknown_id_answers_none(
        self, store: JobStore[_JobModel]
    ) -> None:
        """An id that never existed is also "nothing to stop"."""
        assert await store.cancel(uuid4()) is None

    async def test_a_cancelled_job_is_terminal(
        self, store: JobStore[_JobModel]
    ) -> None:
        """The poll stops: ``is_terminal`` covers cancellation too."""
        job = await store.enqueue("summarize")
        cancelled = await store.cancel(job.id)

        assert cancelled is not None
        assert cancelled.is_terminal is True

    async def test_a_cancelled_job_cannot_be_claimed(
        self, store: JobStore[_JobModel]
    ) -> None:
        """A worker that picks the message up later must not start it."""
        job = await store.enqueue("summarize")
        await store.cancel(job.id)

        assert await store.claim(job.id) is None


class TestWorkerSide:
    """What the worker reads, and what happens if it races."""

    async def test_is_cancelled_sees_a_committed_cancel(
        self, store: JobStore[_JobModel]
    ) -> None:
        """The checkpoint reads fresh, not the pre-cancel snapshot."""
        job = await store.enqueue("summarize")
        await store.claim(job.id)
        assert await store.is_cancelled(job.id) is False

        await store.cancel(job.id)

        assert await store.is_cancelled(job.id) is True

    async def test_a_missing_job_reads_as_cancelled(
        self, store: JobStore[_JobModel]
    ) -> None:
        """Nothing left to produce a result for, so stopping is right."""
        assert await store.is_cancelled(uuid4()) is True

    async def test_succeed_after_cancel_is_refused(
        self, store: JobStore[_JobModel]
    ) -> None:
        """A result never lands on top of the user's cancellation.

        This is the last line of defence: the worker that raced past its
        final checkpoint still cannot overwrite the row.
        """
        job = await store.enqueue("summarize")
        await store.claim(job.id)
        await store.cancel(job.id)

        with pytest.raises(JobCancelledError):
            await store.succeed(job.id)

    async def test_the_cancel_error_is_distinguishable(
        self, store: JobStore[_JobModel]
    ) -> None:
        """A cancel and a double-worker collision must not read the same.

        One means the system did as told; the other means something is
        wrong with the concurrency. Subclassing keeps existing handlers
        working while letting a worker tell them apart.
        """
        job = await store.enqueue("summarize")
        await store.claim(job.id)
        await store.cancel(job.id)

        with pytest.raises(JobCancelledError) as excinfo:
            await store.fail(job.id, "boom")

        from tempest_fastapi_sdk.tasks import JobAlreadyFinishedError

        assert isinstance(excinfo.value, JobAlreadyFinishedError)

    async def test_cancellation_watch_drives_run_cancellable(
        self, store: JobStore[_JobModel]
    ) -> None:
        """The two halves meet: a cancel written here stops work there.

        End to end over a real row, because the point of the pair is that
        one process's commit is visible to the other's poll.
        """
        job = await store.enqueue("summarize")
        await store.claim(job.id)
        cancelled_work = {"seen": False}

        async def _work() -> str:
            """Long work that records being cancelled.

            Returns:
                str: Never reached in this test.

            Raises:
                asyncio.CancelledError: Re-raised after recording.
            """
            import asyncio

            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled_work["seen"] = True
                raise
            return "done"

        await store.cancel(job.id)

        with pytest.raises(StageInterruptedError):
            await run_cancellable(
                _work(),
                interrupted=store.cancellation_watch(job.id),
                poll_seconds=0.01,
            )

        assert cancelled_work["seen"] is True
