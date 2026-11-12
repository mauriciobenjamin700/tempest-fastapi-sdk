"""Event types shared by every event store backend.

* :class:`DomainEvent` — base for the typed Pydantic payload of an event.
* :class:`EventRegistry` — maps the stored type name back to the class.
* :class:`EventMetadata` — correlation and causation ids plus free extras.
* :class:`RecordedEvent` — an event as the store hands it back, with its
  stream version, global position and recording time.
* :class:`StreamStateEnum`, :class:`AppendResult`, :class:`Snapshot` — the
  vocabulary of :class:`~tempest_fastapi_sdk.eventsourcing.EventStore`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

E = TypeVar("E", bound="DomainEvent")


class StreamStateEnum(StrEnum):
    """Expected stream states that are not an exact version number.

    * ``ANY`` — append whatever the stream holds (no concurrency check).
    * ``NO_STREAM`` — the stream must not exist yet (creating an aggregate).
    * ``EXISTS`` — the stream must exist, at any version.
    """

    ANY = "any"
    NO_STREAM = "no_stream"
    EXISTS = "exists"


class DomainEvent(BaseModel):
    """Base class for the typed payload of an event.

    Subclass it once per event and register the subclass in an
    :class:`EventRegistry`. The stored type name is the class attribute
    ``event_type`` when the subclass declares one, otherwise the class
    name — declare it explicitly once events are stored, so renaming the
    class does not orphan the rows already written under the old name.

    Instances are frozen: an event is a fact that already happened.
    Unknown fields are ignored on decode, so removing a field from the
    class keeps old events readable; adding a field needs a default for
    the same reason.

    This is a plain Pydantic model on purpose, not
    :class:`~tempest_fastapi_sdk.BaseSchema`: that base strips whitespace
    from every string, which would store a different payload from the one
    the caller built.

    Example:
        >>> class OrderPlaced(DomainEvent):
        ...     event_type: ClassVar[str] = "order.placed"
        ...     order_id: str
        ...     total_cents: int
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    event_type: ClassVar[str]

    @classmethod
    def type_name(cls) -> str:
        """Return the name this class is stored under.

        Returns:
            str: ``event_type`` when the class declares it in its own body
            or inherits it from a ``DomainEvent`` subclass, else the class
            name.
        """
        declared: object = getattr(cls, "event_type", None)
        if isinstance(declared, str) and declared:
            return declared
        return cls.__name__


class UnknownEventTypeError(LookupError):
    """Raised when a stored event's type is not registered.

    Reading a stream decodes every event, so an aggregate stream holding a
    type the service no longer registers fails loudly instead of replaying
    a partial history. ``read_all`` and ``subscribe_all`` never raise it:
    they only return registered types.

    Attributes:
        event_type (str): The unregistered type name.
    """

    def __init__(self, event_type: str) -> None:
        """Initialize the error.

        Args:
            event_type (str): The unregistered type name.
        """
        self.event_type: str = event_type
        super().__init__(f"event type {event_type!r} is not registered")


class EventRegistry:
    """Registry of :class:`DomainEvent` classes by stored type name.

    The store encodes an event as ``(type name, JSON object)`` and decodes
    it back through this registry, so a read returns the same class the
    writer appended.

    Example:
        >>> registry = EventRegistry(OrderPlaced, OrderCancelled)
        >>> @registry.register
        ... class OrderShipped(DomainEvent):
        ...     order_id: str
    """

    def __init__(self, *events: type[DomainEvent]) -> None:
        """Create the registry, registering ``events``.

        Args:
            *events (type[DomainEvent]): Event classes to register.
        """
        self._by_name: dict[str, type[DomainEvent]] = {}
        for event in events:
            self.register(event)

    def register(self, event: type[E]) -> type[E]:
        """Register an event class; usable as a class decorator.

        Registering the same class twice is a no-op.

        Args:
            event (type[E]): The event class.

        Returns:
            type[E]: The same class, so the method works as a decorator.

        Raises:
            TypeError: If ``event`` is not a :class:`DomainEvent` subclass.
            ValueError: If another class is already registered under the
                same type name.
        """
        if not (isinstance(event, type) and issubclass(event, DomainEvent)):
            raise TypeError(f"{event!r} is not a DomainEvent subclass")
        name = event.type_name()
        existing = self._by_name.get(name)
        if existing is not None and existing is not event:
            raise ValueError(
                f"event type {name!r} is already registered to "
                f"{existing.__module__}.{existing.__qualname__}"
            )
        self._by_name[name] = event
        return event

    @property
    def types(self) -> frozenset[str]:
        """Return every registered type name.

        Returns:
            frozenset[str]: The registered names.
        """
        return frozenset(self._by_name)

    def __contains__(self, event_type: object) -> bool:
        """Return whether a type name is registered.

        Args:
            event_type (object): The type name.

        Returns:
            bool: ``True`` when registered.
        """
        return event_type in self._by_name

    def __iter__(self) -> Iterator[type[DomainEvent]]:
        """Iterate over the registered classes.

        Returns:
            Iterator[type[DomainEvent]]: The classes, in registration order.
        """
        return iter(self._by_name.values())

    def __len__(self) -> int:
        """Return how many classes are registered.

        Returns:
            int: The count.
        """
        return len(self._by_name)

    def encode(self, event: DomainEvent) -> tuple[str, dict[str, Any]]:
        """Encode an event as its type name and a JSON-compatible object.

        Args:
            event (DomainEvent): The event.

        Returns:
            tuple[str, dict[str, Any]]: The type name and the payload.

        Raises:
            UnknownEventTypeError: If the event's class is not registered —
                appending a type nobody can decode would poison the stream.
        """
        name = type(event).type_name()
        if self._by_name.get(name) is not type(event):
            raise UnknownEventTypeError(name)
        return name, event.model_dump(mode="json")

    def decode(self, event_type: str, data: Mapping[str, Any]) -> DomainEvent:
        """Decode a stored payload back into its event class.

        Args:
            event_type (str): The stored type name.
            data (Mapping[str, Any]): The stored payload.

        Returns:
            DomainEvent: The validated event.

        Raises:
            UnknownEventTypeError: If ``event_type`` is not registered.
        """
        event_cls = self._by_name.get(event_type)
        if event_cls is None:
            raise UnknownEventTypeError(event_type)
        return event_cls.model_validate(dict(data))


