# Queues and Tasks

Background work without the pain. The SDK wraps **FastStream** (messaging) and **TaskIQ** (tasks + scheduling) in typed classes with a single vocabulary — the common path (publish, consume, enqueue, schedule) imports neither `faststream` nor `taskiq` in application code. The exception is a transport-specific option, such as the `RabbitExchange` passed as `exchange=` further down.

!!! tip "Which tool?"
    - **`MessageBroker`** (messaging) — an event happens and **many** services/consumers react. Fan-out, at-least-once, decoupled from the request. E.g. "order paid" → inventory, email, analytics.
    - **`TaskQueue`** (tasks) — offload slow work from **one** request handler to a worker, keeping the HTTP response fast. E.g. send an email, render a PDF.
    - **`TaskQueue.cron` / `.interval`** (scheduling) — periodic runs.
    - **Outbox** — when publishing *must* be atomic with a database `INSERT`.

Every class shares the same lifecycle — `connect()` / `disconnect()` / `lifespan()` / `health_check()` / `is_connected` — and exposes the raw underlying object (`.broker`) as an escape hatch.

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [Messaging with `MessageBroker`](queue-tasks/broker.md) | Use to publish and consume events by channel, with class-based consumers and publishers and Kafka (key, group and commit). |
| [Queue topology with `QueueSpec`](queue-tasks/topology.md) | Use when the queue must survive a restart, fan out, have a reprocessable dead-letter queue and a declared exchange. |
| [Event-path reliability](queue-tasks/events-reliability.md) | Use to stop losing failing messages: dead-letter, delayed retry, metrics, prefetch, idempotency and tracing through the queue. |
| [Background tasks with `TaskQueue`](queue-tasks/tasks.md) | Use to move slow work out of the request: enqueue, enqueue by name, worker resources and class-based tasks. |
| [Periodic tasks: `cron` and `interval`](queue-tasks/periodic.md) | Use to schedule periodic tasks on the same `TaskQueue`, with no separate scheduler in your code. |
| [Task reliability and observability](queue-tasks/tasks-reliability.md) | Use to turn on typed retry, dead-letter, per-task Prometheus metrics and the dead-letter panel in the admin. |
| [Workers, healthcheck and scheduler in production](queue-tasks/production.md) | Use to run the worker and scheduler in production, check they are alive and schedule from the lifespan with a single replica. |
| [Transport from settings and the outbox](queue-tasks/from-settings.md) | Use to pick the transport in one line with `from_settings` and publish atomically with the `INSERT` (outbox). |

## Recap / next steps

- **`MessageBroker`** — typed, transport-agnostic pub/sub over FastStream: `@mq.on("channel")` + `await mq.publish("channel", model)`. At-least-once fan-out across services.
- **`TaskQueue`** — tasks over TaskIQ: `@tq.task` → `await task.enqueue(...)` (to the worker) or `await task.run(...)` (inline). `.memory()` for tests.
- **`@tq.cron` / `@tq.interval`** — periodic on the same object; `start_scheduler()` in dev, standalone CLI in production.
- **Heartbeat** — `TASKIQ_WORKER_HEARTBEAT_FILE` / `TASKIQ_SCHEDULER_HEARTBEAT_FILE` + `python -m tempest_fastapi_sdk.tasks.heartbeat` in the `HEALTHCHECK`; Docker marks the container `unhealthy`, it does not restart it. The worker beat is local to each replica.
- **Cron without syntax** — `CronEnum` / `CronOffsetEnum` / `WeekdayEnum` + helpers (`daily`, `weekdays`, `every_n_minutes`, …) to schedule by name; `CronOffsetEnum.BRASILIA` instead of `"-03:00"`.
- **Styles** — decorators (`@mq.on`, `@tq.task`, `@tq.cron`) **or** classes (`Consumer` + `mq.register`, `TaskDef` + `tq.register`); both coexist.
- **Outbox** — `BaseOutboxModel` + `OutboxRelay` + `save_with_outbox`, with the relay's `publish` pointing at `MessageBroker`. See [Outbox](outbox.md).
- **Rename (v0.94.0)** — `AsyncBrokerManager` → **`AsyncQueueManager`** (thin wrapper; old alias kept). The `MessageBroker` / `TaskQueue` facades stay recommended; `AsyncTaskBrokerManager` / `AsyncTaskScheduler` remain functional legacy.
