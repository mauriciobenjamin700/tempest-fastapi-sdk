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
from src.services.orders import mark_order_paid


# Pick the transport with a constructor — no faststream import.
mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)


class OrderPaid(BaseModel):
    order_id: str
    user_id: str


class OrderCancelled(BaseModel):
    order_id: str
    reason: str


@mq.on("orders.paid")
async def handle_order_paid(event: OrderPaid) -> None:
    """Receives every event published to the 'orders.paid' channel."""
    await mark_order_paid(event.order_id, event.user_id)
```

Note the `event: OrderPaid`: **the type hint drives decoding**. FastStream validates the inbound payload into that Pydantic model **before** your handler runs — a malformed message never reaches your code.

Wire the lifecycle into the FastAPI lifespan and publish from anywhere:

```python
# src/api/app.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.queue import mq, OrderPaid


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await mq.connect()
    try:
        yield
    finally:
        await mq.disconnect()


app = FastAPI(lifespan=lifespan)


@app.post("/orders/{order_id}/pay")
async def pay_order(order_id: str) -> dict[str, str]:
    """Publish from any service/handler — channel first, message second."""
    await mq.publish("orders.paid", OrderPaid(order_id=order_id, user_id="u1"))
    return {"status": "published"}
```

!!! info "Transports"
    `MessageBroker.rabbitmq(url)`, `.redis(url)`, `.kafka(*servers)`, `.nats(servers)`. Each lazily imports the right FastStream backend and raises with the exact install command if the extra is missing. Need a custom (or test) broker? `MessageBroker(my_broker)`.

!!! check "Recap"
    - `MessageBroker.rabbitmq(url)` — pick the transport, hide FastStream.
    - `@mq.on("channel")` — declare a consumer; the parameter type validates the message.
    - `await mq.publish("channel", model)` — publish; channel first.
    - `mq.publish(...)` only works after `connect()` (raises `RuntimeError` before).

Wire it into the health router: `make_health_router(checks={"queue": mq.health_check})`.

### Class-based consumers

Prefer grouping handlers in a class (shared setup, inheritance) over free
functions? `Consumer` offers **two** styles, both explicit (nothing is
guessed from the class name). Register with `mq.register(...)`.

**Constructor form** — pass the channel and the Pydantic schema to the
constructor; override `handle`:

```python
from tempest_fastapi_sdk.queue import Consumer

from src.queue import OrderPaid, mq
from src.services.orders import mark_order_paid


class OrderPaidConsumer(Consumer):
    async def handle(self, event: OrderPaid) -> None:
        await mark_order_paid(event.order_id)


mq.register(OrderPaidConsumer(channel="orders.paid", schema=OrderPaid))
```

**Grouped form** — one class, many channels, each method marked with
`@subscribe`; the schema is the method's own annotation:

```python
from tempest_fastapi_sdk.queue import Consumer, subscribe

from src.queue import OrderCancelled, OrderPaid, mq


# OrderPaid / OrderCancelled defined in the `src/queue/__init__.py` block above.


class OrdersConsumer(Consumer):
    @subscribe("orders.paid")
    async def on_paid(self, event: OrderPaid) -> None: ...

    @subscribe("orders.cancelled")
    async def on_cancelled(self, event: OrderCancelled) -> None: ...


mq.register(OrdersConsumer())
```

!!! info "Explicit, no magic"
    In the constructor form the schema is passed explicitly in `__init__`
    and is what validates the payload — no annotation-sniffing. In the
    grouped form the schema is the method's visible annotation. The
    `@mq.on(...)` function decorator is still available — pick the style
    you like.

## Queue topology — `QueueSpec`

A channel as a string covers most cases. What it does **not** express is exactly what decides whether the queue survives a restart, where a rejected message goes, and how long it lives. On RabbitMQ that lives in the queue declaration, not in the name.

`QueueSpec` carries that topology as typed data, and is accepted anywhere the string was:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import DeadLetterSpec, MessageBroker, QueueSpec, QueueType

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")


class OrderPaid(BaseModel):
    order_id: str
    amount_cents: int

ORDERS_PAID = QueueSpec(
    name="orders.paid",
    dead_letter=DeadLetterSpec(exchange="dlx"),
    message_ttl_ms=60_000,
    queue_type=QueueType.QUORUM,
)


@mq.on(ORDERS_PAID)
async def handle(event: OrderPaid) -> None:
    """Consume from a durable, quorum queue with dead-lettering."""
```

