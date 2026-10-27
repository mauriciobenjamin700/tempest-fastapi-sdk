# Queues and Tasks

Background work without the pain. The SDK wraps **FastStream** (messaging) and **TaskIQ** (tasks + scheduling) in typed classes with a single vocabulary — the common path (publish, consume, enqueue, schedule) imports neither `faststream` nor `taskiq` in application code. The exception is a transport-specific option, such as the `RabbitExchange` passed as `exchange=` further down.

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

Wire it into the health router: `make_health_router(checks={"queue": mq.health_check})`. `health_check()` runs FastStream's real `ping` (`timeout=2.0` by default) and returns `False` before `connect()` or once the connection drops — see [Liveness and readiness](http.md#liveness-and-readiness).

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

The channel may be a string or a [`QueueSpec`](#queue-topology-queuespec), exactly
as in `@mq.on(...)` — declaring a dead-letter exchange or a quorum queue does not
push you back to the decorator. The same holds for the rest of what
`@mq.on(...)` takes: [`prefetch=`](#prefetch-how-many-messages-stay-in-flight)
and the transport's own options (`exchange=`, say) exist in both forms.

```python
from faststream.rabbit import RabbitExchange

from tempest_fastapi_sdk.queue import Consumer, subscribe

from src.queue import OrderCancelled, OrderPaid, mq


class OrdersConsumer(Consumer):
    """One cap for the whole class; one heavy method with its own."""

    prefetch = 32

    @subscribe("reports.generate", prefetch=1)
    async def generate(self, event: OrderPaid) -> None: ...

    @subscribe("orders.cancelled", exchange=RabbitExchange("events", durable=True))
    async def on_cancelled(self, event: OrderCancelled) -> None: ...


mq.register(OrdersConsumer())
```

### Class-based publishers

`Consumer` covered consumption; publishing stayed loose — `await mq.publish("orders.paid", event)`, with the channel as an arbitrary string and the payload typed `Any`. Nothing connected the two ends of what is, in practice, **one** contract.

`Publisher` is that half. It carries the channel and the payload model as class attributes, and `publish` takes exactly the declared type:

```python
from tempest_fastapi_sdk.queue import Publisher

from src.queue import OrderPaid, mq


class OrderPaidPublisher(Publisher[OrderPaid]):
    channel = "orders.paid"
    schema = OrderPaid


orders = mq.publisher_for(OrderPaidPublisher)


async def confirm_order(order_id: str) -> None:
    """Announce that the order was paid.

    Args:
        order_id (str): The confirmed order.
    """
    await orders.publish(OrderPaid(order_id=order_id))
```

Three things the loose call could not give you:

- **The type checker sees the payload.** `Publisher[OrderPaid]` makes `publish` take an `OrderPaid`, so publishing the wrong model is a red squiggle in your editor rather than a message the consumer rejects in production.
- **The schema is enforced on the way out.** If the object is not an instance of the declared model, `publish` raises `TypeError` — the consumer is a process away and can only reject what already left.
- **The topology is registered.** A `QueueSpec` on `channel` goes through the same binding `@mq.on(...)` uses, so a service that **only publishes** still declares the dead-letter exchange it names.

**Channel and schema need not be class attributes.** Both also go through
`__init__` — useful when the channel is only known at runtime (per tenant,
per environment) and you do not want a subclass per value:

```python
from tempest_fastapi_sdk.queue import Publisher

from src.queue import OrderPaid, mq

orders: Publisher[OrderPaid] = Publisher(mq, channel="orders.paid", schema=OrderPaid)
```

`publisher_for` takes the same two, and they **win** over what the class
declares:

```python
from tempest_fastapi_sdk.queue import Publisher

from src.queue import OrderPaid, mq


class TenantPublisher(Publisher[OrderPaid]):
    schema = OrderPaid


def publisher_for_tenant(tenant: str) -> Publisher[OrderPaid]:
    """Return the publisher for that tenant's channel.

    Args:
        tenant (str): The tenant identifier.

    Returns:
        The publisher bound to `orders.paid.<tenant>`.
    """
    return mq.publisher_for(TenantPublisher, channel=f"orders.paid.{tenant}")
```

`channel` and `schema` are named parameters, not `**options` — so the type
checker sees them and a publish option sharing one of those names is not
swallowed.

!!! warning "Not the same as `mq.publisher(channel)`"
    `mq.publisher(...)` returns FastStream's own publisher object — an escape hatch, useful mainly because it makes the channel show up in the generated AsyncAPI. `Publisher` goes through `mq.publish()`, so it keeps the `message_id` deduplication depends on and the `traceparent` / `x-request-id` headers tracing depends on. A publisher that bypassed those would look identical and silently break both.

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

!!! check "No `AckPolicy` in your code"
    A consumer of a `QueueSpec` with a dead letter already gets
    `ack_policy=REJECT_ON_ERROR` pinned by `on()` (and by the `Consumer`'s
    `register()`): do not import `faststream.AckPolicy` for it. The value is
    set on the subscriber itself, so not even a broker-wide `ack_policy` steers
    the failure away from the DLQ — measured in `tests/queue/test_fanout_live.py`
    with the broker on `NACK_ON_ERROR`. Passing `ACK`, `ACK_FIRST` or
    `NACK_ON_ERROR` on a spec with a dead letter raises `ValueError`: none of
    them sends the failure to the DLQ (the first two ack the message, the last
    one requeues it in a loop). `MANUAL` is still accepted.

### The exchange has to exist

RabbitMQ happily declares a queue pointing at an `x-dead-letter-exchange` that does not exist — and then discards at routing time, silently. An exchange that exists **with no queue bound** discards just the same: routing finds no destination. So `connect()` declares the exchanges named by the registered `QueueSpec` (as durable `topic` exchanges) and, when the `DeadLetterSpec` names a `queue`, that queue and its binding — all **before** the consumers start:

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

!!! info "Why before the consumers"
    Up to 0.322.0 the declaration came after `start()`. Measured against RabbitMQ 4: a message already waiting in the queue and rejected on its first delivery went to an exchange that did not exist yet and was lost (3 out of 3 runs). `tests/queue/test_fanout_live.py` pins the new order.

### Fan-out: one copy per consumer

Two modules reacting to the same event cannot subscribe to the same queue: they would split the messages instead of each getting its own. The AMQP shape is **one queue per consumer**, all bound to the same exchange by the event key. `QueueSpec.consumer` builds it:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import ExchangeSpec, MessageBroker, QueueSpec

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")

EVENTS = ExchangeSpec("homework.events")
DEAD_LETTERS = "homework.dlx"


class TaskCompleted(BaseModel):
    task_id: str
    user_id: str


GAMIFICATION = QueueSpec.consumer(
    "gamification",
    "task.completed",
    exchange=EVENTS,
    dead_letter_exchange=DEAD_LETTERS,
)
RULES = QueueSpec.consumer(
    "rules",
    "task.completed",
    exchange=EVENTS,
    dead_letter_exchange=DEAD_LETTERS,
)


@mq.on(GAMIFICATION)
async def award_points(event: TaskCompleted) -> None:
    """Award the points for the completed task."""


@mq.on(RULES)
async def evaluate_rules(event: TaskCompleted) -> None:
    """Run the rules engine over the same event."""


async def emit(event: TaskCompleted) -> None:
    """Publish once; every queue bound to `task.completed` gets its own copy."""
    await mq.publish("task.completed", event, exchange=EVENTS, message_id=event.task_id)
```

What lands on the broker:

```text
homework.events --task.completed--> gamification.task.completed --rejection--> homework.dlx --gamification.task.completed--> gamification.task.completed.dlq
homework.events --task.completed--> rules.task.completed        --rejection--> homework.dlx --rules.task.completed-->        rules.task.completed.dlq
```

- **The queue is named `<consumer>.<channel>`** and bound to the exchange under `task.completed`. `@mq.on` hands the exchange to FastStream, which declares it and binds the queue when the consumer starts.
- **Dead letters are keyed by the queue name**, not by the event key — the event key is the same for every consumer, and a DLQ bound by it would collect everyone's failures. The gamification failure lands in `gamification.task.completed.dlq` only.
- **`publish(..., exchange=...)`** uses the channel as the routing key (passing a consumer's own `QueueSpec` publishes by the event key, not by the queue name). An `ExchangeSpec` is declared on the connection's first publish and not again until `disconnect()` — the outbox relay can run before any consumer has declared the exchange. A bare name (`exchange="amq.topic"`) is passed through **undeclared**: the exchange exists already and its type is not ours to assert.

!!! tip "No `dead_letter_exchange`, no dead letter"
    `QueueSpec.consumer("rules", "task.completed", exchange=EVENTS)` builds the queue and the binding only. As with any `QueueSpec` without `dead_letter`, the rejection is discarded.

### Replaying the dead-letter queue

Once the handler is fixed, the messages that died come back with `replay_dead_letters`:

```python
from tempest_fastapi_sdk.queue import ExchangeSpec, MessageBroker, QueueSpec

GAMIFICATION = QueueSpec.consumer(
    "gamification",
    "task.completed",
    exchange=ExchangeSpec("homework.events"),
    dead_letter_exchange="homework.dlx",
)


async def replay(mq: MessageBroker) -> int:
    """Return up to 100 dead messages to the gamification queue, and only there."""
    return await mq.replay_dead_letters(GAMIFICATION, limit=100)
```

- **Back to the owner only.** The message is republished straight to the origin queue through the default exchange, never to the shared exchange — which would hand the rules engine a second copy.
- **The `ack` comes after the publish.** If the publish fails, the message goes back to the DLQ (`nack(requeue=True)`) and the call raises. The origin queue is checked before the first read: measured, publishing through the default exchange to a queue that does not exist **raises nothing** — the broker returns the message as unroutable — and acking after that would lose it.
- **What travels:** body, `message_id` (the `deduplicate` key), `content_type`, `content_encoding`, `correlation_id`, `priority`, `timestamp`, `type`, `reply_to` and the headers, with `persist=True`. `expiration`, `app_id` and `user_id` stay behind: the replay is a new delivery. The `x-death` / `x-first-death-*` / `x-last-death-*` headers the broker writes when dead-lettering are **left behind**: the retry middleware counts attempts from them, and the replayed message gets a fresh budget.
- **At least once, not exactly once.** If the publish succeeds and the `ack` after it fails (the connection drops between them), the message sits on the origin queue **and** on the DLQ, and the next replay delivers it again. A consumer with `deduplicate` skips the second copy by its `message_id`.
- **`limit` caps one run.** A message that fails again goes to the back of the DLQ; without a cap, a consumer that is still broken would turn the replay into a loop.

The same call takes the `RetryTopology` from `retry_queues` (reads `.dead`, returns to the main queue) or the bare DLQ name with an explicit destination:

```python
from tempest_fastapi_sdk.queue import MessageBroker, retry_queues

ORDERS = retry_queues(
    "orders.paid",
    retry_exchange="orders.retry",
    main_exchange="orders",
    dead_exchange="orders.dead",
)


async def replay_orders(mq: MessageBroker) -> int:
    """Drain the retry chain's `.dead` back into `orders.paid`."""
    return await mq.replay_dead_letters(ORDERS)


async def replay_by_name(mq: MessageBroker) -> int:
    """Topology built outside the SDK: say where from and where to."""
    return await mq.replay_dead_letters("legacy.dlq", into="legacy")
```

!!! warning "A queue that already exists with other arguments"
    Declaring is idempotent only with the **same** arguments. A DLQ created by hand beforehand (another `x-queue-type`, an `x-message-ttl`) makes `connect()` fail with `PRECONDITION_FAILED - inequivalent arg ...` before any consumer starts. Align the `QueueSpec` with what exists, delete the queue, or use `declare_topology=False` and manage the topology outside.

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

??? info "How `x-death` is read (and why the `expired` entry does not count)"
    RabbitMQ keeps **one entry per (queue, reason) pair**, and the retry chain dead-letters twice per round: `rejected` out of the main queue, and `expired` out of the waiting queue when its TTL fires. Summing every entry advances the counter by two per retry — with `max_attempts=3` the message was dropped after **two** runs. `delivery_attempt()` counts only failed deliveries, skipping `expired`.

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

`status` is `ok`, `error` or `duplicate`. The last is a delivery `deduplicate()` rejected because a sibling worker held the claim: its own label, precisely so that deduplication doing its job does not inflate the error rate an alert is built on.


### Prefetch — how many messages stay in flight

Uncapped, the broker delivers as fast as the consumer acks. Three consequences, all in production: a slow handler accumulates messages in process memory; the first replica to connect takes the batch and its siblings sit idle; and the unacked backlog is held in RAM until the pod is OOM-killed and the whole lot is redelivered.

```python
from tempest_fastapi_sdk.queue import MessageBroker

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/", prefetch=32)


@mq.on("reports.generate", prefetch=1)
async def generate(request: dict[str, str]) -> None:
    """Heavy handler: a low cap, without throttling its neighbours."""
```

The broker value applies to the connection; the consumer value overrides it for that consumer alone.

On the class path the knob is the same one, at three heights —
constructor, class and method:

```python
from tempest_fastapi_sdk.queue import Consumer, subscribe

from src.queue import OrderPaid, mq


class ReportsConsumer(Consumer):
    prefetch = 32                       # every binding this class declares

    @subscribe("reports.generate", prefetch=1)
    async def generate(self, event: OrderPaid) -> None:
        """Heavy handler: the method's cap beats the class's."""


class OrderPaidConsumer(Consumer):
    async def handle(self, message: OrderPaid) -> None: ...


mq.register(ReportsConsumer())
mq.register(OrderPaidConsumer(channel="orders.paid", schema=OrderPaid, prefetch=8))
```

!!! note "Why `prefetch` is a named parameter rather than one more `**options`"
    FastStream has no `prefetch` keyword — it carries `basic.qos` on a
    `Channel` object. Forwarding the raw word raises
    `TypeError: RabbitRegistrator.subscriber() got an unexpected keyword
    argument 'prefetch'`, which is exactly what the class path did until
    v0.209.0. Naming the parameter is what allows the translation — and
    what the type checker sees.

!!! warning "There is no good default I could guess"
    Without `prefetch=`, consumption is **uncapped** (the default is `None`) — the SDK exposes the knob, it does not pick the value. Too small serializes consumption and destroys throughput; too large recreates the problem. The right number depends on your handler's latency, and fixing one without measuring would repeat the mistake `DEFAULT_INTRA_OP_THREADS` made in modelops before it was re-justified. Measure with a known-latency consumer before choosing.

!!! info "Prefetch is not handler concurrency"
    Prefetch caps how many messages the **broker delivers** unacked. How many coroutines run at once is a separate decision. Confusing the two is common: `prefetch=1` does not serialize the handler if the handler itself fans out.

!!! check "Publisher confirms are already on"
    FastStream's `Channel` defaults to `publisher_confirms=True`, so a publish lost to a broker restart is not silent. Pinned by a test.

### Consume-side idempotency

Delivery is at-least-once: worker restart, nack with requeue, an ack lost to the network. Redelivery is not a rare case, it is the normal mode.

```python
from tempest_fastapi_sdk.queue import MessageBroker, RedisDedupStore


def wire_dedup(mq: MessageBroker, redis: object) -> None:
    """Run each message id at most once.

    Args:
        mq (MessageBroker): The broker, before connect().
        redis (object): An async Redis client.
    """
    mq.deduplicate(RedisDedupStore(redis), ttl_seconds=86_400)
```

`publish()` now generates a `message_id` when you do not pass one — without a stable id there is no key to deduplicate on, and a redelivery is indistinguishable from a new event.

**Two-phase** marking: the first delivery marks `in_flight` and runs; success marks `done` and the next one is skipped; **a failure releases the key**, so a retry actually retries. Without that third part, trading "processed twice" for "processed never" would be worse than the original problem.

!!! danger "This is not exactly-once — nothing here is"
    The mark and the handler's effect are not atomic. A crash between them leaves an `in_flight` key that expires, and the message runs again. This is at-least-once with a much smaller window, not exactly-once.

!!! tip "When the database already solves it, use the database"
    If the handler's effect is a row keyed by something the domain owns, `INSERT ... ON CONFLICT DO NOTHING` is idempotent with **no extra moving part** — no TTL to tune, no second store to operate. This middleware is for effects that are not rows: an email, a third-party call, a downstream publish.

!!! warning "A concurrent delivery is rejected, not acknowledged"
    If another worker holds the claim, this copy raises `ConcurrentDeliveryError` and the broker rejects it. Acknowledging would be dangerous: the in-flight worker may still fail, and the copy that could have retried would be gone.

### Tracing and request id across the queue

The request opens a trace, publishes an event and answers 201 — and the consumer that charges the card, writes the ledger and sends the mail showed up as **three orphan traces**, with no parent and no relation to each other.

```python
from tempest_fastapi_sdk.queue import MessageBroker


def wire_tracing(mq: MessageBroker) -> None:
    """Open a span per consumed message, linked to the publish.

    Args:
        mq (MessageBroker): The broker, before connect().
    """
    mq.enable_tracing()
```

`publish()` already injects the `traceparent` and the current **request id** into the headers; `enable_tracing()` is the other half.

!!! info "Link, not child"
    The consumer's span references the publish as a **link**, not as a parent. The semantic conventions recommend that for asynchronous consumption, and the reason is practical: the consumer may run minutes later, and a child span of that duration would stretch the request's trace and make its latency unreadable.

!!! tip "The request id is worth more than the span day to day"
    `RequestIDMiddleware` already puts the id on every HTTP log line. The worker now adopts the publisher's id while processing, so `grep` alone correlates request and consumption — without opening Jaeger.

Without the `[otel]` extra all of this is a no-op; request-id propagation works either way, because it does not depend on OpenTelemetry.

## Background tasks — `TaskQueue`

A **task queue** takes slow work out of the request and hands it to a worker. TaskIQ does this but spreads the API across a broker, a scheduler, a schedule source and `.kiq()`. `TaskQueue` folds it all into one object with an obvious vocabulary.

Install with `[tasks]` (pulls `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk import EmailUtils
from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

email = EmailUtils(**settings.email_kwargs())


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

email = "ana@example.com"
user = UserModel(name="Ana", email=email)


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

### Enqueue by name — `enqueue_by_name`

`send_welcome.enqueue(...)` requires importing the task module in the API
process — and with it everything the task imports (mailer, HTTP client, a
heavy model). When the API only needs to **send** the work, send it by name:
the API keeps a string, and the worker resolves it.

```python
# src/tasks/welcome.py — imported by the worker only
from tempest_fastapi_sdk.tasks import RetryPolicy, TaskQueue

from src.core.settings import settings

tq: TaskQueue = TaskQueue.from_settings(settings)

WELCOME_TASK_NAME: str = "users:send_welcome"
"""Stable task name; the API enqueues by it."""


@tq.task(name=WELCOME_TASK_NAME, retry=RetryPolicy(max_retries=3))
async def send_welcome(to: str, name: str) -> None:
    """Runs in a worker, outside the request."""
```

```python
# src/api/routers/users.py — the API never imports src.tasks.welcome
from fastapi import APIRouter

from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

tq: TaskQueue = TaskQueue.from_settings(settings)
router: APIRouter = APIRouter(prefix="/users")


@router.post("/{email}/welcome", status_code=202)
async def welcome(email: str) -> None:
    """Ask for the welcome e-mail and answer without waiting."""
    await tq.enqueue_by_name("users:send_welcome", to=email, name="Ana")
```

**The retry policy still applies.** TaskIQ's worker reads only the labels
the message carries, and a sender working by name does not know the task —
so the message arrives without `retry_on_error`, `max_retries`, `timeout`.
Every `TaskQueue` installs, first in its broker, a middleware that restores
on the worker the labels the task was registered with (the message's own
still win). Measured with a real Redis, a `taskiq` worker in one process and
the send from another that never imported the task: a task with
`RetryPolicy(max_retries=2)` that fails its first attempt runs **2** times;
with the middleware removed, **1**.

!!! warning "A name that does not exist"
    The sender does not check the name — the API process does not know the
    tasks. Over Redis or RabbitMQ the worker logs
    `task "<name>" is not found` and drops the message. With
    `TaskQueue.memory()`, which is its own worker, the send raises
    `SendTaskError` right away. Keep the name in a constant next to the task
    (`*_TASK_NAME`) and use the same string in the API.

### Worker resources — `on_startup` / `on_shutdown`

FastAPI's `lifespan` **does not run in the worker**. Without it the
`taskiq worker` process has nowhere to open the database, the message
broker or an HTTP client — and nowhere to close them: it works by
accident, on the lazy connect of the first query, and never disposes the
pool on the way out.

The hooks are that place:

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.api.dependencies.resources import db
from src.core.settings import settings

tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@tq.on_startup
async def _open_resources() -> None:
    """Open the database when the worker starts."""
    await db.connect()


@tq.on_shutdown
async def _close_resources() -> None:
    """Dispose the pool when the worker stops."""
    await db.disconnect()
```

For the common case — resources that already speak `connect` /
`disconnect` — the same thing fits on one line:

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.api.dependencies.resources import broker, db
from src.core.settings import settings

tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL, resources=[db, broker])
```

`AsyncDatabaseManager`, `MessageBroker` and `AsyncMinIOClient` satisfy the
`LifecycleResource` protocol; any object of yours with both methods does
too. They are opened left to right and closed right to left, so one that
depends on an earlier one still finds it alive while closing. After
construction, `tq.use(db)` does the same.

!!! info "Scope: the worker by default"
    The hook is registered on TaskIQ's `WORKER_STARTUP`, so it does
    **not** fire in the web process — which has its own `lifespan`. Pass
    `scope="client"` (or `"both"`) when you want the opposite:
    `@tq.on_startup(scope="both")`.

!!! tip "Testable without a worker"
    The in-memory broker runs **both** sides' events in one process, so
    `TaskQueue.memory(resources=[db])` executes the worker hooks on
    `connect()` / `disconnect()`. That is how this section's test checks
    the open and close ordering.

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
`UTC`.

!!! info "Two ways to say a timezone, and what each one does"
    TaskIQ reads `cron_offset` two different ways
    (`taskiq/cli/scheduler/run.py`): a `timedelta` is **added** to the
    current time, while a `str` is handed to `ZoneInfo` as an **IANA key**.
    The SDK normalizes at declaration time, so you pick by what you need:

    | You write | Reaches the scheduler as | Follows DST? | Needs a tz database? |
    | --- | --- | --- | --- |
    | `CronOffset.BRASILIA` | `timedelta(hours=-3)` | no | no |
    | `"-03:00"` | `timedelta(hours=-3)` | no | no |
    | `"America/Sao_Paulo"` | the key, already validated | yes | **yes** |

    For Brazil the fixed offset is the right one: daylight saving was
    abolished after 2019 and, measured against the system tz database,
    `America/Sao_Paulo` sits at `-03:00` in all 252 months from 2020 to
    2040.

    An IANA key needs a tz database on the image. Without one even
    `ZoneInfo("UTC")` raises `ZoneInfoNotFoundError`, so a slim image has to
    install the `tzdata` package.

!!! warning "A string that is not a timezone is refused at declaration"
    `cron_offset="-0300"` (no colon) raises `ValueError` right away, naming
    both accepted forms.

    The refusal exists because the error used to be anything but local. The
    scheduler loop guards each tick with `except CronValueError`, so a
    `ZoneInfoNotFoundError` escaped and ended the `while True` — silencing
    **every** task in the process, not only the one that declared the
    offset. Measured on 0.283.1: the loop ended 0.363 s after startup and
    `health_check()` started returning `False`. Refusing at declaration
    trades a silently stopped service for a traceback that names the line.

Prefer raw cron or intervals? Still supported:

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

Any `async` function taking a `DeadLetter` is a sink, whatever you name the parameter — `async def to_dlq(letter: DeadLetter)` passes mypy and basedpyright alike.

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

The worker and the scheduler are separate processes pointing at the raw objects `TaskQueue` exposes. The TaskIQ CLI resolves `module:attribute` with a plain `getattr`, so **bind both to module-level names** — a dotted path does not resolve:

```python
# src/tasks.py — after registering the tasks
from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")

broker = tq.broker
scheduler = tq.scheduler
```

```bash
# consumes and executes the tasks
taskiq worker    src.tasks:broker

# a single scheduler process for the whole cluster
taskiq scheduler src.tasks:scheduler
```

`tq.broker` is the TaskIQ broker (it knows every registered task); `tq.scheduler` is the internal `TaskiqScheduler`. Both come out typed (`AsyncBroker` and `TaskiqScheduler`), so `broker = tq.broker` and `scheduler = tq.scheduler` need no annotation — and you import nothing from `taskiq` to get it.

!!! warning "`src.tasks:tq.broker` does not work"
    The CLI runs `getattr(module, "tq.broker")` and raises
    `AttributeError: module 'src.tasks' has no attribute 'tq.broker'` —
    the process never starts. The same holds for any dotted path
    (`tq.scheduler`, `scheduler.scheduler`). Hence the `broker = tq.broker`
    above.

## Worker and scheduler healthcheck

The healthcheck `TaskQueue` already had (`health_check`) only asks whether the
broker is up. A worker stuck on a task that never finishes, or a scheduler
whose loop stopped, still has a perfectly reachable broker — and the container
stays `healthy`. A **heartbeat** closes that gap: the process proves it is
doing its job by rewriting a file with the current time, and the healthcheck
asks how many seconds old that file is.

Turn it on from the environment — `from_settings` calls `enable_heartbeat`
when either file is set:

```python
# src/tasks.py
from tempest_fastapi_sdk.settings import TaskIQSettings
from tempest_fastapi_sdk.tasks import TaskQueue


class Settings(TaskIQSettings):
    """Worker and scheduler configuration."""


tq: TaskQueue = TaskQueue.from_settings(Settings())

broker = tq.broker
scheduler = tq.scheduler
```

| Variable | Default | What it decides |
| --- | --- | --- |
| `TASKIQ_WORKER_HEARTBEAT_FILE` | empty | File each **worker** process rewrites every period. |
| `TASKIQ_SCHEDULER_HEARTBEAT_FILE` | empty | File the **scheduler** rewrites every period, from inside its loop. |
| `TASKIQ_HEARTBEAT_SECONDS` | `30` | Heartbeat period. |

Both empty (the default) leaves everything as it was: no extra loop, no file.
Each process only writes the file that belongs to it, so both variables can go
on both containers (as the compose below does).

Or explicitly, without settings:

```python
from datetime import timedelta

from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.redis("redis://localhost:6379/0")
tq.enable_heartbeat(
    worker_file="/tmp/worker.heartbeat",
    scheduler_file="/tmp/scheduler.heartbeat",
    every=timedelta(seconds=30),
)

broker = tq.broker
scheduler = tq.scheduler
```

### What each file proves

- **Worker** — each worker process starts, on TaskIQ's startup event, an
  asyncio task that rewrites the file every `every` (`HeartbeatLoop`), and
  cancels it on shutdown. The beat is **local**: it goes through neither the
  broker nor the scheduler, so each replica keeps its own file fresh whichever
  replica the broker picks for each task. A fresh file proves the process is
  alive and its event loop is not blocked — a task that blocks the loop with
  synchronous work stops the beat. A task that awaits forever without
  blocking the loop does not; that is a task-timeout problem.
- **Scheduler** — TaskIQ's loop wakes every second; when the heartbeat is
  due it calls the `pre_send` of the schedule source `enable_heartbeat`
  attaches to `tq.scheduler`. `pre_send` writes the file and **cancels** the
  send (`ScheduledTaskCancelledError`): nothing is published to the broker. A
  fresh file proves the loop is ticking — not that the scheduler can still
  publish.

Measured with real `taskiq worker` + `taskiq scheduler` over Redis, in
Compose, with `TASKIQ_HEARTBEAT_SECONDS=5` and **two** worker replicas: each
replica and the scheduler rewrote its file every 5 s; the `taskiq` stream in
Redis stayed at `XLEN 0` (the heartbeat publishes nothing); with the
scheduler stopped, both replicas stayed `healthy` for over 100 s.

!!! note "With `--workers N`, the file proves at least one process"
    `taskiq worker` starts several child processes (`--workers`), and each
    has its own heartbeat loop writing to the container's one file. One stuck
    child with another alive keeps the file fresh.

!!! info "Two TaskIQ `INFO` lines per scheduler beat"
    TaskIQ's scheduler logs `Sending task tempest:heartbeat ...` and
    `Scheduled task tempest:heartbeat has been cancelled.` every period. It is
    the loop saying it went by; no message leaves.

!!! check "A failed write never takes anything down"
    The files are written atomically (temporary file + `os.replace`), with
    mode `0644` so a checker running as another user on a shared volume can
    read them, and the healthcheck never reads a half-written file. When a
    write fails (read-only filesystem, missing directory), **one** `WARNING`
    is logged and the worker and the scheduler carry on; the stale file is the
    signal.

### The checker

```bash
python -m tempest_fastapi_sdk.tasks.heartbeat /tmp/worker.heartbeat --max-age 90
```

It exits `0` when the file exists and the time recorded in it is less than
`--max-age` seconds old, and `1` otherwise — stale, missing, unreadable, more
than 5 s **in the future** (`HEARTBEAT_FUTURE_TOLERANCE_SECONDS`: a broken
clock or a forged file must not look healthy for hours) or bad arguments,
including a `--max-age` that is not a finite number above zero; never `2`,
which Docker reserves. It prints one line with the reason:

```text
healthy: heartbeat /tmp/worker.heartbeat is 0.2s old (max 90s)
unhealthy: heartbeat /tmp/worker.heartbeat is 10.4s old (max 6s)
```

It only uses the standard library: it runs with the base package installed,
without the `[tasks]` or `[cli]` extras (measured on a `python:3.13-slim`
image with only the base wheel). In code, the same test is
`is_heartbeat_fresh(path, max_age)` and `heartbeat_age(path)`.

!!! tip "`--max-age` of about three periods"
    With `TASKIQ_HEARTBEAT_SECONDS=30`, use `--max-age 90`: one late heartbeat
    (busy worker, GC, clock) does not become a false alarm.

### In `docker-compose.yml`

```yaml
x-tasks-env: &tasks-env
  TASKIQ_BROKER_URL: redis://redis:6379/0
  TASKIQ_WORKER_HEARTBEAT_FILE: /tmp/worker.heartbeat
  TASKIQ_SCHEDULER_HEARTBEAT_FILE: /tmp/scheduler.heartbeat
  TASKIQ_HEARTBEAT_SECONDS: "30"

services:
  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 10

  scheduler:
    build: .
    command: ["taskiq", "scheduler", "src.tasks:scheduler"]
    environment: *tasks-env
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-m", "tempest_fastapi_sdk.tasks.heartbeat",
             "/tmp/scheduler.heartbeat", "--max-age", "90"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  worker:
    build: .
    command: ["taskiq", "worker", "src.tasks:broker"]
    environment: *tasks-env
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-m", "tempest_fastapi_sdk.tasks.heartbeat",
             "/tmp/worker.heartbeat", "--max-age", "90"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  api:
    build: .
    command: ["python", "main.py"]
    depends_on:
      worker:
        condition: service_healthy
```

`start_period` covers startup (importing the service before the first beat).
Each check takes about a second (it imports the SDK), hence `timeout: 10s`.

!!! danger "Plain Docker does **not** restart an `unhealthy` container"
    The healthcheck **marks** the container (`docker ps` shows `(unhealthy)`);
    the `restart:` policy only acts when the process **exits**. Measured with
    Docker 29.6.1 and Compose 5.3.0: with the worker's processes frozen
    (`SIGSTOP`), the replica turned `unhealthy` in ~20 s and stayed that way,
    `RestartCount` at `0`, despite `restart: unless-stopped` — the other
    replica stayed `healthy`. What the healthcheck feeds is:

    - `depends_on: condition: service_healthy` — the `api` above only starts
      once the worker has proven it is alive;
    - `docker ps` / `docker inspect` — the state is visible to whoever
      operates it;
    - your monitoring (alerting on `unhealthy` containers), or an orchestrator
      that acts on it — Swarm and Kubernetes (through a `livenessProbe`)
      replace the container; Docker alone does not.

## The scheduler in the FastAPI lifespan — with one replica scheduling

A small deployment does not want a second process just for cron.
`tq.lifespan(scheduler=True)` runs the scheduler inside the web process, and
plugs straight into FastAPI:

```python
# src/api/app.py

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.settings import settings
from src.tasks import tq


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Open the queue and run periodic tasks in this process."""
    async with tq.lifespan(scheduler=True):
        yield


app: FastAPI = FastAPI(title=settings.TITLE, lifespan=lifespan)
```

!!! danger "A raw `start_scheduler()` fires once per replica"
    The scheduler loop does not know how many processes run it. With
    three uvicorn replicas, `@tq.cron(every_n_minutes(1))` fires **three
    times a minute** — and nothing raises, nothing is logged. The effect
    arrives as duplicated work: the sweep that expires charges expires
    them three times, the backup runs three times, the e-mail goes out
    three times. That `three` is measured, not reasoned — it is the
    second line of the output below.

    `scheduler=True` does not have that problem because it does not run
    the loop directly: one replica holds a **lease** and runs, the others
    stand by and take over when the lease lapses. Three lifespans open
    against the same lease, with the loops counted in both modes:

    ```text
    scheduler=True         loops running: 1   replica-1=1  replica-2=0  replica-3=0
    scheduler='unlocked'   loops running: 3   replica-1=1  replica-2=1  replica-3=1
    ```

    The `3` on the second line is the defect and the `1` on the first is
    the lease fixing it. `tests/tasks/test_scheduler_lease.py` pins both
    counts — the first parametrized over 2, 3 and 5 replicas — and a
    separate case waits for the leader to leave and confirms the standby
    takes over on the first `acquire` that begins after the leader
    releases the lease, without waiting for the TTL to lapse.

### Where the lease comes from

The lease is a Redis lock, and `TaskQueue` derives its URL from wherever one
already is:

* `TaskQueue.from_settings(settings)` or `TaskQueue.redis(url)` → the broker
  itself.
* `TaskQueue.from_settings(settings)` with an AMQP broker and
  `TASKIQ_RESULT_BACKEND_URL` pointing at Redis → the result backend.
* A broker built by hand → none. Pass `scheduler_lock=`.

With no derivable lease, `scheduler=True` **refuses at startup** rather than
scheduling N times:

```text
ValueError: lifespan(scheduler=True) needs a lease so only one replica runs
the schedule, and none could be derived from this broker. Pass
scheduler_lock=RedisSchedulerLock.from_url(...), build the queue with
TaskQueue.from_settings(...) or TaskQueue.redis(...), or ask for
scheduler="unlocked" if you accept every replica firing every schedule.
```

For coordination of your own — etcd, a Postgres advisory lock — implement
`SchedulerLock`, which is three calls and knows nothing about Redis:

```python
from tempest_fastapi_sdk.tasks import RedisSchedulerLock, SchedulerLock

lease: SchedulerLock = RedisSchedulerLock.from_url(
    "redis://localhost:6379/0",
    name="alofans:scheduler",
    ttl_seconds=30.0,
)
```

!!! warning "Two services on one Redis need different `name`s"
    The default is `"tempest:tasks:scheduler"`. Two services sharing the
    instance contend for the same key, and one of them **never**
    schedules.

!!! warning "A clock that jumps shortens the lease"
    Redis stores a key's expiry as an absolute Unix timestamp and checks
    the computer clock even while the instance is running — the
    [`EXPIRE` documentation](https://redis.io/docs/latest/commands/expire/#expires-and-persistence)
    gives the example: a key with a 1000 s TTL and a clock moved 2000 s
    forward expires immediately. For the scheduler lease, that means **a
    forward jump of the Redis server's clock larger than the remaining TTL
    drops the lease** early, and another replica can acquire it.

    This is not a lab hypothetical: on a WSL2 machine we measured the wall
    clock jumping +3.6 s twice a minute, and with a 2 s lease
    `tests/tasks/test_scheduler_lease.py` saw two loops running at once
    (the test now pins fakeredis's clock so it measures the election, not
    the host). NTP *step* corrections and a VM resumed after a pause are
    the other common sources.

    The default `ttl_seconds=30.0`, renewed every third of it, always
    keeps at least ~20 s of TTL left — that is the size of jump it
    absorbs. In production:

    * keep the Redis host's clock synced by *slewing* (chrony/NTP), not
      *stepping*;
    * don't lower the TTL to a few seconds to "take over faster" — a
      leader that exits releases the lease on shutdown, so a planned
      handover already does not wait for the TTL;
    * keep scheduled tasks idempotent: the lease reduces double firing, it
      does not make it impossible.

!!! info "`scheduler="unlocked"` exists, and has to be typed"
    A service that genuinely runs a single replica passes
    `scheduler="unlocked"` and skips the lease. That is a choice, which
    is why it is not the default: the default is `scheduler=False` (broker
    only, nothing scheduled), and `True` is the guarded way to turn the
    scheduler on. A service that forgot to ask schedules nothing — not
    everything N times.

## One line for the transport — `from_settings`

The block every service rewrote decided three things: which transport the URL
means, whether results are stored, and what an empty URL does.
`TaskQueue.from_settings` decides all three:

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

tq: TaskQueue = TaskQueue.from_settings(settings)
```

| `TASKIQ_BROKER_URL` | Broker | Results |
| --- | --- | --- |
| `redis://` / `rediss://` | Redis Streams | on the same URL, with a TTL |
| `amqp://` / `amqps://` | RabbitMQ | on `TASKIQ_RESULT_BACKEND_URL`, if set, with a TTL |
| empty | in-memory — `@tq.task` still registers, and `enqueue` runs the task in this process instead of handing it to a worker | in-memory |
| any other scheme | `ValueError` naming the scheme | — |

!!! check "Passes `mypy --strict` without `type: ignore`"
    `TaskIQSettingsLike` declares its fields as `@property` (read-only), so
    the frozen `Settings` the mixins build
    (`class Settings(TaskIQSettings, BaseAppSettings)`) is accepted — and so
    is a plain test object. And `@tq.task` / `@tq.task(...)` return
    `Task[P, R]` carrying the function's signature: under `--strict`,
    `reveal_type` of a decorated `async def add(a: int, b: int) -> int`
    shows `Task[[a: int, b: int], int]`, and `await add.run("x", 1)` is an
    `arg-type` error. Before, `from_settings(Settings())` was rejected
    (`expected settable variable, got read-only attribute`) and
    `@tq.task(name=...)` was an `untyped-decorator`.

!!! danger "A stored result now expires — it used not to"
    `taskiq-redis` writes results with **no expiry** of its own
    (`result_ex_time=None`, `result_px_time=None`, `keep_results=True`).
    Measured against Redis 8.2.9, with all of its defaults:

    ```text
    keys written:  ['task-abc-123']      # the bare task id
    TTL:           [-1]                  # -1 = never expires
    MEMORY USAGE:  [144] bytes           # task returning None
    ```

    A `@tq.cron(Cron.EVERY_MINUTE)` task leaves **1440 keys a day**,
    growing without a ceiling. On a shared Redis running `allkeys-lru`
    that growth starts **evicting the keys of whoever is using Redis to
    work** — a rate limit, an idempotency marker, the scheduler lease —
    and the service that never reads a result pays the whole bill.

    The SDK now applies `TASKIQ_RESULT_TTL_SECONDS` (default `86400`,
    one day — the same as Celery's `result_expires`) and the
    `tempest:results` prefix, on **both** transports. Measured after:

    ```text
    keys written:  ['tempest:results:abc-123']
    TTL:           [86400]
    ```

    If you need the old behaviour, ask for it:
    `TASKIQ_RESULT_TTL_SECONDS=0`, or
    `TaskQueue.redis(url, result_ttl_seconds=0)`.

!!! tip "A cron-only service does not need to store results"
    `TASKIQ_STORE_RESULTS=False` leaves TaskIQ's `DummyResultBackend` in
    place, on both transports. That is the shape of a service that only
    runs crons: no caller is waiting on a return value, so every stored
    result is cost with no reader.

    It also settles an asymmetry between the transports. An empty
    `TASKIQ_RESULT_BACKEND_URL` meant "no results" on RabbitMQ and
    "results on the broker URL" on Redis — one environment, two
    readings. The flag now decides, the same way on both.

!!! warning "Result-backend arguments do not travel through `**options`"
    `TaskQueue.redis(url, result_ex_time=3600)` is **accepted at
    construction** — `RedisStreamBroker` swallows unknown keys into its
    `**connection_kwargs` — and only blows up later, inside `connect()`:

    ```text
    TypeError: AbstractConnection.__init__() got an unexpected keyword
    argument 'result_ex_time'
    ```

    Measured: the backend keeps `result_ex_time=None`. That is why the
    TTL and the prefix are named parameters (`result_ttl_seconds`,
    `result_prefix`) rather than keys in `**options`.

!!! warning "The mixin's default is an AMQP URL, not empty"
    `TaskIQSettings.TASKIQ_BROKER_URL` defaults to
    `"amqp://guest:guest@localhost:5672/"`. To get the in-memory
    fallback in development you **declare `TASKIQ_BROKER_URL=` empty**
    in `.env` — omitting it is not enough. And an unknown scheme raises
    rather than downgrading to memory on purpose: a typo in a deployed
    environment variable would otherwise become a queue that looks
    healthy while nothing is queued.

!!! check "Redis stores results now"
    Up to v0.281.0 `TaskQueue.redis()` left TaskIQ's
    `DummyResultBackend` in place and offered no way to ask for another,
    so reading a result through the facade was impossible — measured:

    ```text
    broker: RedisStreamBroker
    result backend: DummyResultBackend
    ```

    The default is now to store them on the broker's URL.
    `results="redis://..."` sends them elsewhere, `results=False`
    restores the previous behavior. Requires the **`[tasks-redis]`**
    extra (new in v0.282.0) — `taskiq-redis` was declared in no extra at
    all.

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
- **Heartbeat** — `TASKIQ_WORKER_HEARTBEAT_FILE` / `TASKIQ_SCHEDULER_HEARTBEAT_FILE` + `python -m tempest_fastapi_sdk.tasks.heartbeat` in the `HEALTHCHECK`; Docker marks the container `unhealthy`, it does not restart it. The worker beat is local to each replica.
- **Cron without syntax** — `Cron` / `CronOffset` / `Weekday` + helpers (`daily`, `weekdays`, `every_n_minutes`, …) to schedule by name; `CronOffset.BRASILIA` instead of `"-03:00"`.
- **Styles** — decorators (`@mq.on`, `@tq.task`, `@tq.cron`) **or** classes (`Consumer` + `mq.register`, `TaskDef` + `tq.register`); both coexist.
- **Outbox** — `BaseOutboxModel` + `OutboxRelay` + `save_with_outbox`, with the relay's `publish` pointing at `MessageBroker`. See [Outbox](outbox.md).
- **Rename (v0.94.0)** — `AsyncBrokerManager` → **`AsyncQueueManager`** (thin wrapper; old alias kept). The `MessageBroker` / `TaskQueue` facades stay recommended; `AsyncTaskBrokerManager` / `AsyncTaskScheduler` remain functional legacy.
