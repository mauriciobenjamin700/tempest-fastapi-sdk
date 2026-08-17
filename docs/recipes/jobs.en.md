# Jobs — long work with a status

A queue hands the call to a worker. It answers none of what the person
in front of the screen is asking:

- has anything picked this up, or is it still queued?
- is it running **right now**?
- did it finish? what did it produce?
- if it stopped, why — in their language, not as a traceback.

TaskIQ's `AsyncResultBackend` comes close, but it is keyed by task id,
holds the function's return value, and is not a table the application
queries, paginates or shows in an admin. What the interface wants is a
**row**.

This is the symmetric half of the [outbox](outbox.md): there it is *a
message to publish*, here it is *work to execute*.

## 1. The table

Subclass `BaseJobModel` and pick a `__tablename__` — exactly like
`BaseOutboxModel`:

```python
# src/db/models/job.py
from tempest_fastapi_sdk.tasks import BaseJobModel


class JobModel(BaseJobModel):
    """One unit of long-running work in this application."""

    __tablename__ = "jobs"
```

Three lines, because the rest comes with it: `kind`, `status`, `params`,
`payload`, `result_id`, `error`, `attempts`, `max_attempts`,
`started_at`, `finished_at` — plus the `id` / `is_active` /
`created_at` / `updated_at` every `BaseModel` has.

| Column | What for |
| --- | --- |
| `kind` | which work this is; the worker branches on it and the interface filters by it |
| `status` | `queued` → `running` → `done` / `failed`, indexed |
| `params` | small input, as JSON |
| `payload` | large input — the file a broker should not be carrying |
| `result_id` | the row the work produced, so the screen links straight to it |
| `error` | why it stopped, written for the user |

## 2. Enqueue

`JobStore` takes the `AsyncDatabaseManager`, not a session: every call
opens and closes its own, because its users are a handler that enqueues,
a worker that grinds for minutes, and a screen asking every couple of
seconds — none of them should hold a session across that.

```python
# src/api/routers/extraction.py
from uuid import UUID

from fastapi import APIRouter, UploadFile

from src.db.models.job import JobModel
from src.api.dependencies.resources import db
from src.tasks import extract_document

from tempest_fastapi_sdk.tasks import JobStore

router = APIRouter(prefix="/api/extraction")
store: JobStore[JobModel] = JobStore(db, model=JobModel, stale_after=300.0)


@router.post("/")
async def start_extraction(file: UploadFile) -> dict[str, UUID]:
    """Accept the document and return the job id to follow."""
    job = await store.enqueue(
        "extract",
        params={"filename": file.filename or "unnamed.pdf"},
        payload=await file.read(),
    )
    await extract_document.enqueue(str(job.id))
    return {"job_id": job.id}
```

That order is deliberate: **write the row, then send the task**. The row
is what the interface reads, and it has to exist before the worker can
claim it.

## 3. The worker

```python
# src/tasks/__init__.py
from uuid import UUID, uuid4

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore, TaskQueue


class JobModel(BaseJobModel):
    """One unit of long-running work in this application."""

    __tablename__ = "jobs"


class UnsupportedFormat(Exception):
    """The uploaded file is not something we know how to read."""


async def read_tender(payload: bytes | None) -> UUID:
    """The real work; returns the id of what it produced.

    Args:
        payload (bytes | None): The document claimed with the job.

    Returns:
        UUID: The id of the generated draft.

    Raises:
        UnsupportedFormat: When the document cannot be read.
    """
    if not payload:
        raise UnsupportedFormat("empty file")
    return uuid4()


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/", resources=[db])
store: JobStore[JobModel] = JobStore(db, model=JobModel, stale_after=300.0)


@tq.task
async def extract_document(job_id: str) -> None:
    """Claim the job, do the work, close the row.

    Args:
        job_id (str): The id the route sent along with the task.
    """
    job = await store.claim(UUID(job_id))
    if job is None:
        return
    try:
        draft_id = await read_tender(job.payload)
    except UnsupportedFormat as exc:
        await store.fail(job.id, f"I could not read the file: {exc}")
    else:
        await store.succeed(job.id, result_id=draft_id)
```

