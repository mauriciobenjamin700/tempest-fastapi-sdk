"""Inbound webhooks: record raw, answer 200, process once, give up eventually.

A provider that delivers webhooks retries until it sees a ``2xx``, and most
of them say plainly that the same event can arrive more than once. A route
that does the work inline has two problems at once: a slow handler runs past
the provider's timeout and gets the delivery retried, and a retried delivery
runs the handler twice.

The inbox is the consumer-side mirror of
:mod:`tempest_fastapi_sdk.db.outbox`:

1. **record** — the route writes the raw event keyed by
   ``(provider, external_id)`` and answers ``200``. A repeated delivery hits
   the unique constraint and records nothing; the route still answers ``200``,
   because the provider only needs to stop retrying.
2. **claim** — a worker moves the row from ``RECEIVED`` to ``PROCESSING``
   with one conditional ``UPDATE``. Two workers racing for the same id cannot
   both win.
3. **succeed** or **fail** — the handler runs inside a transaction that also
   marks the row ``PROCESSED``, so a database effect and the mark commit
   together. A failure puts the row back with exponential backoff, and once
   ``attempts`` reaches ``max_attempts`` the row is ``FAILED`` and leaves the
   sweep for good.

The last step is the one services get wrong by hand. A sweep that retries
whatever raised, without a budget, re-runs a poisoned event every few minutes
forever; ``max_attempts`` is a column here so it cannot be forgotten.

* :class:`WebhookEventStatusEnum` — the lifecycle.
* :class:`BaseWebhookEventModel` — the abstract table; the project subclasses
  it and picks ``__tablename__``, exactly like
  :class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel`.
* :class:`WebhookInbox` — record, claim, process, fail, the sweep
  (:meth:`WebhookInbox.drain_once`) and the stale-claim reclaim.

Nothing here knows a provider. The verification of each provider's
signature stays in its integration (``integrations.payment.stripe``,
``integrations.subscriptions.revenuecat``, ...); what this module receives is
an id that provider promises is stable across retries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CursorResult,
    Integer,
    String,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

logger = logging.getLogger("tempest_fastapi_sdk.db.webhook_inbox")

DEFAULT_WEBHOOK_MAX_ATTEMPTS: int = 5
"""Processing attempts a recorded event gets before it is ``FAILED``."""

DEFAULT_WEBHOOK_BACKOFF_SECONDS: float = 60.0
"""Base of the exponential backoff between attempts (``base * 2 ** (n - 1)``).

One minute, then two, four, eight: a dependency that is down for a few
minutes gets the time to come back before the budget is spent.
"""

STALE_WEBHOOK_ERROR: str = (
    "The worker stopped responding while processing this event, "
    "and the attempt budget is spent."
)
"""``last_error`` written by :meth:`WebhookInbox.reclaim_stale` when it gives up."""

_ERROR_MAX_LENGTH: int = 2000
"""How much of a handler's error message is kept in ``last_error``."""


