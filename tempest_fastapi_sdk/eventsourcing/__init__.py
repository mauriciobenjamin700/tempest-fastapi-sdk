"""Event sourcing — an event store protocol with SQL and KurrentDB backends.

* :class:`EventStore` — the protocol: append with optimistic concurrency,
  read a stream forwards or backwards, read and subscribe to the
  store-wide log (``$all``) by position, snapshots.
* :class:`SqlEventStore` — on the PostgreSQL / SQLite the service already
  has, over the abstract tables in
  :mod:`~tempest_fastapi_sdk.eventsourcing.models`; can write every event to
  the transactional outbox in the same transaction.
* :class:`KurrentDBEventStore` — on KurrentDB, through the official client
  (``kurrentdb`` extra, imported only when used).
* :class:`DomainEvent` + :class:`EventRegistry` — typed Pydantic payloads,
  stored by type name.
* :class:`Aggregate` + :class:`AggregateRepository` — load by replay (after a
  snapshot), save by one append.
* :class:`ProjectionRunner` — feed a read model from ``$all`` and a
  checkpoint.

Both stores raise :class:`~tempest_fastapi_sdk.WrongExpectedVersionException`
(409) when an append's ``expected_version`` does not match the stream.
"""

from tempest_fastapi_sdk.eventsourcing.aggregate import Aggregate as Aggregate
from tempest_fastapi_sdk.eventsourcing.aggregate import (
    AggregateRepository as AggregateRepository,
)
from tempest_fastapi_sdk.eventsourcing.events import AppendResult as AppendResult
from tempest_fastapi_sdk.eventsourcing.events import DomainEvent as DomainEvent
from tempest_fastapi_sdk.eventsourcing.events import EventMetadata as EventMetadata
from tempest_fastapi_sdk.eventsourcing.events import EventRegistry as EventRegistry
from tempest_fastapi_sdk.eventsourcing.events import RecordedEvent as RecordedEvent
from tempest_fastapi_sdk.eventsourcing.events import Snapshot as Snapshot
from tempest_fastapi_sdk.eventsourcing.events import (
    StreamStateEnum as StreamStateEnum,
)
from tempest_fastapi_sdk.eventsourcing.events import (
    UnknownEventTypeError as UnknownEventTypeError,
)
from tempest_fastapi_sdk.eventsourcing.kurrentdb import (
    SNAPSHOT_EVENT_TYPE as SNAPSHOT_EVENT_TYPE,
)
from tempest_fastapi_sdk.eventsourcing.kurrentdb import (
    KurrentDBEventStore as KurrentDBEventStore,
)
from tempest_fastapi_sdk.eventsourcing.models import BaseEventModel as BaseEventModel
from tempest_fastapi_sdk.eventsourcing.models import (
    BaseEventSnapshotModel as BaseEventSnapshotModel,
)
from tempest_fastapi_sdk.eventsourcing.models import (
    BaseEventStreamModel as BaseEventStreamModel,
)
from tempest_fastapi_sdk.eventsourcing.models import (
    BaseProjectionCheckpointModel as BaseProjectionCheckpointModel,
)
from tempest_fastapi_sdk.eventsourcing.projection import (
    CheckpointStore as CheckpointStore,
)
from tempest_fastapi_sdk.eventsourcing.projection import (
    InMemoryCheckpointStore as InMemoryCheckpointStore,
)
from tempest_fastapi_sdk.eventsourcing.projection import (
    ProjectionRunner as ProjectionRunner,
)
from tempest_fastapi_sdk.eventsourcing.projection import (
    SqlCheckpointStore as SqlCheckpointStore,
)
from tempest_fastapi_sdk.eventsourcing.sql import SqlEventStore as SqlEventStore
from tempest_fastapi_sdk.eventsourcing.store import EventStore as EventStore
from tempest_fastapi_sdk.exceptions.eventsourcing import (
    WrongExpectedVersionException as WrongExpectedVersionException,
)

__all__: list[str] = [
    "SNAPSHOT_EVENT_TYPE",
    "Aggregate",
    "AggregateRepository",
    "AppendResult",
    "BaseEventModel",
    "BaseEventSnapshotModel",
    "BaseEventStreamModel",
    "BaseProjectionCheckpointModel",
    "CheckpointStore",
    "DomainEvent",
    "EventMetadata",
    "EventRegistry",
    "EventStore",
    "InMemoryCheckpointStore",
    "KurrentDBEventStore",
    "ProjectionRunner",
    "RecordedEvent",
    "Snapshot",
    "SqlCheckpointStore",
    "SqlEventStore",
    "StreamStateEnum",
    "UnknownEventTypeError",
    "WrongExpectedVersionException",
]
