# Task panel — what is scheduled and what ran

Use to see in the panel what is scheduled on the broker and what the workers already ran, by wiring a `TaskPanelService`.

Part of the [Admin site](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin.md) recipe.

`TaskPanelService` wires the admin to what the workers already produce: the
**schedule** declared on the broker and the **runs** a `JobStore` persisted.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import TaskPanelService
from tempest_fastapi_sdk.tasks import JobStore, TaskQueue

from src.core.settings import settings
from src.db.models import JobModel, UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
tq: TaskQueue = TaskQueue.rabbitmq(settings.RABBITMQ_URL)
jobs: JobStore[JobModel] = JobStore(db, model=JobModel)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        tasks=TaskPanelService(queue=tq, job_store=jobs),
    )
)
```

That mounts three routes and a "Tasks" entry in the sidebar:

| Route | What it does |
| --- | --- |
| `GET /admin/tasks` | recent runs (filterable by status) + the declared schedule |
| `GET /admin/tasks/{job_id}` | one run: progress, stage, attempts, error |
| `POST /admin/tasks/{job_id}/cancel` | asks it to stop, while the status still allows it |

!!! warning "The panel does not show the broker's queue"
    It shows what is **persisted** (`JobStore` rows) and what is **declared**
    (the broker's registry). Queue depth is absent because TaskIQ exposes
    none — and a screen implying otherwise would be worse than no screen.

!!! tip "Either half is enough"
    `queue=` alone shows the schedule; `job_store=` alone shows the runs. A
    section with no source is not rendered, rather than rendering empty and
    implying there is nothing to see. Passing neither raises `ValueError` at
    construction.

!!! info "The `Schedule` column carries every declared trigger"
    A task may declare several triggers, and not every trigger is a cron:
    TaskIQ accepts `cron`, `interval` and `time` (one-shot) entries in the
    same list. The column shows one line per trigger, with the timezone
    beside the expression whenever the task declared one:

    | Declared | Shows as |
    | --- | --- |
    | `@tq.cron(daily(hour=2), cron_offset=CronOffsetEnum.BRASILIA)` | `0 2 * * *` `-03:00` |
    | `@tq.cron(daily(hour=2))` | `0 2 * * *` |
    | `@tq.interval(timedelta(seconds=30))` | `every 30s` |
    | `schedule=[{"time": when}]` | `once at 2026-01-01 12:00:00+00:00` |
    | `@tq.task` | `on demand` |

    Showing the offset is not decoration. A cron expression **reads as
    complete** without it, so nothing invites the reader to check — and
    `0 2 * * *` with `cron_offset=CronOffsetEnum.BRASILIA` fires at 05:00 UTC.
    Whoever opens the panel to learn when the backup runs would be three
    hours off, on the one screen whose only alternative source is the code.

    The panel shows what was **declared**, not TaskIQ's default: an
    expression with no offset appears with no offset, not as `(UTC)`.

!!! tip "Rendering the schedule yourself"
    `ScheduledTask.triggers` is the tuple of `TaskTrigger`, in registry
    order, and `TaskTrigger.cron_offset_label` formats the timezone
    (`"-03:00"` for a fixed offset, the IANA key for a named zone). The flat
    fields — `cron`, `interval_seconds`, `cron_offset`, `run_at` — summarize
    the first trigger of each kind, for callers that need only one. A
    schedule key the SDK does not model yet lands in `TaskTrigger.extra`
    rather than being dropped.

!!! note "Why there is no \"next run\" column"
    `pycron`, which arrives with TaskIQ, exposes `is_now` and `has_been` —
    and no `next()`. Computing the next run would mean sweeping candidate
    minutes on every render (up to ~44k iterations for a monthly cron) or
    taking a new dependency for one column. The panel shows the expression —
    with its offset beside it, because without one the expression is not
    legible.

For the paginated, filterable, exportable list the admin already knows how to
render, register the job model too — symmetric to the dead-letter panel:

```python
from tempest_fastapi_sdk import AdminSite
from tempest_fastapi_sdk.tasks import make_job_admin_model

from src.db.models import JobModel

site: AdminSite = AdminSite(title="Ops")
site.register(make_job_admin_model(JobModel))
```
