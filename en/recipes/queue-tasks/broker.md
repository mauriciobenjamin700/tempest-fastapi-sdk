# Messaging with `MessageBroker`

Use to publish and consume events by channel, with class-based consumers and publishers and Kafka (key, group and commit).

Part of the [Queues and Tasks](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks.md) recipe.

The problem FastStream handles poorly: its API changes shape with the transport. You subscribe with `@broker.subscriber("q")` and publish with `broker.publish(msg, queue="q")` on RabbitMQ, `topic=` on Kafka, `subject=` on NATS. Confusing and non-portable.

`MessageBroker` hides that behind **one** concept: a **channel** (a string). You publish to a channel and everyone subscribed to it receives the message.

Install with `[queue]` (pulls `faststream[rabbit]`). The other transports have their own extra, because `[queue]` carries the RabbitMQ client only: `[queue-kafka]`, `[queue-nats]`, `[queue-redis]`.

```python
# src/queue/broker.py

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

`src/queue/__init__.py` only re-exports — `from .broker import OrderPaid as OrderPaid`, the same for `OrderCancelled` and `mq`, plus `__all__` — and it is what the rest of the code imports from (`from src.queue import mq`). A definition or a call in the `__init__.py` reports `INIT001` in `tempest check`.

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
    `MessageBroker.rabbitmq(url)`, `.redis(url)`, `.kafka(*servers)`, `.nats(servers)`. Each lazily imports the right FastStream backend and, when it is missing, raises naming the extra that installs it (`[queue]`, `[queue-redis]`, `[queue-kafka]`, `[queue-nats]`). On Kafka, see [Kafka: key, group and commit](#kafka-key-group-and-commit). Need a custom (or test) broker? `MessageBroker(my_broker)`.

!!! check "Recap"
    - `MessageBroker.rabbitmq(url)` — pick the transport, hide FastStream.
    - `@mq.on("channel")` — declare a consumer; the parameter type validates the message.
    - `await mq.publish("channel", model)` — publish; channel first.
    - `mq.publish(...)` only works after `connect()` (raises `RuntimeError` before).

Wire it into the health router: `make_health_router(checks={"queue": mq.health_check})`. `health_check()` runs FastStream's real `ping` (`timeout=2.0` by default) and returns `False` before `connect()` or once the connection drops — see [Liveness and readiness](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/bootstrap.md#liveness-and-readiness).

## Class-based consumers

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


# OrderPaid / OrderCancelled defined in the `src/queue/broker.py` block above.


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

The channel may be a string or a [`QueueSpec`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks/topology.md), exactly
as in `@mq.on(...)` — declaring a dead-letter exchange or a quorum queue does not
push you back to the decorator. The same holds for the rest of what
`@mq.on(...)` takes: [`prefetch=`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks/events-reliability.md#prefetch-how-many-messages-stay-in-flight)
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

## Class-based publishers

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

## Kafka: key, group and commit

Install with `[queue-kafka]`. It pulls `faststream[kafka]` and declares `aiokafka>=0.10.0` on its own: FastStream accepts `aiokafka>=0.9`, and under 0.9.0 every consumer fails to start with `TypeError: AIOKafkaConsumer.__init__() got an unexpected keyword argument 'group_instance_id'` (measured with FastStream 0.7.5).

Kafka has no queues: the channel is a **topic**, and who gets a copy is decided by the **consumer group**. Three knobs cover it, all through the typed facade:

```python
# src/queue/billing.py

from pydantic import BaseModel

from tempest_fastapi_sdk.queue import (
    Consumer,
    MessageBroker,
    OffsetResetEnum,
    Publisher,
)

mq = MessageBroker.kafka("localhost:9092")


class OrderPaid(BaseModel):
    order_id: str


@mq.on(
    "orders.paid",
    group_id="billing",
    auto_offset_reset=OffsetResetEnum.EARLIEST,
)
async def bill(event: OrderPaid) -> None:
    """One replica of the `billing` group gets each order."""
    print(f"billing {event.order_id}")


class AuditConsumer(Consumer):
    """Same topic, another group: audit reads its own copy."""

    group_id = "audit"
    auto_offset_reset = OffsetResetEnum.EARLIEST

    async def handle(self, event: OrderPaid) -> None:
        print(f"auditing {event.order_id}")


class OrderPaidPublisher(Publisher[OrderPaid]):
    channel = "orders.paid"
    schema = OrderPaid


mq.register(AuditConsumer(channel="orders.paid", schema=OrderPaid))
orders = mq.publisher_for(OrderPaidPublisher)


async def pay(order_id: str) -> None:
    """The key picks the partition: one order's events arrive in order."""
    await orders.publish(OrderPaid(order_id=order_id), key=order_id)
