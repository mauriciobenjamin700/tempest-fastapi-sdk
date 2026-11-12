# Event sourcing (event store)

Instead of storing an order's current state in a row you update, you store
**the facts that happened to it** — `order.placed`, `order.shipped` — in a
sequence that only grows. The state is the sum of the facts. You get the full
history, an audit trail for free, and read models you can rebuild from
scratch.

The SDK ships this in `tempest_fastapi_sdk.eventsourcing`, with **one
protocol and two backends**:

| Backend | Where it writes | Install |
| --- | --- | --- |
| `SqlEventStore` | the PostgreSQL / SQLite the service already has | no new dependency |
| `KurrentDBEventStore` | KurrentDB (formerly EventStoreDB) | `[kurrentdb]` extra |

Code written against `EventStore` — aggregates, projections — runs on both.

## Define the events

Each event is a Pydantic class inheriting `DomainEvent`. The registry
(`EventRegistry`) maps the stored name back to the class, so a read returns
the same type that was written.

```python
from typing import ClassVar

from tempest_fastapi_sdk.eventsourcing import DomainEvent, EventRegistry


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class OrderShipped(DomainEvent):
    event_type: ClassVar[str] = "order.shipped"

    order_id: str


registry = EventRegistry(OrderPlaced, OrderShipped)
```

- **`event_type` is the stored name.** Without it the class name is used —
  declare it as soon as events are stored, so renaming the class does not
  orphan the rows already written.
- **An event is immutable** (`frozen`). Unknown fields are ignored on read, so
  removing a field keeps old events readable; a new field needs a default for
  the same reason.
- **Not `BaseSchema`, on purpose:** that base strips whitespace from strings,
  and the stored event would differ from the one you built.

## Write and read (SQL backend)

The SQL backend uses abstract tables you subclass and name, like
`BaseOutboxModel` — they live in your `metadata` and your migrations.

```python
import asyncio
from typing import ClassVar

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    WrongExpectedVersionException,
)
from tempest_fastapi_sdk.eventsourcing import (
    BaseEventModel,
    BaseEventStreamModel,
    DomainEvent,
    EventMetadata,
    EventRegistry,
    SqlEventStore,
    StreamStateEnum,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class OrderShipped(DomainEvent):
    event_type: ClassVar[str] = "order.shipped"

    order_id: str


class EventModel(BaseEventModel):
    __tablename__ = "events"


class EventStreamModel(BaseEventStreamModel):
    __tablename__ = "event_streams"


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
    await db.create_tables(BaseModel.metadata)
    store = SqlEventStore(
        db,
        registry=EventRegistry(OrderPlaced, OrderShipped),
        event_model=EventModel,
        stream_model=EventStreamModel,
    )

    result = await store.append(
        "order-42",
        [OrderPlaced(order_id="42", total_cents=990)],
        expected_version=StreamStateEnum.NO_STREAM,
        metadata=EventMetadata(correlation_id="req-1"),
    )
    print(result.version, result.position)  # 0 1

    await store.append(
        "order-42", [OrderShipped(order_id="42")], expected_version=result.version
    )
    try:
        await store.append(
            "order-42", [OrderShipped(order_id="42")], expected_version=result.version
        )
    except WrongExpectedVersionException as error:
        print(error.status_code, error.expected_version, error.actual_version)
        # 409 0 1

    for recorded in await store.read_stream("order-42"):
        print(recorded.version, recorded.event_type, recorded.event)
    # 0 order.placed order_id='42' total_cents=990
    # 1 order.shipped order_id='42'
    await db.disconnect()


asyncio.run(main())
```

Piece by piece:

- **Versions start at `0`.** The first event of a stream is version `0`; a
  stream holding three events is at version `2`. It is KurrentDB's
  convention too, so switching backends changes no number.
