"""Transactional outbox: persist events in the same tx as the write.

The outbox pattern fixes the dual-write problem. When a handler both
writes a row **and** publishes an event, doing them as two independent
operations is unsafe: if the process dies after the commit but before
the publish, the event is lost; if it dies after the publish but before
the commit, a phantom event references a row that never existed.

The fix: write the business row **and** an ``outbox`` row in the **same
database transaction**. Either both commit or neither does. A separate
relay process then reads pending outbox rows and publishes them to the
broker, marking each one sent. The broker can be down for minutes — the
events wait durably in the table until it recovers.

This module ships three pieces:

* :class:`OutboxStatus` — the lifecycle enum (pending → sent / failed).
* :class:`BaseOutboxModel` — the abstract outbox table; the consuming
  project subclasses it (picks ``__tablename__``), exactly like
  :class:`~tempest_fastapi_sdk.db.user_model.BaseUserModel`.
* :class:`OutboxRelay` — drains pending rows and publishes them through
  a caller-supplied async ``publish`` callable, so the relay never has
  to import a specific broker (works with
  :class:`~tempest_fastapi_sdk.queue.AsyncBrokerManager` or anything
  else).

The write side is
:meth:`~tempest_fastapi_sdk.db.repository.BaseRepository.save_with_outbox`,
which adds the business model and the outbox event and commits them
together.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import JSON, TIMESTAMP, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

logger = logging.getLogger("tempest_fastapi_sdk.db.outbox")


class OutboxStatus(StrEnum):
    """Lifecycle of an outbox event.

    * ``PENDING`` — written, not yet published. The relay picks these.
    * ``SENT`` — successfully published to the broker.
    * ``FAILED`` — exhausted ``max_attempts``; needs manual attention
      (kept in the table for inspection, never auto-retried again).
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class BaseOutboxModel(BaseModel):
    """Abstract outbox table — one row per event awaiting publication.

    The consuming project subclasses this and picks a
    ``__tablename__`` (``outbox`` by convention), mirroring how
    :class:`~tempest_fastapi_sdk.db.user_model.BaseUserModel` is
    subclassed. The row carries everything the relay needs to publish
    and to retry with backoff.

    Inherits the canonical four columns from
    :class:`~tempest_fastapi_sdk.db.model.BaseModel` (``id``,
    ``is_active``, ``created_at``, ``updated_at``).

    Attributes:
        topic (str): Where the event goes — a queue name, routing key,
            exchange/topic, etc. Interpreted by the ``publish`` callable.
        payload (dict[str, Any]): The event body, stored as JSON.
        status (str): One of :class:`OutboxStatus`. Indexed so the
            relay's ``WHERE status = 'pending'`` scan stays cheap.
        attempts (int): How many publish attempts have been made.
        max_attempts (int): Attempt budget; once ``attempts`` reaches
            it the row is marked ``FAILED`` instead of retried.
        available_at (datetime): Earliest time the relay may pick this
            row. Pushed into the future on each failure for backoff.
        sent_at (datetime | None): When the row was published, or
            ``None`` while pending/failed.
        last_error (str | None): The last publish error, for triage.
    """

    __abstract__ = True

    topic: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Destination topic / queue / routing key for the event.",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        doc="Event body, serialized as JSON.",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=OutboxStatus.PENDING.value,
        index=True,
        doc="Lifecycle status (OutboxStatus value).",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of publish attempts made so far.",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        doc="Attempt budget before the row is marked FAILED.",
    )
    available_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utcnow,
        index=True,
        doc="Earliest time the relay may pick this row (retry backoff).",
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="When the event was published, or NULL while unpublished.",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="The last publish error message, for triage.",
    )

    @classmethod
    def new_event(
        cls,
        topic: str,
        payload: dict[str, Any],
        *,
        max_attempts: int = 5,
    ) -> BaseOutboxModel:
        """Build a fresh pending outbox row.

        Convenience constructor so call sites read as
        ``OutboxModel.new_event("orders.created", {...})`` instead of
        spelling out every default.

        Args:
            topic (str): Destination topic / queue / routing key.
            payload (dict[str, Any]): The event body (JSON-serializable).
            max_attempts (int): Attempt budget before the row fails.
                Defaults to ``5``.

        Returns:
            BaseOutboxModel: A new ``PENDING`` instance ready to add to
            the session.
        """
        return cls(
            id=uuid4(),
            topic=topic,
            payload=payload,
            status=OutboxStatus.PENDING.value,
            attempts=0,
            max_attempts=max_attempts,
            available_at=utcnow(),
        )