```

| Knob | Where | What it does |
| --- | --- | --- |
| `key=` | `mq.publish`, `Publisher.publish` | Same key, same partition, read in the order it was written. A `str` is encoded as UTF-8 — `aiokafka` takes `bytes` only and raises `TypeError: a bytes-like object is required, not 'str'`. |
| `group_id=` | `@mq.on`, `@subscribe`, `Consumer` | Replicas sharing a group split the partitions; each distinct group reads everything. Without a group there is nowhere to commit an offset (see the `ack_policy` table below). |
| `auto_offset_reset=` | `@mq.on`, `@subscribe`, `Consumer` | Where a group **with no committed offset** starts. Kafka's default is `LATEST`: a new consumer does not see what was published before its first deploy. A group that has committed resumes from the commit, whatever this says. |

`Consumer` and `@subscribe` follow the same rule as `prefetch` and `ack_policy`: the class value applies to every binding, and a `@subscribe` value overrides it for that method.

What was measured against a real Kafka 3.9.1 (`tests/queue/test_kafka_live.py`, on `make test-docker`, under FastStream 0.7.5 + `aiokafka` 0.10.0 and under FastStream 0.7.8):

- 6 keys × 8 records on a 3-partition topic: each key landed on one partition only, the one `publish` returned in the `RecordMetadata`, and was read in order 0..7; the 6 keys used more than one partition.
- 40 records on a 2-partition topic, two replicas in the `workers` group and one in the `audit` group: each replica received a non-empty share, with no record repeated between them, and together they added up to the 40; `audit` received all 40.

### On Kafka, `ack_policy` is the **commit** policy

| `ack_policy` | Behavior |
| --- | --- |
| `None` (`ACK_FIRST`) | FastStream turns on `aiokafka`'s `enable_auto_commit`: the offset is committed on a timer, whether the handler succeeded or not. |
| `ACK` | Commit after the handler — also when it raised. Measured: the handler raised on record 1 of 3, the replica stopped, another joined the same group and read only the new records. |
| `NACK_ON_ERROR` | Seeks the consumer back to the failed record and reads it again. Measured: a handler raising the first time ran twice with the same record. |
| `REJECT_ON_ERROR` | Same as `ACK` on Kafka; FastStream says so with a `UserWarning` (`has the same effect as AckPolicy.ACK`) — which **raises** for anyone running `filterwarnings = ["error"]`. Prefer `ACK`. |
| `MANUAL` | The handler commits. |

Any policy other than `ACK_FIRST` needs `group_id`, because Kafka keeps offsets per group. Without a group, `aiokafka` refuses the commit with `IllegalOperation('Requires group_id')` on **every** message, after the handler already ran. The facade refuses earlier, at registration:

```text
ValueError: Consumer of 'orders.paid' runs under ack_policy=ACK, which commits
the offset after the handler, and Kafka commits only for a consumer group.
Pass group_id=..., or leave ack_policy unset (ACK_FIRST: offsets
auto-committed on a timer).
```

The policy counts when it comes from the whole broker too (`MessageBroker.kafka(..., ack_policy=AckPolicy.ACK)`).

### Deduplication on Kafka

Kafka has no AMQP `message_id` field, and FastStream's Kafka `publish` takes no such argument. `mq.publish` puts the id in the `message_id` header (`MESSAGE_ID_HEADER`), and `mq.deduplicate(...)` reads it from there first — your explicit `message_id=` takes the same path. Measured: two `publish` calls with the same `message_id` become two records, at two offsets, and the handler ran once.

A record published by another producer, without the header, is deduplicated by `topic:partition:offset`. The id FastStream gives a Kafka message is `<offset>-<timestamp>` (`0-1794491612333`, for instance), and the offset is per partition: two records at the same offset of different partitions, written in the same millisecond, share the id. Measured: two records at offset 0 of partitions 0 and 1, with the same `timestamp_ms`, deduplicated by that id — the second was skipped; by coordinates, both run.

### What Kafka cannot express **raises**

`prefetch` is AMQP's `basic.qos`: on Kafka, NATS and Redis it raises `UnsupportedTopologyError`. It used to reach FastStream as a RabbitMQ `Channel` and fail with `TypeError: KafkaRegistrator.subscriber() got an unexpected keyword argument 'channel'`, without naming the knob. The other way around, `group_id`, `auto_offset_reset` and `key=` off Kafka raise `UnsupportedTopologyError` too — on `@mq.on`, `mq.register` and `mq.publish` — because dropping them would trade "one copy per group" for whatever the other transport does by default.

### Connecting from the environment — `KafkaSettings`

The example above writes `"localhost:9092"` in code. In production the cluster asks for TLS and SASL, and building that by hand means picking the right `faststream.security` class and creating an `ssl.SSLContext` in every service. The `KafkaSettings` mixin reads it all from the environment and `MessageBroker.kafka_from_settings` builds the broker:

```python
# src/queue/broker.py