- **`expected_version` is required.** A number says "the stream is at this
  version"; `StreamStateEnum.NO_STREAM` says "the stream does not exist yet";
  `EXISTS` requires it to exist; `ANY` turns the check off. There is no
  default — the concurrency check is a decision, not something to forget.
  A member's plain value (`"no_stream"`, which is what a field under
  `use_enum_values` holds) means the member; any other string is a
  `ValueError`.
- **`EventMetadata`** carries `correlation_id`, `causation_id` and `extra`.
  The time comes from the store: `recorded.recorded_at`, in UTC.
- **`read_stream`** reads forwards or backwards (`backwards=True`), from an
  **inclusive** version (`from_version`), with `limit`. A missing stream
  returns `[]`.

## Optimistic concurrency

Two processes load the order at version `1` and decide different things. The
first one writes; the second gets `WrongExpectedVersionException` (status
`409`, code `EVENTSOURCING_WRONG_EXPECTED_VERSION`) with `expected_version` and
`actual_version`. The right reaction is to **reload and decide again** —
repeating the same append blindly would erase the other writer's decision.

On SQL the check is a conditional `UPDATE` of the stream row
(`... WHERE version = :expected`) issued **before** any event is inserted.
Measured with real transactions on separate connections:

- with the first transaction **open**, the second one blocks (confirmed in
  PostgreSQL 16's `pg_stat_activity`) and, once the first commits, is
  refused — on SQLite (a file) and on PostgreSQL;
- 8 writers with the same expectation, 20 rounds: exactly one writes per
  round, on both databases (`tests/eventsourcing/test_sql_concurrency.py`).

!!! warning "One append at a time on PostgreSQL"
    The global position (`position`) is `max + 1` under a transaction-scoped
    advisory lock, so events are numbered **in commit order, without gaps**.
    A sequence would not give that — and it was measured: transaction 1 draws
    position `1` and stays open, transaction 2 draws `2` and commits, and a
    reader sees `2` without `1`. A projection that checkpointed `2` would
    never read `1`. The price is that appends to the whole store run one at
    a time, and the lock is held until commit: when you pass your own
    `session=`, keep the transaction short. Without the lock, the suite
    measures concurrent appends to different streams colliding on the
    `position` unique constraint.

## Inside your use case's transaction

Every `SqlEventStore` method takes `session=`. With it, the append joins
**your** transaction and the commit is yours — the event and an update to a
read model, or to any other table, land together or not at all.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.eventsourcing import (
    DomainEvent,
    SqlEventStore,
    StreamStateEnum,
)


async def place_order(
    store: SqlEventStore, session: AsyncSession, order_id: str, event: DomainEvent
) -> None:
    await store.append(
        f"order-{order_id}",
        [event],
        expected_version=StreamStateEnum.NO_STREAM,
        session=session,
    )
    await session.commit()
```

A `NO_STREAM` conflict inside your session does not break the transaction:
the insert runs in a savepoint, and the suite checks that a following append
in the same session still commits.

!!! note "SQLite: write before you read"
    On SQLite, a transaction that already read and then writes after another
    writer committed fails at once with `database is locked` — measured with
    `AsyncDatabaseManager`'s 30 s busy timeout. `append` writes before it
    reads for that reason; when you pass your own `session=`, append before
    any read in the same transaction.

## Publishing to the broker: outbox

Pass `outbox_model=` and every stored event also gets an
[outbox](outbox.md) row **in the same transaction**, with the payload of
`RecordedEvent.to_payload()` and the topic set to the `event_type` (change it
with `outbox_topic=`). `OutboxRelay` publishes it (at-least-once). An append
refused by `expected_version` leaves no outbox row — the suite checks that,
after one accepted two-event append and one refused append, the relay
publishes two.

```python
from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.db.outbox import BaseOutboxModel, OutboxRelay
from tempest_fastapi_sdk.eventsourcing import (
    BaseEventModel,
    BaseEventStreamModel,
    EventRegistry,
    SqlEventStore,
)


class EventModel(BaseEventModel):
    __tablename__ = "events"


class EventStreamModel(BaseEventStreamModel):
    __tablename__ = "event_streams"


class OutboxModel(BaseOutboxModel):
    __tablename__ = "outbox"


def build(
    db: AsyncDatabaseManager, registry: EventRegistry
) -> tuple[SqlEventStore, OutboxRelay]:
    store = SqlEventStore(
        db,
        registry=registry,
        event_model=EventModel,
        stream_model=EventStreamModel,
        outbox_model=OutboxModel,
    )

    async def publish(row: BaseOutboxModel) -> None:
        print(row.topic, row.payload["stream_id"], row.payload["version"])
        # order.placed order-42 0

    return store, OutboxRelay(db, model=OutboxModel, publish=publish)
```

## `$all`, positions and projections

`read_all(after_position=...)` reads the store-wide log in position order,
**after** the given position (exclusive), registered types only.
`subscribe_all()` does the same and keeps waiting for new events (polling on
SQL, pushed on KurrentDB).

To feed a read model, use `ProjectionRunner`: it reads from the checkpoint,
hands each event to your handler and moves the checkpoint forward.

```python
from typing import ClassVar

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.eventsourcing import (
    BaseProjectionCheckpointModel,
    DomainEvent,
    EventStore,
    ProjectionRunner,
    RecordedEvent,
    SqlCheckpointStore,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class ProjectionCheckpointModel(BaseProjectionCheckpointModel):
    __tablename__ = "projection_checkpoints"


async def project_totals(store: EventStore, db: AsyncDatabaseManager) -> None:
    totals: dict[str, int] = {}

    async def project(event: RecordedEvent) -> None:
        if isinstance(event.event, OrderPlaced):
            totals[event.event.order_id] = event.event.total_cents

    runner = ProjectionRunner(
        store,
        name="order-totals",
        handler=project,
        checkpoints=SqlCheckpointStore(db, model=ProjectionCheckpointModel),
    )
    await runner.run_once()
```

- **Delivery is at-least-once.** If the process dies after the handler and
  before the checkpoint, the event comes back. Make the handler idempotent
  (upsert by `event_id` or by version).
- A handler that raises stops the batch there: the checkpoint is saved at the
  last event that finished, and the next run starts at the one that failed.
- `runner.run(poll_interval=..., stop_event=...)` loops, shaped like
  `OutboxRelay.run`.
- A position is only compared and stored. On SQL it is `1, 2, 3...`; on
  KurrentDB it is the commit position, which skips values.

## Aggregates

`Aggregate` is a thin base class: you implement `apply` (how each event
changes the state) and call `record` from your command methods.
`AggregateRepository` loads by replaying the stream and saves with one append
whose `expected_version` is the version the aggregate was loaded at.

```python
from typing import Any, ClassVar

from tempest_fastapi_sdk.eventsourcing import (
    Aggregate,
    AggregateRepository,
    DomainEvent,
    EventStore,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class OrderShipped(DomainEvent):
    event_type: ClassVar[str] = "order.shipped"

    order_id: str


class Order(Aggregate):
    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.total_cents = 0
        self.shipped = False

    def place(self, total_cents: int) -> None:
        self.record(OrderPlaced(order_id=self.aggregate_id, total_cents=total_cents))

    def ship(self) -> None:
        if self.shipped:
            raise ValueError("already shipped")
        self.record(OrderShipped(order_id=self.aggregate_id))

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.total_cents = event.total_cents
        elif isinstance(event, OrderShipped):
            self.shipped = True

    def snapshot(self) -> dict[str, Any] | None:
        return {"total_cents": self.total_cents, "shipped": self.shipped}

    def restore(self, state: dict[str, Any]) -> None:
        self.total_cents = int(state["total_cents"])
        self.shipped = bool(state["shipped"])


async def ship(store: EventStore, order_id: str) -> None:
    orders = AggregateRepository(store, Order, stream_prefix="order-", snapshot_every=50)
    order = await orders.load(order_id)
    if order is None:
        raise LookupError(order_id)
    order.ship()
    await orders.save(order)
```

- `load` returns `None` for a missing stream; a new aggregate (`Order("43")`)
  is saved with `NO_STREAM`, so creating the same id twice is a `409`.
- A conflicting `save` keeps the pending events on the aggregate.
- **Snapshots are opt-in:** `snapshot_every=50` saves a snapshot each time the
  stream crosses a multiple of 50 events, and `load` applies only what came
  after it. On SQL, pass `snapshot_model=` (a `BaseEventSnapshotModel`
  subclass) to the store. A snapshot older than the stored one is ignored.

## KurrentDB

```bash
uv add "tempest-fastapi-sdk[kurrentdb]"
docker run -d --name kurrentdb -p 127.0.0.1:2113:2113 \
  -e KURRENTDB_INSECURE=true kurrentplatform/kurrentdb:26.2.3
```

```python
import asyncio
from typing import ClassVar

from tempest_fastapi_sdk import WrongExpectedVersionException
from tempest_fastapi_sdk.eventsourcing import (
    DomainEvent,
    EventRegistry,
    KurrentDBEventStore,
    StreamStateEnum,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


async def main() -> None:
    store = KurrentDBEventStore.from_uri(
        "kurrentdb://127.0.0.1:2113?Tls=false",
        registry=EventRegistry(OrderPlaced),
    )
    try:
        first = await store.append(
            "order-42",
            [OrderPlaced(order_id="42", total_cents=990)],
            expected_version=StreamStateEnum.NO_STREAM,
        )
        print(first.version)  # 0
        try:
            await store.append(
                "order-42",
                [OrderPlaced(order_id="42", total_cents=1)],
                expected_version=StreamStateEnum.NO_STREAM,
            )
        except WrongExpectedVersionException as error:
            print(error.expected_version, error.actual_version)  # no_stream 0

        async for recorded in store.subscribe_all():
            print(recorded.stream_id, recorded.version, recorded.event)
            break
    finally:
        await store.close()


asyncio.run(main())
```

Same protocol; what changes underneath:

- **The official `kurrentdbclient`** (`esdbclient` stopped at 1.1.7, April
  2025, pinned to `grpcio<1.72`). Imported only when used: the
  `eventsourcing` package imports without the extra.
- **The client's `WrongCurrentVersionError` becomes
  `WrongExpectedVersionException`**, with the same `actual_version`.
- **`after_position` is exclusive in both methods.** In the client,
  `read_all(commit_position=p)` includes the event at `p` and
  `subscribe_to_all(commit_position=p)` starts after it — measured; the store
  hides the difference.
- **Only registered types in `$all`.** The client builds its filter as
  `"^" + "|".join(patterns) + "$"`, which anchors only the first and last
  alternatives; the store passes one grouped pattern. Without it, registering
  `order.placed` brought an unregistered `order.placed.v2` back from `$all`
  (measured on KurrentDB 26.2.3).
- **Metadata** uses KurrentDB's own `$correlationId` and `$causationId` keys,
  which its `$by_correlation_id` system projection reads.
- **Snapshots** live in the `snapshot-<stream>` stream, with `$maxCount: 1`.
- Persistent subscriptions (competing consumers) are not wrapped: use
  `store.client` directly.

Measured with KurrentDB 26.2.3 (image `kurrentplatform/kurrentdb:26.2.3`) and
`kurrentdbclient` 1.3.0 (the declared floor) and 1.3.6. No other server
version was exercised.

## Recap

- `DomainEvent` + `EventRegistry`: typed events, stored by name.
- `EventStore.append(..., expected_version=...)`: all or nothing, with
  `WrongExpectedVersionException` (409) when another writer got there first.
- `SqlEventStore`: no new dependency, `session=` for your transaction,
  `outbox_model=` to publish; on PostgreSQL appends are serialized so `$all`
  has no gaps.
- `KurrentDBEventStore`: same protocol, `[kurrentdb]` extra.
- `AggregateRepository` (replay + snapshot) and `ProjectionRunner`
  (checkpoint over `$all`) work with either.
