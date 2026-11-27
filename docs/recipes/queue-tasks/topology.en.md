---
description: "Use when the RabbitMQ queue needs declared topology via `QueueSpec`; covers the required exchange, per-consumer fan-out, replaying the dead-letter queue and rejected fields."
---

# Queue topology with `QueueSpec`

Use when the queue must survive a restart, fan out, have a reprocessable dead-letter queue and a declared exchange.

Part of the [Queues and Tasks](../queue-tasks.md) recipe.

A channel as a string covers most cases. What it does **not** express is exactly what decides whether the queue survives a restart, where a rejected message goes, and how long it lives. On RabbitMQ that lives in the queue declaration, not in the name.

`QueueSpec` carries that topology as typed data, and is accepted anywhere the string was:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import DeadLetterSpec, MessageBroker, QueueSpec, QueueTypeEnum

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")


class OrderPaid(BaseModel):
    order_id: str
    amount_cents: int

ORDERS_PAID = QueueSpec(
    name="orders.paid",
    dead_letter=DeadLetterSpec(exchange="dlx"),
    message_ttl_ms=60_000,
    queue_type=QueueTypeEnum.QUORUM,
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

## The exchange has to exist

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

## Fan-out: one copy per consumer

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

## Replaying the dead-letter queue

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

## A field the transport cannot express **raises**

`MessageBroker` is multi-transport, and `dead_letter` / TTL / priority are AMQP. Asking for them on a broker without the concept is not ignored:

```text
UnsupportedTopologyError: QueueSpec('orders.paid') sets dead_letter, which the
kafka transport cannot express. Remove the field, or use a bare channel name
and configure the topology outside the SDK.
```

Ignoring it silently would produce a queue that **looks** configured and discards every failure — exactly the defect `QueueSpec` exists to prevent. Same choice as `op.replace_enum`, which raises on an unsupported dialect instead of emitting DDL that does nothing.

A bare `QueueSpec(name=...)` stays portable on any transport: it asks for nothing beyond the name.