from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.settings import KafkaSettings


class Settings(KafkaSettings, BaseAppSettings):
    """Service settings."""


settings: Settings = Settings()
mq: MessageBroker = MessageBroker.kafka_from_settings(settings)
```

With this `.env`:

```text
KAFKA_BOOTSTRAP_SERVERS=["kafka-1:9093","kafka-2:9093"]
KAFKA_CLIENT_ID=billing-api
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISM=SCRAM-SHA-512
KAFKA_SASL_USERNAME=billing
KAFKA_SASL_PASSWORD=change-me
```

`print(settings)` shows the password masked, and the broker comes out with a `SASLScram512` (measured):

```text
KAFKA_BOOTSTRAP_SERVERS=['kafka-1:9093', 'kafka-2:9093'] KAFKA_CLIENT_ID='billing-api' KAFKA_SECURITY_PROTOCOL=<KafkaSecurityProtocolEnum.SASL_SSL: 'SASL_SSL'> KAFKA_SASL_MECHANISM=<KafkaSaslMechanismEnum.SCRAM_SHA_512: 'SCRAM-SHA-512'> KAFKA_SASL_USERNAME='billing' KAFKA_SASL_PASSWORD=SecretStr('**********') KAFKA_SSL_CAFILE=None KAFKA_SSL_CERTFILE=None KAFKA_SSL_KEYFILE=None KAFKA_SSL_KEY_PASSWORD=SecretStr('')
```

With nothing in the environment, the defaults point at `localhost:9092` over `PLAINTEXT` — the development broker.

| `KAFKA_SECURITY_PROTOCOL` | What `kafka_kwargs()` puts in `security` |
| --- | --- |
| `PLAINTEXT` (default) | `None`: no TLS, no authentication |
| `SSL` | `BaseSecurity` with an `ssl.SSLContext` that verifies certificate and host name; `KAFKA_SSL_CAFILE` replaces the system store, `KAFKA_SSL_CERTFILE` + `KAFKA_SSL_KEYFILE` + `KAFKA_SSL_KEY_PASSWORD` add the client certificate (mTLS) |
| `SASL_PLAINTEXT` | `SASLPlaintext`, `SASLScram256` or `SASLScram512`, by `KAFKA_SASL_MECHANISM` (`PLAIN`, `SCRAM-SHA-256`, `SCRAM-SHA-512`), without TLS |
| `SASL_SSL` | the same SASL class, with the `SSL` context |

`kafka_kwargs()` returns only what goes by keyword (`security`, plus `client_id` when `KAFKA_CLIENT_ID` is set): the seeds are positional on `MessageBroker.kafka`. That is why `MessageBroker.kafka(**settings.kafka_kwargs())` on its own is refused, instead of building a broker with no seed that would fail only on `connect()` with `KafkaConnectionError: Unable to bootstrap from []`:

```text
ValueError: MessageBroker.kafka() needs at least one bootstrap server. From a KafkaSettings, use MessageBroker.kafka_from_settings(settings), or pass *settings.KAFKA_BOOTSTRAP_SERVERS before **settings.kafka_kwargs().
```

A combination the protocol would ignore fails at boot, not on the first `connect()`: `SASL_*` without `KAFKA_SASL_USERNAME`, a `KAFKA_SSL_*` file under a protocol without TLS, and `KAFKA_SSL_KEYFILE` without `KAFKA_SSL_CERTFILE`:

```text
1 validation error for KafkaSettings
  Value error, KAFKA_SSL_CAFILE set, but KAFKA_SECURITY_PROTOCOL=PLAINTEXT has no TLS; use SSL or SASL_SSL, or drop the file. [type=value_error]
```

Composed with `EnvironmentSettings` and `ENV=production`, the mixin refuses `SASL_PLAINTEXT` with `PLAIN` (the password crosses the network in clear; SCRAM does not send the password and passes) and an empty SASL password:

```text
1 validation error for Settings
  Value error, ENV=production refuses development values:
- KAFKA_SASL_PASSWORD: empty under a SASL security protocol
- KAFKA_SECURITY_PROTOCOL: SASL_PLAINTEXT with the PLAIN mechanism sends the password unencrypted; use SASL_SSL [type=value_error]
```

!!! tip "A FastStream option the mixin does not model"
    `MessageBroker.kafka_from_settings(settings, acks="all", enable_idempotence=True)` forwards the rest to `KafkaBroker`. A key `kafka_kwargs()` also returns (`security`, `client_id`) wins over the settings.