It translates to the arguments AMQP expects:

```text
{"x-queue-type": "quorum", "x-dead-letter-exchange": "dlx", "x-message-ttl": 60000}
```

!!! danger "Without `dead_letter`, a failure is a silent discard"
    The consumer policy is `REJECT_ON_ERROR`: a handler that raises issues `basic.reject` with `requeue=False`. That avoids a poison-message loop — but **without `x-dead-letter-exchange` RabbitMQ throws the message away**. No error, no dead queue, no metric. That is why `DeadLetterSpec` exists.

### The exchange has to exist

RabbitMQ happily declares a queue pointing at an `x-dead-letter-exchange` that does not exist — and then discards at routing time, silently. So `connect()` declares the exchanges named by the registered `QueueSpec`, as durable `topic` exchanges:

```python
from tempest_fastapi_sdk.queue import MessageBroker


async def startup(mq: MessageBroker) -> None:
    """Start the broker; the registered specs' DLX are declared here."""
    await mq.connect()
```

Where the broker is managed and the application has no permission to declare, turn it off and handle topology outside:

```python
from tempest_fastapi_sdk.queue import MessageBroker

mq = MessageBroker.rabbitmq(
    "amqp://guest:guest@localhost:5672/",
    declare_topology=False,
)
```

### A field the transport cannot express **raises**

`MessageBroker` is multi-transport, and `dead_letter` / TTL / priority are AMQP. Asking for them on a broker without the concept is not ignored:

```text
UnsupportedTopologyError: QueueSpec('orders.paid') sets dead_letter, which the
kafka transport cannot express. Remove the field, or use a bare channel name
and configure the topology outside the SDK.
```

Ignoring it silently would produce a queue that **looks** configured and discards every failure — exactly the defect `QueueSpec` exists to prevent. Same choice as `op.replace_enum`, which raises on an unsupported dialect instead of emitting DDL that does nothing.

A bare `QueueSpec(name=...)` stays portable on any transport: it asks for nothing beyond the name.


## Event-path reliability

The consumer policy is `REJECT_ON_ERROR`. A handler that raises issues `basic.reject` with `requeue=False` — no loop, and **gone**. Three pieces close that, mirroring what `TaskQueue` already has.

### Dead-letter: a failure becomes a record

```python
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.tasks import DbDeadLetterSink


def wire_dead_letter(mq: MessageBroker, sink: DbDeadLetterSink) -> None:
    """Send every terminal consumer failure to the sink.

    Args:
        mq (MessageBroker): The broker, before connect().
        sink (DbDeadLetterSink): Where the dead event is stored.
    """
    mq.dead_letter(sink, max_attempts=3)
```

The sink is the **same** protocol the task path uses, so `DbDeadLetterSink`, the admin panel and `make_requeue_action` work unchanged — a dead task and a dead event on one screen.

The mapping is deliberate: `task_name` carries the **channel**, `task_id` the broker's message id, and `kwargs["body"]` the raw body.

!!! tip "Reported once, not per attempt"
    The sink fires only on the delivery that exhausts `max_attempts`, read from the `x-death` header. Alerting on every attempt turns one bad message into a stream of alerts.

### Delayed retry, performed by the broker

AMQP has no per-message delay. The portable way is a pair of queues: the main one sends the rejected message to a queue whose only job is to hold it, and that queue's TTL returns it to the main exchange when it expires.