class EventMetadata(BaseModel):
    """Metadata appended alongside every event of one ``append`` call.

    ``correlation_id`` ties together everything one request or workflow
    caused; ``causation_id`` names the message (command or event) that
    directly caused this one. The recording time is not here: the store
    sets it (:attr:`RecordedEvent.recorded_at`).

    Attributes:
        correlation_id (str | None): The workflow the event belongs to.
        causation_id (str | None): The message that caused the event.
        extra (dict[str, Any]): Any other JSON-compatible metadata.
    """

    model_config = ConfigDict(frozen=True)

    correlation_id: str | None = None
    causation_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    """An event as stored, returned by every read of an event store.

    Attributes:
        event_id (UUID): The event's unique id.
        stream_id (str): The stream it belongs to.
        version (int): Its 0-based position in that stream — the first
            event of a stream is version ``0``.
        position (int): Its position in the store-wide log (``$all``).
            Increases with every append, but is only comparable within one
            store: the SQL store numbers events ``1, 2, 3, ...``, KurrentDB
            reports its commit position, which skips values.
        event_type (str): The registered type name.
        event (DomainEvent): The decoded payload.
        metadata (EventMetadata): Correlation, causation and extras.
        recorded_at (datetime | None): When the store recorded it, in UTC.
            The SQL store always sets it; KurrentDB reports it from the
            server, and the client types it as optional.
    """

    event_id: UUID
    stream_id: str
    version: int
    position: int
    event_type: str
    event: DomainEvent
    metadata: EventMetadata
    recorded_at: datetime | None

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-compatible dict of the whole record.

        This is the body the SQL store writes to the outbox, so a consumer
        on the broker side sees the same fields a projection sees.

        Returns:
            dict[str, Any]: ``event_id``, ``stream_id``, ``version``,
            ``position``, ``event_type``, ``data``, ``metadata`` and
            ``recorded_at`` (ISO 8601, or ``None``).
        """
        return {
            "event_id": str(self.event_id),
            "stream_id": self.stream_id,
            "version": self.version,
            "position": self.position,
            "event_type": self.event_type,
            "data": self.event.model_dump(mode="json"),
            "metadata": self.metadata.model_dump(mode="json"),
            "recorded_at": (
                self.recorded_at.isoformat() if self.recorded_at is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class AppendResult:
    """What an append wrote.

    Attributes:
        version (int): The stream version of the last appended event —
            the ``expected_version`` for the next append.
        position (int): The ``$all`` position of the last appended event.
        event_ids (tuple[UUID, ...]): The ids given to the appended events,
            in order.
    """

    version: int
    position: int
    event_ids: tuple[UUID, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A saved aggregate state, to skip replaying a long stream.

    Attributes:
        stream_id (str): The stream the state was built from.
        version (int): The last stream version folded into ``state``.
        state (dict[str, Any]): The JSON-compatible state.
    """

    stream_id: str
    version: int
    state: dict[str, Any]


def _expected_version_label(expected_version: int | StreamStateEnum) -> int | str:
    """Return ``expected_version`` as an ``int`` or the enum's plain value.

    Args:
        expected_version (int | StreamStateEnum): The expected version.

    Returns:
        int | str: A JSON-compatible form for error details.
    """
    if isinstance(expected_version, StreamStateEnum):
        return expected_version.value
    return expected_version


__all__: list[str] = [
    "AppendResult",
    "DomainEvent",
    "EventMetadata",
    "EventRegistry",
    "RecordedEvent",
    "Snapshot",
    "StreamStateEnum",
    "UnknownEventTypeError",
]
