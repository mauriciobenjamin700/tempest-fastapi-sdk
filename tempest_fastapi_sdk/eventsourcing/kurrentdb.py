"""Event store on KurrentDB (formerly EventStoreDB).

Requires the ``kurrentdb`` extra (``kurrentdbclient``). The client is
imported inside the functions that use it, so importing
:mod:`tempest_fastapi_sdk.eventsourcing` does not need it installed.

Mapping onto the official client, and the two places where its defaults
would bite:

* versions are KurrentDB stream positions (0-based, the same convention as
  :class:`~tempest_fastapi_sdk.eventsourcing.SqlEventStore`) and positions
  are commit positions — they increase but skip values;
* the client's ``read_all(commit_position=p)`` **includes** the event at
  ``p`` while ``subscribe_to_all(commit_position=p)`` starts **after** it;
  this store gives both ``after_position`` the exclusive meaning;
* the client builds its include filter as ``"^" + "|".join(patterns) + "$"``,
  which anchors only the first and last alternatives, so the store passes
  one grouped pattern ``(?:a|b)``. Without the group, a store registering
  ``order.placed`` and ``order.shipped`` received an unregistered
  ``order.placed.v2`` from ``$all`` on KurrentDB 26.2.3.

Metadata is written as JSON with KurrentDB's own ``$correlationId`` and
``$causationId`` keys, which its system projections (``$by_correlation_id``)
read; ``extra`` keys sit next to them.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

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

if TYPE_CHECKING:
    from kurrentdbclient import AsyncKurrentDBClient, StreamState
    from kurrentdbclient import RecordedEvent as KurrentRecordedEvent

SNAPSHOT_EVENT_TYPE: str = "TempestSnapshot"
"""Event type of snapshot events, kept out of ``$all`` reads by the filter."""

_CORRELATION_KEY: str = "$correlationId"
_CAUSATION_KEY: str = "$causationId"


def _encode_metadata(metadata: EventMetadata) -> bytes:
    """Serialize metadata with KurrentDB's correlation keys.

    Args:
        metadata (EventMetadata): The metadata.

    Returns:
        bytes: JSON bytes.
    """
    body: dict[str, Any] = dict(metadata.extra)
    if metadata.correlation_id is not None:
        body[_CORRELATION_KEY] = metadata.correlation_id
    if metadata.causation_id is not None:
        body[_CAUSATION_KEY] = metadata.causation_id
    return json.dumps(body).encode()


def _decode_metadata(raw: bytes) -> EventMetadata:
    """Parse metadata written by :func:`_encode_metadata`.

    Args:
        raw (bytes): The stored metadata bytes; empty for none.

    Returns:
        EventMetadata: The parsed metadata.
    """
    parsed: object = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        return EventMetadata()
    body: dict[str, Any] = parsed
    correlation = body.pop(_CORRELATION_KEY, None)
    causation = body.pop(_CAUSATION_KEY, None)
    return EventMetadata(
        correlation_id=correlation if isinstance(correlation, str) else None,
        causation_id=causation if isinstance(causation, str) else None,
        extra=body,
    )


class KurrentDBEventStore:
    """:class:`~tempest_fastapi_sdk.eventsourcing.EventStore` on KurrentDB.

    Build it from a connection string with :meth:`from_uri`, or hand it an
    ``AsyncKurrentDBClient`` you already configured. The client connects on
    its first call; :meth:`close` closes it.

    Snapshots live in a stream of their own, ``snapshot_stream_prefix +
    stream_id``, capped at one event with ``$maxCount``.

    Example:
        >>> store = KurrentDBEventStore.from_uri(
        ...     "kurrentdb://127.0.0.1:2113?Tls=false", registry=registry
        ... )
        >>> await store.append(
        ...     "order-42", [OrderPlaced(order_id="42", total_cents=990)],
        ...     expected_version=StreamStateEnum.NO_STREAM,
        ... )
    """

    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        registry: EventRegistry,
        snapshot_stream_prefix: str = "snapshot-",
    ) -> None:
        """Initialize the store over an existing client.

        Args:
            client (AsyncKurrentDBClient): The client.
            registry (EventRegistry): The event classes this store reads
                and writes.
            snapshot_stream_prefix (str): Prefix of the snapshot streams.
        """
        self.client: AsyncKurrentDBClient = client
        self.registry: EventRegistry = registry
        self.snapshot_stream_prefix: str = snapshot_stream_prefix

    @classmethod
    def from_uri(
        cls,
        uri: str,
        *,
        registry: EventRegistry,
        root_certificates: str | bytes | None = None,
        snapshot_stream_prefix: str = "snapshot-",
    ) -> KurrentDBEventStore:
        """Build the store and its client from a connection string.

        Args:
            uri (str): A ``kurrentdb://`` or ``kurrentdb+discover://`` URI.
            registry (EventRegistry): The event classes.
            root_certificates (str | bytes | None): PEM certificates that
                sign the server's TLS certificate, for a secure node.
            snapshot_stream_prefix (str): Prefix of the snapshot streams.

        Returns:
            KurrentDBEventStore: The store; the client connects on first use.
        """
        from kurrentdbclient import AsyncKurrentDBClient

        return cls(
            AsyncKurrentDBClient(uri, root_certificates=root_certificates),
            registry=registry,
            snapshot_stream_prefix=snapshot_stream_prefix,
        )

    async def close(self) -> None:
        """Close the client's connection; a no-op if it never connected.

        The client raises ``ProgrammingError("Connection was never
        established")`` when closed before its first call, which would turn a
        shutdown hook of an idle service into an error.
        """
        from kurrentdbclient.exceptions import ProgrammingError

        try:
            await self.client.close()
        except ProgrammingError:
            return

    def _type_filter(self) -> tuple[str, ...]:
        """Return the include filter matching exactly the registered types.

        Returns:
            tuple[str, ...]: One grouped, escaped pattern.
        """
        names = "|".join(re.escape(name) for name in sorted(self.registry.types))
        return (f"(?:{names})",)

    def _record(self, event: KurrentRecordedEvent) -> RecordedEvent:
        """Turn a client event into a :class:`RecordedEvent`.

        Args:
            event (KurrentRecordedEvent): The client's event.

        Returns:
            RecordedEvent: The decoded event.
        """
        return RecordedEvent(
            event_id=event.id,
            stream_id=event.stream_name,
            version=event.stream_position,
            position=event.commit_position,
            event_type=event.type,
            event=self.registry.decode(event.type, json.loads(event.data)),
            metadata=_decode_metadata(event.metadata),
            recorded_at=event.recorded_at,
        )

    @staticmethod
    def _client_state(expected_version: int | StreamStateEnum) -> int | StreamState:
        """Translate an expected version into the client's vocabulary.

        Args:
            expected_version (int | StreamStateEnum): Our expectation.

        Returns:
            int | StreamState: The client's expectation.
        """
        from kurrentdbclient import StreamState

        if expected_version is StreamStateEnum.ANY:
            return StreamState.ANY
        if expected_version is StreamStateEnum.NO_STREAM:
            return StreamState.NO_STREAM
        if expected_version is StreamStateEnum.EXISTS:
            return StreamState.EXISTS
        return int(expected_version)

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
            AppendResult: The new stream version and commit position.

        Raises:
            WrongExpectedVersionException: When the stream does not match
                ``expected_version`` (the client's
                ``WrongCurrentVersionError``).
            ValueError: When ``events`` is empty, or ``expected_version``
                is a negative number or a string naming no
                :class:`StreamStateEnum` member (the member's plain value,
                ``"exists"``, is accepted and means the member).
            TypeError: When ``expected_version`` is neither an ``int`` nor
                a :class:`StreamStateEnum`.
            UnknownEventTypeError: When an event's class is not registered.
        """
        from kurrentdbclient import NewEvent
        from kurrentdbclient.exceptions import WrongCurrentVersionError

        if not events:
            raise ValueError("append needs at least one event")
        expected_version = _check_expected_version(expected_version)
        meta = _encode_metadata(metadata if metadata is not None else EventMetadata())
        new_events = []
        for event in events:
            event_type, data = self.registry.encode(event)
            new_events.append(
                NewEvent(
                    type=event_type,
                    data=json.dumps(data).encode(),
                    metadata=meta,
                    id=uuid4(),
                )
            )
        try:
            commit_position = await self.client.append_to_stream(
                stream_id,
                events=new_events,
                current_version=self._client_state(expected_version),
            )
        except WrongCurrentVersionError as exc:
            actual = exc.actual_version if isinstance(exc.actual_version, int) else None
            raise WrongExpectedVersionException(
                stream_id,
                expected_version=_expected_version_label(expected_version),
                actual_version=actual,
            ) from exc
        ids = tuple(event.id for event in new_events)
        if isinstance(expected_version, int):
            version = expected_version + len(new_events)
        elif expected_version is StreamStateEnum.NO_STREAM:
            version = len(new_events) - 1
        else:
            version = await self._version_of(stream_id, ids[-1], commit_position)
        return AppendResult(version=version, position=commit_position, event_ids=ids)

    async def _version_of(
        self, stream_id: str, event_id: UUID, commit_position: int
    ) -> int:
        """Find the stream version an appended event landed at.

        The client returns only the commit position, so for ``ANY`` and
        ``EXISTS`` the version is read back from the event at that
        position — exact even if another writer appended right after.

        Args:
            stream_id (str): The stream.
            event_id (UUID): The last appended event's id.
            commit_position (int): Its commit position.

        Returns:
            int: Its stream version.
        """
        async with await self.client.read_all(
            commit_position=commit_position, limit=1, filter_exclude=()
        ) as response:
            async for event in response:
                if event.id == event_id:
                    return event.stream_position
        for event in await self.client.get_stream(
            stream_id, backwards=True, limit=1000
        ):
            if event.id == event_id:
                return event.stream_position
        raise LookupError(f"appended event {event_id} not found in {stream_id!r}")

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
                ``None`` starts at the beginning (forwards) or the end
                (backwards).
            backwards (bool): Read from newest to oldest.
            limit (int | None): Maximum events; ``None`` reads to the end.

        Returns:
            list[RecordedEvent]: The events; empty for a missing stream.

        Raises:
            UnknownEventTypeError: When the stream holds an unregistered
                type.
        """
        from kurrentdbclient.exceptions import NotFoundError

        if limit is not None and limit <= 0:
            return []
        kwargs: dict[str, Any] = {
            "stream_position": from_version,
            "backwards": backwards,
        }
        if limit is not None:
            kwargs["limit"] = limit
        try:
            events = await self.client.get_stream(stream_id, **kwargs)
        except NotFoundError:
            return []
        return [self._record(event) for event in events]

    async def stream_version(self, stream_id: str, /) -> int | None:
        """Return a stream's last version.

        Args:
            stream_id (str): The stream.

        Returns:
            int | None: The last version, or ``None`` for a missing stream.
        """
        from kurrentdbclient.exceptions import NotFoundError

        try:
            events = await self.client.get_stream(stream_id, backwards=True, limit=1)
        except NotFoundError:
            return None
        return events[0].stream_position if events else None

    async def read_all(
        self,
        *,
        after_position: int | None = None,
        limit: int = 500,
    ) -> list[RecordedEvent]:
        """Read ``$all`` forwards, registered types only.

        Args:
            after_position (int | None): Return events strictly after this
                commit position; ``None`` starts at the beginning.
            limit (int): Maximum events to return.

        Returns:
            list[RecordedEvent]: The events, in position order.
        """
        if limit <= 0 or not self.registry.types:
            return []
        records: list[RecordedEvent] = []
        async with await self.client.read_all(
            commit_position=after_position,
            limit=limit + 1 if after_position is not None else limit,
            filter_include=self._type_filter(),
        ) as response:
            async for event in response:
                if (
                    after_position is not None
                    and event.commit_position <= after_position
                ):
                    continue
                records.append(self._record(event))
                if len(records) == limit:
                    break
        return records

    async def subscribe_all(
        self,
        *,
        after_position: int | None = None,
        poll_interval: float = 1.0,
    ) -> AsyncIterator[RecordedEvent]:
        """Yield registered events after a position, then live ones.

        A KurrentDB catch-up subscription to ``$all`` filtered to the
        registered types. The server pushes new events, so
        ``poll_interval`` is accepted for protocol compatibility and
        ignored.

        Args:
            after_position (int | None): Start strictly after this commit
                position; ``None`` starts at the beginning.
            poll_interval (float): Ignored.

        Yields:
            RecordedEvent: The events, in position order, until the
            consumer stops iterating.
        """
        del poll_interval
        if not self.registry.types:
            return
        subscription = await self.client.subscribe_to_all(
            commit_position=after_position,
            filter_include=self._type_filter(),
        )
        try:
            async for event in subscription:
                yield self._record(event)
        finally:
            await subscription.stop()

    def _snapshot_stream(self, stream_id: str) -> str:
        """Return the stream that holds a stream's snapshots.

        Args:
            stream_id (str): The stream.

        Returns:
            str: The snapshot stream name.
        """
        return f"{self.snapshot_stream_prefix}{stream_id}"

    async def save_snapshot(
        self,
        stream_id: str,
        /,
        *,
        version: int,
        state: Mapping[str, Any],
    ) -> None:
        """Save a snapshot, keeping only the newest one.

        A snapshot older than the stored one is ignored. The first save
        sets ``$maxCount: 1`` on the snapshot stream so it never grows.

        Args:
            stream_id (str): The stream.
            version (int): The last version folded into ``state``.
            state (Mapping[str, Any]): JSON-compatible state.
        """
        from kurrentdbclient import NewEvent, StreamState

        current = await self.load_snapshot(stream_id)
        if current is not None and current.version > version:
            return
        name = self._snapshot_stream(stream_id)
        await self.client.append_to_stream(
            name,
            events=[
                NewEvent(
                    type=SNAPSHOT_EVENT_TYPE,
                    data=json.dumps(
                        {"version": version, "state": dict(state)}
                    ).encode(),
                )
            ],
            current_version=StreamState.ANY,
        )
        if current is None:
            await self.client.set_stream_metadata(name, metadata={"$maxCount": 1})

    async def load_snapshot(self, stream_id: str, /) -> Snapshot | None:
        """Return the latest snapshot of a stream.

        Args:
            stream_id (str): The stream.

        Returns:
            Snapshot | None: The snapshot, or ``None``.
        """
        from kurrentdbclient.exceptions import NotFoundError

        try:
            events = await self.client.get_stream(
                self._snapshot_stream(stream_id), backwards=True, limit=1
            )
        except NotFoundError:
            return None
        if not events:
            return None
        body = json.loads(events[0].data)
        return Snapshot(
            stream_id=stream_id,
            version=int(body["version"]),
            state=dict(body["state"]),
        )


__all__: list[str] = [
    "SNAPSHOT_EVENT_TYPE",
    "KurrentDBEventStore",
]
