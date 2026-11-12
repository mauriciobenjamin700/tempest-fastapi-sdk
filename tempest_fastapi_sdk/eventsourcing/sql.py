"""Event store over the SQL database the service already has.

No new dependency: the event log, the stream table and the optional
snapshot table are ordinary SQLAlchemy models the project subclasses (see
:mod:`tempest_fastapi_sdk.eventsourcing.models`), on PostgreSQL or SQLite.

Two decisions carry the correctness of this backend:

* **The concurrency check is a conditional ``UPDATE``** of the stream row
  (``SET version = :new WHERE stream_id = :id AND version = :expected``),
  issued before any event is inserted. Of two appends with the same
  expectation, the second one blocks until the first commits and then
  matches zero rows, so it is refused with
  :class:`~tempest_fastapi_sdk.WrongExpectedVersionException` instead of
  writing a fork of the stream — the suite holds the first transaction
  open to show it, and on PostgreSQL the check alone (advisory lock
  removed) still refuses. Writing first matters on SQLite: a transaction
  that reads, then writes after another writer committed, fails at once
  with ``database is locked`` even under the manager's 30 s busy timeout.
* **On PostgreSQL every append takes one transaction-scoped advisory
  lock** before it numbers its events. ``position`` is ``max + 1`` under
  that lock, so positions are assigned in commit order with no gaps. A
  sequence would not give that: two transactions draw ``5`` and ``6``,
  ``6`` commits first, and a projection that reads ``6`` and checkpoints
  it never sees ``5``. The price is that appends to the whole store run
  one at a time — the lock is held until the transaction commits, so keep
  the transaction short when passing your own ``session``. SQLite already
  serializes writers.
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from sqlalchemy import CursorResult, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk.eventsourcing.events import (
    AppendResult,
    DomainEvent,
    EventMetadata,
    EventRegistry,
    RecordedEvent,
    Snapshot,
    StreamStateEnum,
    _check_expected_version,
    _expected_version_label,
)
from tempest_fastapi_sdk.exceptions.eventsourcing import (
    WrongExpectedVersionException,
)
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.db.outbox import BaseOutboxModel
    from tempest_fastapi_sdk.eventsourcing.models import (
        BaseEventModel,
        BaseEventSnapshotModel,
        BaseEventStreamModel,
    )


def _default_outbox_topic(event: RecordedEvent) -> str:
    """Route an event to the outbox topic named after its type.

    Args:
        event (RecordedEvent): The appended event.

    Returns:
        str: The event type.
    """
    return event.event_type


class SqlEventStore:
    """:class:`~tempest_fastapi_sdk.eventsourcing.EventStore` on SQLAlchemy.

    Every method opens its own unit of work through the manager unless a
    ``session`` is passed, in which case it works inside the caller's
    transaction and leaves the commit to the caller — which is how an
    append and a read-model update commit together.

    With ``outbox_model`` set, each append also writes one
    :class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel` row per event in
    the same transaction, whose payload is :meth:`RecordedEvent.to_payload`.
    :class:`~tempest_fastapi_sdk.db.outbox.OutboxRelay` then publishes them
    to the broker: an event is in the broker if and only if it is in the
    store.

    Example:
        >>> store = SqlEventStore(
        ...     db,
        ...     registry=registry,
        ...     event_model=EventModel,
        ...     stream_model=EventStreamModel,
        ... )
        >>> await store.append(
        ...     "order-42", [OrderPlaced(order_id="42", total_cents=990)],
        ...     expected_version=StreamStateEnum.NO_STREAM,
        ... )
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        registry: EventRegistry,
        event_model: type[BaseEventModel],
        stream_model: type[BaseEventStreamModel],
        snapshot_model: type[BaseEventSnapshotModel] | None = None,
        outbox_model: type[BaseOutboxModel] | None = None,
        outbox_topic: Callable[[RecordedEvent], str] = _default_outbox_topic,
    ) -> None:
        """Initialize the store.

        Args:
            db (AsyncDatabaseManager): The database manager.
            registry (EventRegistry): The event classes this store reads
                and writes.
            event_model (type[BaseEventModel]): The concrete event table.
            stream_model (type[BaseEventStreamModel]): The concrete stream
                table.
            snapshot_model (type[BaseEventSnapshotModel] | None): The
                concrete snapshot table; required by the snapshot methods.
            outbox_model (type[BaseOutboxModel] | None): When set, every
                appended event is also written to this outbox table in the
                same transaction.
            outbox_topic (Callable[[RecordedEvent], str]): Topic of each
                outbox row. Defaults to the event type.
        """
        self._db: AsyncDatabaseManager = db
        self.registry: EventRegistry = registry
        self._events: type[BaseEventModel] = event_model
        self._streams: type[BaseEventStreamModel] = stream_model
        self._snapshots: type[BaseEventSnapshotModel] | None = snapshot_model
        self._outbox: type[BaseOutboxModel] | None = outbox_model
        self._outbox_topic: Callable[[RecordedEvent], str] = outbox_topic
        table_name = cast("Table", event_model.__table__).name
        self._lock_key: int = zlib.crc32(
            f"tempest_fastapi_sdk.eventsourcing:{table_name}".encode()
        )

    @asynccontextmanager
    async def _unit(self, session: AsyncSession | None) -> AsyncIterator[AsyncSession]:
        """Yield the caller's session, or a committing one of our own.

        Args:
            session (AsyncSession | None): The caller's session.

        Yields:
            AsyncSession: The session to work in.
        """
        if session is not None:
            yield session
            return
        async with self._db.get_session_context() as owned:
            yield owned

    async def append(
        self,
        stream_id: str,
        events: Sequence[DomainEvent],
        /,
        *,
        expected_version: int | StreamStateEnum,
        metadata: EventMetadata | None = None,
        session: AsyncSession | None = None,
    ) -> AppendResult:
        """Append ``events`` to a stream, all or nothing.

        Args:
            stream_id (str): The stream.
            events (Sequence[DomainEvent]): One or more registered events.
            expected_version (int | StreamStateEnum): The stream's current
                last version, or a :class:`StreamStateEnum` condition.
            metadata (EventMetadata | None): Metadata stored with every
                event of this call.
            session (AsyncSession | None): Append inside this transaction
                instead of committing one of our own. On PostgreSQL the
                append lock is held until that transaction ends. On SQLite,
                a transaction that already read fails with ``database is
                locked`` if another writer committed since that read.

        Returns:
            AppendResult: The new stream version and ``$all`` position.

        Raises:
            WrongExpectedVersionException: When the stream does not match
                ``expected_version``.
            ValueError: When ``events`` is empty, or ``expected_version``
                is a negative number or a string naming no
                :class:`StreamStateEnum` member (the member's plain value,
                ``"exists"``, is accepted and means the member).
            TypeError: When ``expected_version`` is neither an ``int`` nor
                a :class:`StreamStateEnum`.
            UnknownEventTypeError: When an event's class is not registered.
        """
        if not events:
            raise ValueError("append needs at least one event")
        expected_version = _check_expected_version(expected_version)
        encoded = [self.registry.encode(event) for event in events]
        meta = metadata if metadata is not None else EventMetadata()
        async with self._unit(session) as active:
            return await self._append(
                active, stream_id, events, encoded, expected_version, meta
            )

    async def _append(
        self,
        session: AsyncSession,
        stream_id: str,
        events: Sequence[DomainEvent],
        encoded: list[tuple[str, dict[str, Any]]],
        expected_version: int | StreamStateEnum,
        metadata: EventMetadata,
    ) -> AppendResult:
        """Run the append inside ``session``.

        Args:
            session (AsyncSession): The session.
            stream_id (str): The stream.
            events (Sequence[DomainEvent]): The events.
            encoded (list[tuple[str, dict[str, Any]]]): Type and payload of
                each event.
            expected_version (int | StreamStateEnum): The expectation.
            metadata (EventMetadata): The metadata.

        Returns:
            AppendResult: What was written.
        """
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(select(func.pg_advisory_xact_lock(self._lock_key)))
        last_version = await self._advance_stream(
            session, stream_id, len(events), expected_version
        )
        first_version = last_version - len(events) + 1
        top = (
            await session.execute(
                select(func.coalesce(func.max(self._events.position), 0))
            )
        ).scalar_one()
        recorded_at = utcnow()
        records: list[RecordedEvent] = []
        rows: list[dict[str, Any]] = []
        for offset, (event, (event_type, data)) in enumerate(
            zip(events, encoded, strict=True)
        ):
            event_id = uuid4()
            position = int(top) + offset + 1
            version = first_version + offset
            rows.append(
                {
                    "id": event_id,
                    "position": position,
                    "stream_id": stream_id,
                    "version": version,
                    "event_type": event_type,
                    "data": data,
                    "correlation_id": metadata.correlation_id,
                    "causation_id": metadata.causation_id,
                    "extra": dict(metadata.extra),
                    "recorded_at": recorded_at,
                }
            )
            records.append(
                RecordedEvent(
                    event_id=event_id,
                    stream_id=stream_id,
                    version=version,
                    position=position,
                    event_type=event_type,
                    event=event,
                    metadata=metadata,
                    recorded_at=recorded_at,
                )
            )
        await session.execute(insert(self._events), rows)
        if self._outbox is not None:
            for record in records:
                session.add(
                    self._outbox.new_event(
                        self._outbox_topic(record), record.to_payload()
                    )
                )
            await session.flush()
        return AppendResult(
            version=last_version,
            position=records[-1].position,
            event_ids=tuple(record.event_id for record in records),
        )

    async def _advance_stream(
        self,
        session: AsyncSession,
        stream_id: str,
        count: int,
        expected_version: int | StreamStateEnum,
    ) -> int:
        """Move the stream row forward by ``count`` if it matches.

        For ``ANY`` on a missing stream the row is inserted; when that insert
        hits the unique constraint, another writer created the stream between
        the ``UPDATE`` and the ``INSERT``, so the row is bumped again.

        Args:
            session (AsyncSession): The session.
            stream_id (str): The stream.
            count (int): How many events are being appended.
            expected_version (int | StreamStateEnum): The expectation.

        Returns:
            int: The stream's new last version.

        Raises:
            WrongExpectedVersionException: When the stream does not match.
        """
        streams = self._streams
        if expected_version is StreamStateEnum.NO_STREAM:
            if await self._create_stream(session, stream_id, count - 1):
                return count - 1
            raise await self._conflict(session, stream_id, expected_version)
        if isinstance(expected_version, int):
            moved = await self._update_rowcount(
                session,
                update(streams)
                .where(
                    streams.stream_id == stream_id,
                    streams.version == expected_version,
                )
                .values(version=expected_version + count),
            )
            if moved != 1:
                raise await self._conflict(session, stream_id, expected_version)
            return expected_version + count
        bump = (
            update(streams)
            .where(streams.stream_id == stream_id)
            .values(version=streams.version + count)
        )
        if await self._update_rowcount(session, bump) == 1:
            return await self._bumped_version(session, stream_id)
        if expected_version is StreamStateEnum.EXISTS:
            raise await self._conflict(session, stream_id, expected_version)
        if await self._create_stream(session, stream_id, count - 1):
            return count - 1
        await self._update_rowcount(session, bump)
        return await self._bumped_version(session, stream_id)

    async def _create_stream(
        self, session: AsyncSession, stream_id: str, version: int
    ) -> bool:
        """Insert the stream row, reporting whether it already existed.

        Args:
            session (AsyncSession): The session.
            stream_id (str): The stream.
            version (int): The new stream's last version.

        Returns:
            bool: ``True`` when the row was created, ``False`` when the
            stream already existed.
        """
        try:
            async with session.begin_nested():
                await session.execute(
                    insert(self._streams).values(
                        id=uuid4(), stream_id=stream_id, version=version
                    )
                )
        except IntegrityError:
            return False
        return True

    @staticmethod
    async def _update_rowcount(session: AsyncSession, statement: Any) -> int:
        """Execute an ``UPDATE`` and return how many rows it matched.

        Args:
            session (AsyncSession): The session.
            statement (Any): The ``UPDATE`` statement.

        Returns:
            int: The matched row count.
        """
        result = await session.execute(
            statement.execution_options(synchronize_session=False)
        )
        return int(cast("CursorResult[Any]", result).rowcount)

    async def _current_version(
        self, session: AsyncSession, stream_id: str
    ) -> int | None:
        """Read the stream row's version.

        Args:
            session (AsyncSession): The session.
            stream_id (str): The stream.

        Returns:
            int | None: The version, or ``None`` when the stream does not exist.
        """
        version = (
            await session.execute(
                select(self._streams.version).where(
                    self._streams.stream_id == stream_id
                )
            )
        ).scalar_one_or_none()
        return None if version is None else int(version)

    async def _bumped_version(self, session: AsyncSession, stream_id: str) -> int:
        """Read the version of a stream row this transaction just updated.

        Args:
            session (AsyncSession): The session holding the row lock.
            stream_id (str): The stream.

        Returns:
            int: The stream's new last version.
        """
        version = (
            await session.execute(
                select(self._streams.version).where(
                    self._streams.stream_id == stream_id
                )
            )
        ).scalar_one()
        return int(version)

    async def _conflict(
        self,
        session: AsyncSession,
        stream_id: str,
        expected_version: int | StreamStateEnum,
    ) -> WrongExpectedVersionException:
        """Build the conflict error with the stream's actual version.

        Args:
            session (AsyncSession): The session.
            stream_id (str): The stream.
            expected_version (int | StreamStateEnum): The expectation.

        Returns:
            WrongExpectedVersionException: The error to raise.
        """
        return WrongExpectedVersionException(
            stream_id,
            expected_version=_expected_version_label(expected_version),
            actual_version=await self._current_version(session, stream_id),
        )

    def _record(self, row: BaseEventModel) -> RecordedEvent:
        """Turn an event row into a :class:`RecordedEvent`.

        Args:
            row (BaseEventModel): The row.

        Returns:
            RecordedEvent: The decoded event.
        """
        return RecordedEvent(
            event_id=row.id,
            stream_id=row.stream_id,
            version=row.version,
            position=row.position,
            event_type=row.event_type,
            event=self.registry.decode(row.event_type, row.data),
            metadata=EventMetadata(
                correlation_id=row.correlation_id,
                causation_id=row.causation_id,
                extra=dict(row.extra or {}),
            ),
            recorded_at=row.recorded_at,
        )

    async def read_stream(
        self,
        stream_id: str,
        /,
        *,
        from_version: int | None = None,
        backwards: bool = False,
        limit: int | None = None,
        session: AsyncSession | None = None,
    ) -> list[RecordedEvent]:
        """Read one stream in version order.

        Args:
            stream_id (str): The stream.
            from_version (int | None): First version to return, inclusive.
                ``None`` starts at the beginning (forwards) or the end
                (backwards).
            backwards (bool): Read from newest to oldest.
            limit (int | None): Maximum events; ``None`` reads to the end.
            session (AsyncSession | None): Read inside this transaction.

        Returns:
            list[RecordedEvent]: The events; empty for a missing stream.

        Raises:
            UnknownEventTypeError: When the stream holds an unregistered
                type.
        """
        if limit is not None and limit <= 0:
            return []
        model = self._events
        statement = select(model).where(model.stream_id == stream_id)
        if backwards:
            if from_version is not None:
                statement = statement.where(model.version <= from_version)
            statement = statement.order_by(model.version.desc())
        else:
            if from_version is not None:
                statement = statement.where(model.version >= from_version)
            statement = statement.order_by(model.version)
        if limit is not None:
            statement = statement.limit(limit)
        async with self._unit(session) as active:
            rows = (await active.execute(statement)).scalars().all()
            return [self._record(row) for row in rows]

    async def stream_version(
        self, stream_id: str, /, *, session: AsyncSession | None = None
    ) -> int | None:
        """Return a stream's last version.

        Args:
            stream_id (str): The stream.
            session (AsyncSession | None): Read inside this transaction.

        Returns:
            int | None: The last version, or ``None`` for a missing stream.
        """
        async with self._unit(session) as active:
            return await self._current_version(active, stream_id)

    async def read_all(
        self,
        *,
        after_position: int | None = None,
        limit: int = 500,
        session: AsyncSession | None = None,
    ) -> list[RecordedEvent]:
        """Read the store-wide log forwards, registered types only.

        Args:
            after_position (int | None): Return events strictly after this
                position; ``None`` starts at the beginning.
            limit (int): Maximum events to return.
            session (AsyncSession | None): Read inside this transaction.

        Returns:
            list[RecordedEvent]: The events, in position order.
        """
        types = self.registry.types
        if limit <= 0 or not types:
            return []
        model = self._events
        statement = select(model).where(model.event_type.in_(sorted(types)))
        if after_position is not None:
            statement = statement.where(model.position > after_position)
        statement = statement.order_by(model.position).limit(limit)
        async with self._unit(session) as active:
            rows = (await active.execute(statement)).scalars().all()
            return [self._record(row) for row in rows]

    async def subscribe_all(
        self,
        *,
        after_position: int | None = None,
        poll_interval: float = 1.0,
        batch_size: int = 500,
    ) -> AsyncIterator[RecordedEvent]:
        """Yield registered events after a position, polling for new ones.

        Args:
            after_position (int | None): Start strictly after this
                position; ``None`` starts at the beginning.
            poll_interval (float): Seconds to wait when a poll found
                nothing.
            batch_size (int): Events read per poll.

        Yields:
            RecordedEvent: The events, in position order, forever.
        """
        position = after_position
        while True:
            batch = await self.read_all(after_position=position, limit=batch_size)
            for event in batch:
                yield event
                position = event.position
            if not batch:
                await asyncio.sleep(poll_interval)

    def _require_snapshots(self) -> type[BaseEventSnapshotModel]:
        """Return the snapshot model or explain how to configure it.

        Returns:
            type[BaseEventSnapshotModel]: The snapshot model.

        Raises:
            RuntimeError: When the store was built without one.
        """
        if self._snapshots is None:
            raise RuntimeError(
                "SqlEventStore was built without snapshot_model; pass a "
                "BaseEventSnapshotModel subclass to use snapshots"
            )
        return self._snapshots

    async def save_snapshot(
        self,
        stream_id: str,
        /,
        *,
        version: int,
        state: Mapping[str, Any],
        session: AsyncSession | None = None,
    ) -> None:
        """Save a snapshot, keeping the newest one per stream.

        A snapshot older than the stored one is ignored, so two writers
        racing never move the snapshot backwards: the conditional ``UPDATE``
        matches nothing, and the ``INSERT`` that follows hits the unique
        ``stream_id`` and is dropped, keeping the newer row.

        Args:
            stream_id (str): The stream.
            version (int): The last version folded into ``state``.
            state (Mapping[str, Any]): JSON-compatible state.
            session (AsyncSession | None): Write inside this transaction.

        Raises:
            RuntimeError: When the store has no ``snapshot_model``.
        """
        model = self._require_snapshots()
        async with self._unit(session) as active:
            moved = await self._update_rowcount(
                active,
                update(model)
                .where(model.stream_id == stream_id, model.version <= version)
                .values(version=version, state=dict(state)),
            )
            if moved:
                return
            try:
                async with active.begin_nested():
                    await active.execute(
                        insert(model).values(
                            id=uuid4(),
                            stream_id=stream_id,
                            version=version,
                            state=dict(state),
                        )
                    )
            except IntegrityError:
                return

    async def load_snapshot(
        self, stream_id: str, /, *, session: AsyncSession | None = None
    ) -> Snapshot | None:
        """Return the latest snapshot of a stream.

        Args:
            stream_id (str): The stream.
            session (AsyncSession | None): Read inside this transaction.

        Returns:
            Snapshot | None: The snapshot, or ``None``.

        Raises:
            RuntimeError: When the store has no ``snapshot_model``.
        """
        model = self._require_snapshots()
        async with self._unit(session) as active:
            row = (
                await active.execute(select(model).where(model.stream_id == stream_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            return Snapshot(
                stream_id=row.stream_id, version=row.version, state=dict(row.state)
            )


__all__: list[str] = [
    "SqlEventStore",
]
