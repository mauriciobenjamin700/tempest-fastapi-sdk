# Event-path reliability

Use to stop losing failing messages: dead-letter, delayed retry, metrics, prefetch, idempotency and tracing through the queue.

Part of the [Queues and Tasks](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks.md) recipe.

The consumer policy is `REJECT_ON_ERROR`. A handler that raises issues `basic.reject` with `requeue=False` — no loop, and **gone**. Three pieces close that, mirroring what `TaskQueue` already has.

## Dead-letter: a failure becomes a record

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

## Delayed retry, performed by the broker

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

## Metrics

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


## Prefetch — how many messages stay in flight

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

## Consume-side idempotency

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

## Tracing and request id across the queue

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
