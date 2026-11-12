"""Projections: consume ``$all`` from a checkpoint into a read model.

A projection is a handler plus a name. :class:`ProjectionRunner` reads the
registered events after the projection's checkpoint, hands each one to the
handler, and moves the checkpoint forward. Delivery is **at-least-once**:
a crash after the handler ran and before the checkpoint was saved replays
that event, so handlers upsert by ``event_id`` or by the version they hold.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk.eventsourcing.events import RecordedEvent
from tempest_fastapi_sdk.eventsourcing.store import EventStore

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.eventsourcing.models import (
        BaseProjectionCheckpointModel,
    )

logger = logging.getLogger("tempest_fastapi_sdk.eventsourcing.projection")


@runtime_checkable
class CheckpointStore(Protocol):
    """Where a projection keeps the last ``$all`` position it processed."""

    async def load(self, name: str, /) -> int | None:
        """Return the checkpoint of a projection.

        Args:
            name (str): The projection name.

        Returns:
            int | None: The last processed position, or ``None``.
        """
        ...

    async def save(self, name: str, position: int, /) -> None:
        """Store the checkpoint of a projection.

        Args:
            name (str): The projection name.
            position (int): The last processed position.
        """
        ...


class InMemoryCheckpointStore:
    """Checkpoints in a dict — for tests and projections rebuilt at boot."""

    def __init__(self) -> None:
        """Create an empty store."""
        self.positions: dict[str, int] = {}

    async def load(self, name: str, /) -> int | None:
        """Return the checkpoint of a projection.

        Args:
            name (str): The projection name.

        Returns:
            int | None: The last processed position, or ``None``.
        """
        return self.positions.get(name)

    async def save(self, name: str, position: int, /) -> None:
        """Store the checkpoint of a projection.

        Args:
            name (str): The projection name.
            position (int): The last processed position.
        """
        self.positions[name] = position


class SqlCheckpointStore:
    """Checkpoints in a :class:`BaseProjectionCheckpointModel` table."""

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        model: type[BaseProjectionCheckpointModel],
    ) -> None:
        """Initialize the store.

        Args:
            db (AsyncDatabaseManager): The database manager.
            model (type[BaseProjectionCheckpointModel]): The concrete table.
        """
        self._db: AsyncDatabaseManager = db
        self._model: type[BaseProjectionCheckpointModel] = model

    async def load(self, name: str, /) -> int | None:
        """Return the checkpoint of a projection.

        Args:
            name (str): The projection name.

        Returns:
            int | None: The last processed position, or ``None``.
        """
        async with self._db.get_session_context() as session:
            position = (
                await session.execute(
                    select(self._model.position).where(self._model.name == name)
                )
            ).scalar_one_or_none()
        return None if position is None else int(position)

    async def save(self, name: str, position: int, /) -> None:
        """Store the checkpoint of a projection.

        Args:
            name (str): The projection name.
            position (int): The last processed position.
        """
        model = self._model
        async with self._db.get_session_context() as session:
            result = await session.execute(
                update(model)
                .where(model.name == name)
                .values(position=position)
                .execution_options(synchronize_session=False)
            )
            if getattr(result, "rowcount", 0):
                return
            try:
                async with session.begin_nested():
                    await session.execute(
                        insert(model).values(id=uuid4(), name=name, position=position)
                    )
            except IntegrityError:
                await session.execute(
                    update(model)
                    .where(model.name == name)
                    .values(position=position)
                    .execution_options(synchronize_session=False)
                )


class ProjectionRunner:
    """Feed a projection handler from ``$all``, keeping its checkpoint.

    Example:
        >>> async def project(event: RecordedEvent) -> None:
        ...     if isinstance(event.event, OrderPlaced):
        ...         totals[event.stream_id] = event.event.total_cents
        >>> runner = ProjectionRunner(
        ...     store, name="order-totals", handler=project,
        ...     checkpoints=InMemoryCheckpointStore(),
        ... )
        >>> await runner.run_once()

    Attributes:
        name (str): The projection name, the checkpoint key.
        batch_size (int): Events read per :meth:`run_once`.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        name: str,
        handler: Callable[[RecordedEvent], Awaitable[object]],
        checkpoints: CheckpointStore,
        batch_size: int = 100,
    ) -> None:
        """Initialize the runner.

        Args:
            store (EventStore): The event store to read.
            name (str): The projection name.
            handler (Callable[[RecordedEvent], Awaitable[object]]): Async
                callable applying one event to the read model. Its return
                value is ignored; raise to stop at that event.
            checkpoints (CheckpointStore): Where the position is kept.
            batch_size (int): Events read per batch.

        Raises:
            ValueError: If ``batch_size`` is not positive.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.store: EventStore = store
        self.name: str = name
        self._handler: Callable[[RecordedEvent], Awaitable[object]] = handler
        self.checkpoints: CheckpointStore = checkpoints
        self.batch_size: int = batch_size

    async def run_once(self) -> int:
        """Process one batch after the checkpoint.

        The checkpoint is saved after the batch, or — when the handler
        raises — at the last event it finished, before re-raising, so the
        failed event is the first one of the next run.

        Returns:
            int: How many events the handler processed.
        """
        position = await self.checkpoints.load(self.name)
        batch = await self.store.read_all(
            after_position=position, limit=self.batch_size
        )
        processed = 0
        try:
            for event in batch:
                await self._handler(event)
                position = event.position
                processed += 1
        finally:
            if processed and position is not None:
                await self.checkpoints.save(self.name, position)
        return processed

    async def run(
        self,
        *,
        poll_interval: float = 1.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Loop :meth:`run_once` until ``stop_event`` is set or cancelled.

        A batch that failed is logged and retried after ``poll_interval``.

        Args:
            poll_interval (float): Seconds to sleep when idle or after a
                failure.
            stop_event (asyncio.Event | None): Ends the loop after the
                current batch when set.
        """
        while stop_event is None or not stop_event.is_set():
            try:
                processed = await self.run_once()
            except Exception:
                logger.exception("projection %s failed; retrying", self.name)
                processed = 0
            if processed < self.batch_size:
                await asyncio.sleep(poll_interval)


__all__: list[str] = [
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "ProjectionRunner",
    "SqlCheckpointStore",
]