class OutboxRelay:
    """Drain pending outbox rows and publish them through a callable.

    The relay is deliberately decoupled from any specific broker: it
    calls a caller-supplied async ``publish`` function with each event,
    so it works with :class:`~tempest_fastapi_sdk.queue.AsyncBrokerManager`,
    a raw FastStream broker, an HTTP webhook, or a test spy.

    On a backend that supports it (PostgreSQL, MySQL), the relay locks
    the batch with ``FOR UPDATE SKIP LOCKED`` so several relay workers
    can run concurrently without publishing the same row twice. On
    SQLite (no row locks) it falls back to a plain select — fine for a
    single worker.

    Example:
        >>> relay = OutboxRelay(
        ...     db,
        ...     model=OutboxModel,
        ...     publish=lambda e: broker.publish(e.payload, e.topic),
        ... )
        >>> await relay.run(poll_interval=1.0)  # loops until cancelled

    Attributes:
        batch_size (int): Max rows drained per :meth:`drain_once`.
        backoff_base_seconds (float): Base for exponential retry
            backoff (``base * 2 ** attempts`` seconds).
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        model: type[BaseOutboxModel],
        publish: Callable[[BaseOutboxModel], Awaitable[Any]],
        batch_size: int = 100,
        backoff_base_seconds: float = 2.0,
    ) -> None:
        """Initialize the relay.

        Args:
            db (AsyncDatabaseManager): The database manager — the relay
                opens its own short transactions per drain.
            model (type[BaseOutboxModel]): The concrete outbox model.
            publish (Callable[[BaseOutboxModel], Awaitable[Any]]): Async
                callable that publishes one event. It receives the
                outbox row; raise to signal a failure (the row is
                retried with backoff).
            batch_size (int): Max rows drained per call. Defaults to
                ``100``.
            backoff_base_seconds (float): Base for the exponential
                retry backoff. Defaults to ``2.0``.

        Raises:
            ValueError: If ``batch_size`` is not positive.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._db: AsyncDatabaseManager = db
        self._model: type[BaseOutboxModel] = model
        self._publish: Callable[[BaseOutboxModel], Awaitable[Any]] = publish
        self.batch_size: int = batch_size
        self.backoff_base_seconds: float = backoff_base_seconds

    async def drain_once(self) -> int:
        """Publish one batch of due pending events.

        Selects up to :attr:`batch_size` rows that are ``PENDING`` and
        whose ``available_at`` is in the past, publishes each, and marks
        it ``SENT`` — or, on failure, increments ``attempts``, records
        the error, and either reschedules it (backoff) or marks it
        ``FAILED`` once the attempt budget is spent. All within one
        transaction.

        Returns:
            int: The number of events successfully published.
        """
        published = 0
        async with self._db.get_session_context() as session:
            now = utcnow()
            stmt = (
                select(self._model)
                .where(
                    self._model.status == OutboxStatus.PENDING.value,
                    self._model.available_at <= now,
                )
                .order_by(self._model.available_at)
                .limit(self.batch_size)
            )
            if session.bind is not None and session.bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)

            events = list((await session.execute(stmt)).scalars().all())
            for event in events:
                try:
                    await self._publish(event)
                except Exception as exc:
                    self._mark_failure(event, exc)
                else:
                    event.status = OutboxStatus.SENT.value
                    event.sent_at = utcnow()
                    published += 1
        return published

    def _mark_failure(self, event: BaseOutboxModel, exc: Exception) -> None:
        """Record a publish failure and reschedule or fail the event.

        Args:
            event (BaseOutboxModel): The row that failed to publish.
            exc (Exception): The error raised by the ``publish`` callable.
        """
        event.attempts += 1
        event.last_error = str(exc)
        if event.attempts >= event.max_attempts:
            event.status = OutboxStatus.FAILED.value
            logger.error(
                "outbox event %s exhausted %d attempts, marking FAILED: %s",
                event.id,
                event.max_attempts,
                exc,
            )
            return
        delay = self.backoff_base_seconds * (2 ** (event.attempts - 1))
        event.available_at = utcnow() + timedelta(seconds=delay)
        logger.warning(
            "outbox event %s publish failed (attempt %d/%d), retry in %.1fs: %s",
            event.id,
            event.attempts,
            event.max_attempts,
            delay,
            exc,
        )

    async def run(
        self,
        *,
        poll_interval: float = 1.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Loop :meth:`drain_once` forever (or until ``stop_event``).

        Sleeps ``poll_interval`` seconds between drains only when the
        last drain published nothing — a non-empty batch is followed by
        an immediate drain so a backlog clears fast.

        Args:
            poll_interval (float): Seconds to sleep when idle. Defaults
                to ``1.0``.
            stop_event (asyncio.Event | None): When set, the loop exits
                after finishing the current drain. Wire it to your
                shutdown handler. When ``None``, the loop runs until the
                task is cancelled.
        """
        while stop_event is None or not stop_event.is_set():
            try:
                published = await self.drain_once()
            except Exception:
                logger.exception("outbox relay drain failed; backing off")
                published = 0
            if published == 0:
                await asyncio.sleep(poll_interval)


__all__: list[str] = [
    "BaseOutboxModel",
    "OutboxRelay",
    "OutboxStatus",
]
