"""Long-running work with a status the interface can show.

A queue hands a call to a worker. It does not answer any of the
questions the **user** in front of a screen is asking: has anything
picked this up yet, is it running, did it finish, why did it stop, and
what did it produce. TaskIQ's result backend is close but keyed by task
id, holds the function's return value, and is not a table an application
paginates or shows in an admin. What the interface wants is a **row**.

This module is the symmetric half of
:mod:`tempest_fastapi_sdk.db.outbox`: the outbox is *a message to
publish*, this is *work to execute*.

* :class:`JobStatus` — the lifecycle (queued → running → done / failed).
* :class:`BaseJobModel` — the abstract table; the project subclasses it
  and picks a ``__tablename__``, exactly like
  :class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel`.
* :class:`JobStore` — enqueue / claim / succeed / fail / list, each in
  its own short transaction, plus :meth:`JobStore.watch` for the "is it
  done yet?" poll and :meth:`JobStore.reclaim_stale` for the worker that
  died holding a job.

Three details in here each cost somebody a discovery:

* **``succeed`` and ``fail`` drop the payload.** Without that, the table
  of finished jobs becomes a pile of documents.
* **``claim`` is what separates "queued" from "running".** Without it the
  interface cannot tell "the worker is busy" from "nobody picked it up",
  which is the exact question when something takes a while.
* **A job whose worker died stays ``RUNNING`` forever** unless something
  readmits it — that is :meth:`JobStore.reclaim_stale`, bounded by
  ``max_attempts`` so a job that kills its worker cannot loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    CursorResult,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

logger = logging.getLogger("tempest_fastapi_sdk.tasks.jobs")


class JobStatus(StrEnum):
    """Lifecycle of a unit of long-running work.

    * ``QUEUED`` — the row exists, nobody has picked it up.
    * ``RUNNING`` — a worker claimed it (see :meth:`JobStore.claim`).
    * ``DONE`` — finished; ``result_id`` points at what it produced.
    * ``FAILED`` — stopped; ``error`` says why, in the user's language.
    * ``CANCELLED`` — the user asked it to stop (see
      :meth:`JobStore.cancel`). Terminal like the two above, but **not** a
      failure: nothing went wrong, so an interface that highlights
      ``FAILED`` should leave this one alone, and an alert that pages on
      failures should not fire.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES: frozenset[str] = frozenset(
    {JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value},
)
"""Statuses a job never leaves: the poll stops, the payload is gone."""

CANCELLABLE_JOB_STATUSES: frozenset[str] = frozenset(
    {JobStatus.QUEUED.value, JobStatus.RUNNING.value},
)
"""Statuses with work left to stop — what :meth:`JobStore.cancel` accepts."""

STALE_JOB_ERROR: str = (
    "The worker stopped responding while this job was running, "
    "and the claim budget is spent."
)
"""``error`` written by :meth:`JobStore.reclaim_stale` when it gives up.

A constant rather than a per-row message because the sweep is a single
``UPDATE`` and interpolating each row's attempt count into it would mean
reading the rows first — the read-then-write shape this module avoids.
"""


class JobNotFoundError(LookupError):
    """Raised when a job id matches no row.

    A ``LookupError`` rather than an
    :class:`~tempest_fastapi_sdk.exceptions.AppException` because the
    store runs in the worker as often as in a request, and a worker has
    no HTTP status to answer with. Map it at the boundary — a route that
    exposes job status can raise its own 404 from
    :func:`~tempest_fastapi_sdk.not_found_exception`.
    """


class JobAlreadyFinishedError(RuntimeError):
    """Raised when finishing a job that already reached a terminal state.

    Surfaced rather than swallowed: a second ``succeed`` or ``fail`` on
    the same row means two workers believe they own the job, or one
    worker ran the same job twice. Silently overwriting the first
    outcome would erase the evidence.
    """