Three things happening there, each for a reason:

- **`claim` is what separates "queued" from "running".** Without it the
  interface cannot tell "the worker is busy" from "nobody picked it
  up" — which is the exact question when something takes a while.
- **`claim` returns `None` when the job is not yours** (someone else
  claimed it, or the id does not exist). It is a conditional `UPDATE`, so
  two workers racing for one id cannot both win: one sees a row change,
  the other does not.
- **`succeed` / `fail` drop the `payload`.** Without that, the table of
  finished jobs becomes a pile of documents.

!!! warning "Do not hold the session across the work"
    `claim` already returned the `payload`; from there the worker works
    with **no session open** and only comes back to close the row. A
    transaction that reads first and writes minutes later is the case no
    `busy_timeout` can rescue — see
    [Database](database.md#sqlite-with-a-worker-wal-and-the-busy-timeout).

## 4. The screen asking "is it done yet?"

```python
# src/ui/pages/extraction.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore


class JobModel(BaseJobModel):
    """One unit of long-running work in this application."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def show_progress(job_id: UUID) -> list[str]:
    """Follow the job until it finishes.

    Args:
        job_id (UUID): The job the screen is watching.

    Returns:
        list[str]: Every status the job went through.
    """
    seen: list[str] = []
    async for job in store.watch(job_id, interval=2.0):
        seen.append(job.status)
    return seen
```

`watch` yields the job on **every status change**, until a terminal
state, and then ends. The current status comes out immediately, so a
caller subscribing after the job already finished still gets exactly one
value.

The detail this helper exists to stop you getting wrong: **no session is
held between ticks**. Each poll opens and closes its own, so the worker
writing to the same database is never blocked by the screen watching it.

`timeout=` gives up with `TimeoutError` instead of waiting forever.

## 5. The worker that died holding the job

A `running` row nobody will ever close is the failure a queue cannot
see: the task is gone, the row is not. `reclaim_stale()` readmits it:

```python
# src/tasks/__init__.py
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore, TaskQueue


class JobModel(BaseJobModel):
    """One unit of long-running work in this application."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/", resources=[db])
store: JobStore[JobModel] = JobStore(db, model=JobModel, stale_after=300.0)


@tq.interval(seconds=60)
async def reclaim_jobs() -> None:
    """Requeue what a dead worker left in RUNNING."""
    await store.reclaim_stale()
```

Rows whose `started_at` is older than `stale_after` go back to `queued` —
unless they already spent their `max_attempts`, in which case they are
closed as `failed`. Without that budget, a job that kills its worker
would be readmitted forever.

!!! info "Without `stale_after`, the method refuses"
    `JobStore(db, model=JobModel)` with no `stale_after` raises
    `RuntimeError` from `reclaim_stale()` rather than guessing a
    threshold.

## 6. Listing

```python
# src/services/extraction.py
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStatus, JobStore


class JobModel(BaseJobModel):
    """One unit of long-running work in this application."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def dashboard() -> tuple[list[JobModel], list[JobModel]]:
    """Read what the progress screen shows.

    Returns:
        tuple[list[JobModel], list[JobModel]]: The recent jobs and the
        ones running right now.
    """
    recent = await store.list_recent(kind="extract", limit=20)
    running = await store.list_recent(status=JobStatus.RUNNING)
    return recent, running
```

Returns `[]` when nothing matches — "no jobs yet" is a successful
answer, not a 404.

## 7. Cancelling

The user clicked "cancel". Nothing in TaskIQ — or in any broker the SDK
speaks — offers "kill the task with this id": once it is running inside the
worker process, only that process can stop it. So cancellation is
**cooperative**: the request writes `cancelled` and answers immediately; the
worker reads that status at agreed checkpoints and gives up.

```python
# src/services/extraction.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import BaseJobModel, JobStore


class JobModel(BaseJobModel):
    """A unit of long-running work in this application."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def cancel(job_id: UUID) -> bool:
    """Ask the job to stop.

    Args:
        job_id (UUID): The job to cancel.

    Returns:
        bool: True when there was something to stop.
    """
    job: JobModel | None = await store.cancel(job_id, reason="cancelled by the user")
    return job is not None
```

!!! tip "Idempotent on purpose"
    `cancel()` returns `None` — rather than raising — when there is nothing
    to stop: an unknown id, a job already done, already failed, or already
    cancelled. Double-clicking, or clicking just as the job finished on its
    own, is not an error.

### The worker gives up

`run_cancellable` is the checkpoint that runs **during** the work rather
than between steps. It races the coroutine against a predicate polled on an
interval, and when the predicate says stop, the coroutine is cancelled for
real — an in-flight HTTP request is aborted and the worker is free within
the poll interval, instead of finishing a call whose result nobody wants.

```python
# src/tasks/extract.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import (
    BaseJobModel,
    JobStore,
    StageInterruptedError,
    run_cancellable,
)


class JobModel(BaseJobModel):
    """A unit of long-running work in this application."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def summarize(text: str) -> str:
    """Genuinely long work (a network call, cancellable).

    Args:
        text (str): The text to summarize.

    Returns:
        str: The summary.
    """
    return text[:100]


async def run(job_id: UUID) -> None:
    """Run the job, giving up if it is cancelled midway.

    Args:
        job_id (UUID): The job to run.
    """
    job: JobModel | None = await store.claim(job_id)
    if job is None:
        return

    try:
        summary: str = await run_cancellable(
            summarize("a long text"),
            interrupted=store.cancellation_watch(job_id),
        )
    except StageInterruptedError:
        return

    await store.succeed(job_id)
    print(summary)
```

!!! danger "Only works on genuinely cancellable awaits"
    Work handed to `asyncio.to_thread` is **not** cancellable: cancelling
    the coroutine abandons the wrapper while the thread runs on to
    completion, still holding the CPU and still competing with the next
    job. For that shape — local inference, say — check between steps, and
    check again before writing the result.

!!! info "`succeed` refuses to land on top of a cancellation"
    A worker that raced past its last checkpoint still cannot overwrite the
    row: `succeed()`/`fail()` raise `JobCancelledError`, a subclass of
    `JobAlreadyFinishedError`. The two are distinct on purpose — a plain
    `JobAlreadyFinishedError` says two workers believe the job is theirs,
    while this one says the system did exactly what it was told. Log it and
    move on; do not alert.

!!! warning "`cancelled` is terminal, but it is not a failure"
    It joins `TERMINAL_JOB_STATUSES` (the poll stops, the `payload` is
    dropped), but nothing went wrong. An interface that highlights `failed`
    should leave this one alone, and an alert that pages on failures should
    not fire.

## Errors

| Exception | When |
| --- | --- |
| `JobNotFoundError` | the id does not exist (`get`, `succeed`, `fail`, `watch`) |
| `JobAlreadyFinishedError` | closing a job that is already terminal — two workers believe it is theirs |
| `JobCancelledError` | closing a job the user cancelled midway; a subclass of the above, so a worker can tell "we did as told" from "the concurrency is wrong" |
| `StageInterruptedError` | `run_cancellable` saw the cancellation; not a failure, the handler just returns |

They are `LookupError` / `RuntimeError`, not `AppException`: the store
runs in the worker as often as in a request, and a worker has no HTTP
status to answer with. Translate at the boundary with
[`not_found_exception(...)`](openapi-errors.md#the-factory-not_found_exception-conflict_exception).

**Recap:** subclass `BaseJobModel` and get the table; `enqueue` writes
the row before the task leaves; `claim` separates "queued" from
"running" and is safe under contention; `succeed`/`fail` close the row
and drop the payload; `watch` is the poll with no session left hanging;
`reclaim_stale` frees what a dead worker left stuck; `cancel` +
`run_cancellable` stop work already running, cooperatively, because
there is no other way.