class WebhookEventStatusEnum(BaseStrEnum):
    """Lifecycle of a recorded webhook event.

    * ``RECEIVED`` — recorded, waiting for a worker (also where a failed
      attempt goes back to, until the budget is spent).
    * ``PROCESSING`` — a worker claimed it (see :meth:`WebhookInbox.claim`).
    * ``PROCESSED`` — the handler finished and its transaction committed.
    * ``FAILED`` — ``attempts`` reached ``max_attempts``. Terminal: the sweep
      never picks it again, so it waits for a person.
    """

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class BaseWebhookEventModel(BaseModel):
    """Abstract inbox table — one row per distinct inbound event.

    The consuming project subclasses this and picks a ``__tablename__``
    (``webhook_events`` by convention). The unique constraint on
    ``(provider, external_id)`` is declared here rather than left to the
    subclass: it **is** the idempotency, and a subclass that forgot it would
    process every retry.

    Inherits the canonical four columns from
    :class:`~tempest_fastapi_sdk.db.model.BaseModel` (``id``, ``is_active``,
    ``created_at``, ``updated_at``), so ``created_at`` is when the delivery
    arrived.

    Attributes:
        provider (str): Who sent it (``"revenuecat"``, ``"stripe"``, ...).
        external_id (str): The provider's event id — the value the provider
            promises is the same on every retry of one event.
        event_type (str): The provider's event type, as delivered. Indexed,
            because "every ``EXPIRATION`` that failed" is a real question.
        payload (dict[str, Any]): The event as JSON. Whatever the caller
            passes to :meth:`WebhookInbox.record` — strip personal data
            before that, not after.
        status (str): One of :class:`WebhookEventStatusEnum`. Indexed.
        attempts (int): Processing attempts so far, incremented by
            :meth:`WebhookInbox.claim`.
        max_attempts (int): Budget; reaching it makes a failure terminal.
        available_at (datetime): Earliest time the sweep may claim the row.
            Pushed forward on each failure.
        started_at (datetime | None): When the current attempt was claimed.
        processed_at (datetime | None): When the handler's transaction
            committed.
        last_error (str | None): The last failure, for triage.
    """

    __abstract__ = True

    provider: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Who sent the event.",
    )
    external_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="The provider's event id, stable across retries.",
    )
    event_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        doc="The provider's event type, as delivered.",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        doc="The event body, serialized as JSON.",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=WebhookEventStatusEnum.RECEIVED.value,
        index=True,
        doc="Lifecycle status (WebhookEventStatusEnum value).",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Processing attempts made so far.",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_WEBHOOK_MAX_ATTEMPTS,
        doc="Attempt budget before the event is marked FAILED.",
    )
    available_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        default=utcnow,
        index=True,
        doc="Earliest time the sweep may claim this row (retry backoff).",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="When the current attempt was claimed.",
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="When the handler's transaction committed.",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="The last processing error, for triage.",
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:  # noqa: N805
        """Declare the idempotency constraint on every concrete subclass.

        Built per subclass because a
        :class:`~sqlalchemy.UniqueConstraint` instance binds to exactly one
        ``Table``. The name comes from the metadata naming convention, so
        Alembic sees the same identifier on every machine.

        Returns:
            tuple[Any, ...]: The ``(provider, external_id)`` unique
            constraint.
        """
        return (UniqueConstraint("provider", "external_id"),)


def make_webhook_event_model(
    *,
    tablename: str = "webhook_events",
    class_name: str = "WebhookEventModel",
) -> type[BaseWebhookEventModel]:
    """Build a concrete inbox model bound to ``tablename``.

    For tests and lightweight scripts; production code should subclass
    :class:`BaseWebhookEventModel` by hand so Alembic picks it up statically.

    Args:
        tablename (str): The table name.
        class_name (str): The generated class name.

    Returns:
        type[BaseWebhookEventModel]: The concrete model class.
    """
    return type(
        class_name,
        (BaseWebhookEventModel,),
        {
            "__tablename__": tablename,
            "__module__": __name__,
            "__qualname__": class_name,
        },
    )


@dataclass(slots=True)
class InboxDrainReport:
    """What one :meth:`WebhookInbox.drain_once` did.

    Attributes:
        processed (list[UUID]): Events whose handler committed.
        retrying (list[UUID]): Events that failed and went back to
            ``RECEIVED`` with backoff.
        failed (list[UUID]): Events that failed for the last time and are
            now ``FAILED`` — including stale claims given up on.
        skipped (list[UUID]): Events another worker claimed first.
    """

    processed: list[UUID] = field(default_factory=list)
    retrying: list[UUID] = field(default_factory=list)
    failed: list[UUID] = field(default_factory=list)
    skipped: list[UUID] = field(default_factory=list)


EventT = TypeVar("EventT", bound=BaseWebhookEventModel)


class _AlreadyFinishedError(RuntimeError):
    """Raised inside the handler's transaction to roll its effect back.

    The row left ``PROCESSING`` while the handler ran — a stale reclaim
    readmitted it — so committing would apply the effect a second time
    beside whoever holds the row now.
    """


