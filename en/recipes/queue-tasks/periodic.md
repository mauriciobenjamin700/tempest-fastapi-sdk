# Periodic tasks: `cron` and `interval`

Use to schedule periodic tasks on the same `TaskQueue`, with no separate scheduler in your code.

Part of the [Queues and Tasks](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks.md) recipe.

Scheduling is part of the same `TaskQueue` — no separate scheduler in your code.

!!! tip "Don't know cron? Use the enums and helpers (v0.94.0)"
    Nobody should hand-write `"0 9 * * MON-FRI"`. The
    `tempest_fastapi_sdk.tasks` module ships **`CronEnum`** (ready-made
    expressions), **`CronOffsetEnum`** (timezones by place, not digits),
    **`WeekdayEnum`** and **builder functions** (`daily`, `weekdays`,
    `hourly`, `every_n_minutes`, `weekly`, `weekends`, `monthly`). Each
    returns a plain cron string that drops straight into `@tq.cron(...)`.

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import CronEnum, CronOffsetEnum, WeekdayEnum, daily, weekdays

from src.tasks import tq


# Readable, no cron syntax:
@tq.cron(CronEnum.EVERY_WEEKDAY_9AM, cron_offset=CronOffsetEnum.BRASILIA)
async def daily_digest() -> None:
    ...


@tq.cron(daily(hour=9), cron_offset=CronOffsetEnum.BRASILIA)   # 09:00 BRT
async def other_digest() -> None:
    ...


@tq.cron(weekdays(hour=8, minute=30), cron_offset=CronOffsetEnum.BRASILIA)
async def morning_sync() -> None:
    ...


@tq.cron(CronEnum.EVERY_5_MINUTES)
async def heartbeat() -> None:
    ...
```

| To run… | Write |
| --- | --- |
| Every 5 min | `CronEnum.EVERY_5_MINUTES` or `every_n_minutes(5)` |
| Daily at 9am | `daily(hour=9)` |
| Weekdays at 8:30 | `weekdays(hour=8, minute=30)` |
| Every Monday | `weekly(WeekdayEnum.MON)` |
| First of the month | `monthly(day=1)` |
| In Brasília time | `cron_offset=CronOffsetEnum.BRASILIA` |

`CronOffsetEnum` covers Brazil's timezones by name — `BRASILIA` (-03:00),
`FERNANDO_DE_NORONHA` (-02:00), `MANAUS` (-04:00), `ACRE` (-05:00) — plus
`UTC`.

!!! info "Two ways to say a timezone, and what each one does"
    TaskIQ reads `cron_offset` two different ways
    (`taskiq/cli/scheduler/run.py`): a `timedelta` is **added** to the
    current time, while a `str` is handed to `ZoneInfo` as an **IANA key**.
    The SDK normalizes at declaration time, so you pick by what you need:

    | You write | Reaches the scheduler as | Follows DST? | Needs a tz database? |
    | --- | --- | --- | --- |
    | `CronOffsetEnum.BRASILIA` | `timedelta(hours=-3)` | no | no |
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