class JobCancelledError(JobAlreadyFinishedError):
    """Raised when finishing a job the user cancelled meanwhile.

    A subclass so existing ``except JobAlreadyFinishedError`` keeps
    working, and a separate type because the two mean opposite things: a
    plain ``JobAlreadyFinishedError`` says something is wrong with your
    concurrency, while this one says the system did exactly what it was
    told. A worker that races past its last cancellation checkpoint and
    calls ``succeed`` should log this and move on, not alert.

    The write is refused either way — a cancelled job never gets a result
    written over it.
    """


class BaseJobModel(BaseModel):
    """Abstract job table — one row per unit of long-running work.

    The consuming project subclasses this and picks a ``__tablename__``,
    mirroring :class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel`.
    Inherits the canonical four columns from
    :class:`~tempest_fastapi_sdk.db.model.BaseModel` (``id``,
    ``is_active``, ``created_at``, ``updated_at``), so ``created_at`` is
    when the work was requested.

    Attributes:
        kind (str): Which work this is — the discriminator a worker
            branches on and a filter the interface offers. Indexed.
        status (str): One of :class:`JobStatus`. Indexed, because every
            listing and every poll filters on it.
        params (dict[str, Any]): Small input, as JSON. What fits in a
            message.
        payload (bytes | None): Large input — the uploaded document a
            broker has no business carrying. Dropped when the job
            reaches a terminal state.
        result_id (UUID | None): The row the work produced, so the
            interface can navigate to it without a second lookup table.
        error (str | None): Why it stopped, written for the user rather
            than as a traceback.
        attempts (int): How many times the job was claimed. Incremented
            by :meth:`JobStore.claim`.
        max_attempts (int): Claim budget. :meth:`JobStore.reclaim_stale`
            gives up on a job that reached it instead of readmitting it
            forever.
        started_at (datetime | None): When the current attempt was
            claimed; ``None`` while queued.
        finished_at (datetime | None): When it reached ``DONE`` or
            ``FAILED``.
    """

    __abstract__ = True

    kind: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        doc="Which work this row represents.",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=JobStatus.QUEUED.value,
        index=True,
        doc="Lifecycle status (JobStatus value).",
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        doc="Small input for the work, serialized as JSON.",
    )
    payload: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        default=None,
        doc="Large input; dropped once the job finishes.",
    )
    result_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        nullable=True,
        default=None,
        doc="Identifier of whatever the work produced.",
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Why the job stopped, in text meant for the user.",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="How many times this job has been claimed.",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        doc="Claim budget before reclaim_stale gives up on the job.",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="When the current attempt was claimed.",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="When the job reached a terminal status.",
    )

    @property
    def is_terminal(self) -> bool:
        """Whether the job will never change status again.

        Returns:
            bool: ``True`` for ``DONE`` and ``FAILED``.
        """
        return self.status in TERMINAL_JOB_STATUSES

    @classmethod
    def new_job(
        cls,
        kind: str,
        *,
        params: dict[str, Any] | None = None,
        payload: bytes | None = None,
        max_attempts: int = 3,
    ) -> BaseJobModel:
        """Build a fresh queued job row.

        Args:
            kind (str): Which work this is.
            params (dict[str, Any] | None): Small JSON input.
            payload (bytes | None): Large binary input.
            max_attempts (int): Claim budget. Defaults to ``3``.

        Returns:
            BaseJobModel: A new ``QUEUED`` instance ready to add to the
            session.
        """
        return cls(
            id=uuid4(),
            kind=kind,
            status=JobStatus.QUEUED.value,
            params=params or {},
            payload=payload,
            attempts=0,
            max_attempts=max_attempts,
        )


def make_job_model(
    *,
    tablename: str = "jobs",
    class_name: str = "JobModel",
) -> type[BaseJobModel]:
    """Build a concrete job model bound to ``tablename``.

    For tests and lightweight scripts; production code should subclass
    :class:`BaseJobModel` by hand so Alembic picks it up statically.

    Args:
        tablename (str): The table name.
        class_name (str): The generated class name.

    Returns:
        type[BaseJobModel]: The concrete model class.
    """
    return type(
        class_name,
        (BaseJobModel,),
        {
            "__tablename__": tablename,
            "__module__": __name__,
            "__qualname__": class_name,
        },
    )