```python
from tempest_fastapi_sdk.queue import ConsumerRetryPolicy, retry_queues

from tempest_fastapi_sdk.queue import (
    ConsumerRetryPolicy,
    MessageBroker,
    retry_queues,
)

TOPOLOGY = retry_queues(
    "orders.paid",
    ConsumerRetryPolicy(max_attempts=3, delay_ms=30_000),
    retry_exchange="orders.retry",
    main_exchange="orders",
    dead_exchange="orders.dead",
)


async def wire_retry(mq: MessageBroker) -> None:
    """Declare and bind the three queues of the retry chain.

    Args:
        mq (MessageBroker): The broker, already connected.
    """
    await mq.declare_retry_topology(TOPOLOGY)
```

!!! danger "Declaring without binding discards the message"
    Declaring the queues is not enough: each has to be **bound** to its exchange. Without the bindings, a rejected message is routed into an exchange with nothing behind it — and RabbitMQ drops it silently. Measured against a real broker: with the bindings the message comes back on schedule (1.5s gaps for a 1.5s TTL); without them it is delivered once and vanishes. `declare_retry_topology()` does both.

The **broker** does the waiting, so a worker restart in the meantime changes nothing. The alternative is the `rabbitmq_delayed_message_exchange` plugin, simpler to declare and **requiring the plugin** — unavailable on several managed offerings, including the free CloudAMQP tier.

!!! warning "The topology alone retries forever"
    AMQP counts redeliveries in `x-death` but will not stop on its own. What enforces `max_attempts` is the `dead_letter()` middleware. Declaring the topology without installing the middleware yields infinite retries — which is why they are documented together.

### Metrics

```python
from tempest_fastapi_sdk.queue import MessageBroker, QueueMetrics


def wire_metrics(mq: MessageBroker) -> None:
    """Publish consume counts and durations on the shared /metrics.

    Args:
        mq (MessageBroker): The broker, before connect().
    """
    mq.enable_metrics(QueueMetrics())
```

Produces `queue_messages_total{channel,status}` and `queue_message_duration_seconds{channel}`. Without it the consumer failure rate is invisible — the message is rejected, the broker discards it, and nothing counts.


## Background tasks — `TaskQueue`

A **task queue** takes slow work out of the request and hands it to a worker. TaskIQ does this but spreads the API across a broker, a scheduler, a schedule source and `.kiq()`. `TaskQueue` folds it all into one object with an obvious vocabulary.

