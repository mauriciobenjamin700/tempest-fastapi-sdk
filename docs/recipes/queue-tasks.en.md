# Queues and Tasks

Background work without the pain. The SDK wraps **FastStream** (messaging) and **TaskIQ** (tasks + scheduling) in typed classes with a single vocabulary — you **never import** `faststream` or `taskiq` in application code.

!!! tip "Which tool?"
    - **`MessageBroker`** (messaging) — an event happens and **many** services/consumers react. Fan-out, at-least-once, decoupled from the request. E.g. "order paid" → inventory, email, analytics.
    - **`TaskQueue`** (tasks) — offload slow work from **one** request handler to a worker, keeping the HTTP response fast. E.g. send an email, render a PDF.
    - **`TaskQueue.cron` / `.interval`** (scheduling) — periodic runs.
    - **Outbox** — when publishing *must* be atomic with a database `INSERT`.

Every class shares the same lifecycle — `connect()` / `disconnect()` / `lifespan()` / `health_check()` / `is_connected` — and exposes the raw underlying object (`.broker`) as an escape hatch.

## Messaging — `MessageBroker`

The problem FastStream handles poorly: its API changes shape with the transport. You subscribe with `@broker.subscriber("q")` and publish with `broker.publish(msg, queue="q")` on RabbitMQ, `topic=` on Kafka, `subject=` on NATS. Confusing and non-portable.

`MessageBroker` hides that behind **one** concept: a **channel** (a string). You publish to a channel and everyone subscribed to it receives the message.

Install with `[queue]` (pulls `faststream[rabbit]`).

```python
# src/queue/__init__.py
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import MessageBroker

from src.core.settings import settings


# Pick the transport with a constructor — no faststream import.
mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)


class OrderPaid(BaseModel):
    order_id: str
    user_id: str


@mq.on("orders.paid")
async def handle_order_paid(event: OrderPaid) -> None:
    """Receives every event published to the 'orders.paid' channel."""
    await mark_order_paid(event.order_id, event.user_id)
```

Note the `event: OrderPaid`: **the type hint drives decoding**. FastStream validates the inbound payload into that Pydantic model **before** your handler runs — a malformed message never reaches your code.

Wire the lifecycle into the FastAPI lifespan and publish from anywhere:

```python
# src/api/app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.queue import mq, OrderPaid


@asynccontextmanager
async def lifespan(_: FastAPI):
    await mq.connect()
    try:
        yield
    finally:
        await mq.disconnect()


app = FastAPI(lifespan=lifespan)


# From any service/handler — channel first, message second:
await mq.publish("orders.paid", OrderPaid(order_id="abc", user_id="u1"))
```

!!! info "Transports"
    `MessageBroker.rabbitmq(url)`, `.redis(url)`, `.kafka(*servers)`, `.nats(servers)`. Each lazily imports the right FastStream backend and raises with the exact install command if the extra is missing. Need a custom (or test) broker? `MessageBroker(my_broker)`.

!!! check "Recap"
    - `MessageBroker.rabbitmq(url)` — pick the transport, hide FastStream.
    - `@mq.on("channel")` — declare a consumer; the parameter type validates the message.
    - `await mq.publish("channel", model)` — publish; channel first.
    - `mq.publish(...)` only works after `connect()` (raises `RuntimeError` before).

Wire it into the health router: `make_health_router(checks={"queue": mq.health_check})`.

## Background tasks — `TaskQueue`

A **task queue** takes slow work out of the request and hands it to a worker. TaskIQ does this but spreads the API across a broker, a scheduler, a schedule source and `.kiq()`. `TaskQueue` folds it all into one object with an obvious vocabulary.

