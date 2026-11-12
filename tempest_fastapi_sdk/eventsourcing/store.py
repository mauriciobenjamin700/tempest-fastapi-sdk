"""The :class:`EventStore` protocol both backends implement."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.eventsourcing.events import (
    AppendResult,
    DomainEvent,
    EventMetadata,
    RecordedEvent,
    Snapshot,
    StreamStateEnum,
)


@runtime_checkable
class EventStore(Protocol):
    """An append-only store of event streams with a store-wide log.

    Implemented by :class:`~tempest_fastapi_sdk.eventsourcing.SqlEventStore`
    (PostgreSQL / SQLite through SQLAlchemy) and
    :class:`~tempest_fastapi_sdk.eventsourcing.KurrentDBEventStore`. Code
    written against this protocol — aggregates, projections — runs on
    either.

    Versions are 0-based per stream: the first event is version ``0``, and
    a stream holding three events is at version ``2``. Positions order the
    whole store (``$all``) and only increase; checkpoint them, compare
    them, never do arithmetic on them.
    """

    async def append(
        self,
        stream_id: str,
        events: Sequence[DomainEvent],
        /,
        *,
        expected_version: int | StreamStateEnum,
        metadata: EventMetadata | None = None,
    ) -> AppendResult:
        """Append ``events`` to a stream, all or nothing.

        Args:
            stream_id (str): The stream.
            events (Sequence[DomainEvent]): One or more registered events.
            expected_version (int | StreamStateEnum): The stream's current
                last version, or a :class:`StreamStateEnum` condition.
            metadata (EventMetadata | None): Metadata stored with every
                event of this call.

        Returns:
            AppendResult: The new stream version and ``$all`` position.

        Raises:
            WrongExpectedVersionException: When the stream does not match
                ``expected_version``.
            ValueError: When ``events`` is empty.
            UnknownEventTypeError: When an event's class is not registered.
        """
        ...

    async def read_stream(
        self,
        stream_id: str,
        /,
        *,
        from_version: int | None = None,
        backwards: bool = False,
        limit: int | None = None,
    ) -> list[RecordedEvent]:
        """Read one stream in version order.

        Args:
            stream_id (str): The stream.
            from_version (int | None): First version to return, inclusive.
                ``None`` starts at the beginning (forwards) or at the end
                (backwards).
            backwards (bool): Read from newest to oldest.
            limit (int | None): Maximum events to return; ``None`` reads
                to the end.

        Returns:
            list[RecordedEvent]: The events; empty for a missing stream.
        """
        ...

    async def stream_version(self, stream_id: str, /) -> int | None:
        """Return a stream's last version.

        Args:
            stream_id (str): The stream.

        Returns:
            int | None: The last version, or ``None`` when the stream does
            not exist.
        """
        ...

    async def read_all(
        self,
        *,
        after_position: int | None = None,
        limit: int = 500,
    ) -> list[RecordedEvent]:
        """Read the store-wide log forwards, registered types only.

        Args:
            after_position (int | None): Return events strictly after this
                position — the last one a projection processed. ``None``
                starts at the beginning.
            limit (int): Maximum events to return.

        Returns:
            list[RecordedEvent]: The events, in position order.
        """
        ...

    def subscribe_all(
        self,
        *,
        after_position: int | None = None,
        poll_interval: float = 1.0,
    ) -> AsyncIterator[RecordedEvent]:
        """Yield every registered event after a position, then wait for more.

        A catch-up subscription: it first yields what is already stored,
        then each new event as it is appended, and never ends on its own —
        break out of the ``async for`` or cancel the task.

        Args:
            after_position (int | None): Start strictly after this
                position; ``None`` starts at the beginning.
            poll_interval (float): Seconds between polls, for backends
                that poll. KurrentDB pushes and ignores it.

        Returns:
            AsyncIterator[RecordedEvent]: The events, in position order.
        """
        ...

    async def save_snapshot(
        self,
        stream_id: str,
        /,
        *,
        version: int,
        state: Mapping[str, Any],
    ) -> None:
        """Save a snapshot of a stream's folded state.

        Args:
            stream_id (str): The stream.
            version (int): The last version folded into ``state``.
            state (Mapping[str, Any]): JSON-compatible state.
        """
        ...

    async def load_snapshot(self, stream_id: str, /) -> Snapshot | None:
        """Return the latest snapshot of a stream.

        Args:
            stream_id (str): The stream.

        Returns:
            Snapshot | None: The snapshot, or ``None`` when none was saved.
        """
        ...


__all__: list[str] = [
    "EventStore",
]