Install with `[tasks]` (pulls `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

email = "ana@example.com"


tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@tq.task
async def send_welcome(to: str, name: str) -> None:
    """Runs on a worker, off the request path."""
    await email.send(to, "Welcome!", f"Hi, {name}.")
```

!!! note "`email` is your mailer"
    `email` here is your e-mail sender — an `EmailUtils` instance (the
    `[email]` extra) wired up at module level. See the
    [email recipe](email.md); swap it for your own send dependency.

`@tq.task` returns a typed `Task` object with **two** clear actions:

```python
import asyncio

from src.db.models import UserModel
from src.tasks import send_welcome

user = UserModel(name="Ana", email=email)
email = "ana@example.com"


async def main() -> None:
    """Run this example."""
    # Enqueue to the worker and return immediately (the HTTP response doesn't wait):
    await send_welcome.enqueue(to=user.email, name=user.name)

    # Run inline, right here, returning the real value (handy in tests / reuse):
    await send_welcome.run(to="a@b.com", name="Ana")


asyncio.run(main())
```

!!! tip "`enqueue` instead of `.kiq`"
    `enqueue()` makes it obvious what happens: the call goes to the worker. `run()` executes the body locally, no broker. The cryptic `.kiq` name stays hidden (still reachable at `send_welcome.taskiq_task` if you need it).

Lifespan mirrors the message broker:

```python
# src/api/app.py

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.tasks import tq


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await tq.connect()
    try:
        yield
    finally:
        await tq.disconnect()
```

!!! note "Tests without a broker"
    `TaskQueue.memory()` uses TaskIQ's in-memory broker: `enqueue()` runs the task **immediately, in-process**. No worker, no connection. `run()` always works, even without `connect()`.

### Class-based tasks

Symmetric to consumers: group tasks in a class with `TaskDef`.
`tq.register(...)` returns a `Task` (constructor form) or a dict of
`Task` keyed by method (grouped form).

```python
import asyncio

from tempest_fastapi_sdk.tasks import TaskDef, task_method

from src.tasks import tq


# Constructor form — one task; name in the constructor, override run:
class NightlyReport(TaskDef):
    def __init__(self) -> None:
        super().__init__(name="reports:nightly")

    async def run(self, day: str) -> None:
        ...


nightly = tq.register(NightlyReport())        # -> Task


async def main() -> None:
    """Run this example."""
    await nightly.enqueue(day="2026-07-05")


    # Grouped form — many tasks, each method marked with @task_method:
    class ReportTasks(TaskDef):
        @task_method(name="reports:nightly")
        async def nightly(self, day: str) -> None: ...

        @task_method()
        async def weekly(self) -> None: ...


    tasks = tq.register(ReportTasks())            # -> {"nightly": Task, "weekly": Task}
    await tasks["nightly"].enqueue(day="2026-07-05")


asyncio.run(main())
```

The `@tq.task` function decorator is still available — both styles coexist.

## Periodic tasks — `cron` / `interval`

Scheduling is part of the same `TaskQueue` — no separate scheduler in your code.

!!! tip "Don't know cron? Use the enums and helpers (v0.94.0)"
    Nobody should hand-write `"0 9 * * MON-FRI"`. The
    `tempest_fastapi_sdk.tasks` module ships **`Cron`** (ready-made
    expressions), **`CronOffset`** (timezones by place, not digits),
    **`Weekday`** and **builder functions** (`daily`, `weekdays`,
    `hourly`, `every_n_minutes`, `weekly`, `weekends`, `monthly`). Each
    returns a plain cron string that drops straight into `@tq.cron(...)`.

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import Cron, CronOffset, Weekday, daily, weekdays

from src.tasks import tq


# Readable, no cron syntax:
@tq.cron(Cron.EVERY_WEEKDAY_9AM, cron_offset=CronOffset.BRASILIA)
async def daily_digest() -> None:
    ...


@tq.cron(daily(hour=9), cron_offset=CronOffset.BRASILIA)   # 09:00 BRT
async def other_digest() -> None:
    ...


@tq.cron(weekdays(hour=8, minute=30), cron_offset=CronOffset.BRASILIA)
async def morning_sync() -> None:
    ...


@tq.cron(Cron.EVERY_5_MINUTES)
async def heartbeat() -> None:
    ...
```

| To run… | Write |
| --- | --- |
| Every 5 min | `Cron.EVERY_5_MINUTES` or `every_n_minutes(5)` |
| Daily at 9am | `daily(hour=9)` |
| Weekdays at 8:30 | `weekdays(hour=8, minute=30)` |
| Every Monday | `weekly(Weekday.MON)` |
| First of the month | `monthly(day=1)` |
| In Brasília time | `cron_offset=CronOffset.BRASILIA` |

`CronOffset` covers Brazil's timezones by name — `BRASILIA` (-03:00),
`FERNANDO_DE_NORONHA` (-02:00), `MANAUS` (-04:00), `ACRE` (-05:00) — plus
`UTC`. Prefer raw cron or intervals? Still supported:

```python
from datetime import timedelta

from src.tasks import tq


@tq.cron("*/5 * * * *")                        # raw cron string
async def raw_cron() -> None:
    ...


@tq.interval(seconds=30)                        # every 30s
async def poll_remote() -> None:
    ...


@tq.interval(timedelta(minutes=15))
async def warm_cache() -> None:
    ...
```

In dev / single-process, run the scheduler inside the app:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.tasks import tq


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
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

## Task reliability and observability

A worker-only task needs three things TaskIQ has but scatters: **retry**, **dead-letter**, and **metrics**. `TaskQueue` exposes all three as opt-in middleware — call them **before `connect()`**, and nothing touches the broker's middleware API.

### Typed retry

`RetryPolicy` carries the retry config as labels; `enable_retries()` installs the TaskIQ middleware that reads them:

```python
from tempest_fastapi_sdk.tasks import RetryPolicy, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_retries(default_max_retries=3)


@tq.task(name="reports:nightly", retry=RetryPolicy(max_retries=5))
async def nightly() -> None:
    ...   # re-run up to 5x on error
```

### Dead-letter — where terminal failures go

When a task fails **with no retry configured**, or after its retries are exhausted, the call goes to your `DeadLetterSink` exactly once. The target is yours — a `MessageBroker` channel, a DB row, an alert. The SDK assumes no backend:

```python
from tempest_fastapi_sdk.tasks import DeadLetter, TaskQueue

from src.queue import mq


tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")


async def to_dlq(dead_letter: DeadLetter) -> None:
    await mq.publish("tasks.dead", {
        "task": dead_letter.task_name,
        "args": dead_letter.args,
        "error": str(dead_letter.exception),
        "retries": dead_letter.retries,
    })


tq.dead_letter(to_dlq, default_max_retries=3)
```

!!! tip "Pair it with retry"
    Pass the **same** `default_max_retries` to `enable_retries` and `dead_letter` so the "retries exhausted" point lines up for tasks that set no explicit `max_retries`. Install order does not matter — the dead-letter middleware decides on its own by reading the message labels.

### Per-task Prometheus metrics

`TaskMetrics` counts executions (by status) and a duration histogram, labelled by task, into the **same** `/metrics` the SDK already serves (pass the shared `registry`):

```python
from tempest_fastapi_sdk.tasks import TaskMetrics, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_metrics(TaskMetrics())   # tasks_runs_total{task,status} + tasks_duration_seconds{task}
```

### Dead-letter panel in the admin

The `DeadLetterSink` says *what* to do with a failure; to **see and re-run** failures, persist them to a table and surface them in the admin. `DbDeadLetterSink` writes each terminal failure; `make_dead_letter_admin_model` builds a read-mostly `AdminModel` (filter by task, search the error, export) with an optional **requeue** bulk action.

```python
from tempest_fastapi_sdk.admin import AdminSite
from tempest_fastapi_sdk.tasks import (
    DbDeadLetterSink,
    TaskQueue,
    make_dead_letter_admin_model,
    make_dead_letter_model,
)

from src.core.resources import db   # AsyncDatabaseManager


DeadLetterModel = make_dead_letter_model()   # or subclass BaseDeadLetterModel by hand

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.dead_letter(DbDeadLetterSink(db, DeadLetterModel))   # persist terminal failures

site: AdminSite = AdminSite(title="Ops")
site.register(make_dead_letter_admin_model(DeadLetterModel, tq=tq))   # panel + requeue
```

Passing `tq=` wires the **requeue** action: the operator selects rows, each call is re-enqueued with its stored `args` / `kwargs`, and the requeued rows are deleted.

!!! info "No Flower clone"
    TaskIQ exposes no live queue state (Flower is Celery-specific), so this panel does **not** try to show pending/in-flight jobs. It shows what is real and persisted: the terminal failures.

For a "what tasks exist" inventory, `task_inventory(tq)` returns `list[TaskInfo]` (name / schedule / retry) read straight off the broker — serve it as JSON, a log, or your own page:

```python
from tempest_fastapi_sdk.tasks import task_inventory

from src.tasks import tq


for info in task_inventory(tq):
    print(info.name, info.schedule, info.retry_on_error, info.max_retries)
```

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
- **Cron without syntax** — `Cron` / `CronOffset` / `Weekday` + helpers (`daily`, `weekdays`, `every_n_minutes`, …) to schedule by name; `CronOffset.BRASILIA` instead of `"-03:00"`.
- **Styles** — decorators (`@mq.on`, `@tq.task`, `@tq.cron`) **or** classes (`Consumer` + `mq.register`, `TaskDef` + `tq.register`); both coexist.
- **Outbox** — `BaseOutboxModel` + `OutboxRelay` + `save_with_outbox`, with the relay's `publish` pointing at `MessageBroker`. See [Outbox](outbox.md).
- **Rename (v0.94.0)** — `AsyncBrokerManager` → **`AsyncQueueManager`** (thin wrapper; old alias kept). The `MessageBroker` / `TaskQueue` facades stay recommended; `AsyncTaskBrokerManager` / `AsyncTaskScheduler` remain functional legacy.
