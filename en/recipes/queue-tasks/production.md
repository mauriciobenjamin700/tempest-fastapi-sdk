# Workers, healthcheck and scheduler in production

Use to run the worker and scheduler in production, check they are alive and schedule from the lifespan with a single replica.

Part of the [Queues and Tasks](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks.md) recipe.

## Workers in production

The worker and the scheduler are separate processes pointing at the raw objects `TaskQueue` exposes. The TaskIQ CLI resolves `module:attribute` with a plain `getattr`, so **bind both to module-level names** — a dotted path does not resolve:

```python
# src/tasks.py — after registering the tasks
from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")

broker = tq.broker
scheduler = tq.scheduler
```

```bash
# consumes and executes the tasks
taskiq worker    src.tasks:broker

# a single scheduler process for the whole cluster
taskiq scheduler src.tasks:scheduler
```

`tq.broker` is the TaskIQ broker (it knows every registered task); `tq.scheduler` is the internal `TaskiqScheduler`. Both come out typed (`AsyncBroker` and `TaskiqScheduler`), so `broker = tq.broker` and `scheduler = tq.scheduler` need no annotation — and you import nothing from `taskiq` to get it.

!!! warning "`src.tasks:tq.broker` does not work"
    The CLI runs `getattr(module, "tq.broker")` and raises
    `AttributeError: module 'src.tasks' has no attribute 'tq.broker'` —
    the process never starts. The same holds for any dotted path
    (`tq.scheduler`, `scheduler.scheduler`). Hence the `broker = tq.broker`
    above.

## Worker and scheduler healthcheck

The healthcheck `TaskQueue` already had (`health_check`) only asks whether the
broker is up. A worker stuck on a task that never finishes, or a scheduler
whose loop stopped, still has a perfectly reachable broker — and the container
stays `healthy`. A **heartbeat** closes that gap: the process proves it is
doing its job by rewriting a file with the current time, and the healthcheck
asks how many seconds old that file is.

Turn it on from the environment — `from_settings` calls `enable_heartbeat`
when either file is set:

```python
# src/tasks.py
from tempest_fastapi_sdk.settings import TaskIQSettings
from tempest_fastapi_sdk.tasks import TaskQueue


class Settings(TaskIQSettings):
    """Worker and scheduler configuration."""


tq: TaskQueue = TaskQueue.from_settings(Settings())

broker = tq.broker
scheduler = tq.scheduler
```

| Variable | Default | What it decides |
| --- | --- | --- |
| `TASKIQ_WORKER_HEARTBEAT_FILE` | empty | File each **worker** process rewrites every period. |
| `TASKIQ_SCHEDULER_HEARTBEAT_FILE` | empty | File the **scheduler** rewrites every period, from inside its loop. |
| `TASKIQ_HEARTBEAT_SECONDS` | `30` | Heartbeat period. |

Both empty (the default) leaves everything as it was: no extra loop, no file.
Each process only writes the file that belongs to it, so both variables can go
on both containers (as the compose below does).

Or explicitly, without settings:

```python
from datetime import timedelta

from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.redis("redis://localhost:6379/0")
tq.enable_heartbeat(
    worker_file="/tmp/worker.heartbeat",
    scheduler_file="/tmp/scheduler.heartbeat",
    every=timedelta(seconds=30),
)

broker = tq.broker
scheduler = tq.scheduler
```

### What each file proves

- **Worker** — each worker process starts, on TaskIQ's startup event, an
  asyncio task that rewrites the file every `every` (`HeartbeatLoop`), and
  cancels it on shutdown. The beat is **local**: it goes through neither the
  broker nor the scheduler, so each replica keeps its own file fresh whichever
  replica the broker picks for each task. A fresh file proves the process is
  alive and its event loop is not blocked — a task that blocks the loop with
  synchronous work stops the beat. A task that awaits forever without
  blocking the loop does not; that is a task-timeout problem.
- **Scheduler** — TaskIQ's loop wakes every second; when the heartbeat is
  due it calls the `pre_send` of the schedule source `enable_heartbeat`
  attaches to `tq.scheduler`. `pre_send` writes the file and **cancels** the
  send (`ScheduledTaskCancelledError`): nothing is published to the broker. A
  fresh file proves the loop is ticking — not that the scheduler can still
  publish.

Measured with real `taskiq worker` + `taskiq scheduler` over Redis, in
Compose, with `TASKIQ_HEARTBEAT_SECONDS=5` and **two** worker replicas: each
replica and the scheduler rewrote its file every 5 s; the `taskiq` stream in
Redis stayed at `XLEN 0` (the heartbeat publishes nothing); with the
scheduler stopped, both replicas stayed `healthy` for over 100 s.

