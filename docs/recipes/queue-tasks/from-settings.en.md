---
description: "Use to pick the queue transport in one line with `TaskQueue.from_settings` and publish atomically with the `INSERT` through the transactional outbox."
---

# Transport from settings and the outbox

Use to pick the transport in one line with `from_settings` and publish atomically with the `INSERT` (outbox).

Part of the [Queues and Tasks](../queue-tasks.md) recipe.

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

    A `@tq.cron(CronEnum.EVERY_MINUTE)` task leaves **1440 keys a day**,
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

!!! danger "Results are stored as JSON, not pickle — behaviour change"
    On its own, `taskiq_redis` stores results and progress with
    `PickleSerializer`, and reading a pickle runs whatever code the stored
    bytes name: anyone able to write the `tempest:results:*` keys (another
    service on the same Redis, a leaked credential) gets code execution in
    the process that calls `get_result()` / `wait_result()`. So `TaskQueue`
    uses `JSONSerializer` in `redis()`, `from_settings()` and the RabbitMQ
    result backend.

    The value you read back is the same under both: TaskIQ converts the
    result with `model_dump(mode="json")` **before** serializing it, so
    `datetime` and `UUID` already came back as `str`, `set`/`tuple` as
    `list`, and an object pydantic cannot serialize already failed. A task
    exception also comes back with its original class. What changes:

    - a result written by an earlier version (pickle) is not readable by the
      new one until it expires (`TASKIQ_RESULT_TTL_SECONDS`, 24 h by
      default) — `get_result()` fails to decode it;
    - a value that does not become JSON keeps failing when stored:
      measured with taskiq 0.12.4 and taskiq-redis 1.2.3, an arbitrary
      object raises `PydanticSerializationError` in `model_dump` under
      **both** serializers, and non-UTF-8 `bytes` raise
      `UnicodeDecodeError` under both. Whoever relied on pickle for another
      value shape passes `result_serializer="pickle"`.

    A service whose Redis only it can write, and which needs pickle, asks
    for it explicitly: `TaskQueue.redis(url, result_serializer="pickle")` or
    `TaskQueue.from_settings(settings, result_serializer="pickle")`.

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

The full guide — model, producer service with `save_with_outbox`, retention and concurrency — lives in the dedicated **[Outbox](../outbox.md)** recipe.