JobT = TypeVar("JobT", bound=BaseJobModel)


class JobStore(Generic[JobT]):
    """Enqueue, claim and close jobs — one short transaction per call.

    The store takes the :class:`~tempest_fastapi_sdk.db.AsyncDatabaseManager`
    rather than a session on purpose. Its callers are a request handler
    that enqueues, a worker that claims and closes, and a UI poll that
    asks every couple of seconds; none of them should hold a session
    across the long work in between — on SQLite that is exactly the
    read-then-write transaction no busy timeout can rescue.

    Example:
        >>> store = JobStore(db, model=JobModel, stale_after=300.0)
        >>> job = await store.enqueue("extract", payload=pdf_bytes)
        >>> await extract_task.enqueue(str(job.id))
        >>> # in the worker
        >>> claimed = await store.claim(job_id)
        >>> if claimed is not None:
        ...     await store.succeed(job_id, result_id=draft.id)

    Attributes:
        stale_after (timedelta | None): How long a job may stay
            ``RUNNING`` before :meth:`reclaim_stale` considers its
            worker dead. ``None`` disables reclaiming.
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        model: type[JobT],
        stale_after: float | timedelta | None = None,
    ) -> None:
        """Initialize the store.

        Args:
            db (AsyncDatabaseManager): The database manager; the store
                opens its own session per call.
            model (type[JobT]): The concrete job model.
            stale_after (float | timedelta | None): Seconds (or a
                ``timedelta``) a job may stay ``RUNNING`` before
                :meth:`reclaim_stale` readmits it. ``None`` — the
                default — means :meth:`reclaim_stale` refuses to run.

        Raises:
            ValueError: If ``stale_after`` is not positive.
        """
        if isinstance(stale_after, int | float):
            stale_after = timedelta(seconds=stale_after)
        if stale_after is not None and stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        self._db: AsyncDatabaseManager = db
        self._model: type[JobT] = model
        self.stale_after: timedelta | None = stale_after

    async def _require(self, session: AsyncSession, job_id: UUID) -> JobT:
        """Load a job or raise.

        Args:
            session (AsyncSession): The open session.
            job_id (UUID): The job to load.

        Returns:
            JobT: The job row.

        Raises:
            JobNotFoundError: When no row has that id.
        """
        job = (
            await session.execute(
                select(self._model).where(self._model.id == job_id),
            )
        ).scalar_one_or_none()
        if job is None:
            raise JobNotFoundError(f"no job with id {job_id}")
        return job

    async def enqueue(
        self,
        kind: str,
        *,
        params: dict[str, Any] | None = None,
        payload: bytes | None = None,
        max_attempts: int = 3,
    ) -> JobT:
        """Record a unit of work as ``QUEUED``.

        Enqueueing the row and enqueueing the task are two steps on
        purpose: the row is what the interface reads, and it must exist
        before the worker can claim it. Write the row first, then send
        the task its id.

        Args:
            kind (str): Which work this is.
            params (dict[str, Any] | None): Small JSON input.
            payload (bytes | None): Large binary input — the file a
                broker should not be carrying.
            max_attempts (int): Claim budget. Defaults to ``3``.

        Returns:
            JobT: The persisted, queued job.
        """
        async with self._db.get_session_context() as session:
            job = self._model.new_job(
                kind,
                params=params,
                payload=payload,
                max_attempts=max_attempts,
            )
            session.add(job)
            await session.flush()
            return cast("JobT", job)

    async def get(self, job_id: UUID) -> JobT:
        """Return one job.

        Args:
            job_id (UUID): The job to read.

        Returns:
            JobT: The job row, payload included.

        Raises:
            JobNotFoundError: When no row has that id.
        """
        async with self._db.get_session_context() as session:
            return await self._require(session, job_id)

    async def claim(self, job_id: UUID) -> JobT | None:
        """Move a queued job to ``RUNNING`` and return it.

        The transition is a single conditional ``UPDATE``, so two
        workers racing for the same id cannot both win: exactly one sees
        a row change, the other gets ``None``.

        ``None`` means "not yours to run" and covers both a job someone
        else already claimed and an id that does not exist — which is
        what a competing worker should do either way. Call :meth:`get`
        when the difference matters.

        Args:
            job_id (UUID): The job to claim.

        Returns:
            JobT | None: The claimed job, payload included, or ``None``
            when it was not claimable.
        """
        async with self._db.get_session_context() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.id == job_id,
                        self._model.status == JobStatus.QUEUED.value,
                    )
                    .values(
                        status=JobStatus.RUNNING.value,
                        started_at=utcnow(),
                        attempts=self._model.attempts + 1,
                    ),
                ),
            )
            if result.rowcount == 0:
                return None
            return await self._require(session, job_id)

    async def _finish(
        self,
        job_id: UUID,
        *,
        status: JobStatus,
        result_id: UUID | None,
        error: str | None,
    ) -> JobT:
        """Move a job to a terminal status, dropping its payload.

        Args:
            job_id (UUID): The job to close.
            status (JobStatus): ``DONE`` or ``FAILED``.
            result_id (UUID | None): What the work produced.
            error (str | None): Why it stopped.

        Returns:
            JobT: The closed job.

        Raises:
            JobNotFoundError: When no row has that id.
            JobAlreadyFinishedError: When the job is already terminal.
        """
        async with self._db.get_session_context() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.id == job_id,
                        self._model.status.notin_(sorted(TERMINAL_JOB_STATUSES)),
                    )
                    .values(
                        status=status.value,
                        result_id=result_id,
                        error=error,
                        payload=None,
                        finished_at=utcnow(),
                    ),
                ),
            )
            if result.rowcount == 0:
                job = await self._require(session, job_id)
                if job.status == JobStatus.CANCELLED.value:
                    raise JobCancelledError(
                        f"job {job_id} was cancelled while it was running",
                    )
                raise JobAlreadyFinishedError(
                    f"job {job_id} is already {job.status}",
                )
            return await self._require(session, job_id)

    async def succeed(self, job_id: UUID, *, result_id: UUID | None = None) -> JobT:
        """Close a job as ``DONE``.

        The payload is dropped here: a table of finished jobs that still
        carries every uploaded document is a table that outgrows the
        data it was serving.

        Args:
            job_id (UUID): The job to close.
            result_id (UUID | None): The row the work produced, so the
                interface can link straight to it.

        Returns:
            JobT: The finished job.

        Raises:
            JobNotFoundError: When no row has that id.
            JobAlreadyFinishedError: When the job is already terminal.
        """
        return await self._finish(
            job_id,
            status=JobStatus.DONE,
            result_id=result_id,
            error=None,
        )

    async def fail(self, job_id: UUID, reason: str) -> JobT:
        """Close a job as ``FAILED``.

        Args:
            job_id (UUID): The job to close.
            reason (str): Why it stopped, written for the person who
                will read it on a screen — not a traceback.

        Returns:
            JobT: The failed job.

        Raises:
            JobNotFoundError: When no row has that id.
            JobAlreadyFinishedError: When the job is already terminal.
        """
        return await self._finish(
            job_id,
            status=JobStatus.FAILED,
            result_id=None,
            error=reason,
        )

    async def cancel(self, job_id: UUID, *, reason: str | None = None) -> JobT | None:
        """Ask a queued or running job to stop.

        This writes ``CANCELLED`` and returns; it does **not** reach into
        the worker. There is no portable way to kill a coroutine running in
        another process, so cancellation is cooperative: the worker reads
        this status at checkpoints and gives up
        (:func:`~tempest_fastapi_sdk.tasks.run_cancellable`). The screen can
        therefore reflect the cancellation immediately, while the work
        behind it takes a few seconds more to actually stop.

        **Idempotent.** Cancelling something that is not running answers
        ``None`` rather than raising — a user double-clicking, or clicking
        just as the job finished on its own, is not an error. That makes
        ``None`` mean "there was nothing to stop", covering an unknown id,
        a job already done, already failed, or already cancelled.

        Args:
            job_id (UUID): The job to cancel.
            reason (str | None): Stored in ``error`` for the screen. Not a
                failure message — say who asked, not what broke.

        Returns:
            JobT | None: The cancelled job, or ``None`` when it was not
            cancellable.
        """
        async with self._db.get_session_context() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.id == job_id,
                        self._model.status.in_(sorted(CANCELLABLE_JOB_STATUSES)),
                    )
                    .values(
                        status=JobStatus.CANCELLED.value,
                        error=reason,
                        payload=None,
                        finished_at=utcnow(),
                    ),
                ),
            )
            if result.rowcount == 0:
                return None
            return await self._require(session, job_id)

    async def is_cancelled(self, job_id: UUID) -> bool:
        """Has this job been cancelled?

        Reads in a **fresh session** on purpose. The worker's own session
        is inside a transaction that started before the cancel was
        committed, so asking it would serve the pre-cancel snapshot — the
        exact opposite of what a checkpoint needs.

        Args:
            job_id (UUID): The job to check.

        Returns:
            bool: ``True`` when the row says ``CANCELLED``. A job that no
            longer exists answers ``True``: there is nothing left to
            produce a result for, so stopping is the right move.
        """
        async with self._db.get_session_context() as session:
            status = (
                await session.execute(
                    select(self._model.status).where(self._model.id == job_id),
                )
            ).scalar_one_or_none()
        if status is None:
            return True
        return bool(status == JobStatus.CANCELLED.value)

    def cancellation_watch(self, job_id: UUID) -> Callable[[], Awaitable[bool]]:
        """Build the predicate :func:`run_cancellable` polls.

        Args:
            job_id (UUID): The job to watch.

        Returns:
            Callable[[], Awaitable[bool]]: A no-argument coroutine
            answering :meth:`is_cancelled`.

        Example:

            >>> await run_cancellable(
            ...     transcribe(audio),
            ...     interrupted=store.cancellation_watch(job.id),
            ... )
        """

        async def _cancelled() -> bool:
            """Answer whether the watched job was cancelled.

            Returns:
                bool: ``True`` when the job is cancelled or gone.
            """
            return await self.is_cancelled(job_id)

        return _cancelled

    async def list_recent(
        self,
        *,
        kind: str | None = None,
        status: JobStatus | str | None = None,
        limit: int = 20,
    ) -> list[JobT]:
        """Return the most recently requested jobs, newest first.

        Returns an empty list when nothing matches — "no jobs yet" is a
        successful answer, not a 404.

        Args:
            kind (str | None): Restrict to one kind of work.
            status (JobStatus | str | None): Restrict to one status.
            limit (int): Maximum rows. Defaults to ``20``.

        Returns:
            list[JobT]: The matching jobs, newest first.

        Raises:
            ValueError: If ``limit`` is not positive.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._db.get_session_context() as session:
            stmt = select(self._model)
            if kind is not None:
                stmt = stmt.where(self._model.kind == kind)
            if status is not None:
                stmt = stmt.where(self._model.status == str(status))
            stmt = stmt.order_by(self._model.created_at.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def reclaim_stale(self) -> int:
        """Readmit jobs whose worker died holding them.

        A ``RUNNING`` row nobody will ever finish is the failure mode a
        queue cannot see: the task is gone, the row is not. Rows whose
        ``started_at`` is older than :attr:`stale_after` go back to
        ``QUEUED`` — unless they already spent their ``max_attempts``,
        in which case they are closed as ``FAILED``, because a job that
        kills its worker would otherwise be readmitted forever.

        Two conditional ``UPDATE`` statements over disjoint conditions,
        deliberately with **no ``SELECT`` first**: a sweep that reads the
        rows and then writes them is a lock promotion, which on SQLite
        fails outright against a concurrent writer — measured, and the
        reason ``busy_timeout`` cannot help there (see
        :func:`~tempest_fastapi_sdk.enable_sqlite_wal`). The cost is that
        the give-up message names the budget rather than the row's own
        attempt count.

        Returns:
            int: How many rows left ``RUNNING`` — requeued plus given up
            on. The two are logged separately.

        Raises:
            RuntimeError: When the store was built without
                ``stale_after``, since there is no threshold to apply.
        """
        if self.stale_after is None:
            raise RuntimeError(
                "JobStore.reclaim_stale needs stale_after; "
                "build the store with JobStore(db, model=..., stale_after=300.0)",
            )
        cutoff = utcnow() - self.stale_after
        async with self._db.get_session_context() as session:
            given_up = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.status == JobStatus.RUNNING.value,
                        self._model.started_at < cutoff,
                        self._model.attempts >= self._model.max_attempts,
                    )
                    .values(
                        status=JobStatus.FAILED.value,
                        error=STALE_JOB_ERROR,
                        payload=None,
                        finished_at=utcnow(),
                    ),
                ),
            ).rowcount
            requeued = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.status == JobStatus.RUNNING.value,
                        self._model.started_at < cutoff,
                        self._model.attempts < self._model.max_attempts,
                    )
                    .values(
                        status=JobStatus.QUEUED.value,
                        started_at=None,
                    ),
                ),
            ).rowcount
        if requeued or given_up:
            logger.warning(
                "reclaimed %d stale job(s): %d requeued, %d failed",
                requeued + given_up,
                requeued,
                given_up,
            )
        return int(requeued + given_up)

    async def watch(
        self,
        job_id: UUID,
        *,
        interval: float = 2.0,
        timeout: float | None = None,
    ) -> AsyncIterator[JobT]:
        """Yield the job on every status change until it is terminal.

        This replaces the ``while True: sleep; get`` every application
        writes by hand — including the part that is easy to get wrong:
        **no session is held between ticks**. Each poll opens and closes
        its own, so a worker writing to the same database is never
        blocked by the screen watching it.

        The current status is yielded immediately, so a caller that
        subscribes after the job already finished still gets exactly one
        value and the loop ends.

        Example:
            >>> async for job in store.watch(job_id, interval=2.0):
            ...     await session.render(job.status)

        Args:
            job_id (UUID): The job to follow.
            interval (float): Seconds between polls. Defaults to ``2.0``.
            timeout (float | None): Give up after this many seconds.
                ``None`` — the default — waits for a terminal status.

        Yields:
            JobT: The job, each time its status differs from the last
            value yielded.

        Raises:
            JobNotFoundError: When no row has that id.
            TimeoutError: When ``timeout`` elapses with the job still
                running or queued.
            ValueError: If ``interval`` is not positive.
        """
        if interval <= 0:
            raise ValueError("interval must be positive")
        deadline = None if timeout is None else monotonic() + timeout
        last_status: str | None = None
        while True:
            job = await self.get(job_id)
            if job.status != last_status:
                last_status = job.status
                yield job
            if job.is_terminal:
                return
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError(
                    f"job {job_id} still {job.status} after {timeout}s",
                )
            await asyncio.sleep(interval)


__all__: list[str] = [
    "STALE_JOB_ERROR",
    "TERMINAL_JOB_STATUSES",
    "BaseJobModel",
    "JobAlreadyFinishedError",
    "JobNotFoundError",
    "JobStatus",
    "JobStore",
    "make_job_model",
]
