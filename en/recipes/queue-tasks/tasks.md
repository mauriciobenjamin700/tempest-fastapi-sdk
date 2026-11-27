# Background tasks with `TaskQueue`

Use to move slow work out of the request: enqueue, enqueue by name, worker resources and class-based tasks.

Part of the [Queues and Tasks](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks.md) recipe.

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
    [email recipe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/email.md); swap it for your own send dependency.

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

## Enqueue by name — `enqueue_by_name`

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

## Worker resources — `on_startup` / `on_shutdown`

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

## Class-based tasks

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