class WebhookInbox(Generic[EventT]):
    """Record inbound events once and process each at most to its budget.

    Like :class:`~tempest_fastapi_sdk.tasks.JobStore`, the inbox takes the
    :class:`~tempest_fastapi_sdk.db.AsyncDatabaseManager` and opens one short
    transaction per call, so neither the route nor the worker holds a session
    across the provider's wait or the handler's network calls.

    Example:
        >>> inbox = WebhookInbox(db, model=WebhookEventModel)
        >>> event_id = await inbox.record(
        ...     "revenuecat", event.id, event.type, payload
        ... )
        >>> # in the worker or the scheduled sweep
        >>> await inbox.drain_once(apply_event)

    Attributes:
        max_attempts (int): Budget given to newly recorded events.
        backoff_base_seconds (float): Base of the exponential backoff.
        stale_after (timedelta | None): How long a claim may stay
            ``PROCESSING`` before :meth:`reclaim_stale` considers its worker
            dead. ``None`` disables reclaiming.
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        model: type[EventT],
        max_attempts: int = DEFAULT_WEBHOOK_MAX_ATTEMPTS,
        backoff_base_seconds: float = DEFAULT_WEBHOOK_BACKOFF_SECONDS,
        stale_after: float | timedelta | None = 600.0,
    ) -> None:
        """Initialize the inbox.

        Args:
            db (AsyncDatabaseManager): The database manager; the inbox opens
                its own session per call.
            model (type[EventT]): The concrete inbox model.
            max_attempts (int): Attempt budget for new events. Defaults to
                ``5``.
            backoff_base_seconds (float): Base of the backoff between
                attempts, in seconds. ``0`` retries on the next sweep.
            stale_after (float | timedelta | None): Seconds (or a
                ``timedelta``) a claim may last before :meth:`reclaim_stale`
                readmits it. Ten minutes by default — longer than any
                handler should take. ``None`` disables reclaiming.

        Raises:
            ValueError: If ``max_attempts`` is below ``1``, the backoff is
                negative, or ``stale_after`` is not positive.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must not be negative")
        if isinstance(stale_after, int | float):
            stale_after = timedelta(seconds=stale_after)
        if stale_after is not None and stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        self._db: AsyncDatabaseManager = db
        self._model: type[EventT] = model
        self.max_attempts: int = max_attempts
        self.backoff_base_seconds: float = backoff_base_seconds
        self.stale_after: timedelta | None = stale_after

    async def record(
        self,
        provider: str,
        external_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> UUID | None:
        """Store one delivery unless the same event was already stored.

        On PostgreSQL and SQLite this is ``INSERT ... ON CONFLICT DO NOTHING
        RETURNING id`` — no exception, no aborted transaction. Other
        dialects insert inside a savepoint and treat the ``IntegrityError``
        as the duplicate.

        Args:
            provider (str): Who sent it.
            external_id (str): The provider's event id.
            event_type (str): The provider's event type.
            payload (Mapping[str, Any]): The event, JSON-serializable. Remove
                what you do not want stored before calling.

        Returns:
            UUID | None: The new row's id, or ``None`` when
            ``(provider, external_id)`` is already recorded — answer the
            provider ``200`` either way.

        Raises:
            ValueError: If ``provider`` or ``external_id`` is empty — an
                empty id would make every delivery a duplicate of the first.
        """
        if not provider or not external_id:
            raise ValueError("provider and external_id must not be empty")
        event_id = uuid4()
        now = utcnow()
        values: dict[str, Any] = {
            "id": event_id,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
            "provider": provider,
            "external_id": external_id,
            "event_type": event_type,
            "payload": dict(payload),
            "status": WebhookEventStatusEnum.RECEIVED.value,
            "attempts": 0,
            "max_attempts": self.max_attempts,
            "available_at": now,
        }
        async with self._db.get_session_context() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect in {"postgresql", "sqlite"}:
                return await self._insert_on_conflict(session, dialect, values)
            try:
                async with session.begin_nested():
                    await session.execute(insert(self._model).values(**values))
            except IntegrityError:
                return None
            return event_id

    async def _insert_on_conflict(
        self,
        session: AsyncSession,
        dialect: str,
        values: dict[str, Any],
    ) -> UUID | None:
        """Insert with the dialect's ``ON CONFLICT DO NOTHING``.

        Args:
            session (AsyncSession): The open session.
            dialect (str): ``"postgresql"`` or ``"sqlite"``.
            values (dict[str, Any]): The full row.

        Returns:
            UUID | None: The inserted id, or ``None`` on a duplicate.
        """
        conflict_on: list[str] = ["provider", "external_id"]
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt: Any = (
                pg_insert(self._model)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict_on)
            )
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = (
                sqlite_insert(self._model)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict_on)
            )
        inserted: UUID | None = (
            await session.execute(stmt.returning(self._model.id))
        ).scalar_one_or_none()
        return inserted

    async def get(self, event_id: UUID) -> EventT | None:
        """Return one recorded event.

        Args:
            event_id (UUID): The inbox row id.

        Returns:
            EventT | None: The row, or ``None`` when no row has that id.
        """
        async with self._db.get_session_context() as session:
            return (
                await session.execute(
                    select(self._model).where(self._model.id == event_id),
                )
            ).scalar_one_or_none()

    async def claim(self, event_id: UUID) -> EventT | None:
        """Move a due ``RECEIVED`` event to ``PROCESSING`` and return it.

        One conditional ``UPDATE``: of two workers racing for the same id,
        exactly one sees a row change and the other gets ``None``. A row
        whose backoff has not elapsed is not claimable either.

        Args:
            event_id (UUID): The inbox row id.

        Returns:
            EventT | None: The claimed row, or ``None`` when it was not
            claimable (claimed by someone else, finished, failed, in
            backoff, or unknown).
        """
        now = utcnow()
        async with self._db.get_session_context() as session:
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.id == event_id,
                        self._model.status == WebhookEventStatusEnum.RECEIVED.value,
                        self._model.available_at <= now,
                    )
                    .values(
                        status=WebhookEventStatusEnum.PROCESSING.value,
                        started_at=now,
                        attempts=self._model.attempts + 1,
                    ),
                ),
            )
            if result.rowcount == 0:
                return None
            return (
                await session.execute(
                    select(self._model).where(self._model.id == event_id),
                )
            ).scalar_one()

    async def fail(self, event_id: UUID, error: str) -> WebhookEventStatusEnum | None:
        """Record a failed attempt: back to ``RECEIVED``, or ``FAILED``.

        Two conditional ``UPDATE`` statements over disjoint conditions and no
        ``SELECT`` first (the read-then-write shape fails on SQLite against a
        concurrent writer). The first closes a row whose budget is spent;
        the second readmits the rest and returns ``attempts`` so the backoff
        can be computed, which a third ``UPDATE`` writes.

        Args:
            event_id (UUID): The claimed row.
            error (str): Why it failed. Truncated to 2000 characters.

        Returns:
            WebhookEventStatusEnum | None: ``FAILED`` when the budget is
            spent, ``RECEIVED`` when it will be retried, ``None`` when the
            row was not ``PROCESSING`` (already closed or reclaimed).
        """
        message = error[:_ERROR_MAX_LENGTH]
        processing = WebhookEventStatusEnum.PROCESSING.value
        async with self._db.get_session_context() as session:
            given_up = (
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.id == event_id,
                        self._model.status == processing,
                        self._model.attempts >= self._model.max_attempts,
                    )
                    .values(
                        status=WebhookEventStatusEnum.FAILED.value,
                        last_error=message,
                    )
                    .returning(self._model.id),
                )
            ).scalar_one_or_none()
            if given_up is not None:
                logger.error(
                    "webhook event %s exhausted its attempts, marking FAILED: %s",
                    event_id,
                    message,
                )
                return WebhookEventStatusEnum.FAILED
            attempts = (
                await session.execute(
                    update(self._model)
                    .where(
                        self._model.id == event_id,
                        self._model.status == processing,
                    )
                    .values(
                        status=WebhookEventStatusEnum.RECEIVED.value,
                        started_at=None,
                        last_error=message,
                    )
                    .returning(self._model.attempts),
                )
            ).scalar_one_or_none()
            if attempts is None:
                return None
            delay = self.backoff_base_seconds * (2 ** max(attempts - 1, 0))
            await session.execute(
                update(self._model)
                .where(self._model.id == event_id)
                .values(available_at=utcnow() + timedelta(seconds=delay)),
            )
        logger.warning(
            "webhook event %s failed (attempt %d), retry in %.1fs: %s",
            event_id,
            attempts,
            delay,
            message,
        )
        return WebhookEventStatusEnum.RECEIVED

    async def process(
        self,
        event_id: UUID,
        handler: Callable[[EventT, AsyncSession], Awaitable[object]],
    ) -> WebhookEventStatusEnum | None:
        """Claim one event, run ``handler`` and close the row.

        The handler receives the claimed row and an open session, and the
        row is marked ``PROCESSED`` **in that same transaction**. Whatever
        the handler writes through the session commits together with the
        mark, or not at all — so a crash between the effect and the mark
        cannot apply the effect twice. Effects outside the database (an
        e-mail, a call to another API) are not covered by that transaction;
        make those idempotent on the event id.

        If the handler raises, its transaction rolls back and
        :meth:`fail` records the attempt.

        Args:
            event_id (UUID): The inbox row id.
            handler (Callable[[EventT, AsyncSession], Awaitable[object]]):
                Applies the event. Its return value is ignored.

        Returns:
            WebhookEventStatusEnum | None: ``PROCESSED``, ``RECEIVED`` (will
            be retried), ``FAILED`` (budget spent), or ``None`` when the row
            was not claimable.
        """
        event = await self.claim(event_id)
        if event is None:
            return None
        try:
            async with self._db.get_session_context() as session:
                await handler(event, session)
                result = cast(
                    "CursorResult[Any]",
                    await session.execute(
                        update(self._model)
                        .where(
                            self._model.id == event_id,
                            self._model.status
                            == WebhookEventStatusEnum.PROCESSING.value,
                        )
                        .values(
                            status=WebhookEventStatusEnum.PROCESSED.value,
                            processed_at=utcnow(),
                            last_error=None,
                        ),
                    ),
                )
                if result.rowcount == 0:
                    raise _AlreadyFinishedError(str(event_id))
        except _AlreadyFinishedError:
            logger.warning(
                "webhook event %s left PROCESSING while its handler ran; "
                "the handler's transaction was rolled back",
                event_id,
            )
            return None
        except Exception as exc:
            return await self.fail(event_id, f"{type(exc).__name__}: {exc}")
        return WebhookEventStatusEnum.PROCESSED

    async def due(self, *, limit: int = 100) -> list[UUID]:
        """List the ids of ``RECEIVED`` events whose backoff has elapsed.

        Args:
            limit (int): Maximum ids returned, oldest delivery first.

        Returns:
            list[UUID]: The ids, possibly empty.
        """
        async with self._db.get_session_context() as session:
            stmt = (
                select(self._model.id)
                .where(
                    self._model.status == WebhookEventStatusEnum.RECEIVED.value,
                    self._model.available_at <= utcnow(),
                )
                .order_by(self._model.created_at)
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def reclaim_stale(self) -> InboxDrainReport:
        """Readmit events whose worker died holding the claim.

        A ``PROCESSING`` row older than :attr:`stale_after` goes back to
        ``RECEIVED`` — unless its attempts are spent, in which case it is
        ``FAILED`` with :data:`STALE_WEBHOOK_ERROR`. Without the budget, an
        event that kills its worker would be readmitted forever.

        Returns:
            InboxDrainReport: ``retrying`` holds the readmitted ids and
            ``failed`` the ones given up on. Empty when :attr:`stale_after`
            is ``None``.
        """
        report = InboxDrainReport()
        if self.stale_after is None:
            return report
        cutoff = utcnow() - self.stale_after
        processing = WebhookEventStatusEnum.PROCESSING.value
        async with self._db.get_session_context() as session:
            report.failed = list(
                (
                    await session.execute(
                        update(self._model)
                        .where(
                            self._model.status == processing,
                            self._model.started_at < cutoff,
                            self._model.attempts >= self._model.max_attempts,
                        )
                        .values(
                            status=WebhookEventStatusEnum.FAILED.value,
                            last_error=STALE_WEBHOOK_ERROR,
                        )
                        .returning(self._model.id),
                    )
                )
                .scalars()
                .all(),
            )
            report.retrying = list(
                (
                    await session.execute(
                        update(self._model)
                        .where(
                            self._model.status == processing,
                            self._model.started_at < cutoff,
                            self._model.attempts < self._model.max_attempts,
                        )
                        .values(
                            status=WebhookEventStatusEnum.RECEIVED.value,
                            started_at=None,
                        )
                        .returning(self._model.id),
                    )
                )
                .scalars()
                .all(),
            )
        if report.failed or report.retrying:
            logger.warning(
                "reclaimed stale webhook events: %d readmitted, %d failed",
                len(report.retrying),
                len(report.failed),
            )
        return report

    async def drain_once(
        self,
        handler: Callable[[EventT, AsyncSession], Awaitable[object]],
        *,
        limit: int = 100,
    ) -> InboxDrainReport:
        """Reclaim stale claims, then process every due event once.

        This is the body of the scheduled sweep. Run it from a cron task (or
        any loop); several replicas running it at the same time is safe,
        because each event is claimed by exactly one of them.

        Args:
            handler (Callable[[EventT, AsyncSession], Awaitable[object]]):
                Applies one event; see :meth:`process`.
            limit (int): Maximum events processed in this pass.

        Returns:
            InboxDrainReport: What happened to each event touched.
        """
        report = await self.reclaim_stale()
        for event_id in await self.due(limit=limit):
            outcome = await self.process(event_id, handler)
            if outcome is WebhookEventStatusEnum.PROCESSED:
                report.processed.append(event_id)
            elif outcome is WebhookEventStatusEnum.RECEIVED:
                report.retrying.append(event_id)
            elif outcome is WebhookEventStatusEnum.FAILED:
                report.failed.append(event_id)
            else:
                report.skipped.append(event_id)
        return report


__all__: list[str] = [
    "DEFAULT_WEBHOOK_BACKOFF_SECONDS",
    "DEFAULT_WEBHOOK_MAX_ATTEMPTS",
    "STALE_WEBHOOK_ERROR",
    "BaseWebhookEventModel",
    "InboxDrainReport",
    "WebhookEventStatusEnum",
    "WebhookInbox",
    "make_webhook_event_model",
]
