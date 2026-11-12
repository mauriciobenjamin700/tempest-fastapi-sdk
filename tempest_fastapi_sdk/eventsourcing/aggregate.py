"""A thin aggregate base and the repository that loads and saves it.

Loading is replaying the stream (after the latest snapshot, when
snapshots are on); saving is one append with the version the aggregate
was loaded at as ``expected_version``. Nothing else: no command bus, no
handler discovery.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from tempest_fastapi_sdk.eventsourcing.events import (
    AppendResult,
    DomainEvent,
    EventMetadata,
    StreamStateEnum,
)
from tempest_fastapi_sdk.eventsourcing.store import EventStore


class Aggregate:
    """Base for an event-sourced aggregate.

    Subclasses implement :meth:`apply` — how each event changes the state —
    and call :meth:`record` from their command methods. Opt into snapshots
    by implementing :meth:`snapshot` and :meth:`restore`.

    Attributes:
        aggregate_id (str): The aggregate id.
        version (int | None): The stream version the state reflects;
            ``None`` for an aggregate whose stream does not exist yet.
    """

    def __init__(self, aggregate_id: str) -> None:
        """Create an empty aggregate.

        Args:
            aggregate_id (str): The aggregate id.
        """
        self.aggregate_id: str = aggregate_id
        self.version: int | None = None
        self._pending: list[DomainEvent] = []

    @property
    def pending_events(self) -> tuple[DomainEvent, ...]:
        """Return events recorded since the last load or save.

        Returns:
            tuple[DomainEvent, ...]: The unsaved events, in order.
        """
        return tuple(self._pending)

    def apply(self, event: DomainEvent) -> None:
        """Change the state for one event. Must not fail or have effects.

        Args:
            event (DomainEvent): The event.

        Raises:
            NotImplementedError: Unless a subclass implements it.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement apply()")

    def record(self, event: DomainEvent) -> None:
        """Apply a new event and queue it for the next save.

        Args:
            event (DomainEvent): The event the command decided on.
        """
        self.apply(event)
        self._pending.append(event)

    def mark_saved(self, version: int) -> None:
        """Forget the pending events after they were appended at ``version``.

        Args:
            version (int): The stream version of the last appended event.
        """
        self.version = version
        self._pending.clear()

    def snapshot(self) -> dict[str, Any] | None:
        """Return the state as JSON-compatible data, or ``None`` to opt out.

        Returns:
            dict[str, Any] | None: ``None`` unless a subclass overrides it.
        """
        return None

    def restore(self, state: dict[str, Any]) -> None:
        """Restore the state from :meth:`snapshot` output.

        Args:
            state (dict[str, Any]): The saved state.

        Raises:
            NotImplementedError: Unless a subclass implements it.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement restore()")


A = TypeVar("A", bound=Aggregate)


class AggregateRepository(Generic[A]):
    """Load aggregates by replaying their stream, save them by appending.

    The stream of an aggregate is ``stream_prefix + aggregate_id`` —
    ``"order-"`` gives KurrentDB's category convention (``order-42``).

    Example:
        >>> orders = AggregateRepository(store, Order, stream_prefix="order-")
        >>> order = await orders.load("42") or Order("42")
        >>> order.place(total_cents=990)
        >>> await orders.save(order)
    """

    def __init__(
        self,
        store: EventStore,
        factory: Callable[[str], A],
        *,
        stream_prefix: str = "",
        snapshot_every: int | None = None,
    ) -> None:
        """Initialize the repository.

        Args:
            store (EventStore): Any event store.
            factory (Callable[[str], A]): Builds an empty aggregate from its
                id — usually the aggregate class itself.
            stream_prefix (str): Prefix of the stream name.
            snapshot_every (int | None): Save a snapshot each time the
                stream crosses a multiple of this many events. ``None``
                never reads or writes snapshots.

        Raises:
            ValueError: If ``snapshot_every`` is not positive.
        """
        if snapshot_every is not None and snapshot_every <= 0:
            raise ValueError("snapshot_every must be positive")
        self.store: EventStore = store
        self._factory: Callable[[str], A] = factory
        self.stream_prefix: str = stream_prefix
        self.snapshot_every: int | None = snapshot_every

    def stream_id(self, aggregate_id: str) -> str:
        """Return the stream name of an aggregate.

        Args:
            aggregate_id (str): The aggregate id.

        Returns:
            str: The stream name.
        """
        return f"{self.stream_prefix}{aggregate_id}"

    async def load(self, aggregate_id: str) -> A | None:
        """Rebuild an aggregate from its snapshot and stream.

        Args:
            aggregate_id (str): The aggregate id.

        Returns:
            A | None: The aggregate, or ``None`` when its stream does not
            exist.
        """
        stream_id = self.stream_id(aggregate_id)
        aggregate = self._factory(aggregate_id)
        from_version: int | None = None
        if self.snapshot_every is not None:
            snapshot = await self.store.load_snapshot(stream_id)
            if snapshot is not None:
                aggregate.restore(snapshot.state)
                aggregate.version = snapshot.version
                from_version = snapshot.version + 1
        events = await self.store.read_stream(stream_id, from_version=from_version)
        for recorded in events:
            aggregate.apply(recorded.event)
            aggregate.version = recorded.version
        if aggregate.version is None:
            return None
        return aggregate

    async def save(
        self,
        aggregate: A,
        *,
        metadata: EventMetadata | None = None,
    ) -> AppendResult | None:
        """Append the aggregate's pending events.

        Args:
            aggregate (A): The aggregate.
            metadata (EventMetadata | None): Metadata for the events.

        Returns:
            AppendResult | None: What was appended, or ``None`` when there
            was nothing pending.

        Raises:
            WrongExpectedVersionException: When the stream moved since the
                aggregate was loaded; the pending events are kept.
        """
        pending = list(aggregate.pending_events)
        if not pending:
            return None
        before = aggregate.version
        expected: int | StreamStateEnum = (
            StreamStateEnum.NO_STREAM if before is None else before
        )
        result = await self.store.append(
            self.stream_id(aggregate.aggregate_id),
            pending,
            expected_version=expected,
            metadata=metadata,
        )
        aggregate.mark_saved(result.version)
        await self._maybe_snapshot(aggregate, before, result.version)
        return result

    async def _maybe_snapshot(
        self, aggregate: A, before: int | None, after: int
    ) -> None:
        """Save a snapshot when the append crossed a ``snapshot_every`` mark.

        Args:
            aggregate (A): The saved aggregate.
            before (int | None): Its version before the append.
            after (int): Its version after the append.
        """
        every = self.snapshot_every
        if every is None:
            return
        count_before = 0 if before is None else before + 1
        if (after + 1) // every <= count_before // every:
            return
        state = aggregate.snapshot()
        if state is None:
            return
        await self.store.save_snapshot(
            self.stream_id(aggregate.aggregate_id), version=after, state=state
        )


__all__: list[str] = [
    "Aggregate",
    "AggregateRepository",
]