Install with `[tasks]` (pulls `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py
from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings


tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@tq.task
async def send_welcome(to: str, name: str) -> None:
    """Runs on a worker, off the request path."""
    await email.send(to, "Welcome!", f"Hi, {name}.")
```

`@tq.task` returns a typed `Task` object with **two** clear actions:

```python
# Enqueue to the worker and return immediately (the HTTP response doesn't wait):
await send_welcome.enqueue(to=user.email, name=user.name)

# Run inline, right here, returning the real value (handy in tests / reuse):
await send_welcome.run(to="a@b.com", name="Ana")
```

!!! tip "`enqueue` instead of `.kiq`"
    `enqueue()` makes it obvious what happens: the call goes to the worker. `run()` executes the body locally, no broker. The cryptic `.kiq` name stays hidden (still reachable at `send_welcome.taskiq_task` if you need it).

Lifespan mirrors the message broker:

```python
# src/api/app.py
@asynccontextmanager
async def lifespan(_: FastAPI):
    await tq.connect()
    try:
        yield
    finally:
        await tq.disconnect()
```

!!! note "Tests without a broker"
    `TaskQueue.memory()` uses TaskIQ's in-memory broker: `enqueue()` runs the task **immediately, in-process**. No worker, no connection. `run()` always works, even without `connect()`.

## Periodic tasks — `cron` / `interval`

Scheduling is part of the same `TaskQueue` — no separate scheduler in your code:

```python
# src/tasks/__init__.py
from datetime import timedelta


@tq.cron("*/5 * * * *")                       # every 5 minutes
async def heartbeat() -> None:
    ...


@tq.cron("0 9 * * MON-FRI", cron_offset="-03:00")  # 09:00 BRT, weekdays
async def daily_digest() -> None:
    ...


@tq.interval(seconds=30)                       # every 30s
async def poll_remote() -> None:
    ...


@tq.interval(timedelta(minutes=15))
async def warm_cache() -> None:
    ...
```

In dev / single-process, run the scheduler inside the app:

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    await tq.connect()
    await tq.start_scheduler()     # dev / single-process
    try:
        yield
    finally:
        await tq.stop_scheduler()
        await tq.disconnect()
```

!!! warning "The scheduler only enqueues — it doesn't execute"
    `cron`/`interval` **enqueue** the task into the same broker; a **worker** must be running to consume it. With no worker, triggers pile up in the queue.

!!! danger "Production: exactly one scheduler"
    `start_scheduler()` runs inside the FastAPI process — fine for dev. With multiple workers, each replica would run its own scheduler and **duplicate** every trigger. In production run one standalone scheduler and the workers separately.

## Workers in production

The worker and the scheduler are separate processes pointing at the raw objects `TaskQueue` exposes:

```bash
# consumes and executes the tasks
taskiq worker    src.tasks:tq.broker

# a single scheduler process for the whole cluster
taskiq scheduler src.tasks:tq.scheduler
```

`tq.broker` is the TaskIQ broker (it knows every registered task); `tq.scheduler` is the internal `TaskiqScheduler`.

## Transactional outbox

When a handler **writes a row AND publishes an event**, doing them separately is unsafe: a crash between the commit and the publish loses the event; between the publish and the commit creates a phantom event. The outbox pattern writes the business row **and** an outbox row in the **same transaction** — either both commit or neither. A relay then reads the outbox and publishes to the broker later.

!!! check "The SDK already ships the primitive"
    Unlike what the old version of this page said, the outbox **is** an SDK primitive: `BaseOutboxModel` (the table), `OutboxRelay` (the worker that drains and publishes, with exponential backoff and `FOR UPDATE SKIP LOCKED` on Postgres) and `BaseRepository.save_with_outbox` (the write side). The relay takes any async `publish` — it plugs straight into `MessageBroker`:

```python
# src/tasks/__init__.py — outbox relay
from tempest_fastapi_sdk import OutboxRelay

from src.db.models import OutboxModel
from src.queue import mq          # MessageBroker
from src.core.resources import db  # AsyncDatabaseManager


relay = OutboxRelay(
    db,
    model=OutboxModel,
    # channel first, payload second — the same publish signature:
    publish=lambda event: mq.publish(event.topic, event.payload),
)

# In the lifespan (or as a dedicated process): drains until cancelled.
# asyncio.create_task(relay.run(poll_interval=1.0))
```

The full guide — model, producer service with `save_with_outbox`, retention and concurrency — lives in the dedicated **[Outbox](outbox.md)** recipe.

## Recap / next steps

- **`MessageBroker`** — typed, transport-agnostic pub/sub over FastStream: `@mq.on("channel")` + `await mq.publish("channel", model)`. At-least-once fan-out across services.
- **`TaskQueue`** — tasks over TaskIQ: `@tq.task` → `await task.enqueue(...)` (to the worker) or `await task.run(...)` (inline). `.memory()` for tests.
- **`@tq.cron` / `@tq.interval`** — periodic on the same object; `start_scheduler()` in dev, standalone CLI in production.
- **Outbox** — `BaseOutboxModel` + `OutboxRelay` + `save_with_outbox`, with the relay's `publish` pointing at `MessageBroker`. See [Outbox](outbox.md).
- The legacy `AsyncBrokerManager` / `AsyncTaskBrokerManager` / `AsyncTaskScheduler` classes still work, but the facades above are the recommended path.