!!! note "With `--workers N`, the file proves at least one process"
    `taskiq worker` starts several child processes (`--workers`), and each
    has its own heartbeat loop writing to the container's one file. One stuck
    child with another alive keeps the file fresh.

!!! info "Two TaskIQ `INFO` lines per scheduler beat"
    TaskIQ's scheduler logs `Sending task tempest:heartbeat ...` and
    `Scheduled task tempest:heartbeat has been cancelled.` every period. It is
    the loop saying it went by; no message leaves.

!!! check "A failed write never takes anything down"
    The files are written atomically (temporary file + `os.replace`), with
    mode `0644` so a checker running as another user on a shared volume can
    read them, and the healthcheck never reads a half-written file. When a
    write fails (read-only filesystem, missing directory), **one** `WARNING`
    is logged and the worker and the scheduler carry on; the stale file is the
    signal.

### The checker

```bash
python -m tempest_fastapi_sdk.tasks.heartbeat /tmp/worker.heartbeat --max-age 90
```

It exits `0` when the file exists and the time recorded in it is less than
`--max-age` seconds old, and `1` otherwise — stale, missing, unreadable, more
than 5 s **in the future** (`HEARTBEAT_FUTURE_TOLERANCE_SECONDS`: a broken
clock or a forged file must not look healthy for hours) or bad arguments,
including a `--max-age` that is not a finite number above zero; never `2`,
which Docker reserves. It prints one line with the reason:

```text
healthy: heartbeat /tmp/worker.heartbeat is 0.2s old (max 90s)
unhealthy: heartbeat /tmp/worker.heartbeat is 10.4s old (max 6s)
```

It only uses the standard library: it runs with the base package installed,
without the `[tasks]` or `[cli]` extras (measured on a `python:3.13-slim`
image with only the base wheel). In code, the same test is
`is_heartbeat_fresh(path, max_age)` and `heartbeat_age(path)`.

!!! tip "`--max-age` of about three periods"
    With `TASKIQ_HEARTBEAT_SECONDS=30`, use `--max-age 90`: one late heartbeat
    (busy worker, GC, clock) does not become a false alarm.

### In `docker-compose.yml`

```yaml
x-tasks-env: &tasks-env
  TASKIQ_BROKER_URL: redis://redis:6379/0
  TASKIQ_WORKER_HEARTBEAT_FILE: /tmp/worker.heartbeat
  TASKIQ_SCHEDULER_HEARTBEAT_FILE: /tmp/scheduler.heartbeat
  TASKIQ_HEARTBEAT_SECONDS: "30"

services:
  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 10

  scheduler:
    build: .
    command: ["taskiq", "scheduler", "src.tasks:scheduler"]
    environment: *tasks-env
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-m", "tempest_fastapi_sdk.tasks.heartbeat",
             "/tmp/scheduler.heartbeat", "--max-age", "90"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  worker:
    build: .
    command: ["taskiq", "worker", "src.tasks:broker"]
    environment: *tasks-env
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-m", "tempest_fastapi_sdk.tasks.heartbeat",
             "/tmp/worker.heartbeat", "--max-age", "90"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  api:
    build: .
    command: ["python", "main.py"]
    depends_on:
      worker:
        condition: service_healthy
```

`start_period` covers startup (importing the service before the first beat).
Each check takes about a second (it imports the SDK), hence `timeout: 10s`.

!!! danger "Plain Docker does **not** restart an `unhealthy` container"
    The healthcheck **marks** the container (`docker ps` shows `(unhealthy)`);
    the `restart:` policy only acts when the process **exits**. Measured with
    Docker 29.6.1 and Compose 5.3.0: with the worker's processes frozen
    (`SIGSTOP`), the replica turned `unhealthy` in ~20 s and stayed that way,
    `RestartCount` at `0`, despite `restart: unless-stopped` — the other
    replica stayed `healthy`. What the healthcheck feeds is:

    - `depends_on: condition: service_healthy` — the `api` above only starts
      once the worker has proven it is alive;
    - `docker ps` / `docker inspect` — the state is visible to whoever
      operates it;
    - your monitoring (alerting on `unhealthy` containers), or an orchestrator
      that acts on it — Swarm and Kubernetes (through a `livenessProbe`)
      replace the container; Docker alone does not.

## The scheduler in the FastAPI lifespan — with one replica scheduling

A small deployment does not want a second process just for cron.
`tq.lifespan(scheduler=True)` runs the scheduler inside the web process, and
plugs straight into FastAPI:

```python
# src/api/app.py

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.settings import settings
from src.tasks import tq


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Open the queue and run periodic tasks in this process."""
    async with tq.lifespan(scheduler=True):
        yield


app: FastAPI = FastAPI(title=settings.TITLE, lifespan=lifespan)
```

