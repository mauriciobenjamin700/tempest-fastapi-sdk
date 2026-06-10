# Transactional outbox (reliable events)

When a handler **writes a row** and **publishes an event**, doing the two
as independent operations is fragile: if the process dies *after* the
commit but *before* the publish, the event is lost; if it dies after the
publish but before the commit, a phantom event references a row that never
existed. This is the **dual-write problem**.

The **outbox** pattern fixes it: write the business row **and** an
`outbox` row in the **same transaction**. Either both commit or neither
does. A separate *relay* reads the pending rows and publishes them to the
broker, marking each one sent. The broker can be down for minutes — the
events wait, durably, in the table.

!!! info "Where this fits"
    It complements the [`AsyncBrokerManager`](queue-tasks.md): the broker
    *publishes*, the outbox *guarantees* the event exists to be published.
    The relay takes any publish callable — so it works with FastStream, a
    webhook, whatever.

## 1. The outbox table

`BaseOutboxModel` is abstract — the project creates the concrete table
(just like `BaseUserModel`):

```python
from tempest_fastapi_sdk import BaseOutboxModel


class OutboxModel(BaseOutboxModel):
    """The service's pending-events table."""

    __tablename__ = "outbox"
```

It ships `topic`, `payload` (JSON), `status`, `attempts`, `max_attempts`,
`available_at`, `sent_at` and `last_error` — on top of `BaseModel`'s four
canonical columns (`id` / `is_active` / `created_at` / `updated_at`).
Generate the migration with the [`AlembicHelper`](database.md) like any
other table.

## 2. Write atomically

In the service/repository, use `save_with_outbox` instead of `add`: it
inserts the business model **and** the event in one transaction.

```python
from tempest_fastapi_sdk import BaseRepository

from src.db.models import OrderModel, OutboxModel


async def place_order(repo: BaseRepository[OrderModel], data: dict[str, object]) -> OrderModel:
    """Create the order and queue the event in the same transaction."""
    order = OrderModel(**data)
    event = OutboxModel.new_event("orders.created", {"order": data})
    return await repo.save_with_outbox(order, event)
```

If the `commit` fails (e.g. a unique constraint), **both** rows are rolled
back — no orphan event is ever left behind.

## 3. Drain and publish (the relay)

`OutboxRelay` reads pending rows and calls your publish callable. It does
not import any specific broker — you pass the function:

```python
import asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseOutboxModel, OutboxRelay

from src.db.models import OutboxModel


async def run_relay(db: AsyncDatabaseManager, broker: object) -> None:
    """Publish pending events continuously."""

    async def publish(event: BaseOutboxModel) -> None:
        """Forward one event to the broker."""
        await broker.publish(event.payload, event.topic)  # type: ignore[attr-defined]

    relay: OutboxRelay = OutboxRelay(db, model=OutboxModel, publish=publish)
    await relay.run(poll_interval=1.0)  # loops until the task is cancelled
```

Run the relay as a separate process/worker (or a task in the lifespan).
Each published event becomes `status="sent"` with `sent_at` set.

### Failures and retry

If `publish` raises, the relay does **not** mark the event sent: it
increments `attempts`, records the error in `last_error`, and reschedules
the event with exponential backoff (`available_at` in the future). Once
`attempts` reaches `max_attempts`, the row becomes `status="failed"` and
stays in the table for manual inspection (never auto-retried again).

!!! tip "Multiple workers"
    On PostgreSQL/MySQL the relay locks the batch with `FOR UPDATE SKIP
    LOCKED`, so you can run **several** relay workers without publishing
    the same event twice. On SQLite (no row locks) it falls back to a
    plain `SELECT` — use a single worker.

### Drain once (tests / cron)

For loop-free scenarios (a test, a cron job), call `drain_once()`, which
returns how many events were published:

```python
published: int = await relay.drain_once()
```

## Recap

- `BaseOutboxModel` → concrete `OutboxModel(__tablename__="outbox")`.
- `repo.save_with_outbox(model, event)` writes business + event
  **atomically**.
- `OutboxRelay(db, model=..., publish=...).run()` publishes pending rows,
  with retry/backoff and `sent` / `failed` marking.
- `OutboxModel.new_event(topic, payload)` builds the event; `drain_once()`
  drains one batch for tests/cron.
