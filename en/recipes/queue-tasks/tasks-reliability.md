# Task reliability and observability

Use to turn on typed retry, dead-letter, per-task Prometheus metrics and the dead-letter panel in the admin.

Part of the [Queues and Tasks](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks.md) recipe.

A worker-only task needs three things TaskIQ has but scatters: **retry**, **dead-letter**, and **metrics**. `TaskQueue` exposes all three as opt-in middleware — call them **before `connect()`**, and nothing touches the broker's middleware API.

## Typed retry

`RetryPolicy` carries the retry config as labels; `enable_retries()` installs the TaskIQ middleware that reads them:

```python
from tempest_fastapi_sdk.tasks import RetryPolicy, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_retries(default_max_retries=3)


@tq.task(name="reports:nightly", retry=RetryPolicy(max_retries=5))
async def nightly() -> None:
    ...   # re-run up to 5x on error
```

## Dead-letter — where terminal failures go

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

## Per-task Prometheus metrics

`TaskMetrics` counts executions (by status) and a duration histogram, labelled by task, into the **same** `/metrics` the SDK already serves (pass the shared `registry`):

```python
from tempest_fastapi_sdk.tasks import TaskMetrics, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_metrics(TaskMetrics())   # tasks_runs_total{task,status} + tasks_duration_seconds{task}
```

## Dead-letter panel in the admin

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