!!! danger "A raw `start_scheduler()` fires once per replica"
    The scheduler loop does not know how many processes run it. With
    three uvicorn replicas, `@tq.cron(every_n_minutes(1))` fires **three
    times a minute** — and nothing raises, nothing is logged. The effect
    arrives as duplicated work: the sweep that expires charges expires
    them three times, the backup runs three times, the e-mail goes out
    three times. That `three` is measured, not reasoned — it is the
    second line of the output below.

    `scheduler=True` does not have that problem because it does not run
    the loop directly: one replica holds a **lease** and runs, the others
    stand by and take over when the lease lapses. Three lifespans open
    against the same lease, with the loops counted in both modes:

    ```text
    scheduler=True         loops running: 1   replica-1=1  replica-2=0  replica-3=0
    scheduler='unlocked'   loops running: 3   replica-1=1  replica-2=1  replica-3=1
    ```

    The `3` on the second line is the defect and the `1` on the first is
    the lease fixing it. `tests/tasks/test_scheduler_lease.py` pins both
    counts — the first parametrized over 2, 3 and 5 replicas — and a
    separate case waits for the leader to leave and confirms the standby
    takes over on the first `acquire` that begins after the leader
    releases the lease, without waiting for the TTL to lapse.

### Where the lease comes from

The lease is a Redis lock, and `TaskQueue` derives its URL from wherever one
already is:

* `TaskQueue.from_settings(settings)` or `TaskQueue.redis(url)` → the broker
  itself.
* `TaskQueue.from_settings(settings)` with an AMQP broker and
  `TASKIQ_RESULT_BACKEND_URL` pointing at Redis → the result backend.
* A broker built by hand → none. Pass `scheduler_lock=`.

With no derivable lease, `scheduler=True` **refuses at startup** rather than
scheduling N times:

```text
ValueError: lifespan(scheduler=True) needs a lease so only one replica runs
the schedule, and none could be derived from this broker. Pass
scheduler_lock=RedisSchedulerLock.from_url(...), build the queue with
TaskQueue.from_settings(...) or TaskQueue.redis(...), or ask for
scheduler="unlocked" if you accept every replica firing every schedule.
```

For coordination of your own — etcd, a Postgres advisory lock — implement
`SchedulerLock`, which is three calls and knows nothing about Redis:

```python
from tempest_fastapi_sdk.tasks import RedisSchedulerLock, SchedulerLock

lease: SchedulerLock = RedisSchedulerLock.from_url(
    "redis://localhost:6379/0",
    name="alofans:scheduler",
    ttl_seconds=30.0,
)
```

!!! warning "Two services on one Redis need different `name`s"
    The default is `"tempest:tasks:scheduler"`. Two services sharing the
    instance contend for the same key, and one of them **never**
    schedules.

!!! warning "A clock that jumps shortens the lease"
    Redis stores a key's expiry as an absolute Unix timestamp and checks
    the computer clock even while the instance is running — the
    [`EXPIRE` documentation](https://redis.io/docs/latest/commands/expire/#expires-and-persistence)
    gives the example: a key with a 1000 s TTL and a clock moved 2000 s
    forward expires immediately. For the scheduler lease, that means **a
    forward jump of the Redis server's clock larger than the remaining TTL
    drops the lease** early, and another replica can acquire it.

    This is not a lab hypothetical: on a WSL2 machine we measured the wall
    clock jumping +3.6 s twice a minute, and with a 2 s lease
    `tests/tasks/test_scheduler_lease.py` saw two loops running at once
    (the test now pins fakeredis's clock so it measures the election, not
    the host). NTP *step* corrections and a VM resumed after a pause are
    the other common sources.

    The default `ttl_seconds=30.0`, renewed every third of it, always
    keeps at least ~20 s of TTL left — that is the size of jump it
    absorbs. In production:

    * keep the Redis host's clock synced by *slewing* (chrony/NTP), not
      *stepping*;
    * don't lower the TTL to a few seconds to "take over faster" — a
      leader that exits releases the lease on shutdown, so a planned
      handover already does not wait for the TTL;
    * keep scheduled tasks idempotent: the lease reduces double firing, it
      does not make it impossible.

!!! info "`scheduler="unlocked"` exists, and has to be typed"
    A service that genuinely runs a single replica passes
    `scheduler="unlocked"` and skips the lease. That is a choice, which
    is why it is not the default: the default is `scheduler=False` (broker
    only, nothing scheduled), and `True` is the guarded way to turn the
    scheduler on. A service that forgot to ask schedules nothing — not
    everything N times.
