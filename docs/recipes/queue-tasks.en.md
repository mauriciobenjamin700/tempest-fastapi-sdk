# Queue & Tasks

Background work — at-least-once message queues (FastStream/RabbitMQ), task queues (TaskIQ), periodic schedulers, and the transactional outbox pattern.

## Message queues — FastStream


`AsyncBrokerManager` wraps any FastStream broker (RabbitMQ, Kafka, NATS, Redis Streams) with a uniform connect/disconnect/health-check surface. The broker instance is injected so the SDK doesn't pin a single transport.

Install with `[queue]` (pulls `faststream[rabbit]`). Pick the matching FastStream extra for other transports.

```python
# src/queue/__init__.py
from faststream.rabbit import RabbitBroker
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import AsyncBrokerManager

from src.core.settings import settings


broker = RabbitBroker(settings.RABBITMQ_URL)
queue = AsyncBrokerManager(broker)


class OrderMessage(BaseModel):
    order_id: str
    user_id: str


@broker.subscriber("orders.paid")
async def handle_order_paid(msg: OrderMessage) -> None:
    await mark_order_paid(msg.order_id, msg.user_id)


# src/api/app.py lifespan
await queue.connect()
...
await queue.disconnect()


# Publish from anywhere in the application
await queue.publish(OrderMessage(order_id="abc", user_id="x"), queue="orders.paid")
```

The manager exposes:

- `connect()` / `disconnect()` — idempotent; safe to call from FastAPI lifespan.
- `publish(message, *args, **kwargs)` — passthrough to `broker.publish` with a `RuntimeError` guard when the broker isn't started.
- `lifespan()` — async context manager handling start/stop, handy for short scripts.
- `broker_dependency` — FastAPI `Depends` that yields the live broker.
- `health_check()` / `is_connected` — true while the broker is started.

Wire it on the health router with `make_health_router(checks={"queue": queue.health_check})`.


## Background tasks — TaskIQ


`AsyncTaskBrokerManager` wraps any TaskIQ broker (AioPika for RabbitMQ, Redis, in-memory for tests). Install with `[tasks]` (pulls `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py
from taskiq_aio_pika import AioPikaBroker

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager

from src.core.settings import settings


tasks = AsyncTaskBrokerManager(AioPikaBroker(settings.TASKIQ_BROKER_URL))


@tasks.task
async def send_welcome_email(to: str, name: str) -> None:
    await email_utils.send(
        to=to,
        subject="Bem-vindo!",
        body=f"Olá, {name} — sua conta foi criada.",
    )


# src/api/app.py lifespan
await tasks.connect()
...
await tasks.disconnect()


# Enqueue from a request handler
await send_welcome_email.kiq(to=user.email, name=user.name)
```

`register_task(callable, task_name=..., **kwargs)` registers a function without decorator syntax — useful when wiring third-party callables that you can't decorate at definition time. For tests, swap the broker for `taskiq.InMemoryBroker()` so kicked tasks execute synchronously.

The same lifespan guard rails as the queue manager apply: `connect()`/`disconnect()`/`lifespan()`/`broker_dependency`/`health_check()`/`is_connected`.


## Periodic tasks scheduler


`AsyncTaskScheduler` wraps `taskiq.TaskiqScheduler` + `LabelScheduleSource` so periodic tasks are declared with decorators alongside regular tasks and the scheduler is driven from the FastAPI lifespan. It **does not execute task bodies** — it kicks them into the same broker `AsyncTaskBrokerManager` wraps, so a worker process must be running to consume them. Requires the `[tasks]` extra.

```python
# src/tasks/__init__.py
from datetime import timedelta

from taskiq_aio_pika import AioPikaBroker

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager, AsyncTaskScheduler

from src.core.settings import settings


# Use TASKIQ_BROKER_URL (from TaskIQSettings) when the scheduler /
# task broker is a different broker than the FastStream queue
# (RABBITMQ_URL). Reuse the same RabbitMQ URL when they share the
# broker — both env vars can point to the same value.
broker = AioPikaBroker(settings.TASKIQ_BROKER_URL)
tasks = AsyncTaskBrokerManager(broker)
scheduler = AsyncTaskScheduler(broker)


@tasks.task
async def reconcile_invoices(batch_size: int = 100) -> None:
    """Background task — kicked by handlers or the scheduler."""
    ...


@scheduler.cron("*/5 * * * *")          # every five minutes
async def heartbeat() -> None:
    """Liveness ping written to the audit log."""
    ...


@scheduler.cron("0 9 * * MON-FRI", cron_offset="-03:00")  # 09:00 BRT, weekdays
async def daily_digest() -> None:
    ...


@scheduler.interval(seconds=30)         # every 30s
async def poll_remote_queue() -> None:
    ...


@scheduler.interval(timedelta(minutes=15))
async def warm_cache() -> None:
    ...
```

Wire it into the app lifespan next to the broker manager:

```python
# src/api/app.py
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await tasks.connect()
    await scheduler.connect()
    await scheduler.run_in_background()   # dev / single-process services
    try:
        yield
    finally:
        await scheduler.disconnect()
        await tasks.disconnect()
```

Decorator surface:

| Method | When to use |
| --- | --- |
| `@scheduler.cron("*/5 * * * *", cron_offset=None)` | Cron expression; pass `cron_offset` (string like `"-03:00"` or `timedelta`) to anchor to a timezone other than UTC. |
| `@scheduler.interval(seconds=30)` / `@scheduler.interval(timedelta(...))` | Fixed-interval recurrence. |
| `@scheduler.schedule([{...}, {...}])` | Raw TaskIQ schedule list — combine triggers, use one-shot `time`, etc. |
| `scheduler.register(func, schedule=[...], task_name=...)` | Register without decorator syntax (third-party callables). |

Production deployments with multiple workers should run the standalone scheduler CLI instead of `run_in_background()`, so only one scheduler is active across the cluster:

```bash
taskiq scheduler src.tasks:scheduler.scheduler
```

(`scheduler.scheduler` is the inner `TaskiqScheduler` instance exposed on `AsyncTaskScheduler`.) The worker process stays the same:

```bash
taskiq worker src.tasks:tasks.broker
```

Lifecycle controls mirror the broker manager: `connect()` / `disconnect()` / `lifespan()` / `run_in_background()` / `health_check()` / `is_connected`.


## Outbox dispatcher pattern


The transactional outbox pattern keeps a "to publish" table in the same database as the domain rows, so writing the row and recording the side-effect happen in a single transaction. A worker reads the outbox in order and publishes to RabbitMQ (FastStream) / TaskIQ, marking each row as dispatched only after the broker ACKs. Crashes between commit and publish replay safely on the next poll.

The SDK does **not** ship a dedicated `OutboxDispatcher` primitive — the implementation is short, opinionated, and benefits from staying in the service's `db/models/` + `tasks/` boundary. Use the recipe below.

```python
# src/db/models/outbox.py
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class OutboxEventModel(BaseModel):
    """One row per domain event waiting to be published."""

    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        index=True,
    )
    # is_active / created_at / updated_at come from BaseModel.
```

```python
# src/db/repositories/outbox.py
from sqlalchemy import select, update

from tempest_fastapi_sdk import BaseRepository

from src.db.models import OutboxEventModel


class OutboxRepository(BaseRepository[OutboxEventModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=OutboxEventModel)

    async def claim_pending(self, *, limit: int = 100) -> list[OutboxEventModel]:
        """Lock-free claim — fine for single-worker dispatcher."""
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.status == "pending")
            .order_by(OutboxEventModel.created_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_dispatched(self, ids: list[str]) -> None:
        await self.session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id.in_(ids))
            .values(status="dispatched"),
        )
        await self.session.commit()
```

```python
# src/services/orders.py — produce side
from src.db.models import OrderModel, OutboxEventModel


class OrderService:
    async def place_order(self, data: OrderCreateSchema) -> OrderResponseSchema:
        order = OrderModel(**data.to_dict())
        self.repo.session.add(order)
        # Same transaction as the order row.
        self.repo.session.add(
            OutboxEventModel(
                topic="orders.placed",
                payload={"order_id": str(order.id), "amount": order.amount},
            ),
        )
        await self.repo.session.flush()
        await self.repo.session.commit()
        return self.repo.map_to_response(order)
```

```python
# src/tasks/__init__.py — dispatcher side
from tempest_fastapi_sdk.tasks import AsyncTaskScheduler

from src.api.app import broker as queue_broker  # FastStream AsyncBrokerManager
from src.api.app import db, taskiq_broker

scheduler = AsyncTaskScheduler(taskiq_broker)


@scheduler.interval(seconds=5)
async def dispatch_outbox() -> None:
    """Poll the outbox and publish each pending event."""
    async with db.get_session_context() as session:
        repo = OutboxRepository(session)
        events = await repo.claim_pending(limit=100)
        if not events:
            return
        dispatched: list[str] = []
        for event in events:
            try:
                await queue_broker.publish(event.payload, event.topic)
                dispatched.append(str(event.id))
            except Exception:  # noqa: BLE001 — retry on next tick
                continue
        if dispatched:
            await repo.mark_dispatched(dispatched)
```

Trade-offs to keep in mind:

- **Order is best-effort.** When a batch contains one failing publish, every later event in the same batch still runs — but they're still published in `created_at` order. If strict ordering matters, break on the first failure.
- **Single dispatcher.** The naive `claim_pending` does not lock rows; running multiple dispatcher workers will double-publish. Use `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL when you need to scale out.
- **Retention.** Add a periodic `TRUNCATE`-style job to delete `dispatched` rows older than N days, otherwise the outbox table grows unbounded.
- **At-least-once.** Consumers must be idempotent — the dispatcher can crash after publishing but before `mark_dispatched`.

