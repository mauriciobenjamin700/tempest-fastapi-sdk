# tempestweb frontend + tempest-fastapi-sdk backend

**What it solves:** you want a rich Python frontend (tempestweb) talking to a
FastAPI SDK backend — data over typed HTTP, no hand-written JavaScript, no
duplicated types.

**When to reach for it:** you already have (or want) an SDK API and need a UI
that consumes it; or you're building a fullstack app entirely in Python.

If you don't yet know **how to host** the compiled frontend, read
[Fullstack web](../fullstack-web.md) and the
[Serve a build](../ssr.md#serve-a-compiled-tempestweb-build) section first —
this recipe is about the **glue**: the frontend calling the backend.

## The setup

The simplest path is **same origin**: the SDK backend serves the compiled
frontend *and* exposes the API under `/api`. No CORS, no separate host.

```text
browser ──GET /──────────────▶ SPA (make_web_app_router)
        ──GET/POST /api/… ───▶ JSON API (SDK routers)
```

The tempestweb frontend runs in the browser (WASM) or on the server
(server-mode); either way it calls the API with tempestweb's **typed** HTTP
client.

## The backend (SDK)

!!! info "Installation"
    The API and the HTTP primitives ship with `tempest-fastapi-sdk`. Serving
    the frontend build with `make_web_app_router` needs the `[ssr]` extra —
    `uv add "tempest-fastapi-sdk[ssr]"` (pulls in `tempestweb`).

A task API with `BaseRepository`, `IdempotencyMiddleware` to deduplicate
POSTs, and the frontend build served by `make_web_app_router` (included
**last**):

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel as Schema
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    IdempotencyMiddleware,
    MemoryIdempotencyStore,
)
from tempest_fastapi_sdk.ssr import make_web_app_router


class TaskModel(BaseModel):
    __tablename__ = "tasks"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean(), default=False, nullable=False)


class TaskIn(Schema):
    title: str


class TaskOut(Schema):
    id: str
    title: str
    done: bool


db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    IdempotencyMiddleware,
    store=MemoryIdempotencyStore(),   # Redis in production
    ttl_seconds=24 * 3600,
)

api = APIRouter(prefix="/api")


@api.get("/tasks")
async def list_tasks() -> list[TaskOut]:
    async with db.get_session_context() as session:
        rows = await BaseRepository(session, model=TaskModel).list()
    return [TaskOut(id=str(r.id), title=r.title, done=r.done) for r in rows]


@api.post("/tasks")
async def create_task(payload: TaskIn) -> TaskOut:
    async with db.get_session_context() as session:
        repo = BaseRepository(session, model=TaskModel)
        task = await repo.add(TaskModel(title=payload.title))
        await session.commit()
        await session.refresh(task)
    return TaskOut(id=str(task.id), title=task.title, done=task.done)


app.include_router(api)                              # 1) API first
app.include_router(make_web_app_router("web/dist/wasm"))  # 2) frontend last
```

## The frontend (tempestweb)

The tempestweb `web/app.py` (the `make_state` + `view` contract). The handler
is `async` and calls the API with `tempestweb.native.http.request` — a typed
client that returns an `HttpResponse` (`status`, `ok`, `json_body`, …):

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tempest_core import App, Button, Column, Text, Widget
from tempestweb.native import http


@dataclass
class State:
    tasks: list[dict[str, Any]] = field(default_factory=list)
    loading: bool = False


def make_state() -> State:
    return State()


def view(app: App[State]) -> Widget:
    async def load() -> None:
        app.set_state(lambda s: setattr(s, "loading", True))
        res = await http.request("GET", "/api/tasks")
        app.set_state(
            lambda s: (
                setattr(s, "tasks", res.json_body or []),
                setattr(s, "loading", False),
            )
        )

    async def add() -> None:
        # An Idempotency-Key makes the POST safe to retry / replay offline.
        await http.request(
            "POST",
            "/api/tasks",
            json={"title": "New task"},
            idempotency_key=http.generate_idempotency_key(),
        )
        await load()

    return Column(
        children=[
            Button(label="Reload", on_click=load, key="reload"),
            Button(label="Add", on_click=add, key="add"),
            Text(
                content="Loading…" if app.state.loading
                else f"{len(app.state.tasks)} task(s)",
                key="status",
            ),
            *[
                Text(content=t["title"], key=t["id"])
                for t in app.state.tasks
            ],
        ],
    )
```

Build the frontend and the backend serves everything:

```bash
cd web && tempestweb build --mode wasm     # emits web/dist/wasm/
cd .. && uvicorn app:app                    # serves the SPA at / and the API at /api
```

!!! tip "`async` handlers in the view"
    An `on_click` can be `async`: await the request and call
    `app.set_state(...)` once the data arrives — the runtime reschedules the
    render. That's the pattern for I/O in the frontend.

## End-to-end idempotency

Here the two halves click together: the frontend's
`request(..., idempotency_key=...)` sends the **`Idempotency-Key`** header, and
the backend's `IdempotencyMiddleware` **deduplicates** — once the response has
completed once, a retry (or an offline replay) gets the same response, without
creating a duplicate task.

```python
# frontend: generate the key once, reuse it across attempts
key = http.generate_idempotency_key()
await http.request("POST", "/api/tasks", json={"title": "X"}, idempotency_key=key)
```

See [Idempotency](idempotency.md) for the server side (TTL, Redis store, which
methods are covered).

## Typed retry

Network flakiness is normal in a client. `RetryOptions` turns on exponential
backoff for safe requests (idempotent methods, or any method carrying an
`idempotency_key`):

```python
from tempestweb.native.http import RetryOptions

res = await http.request(
    "GET",
    "/api/tasks",
    retry=RetryOptions(attempts=4, base_delay=0.2, factor=2.0),
)
```

## CORS: same origin vs. separate origin

- **Same origin** (the backend serves the frontend, as above) → **no CORS
  needed**. `/api/...` and the SPA come from the same host.
- **Separate origins** (e.g. `tempestweb dev` on one host, the API on
  another) → the browser requires CORS. Enable it on the backend:

  ```python
  from tempest_fastapi_sdk import CORSSettings, apply_cors

  apply_cors(app, CORSSettings(allow_origins=["http://localhost:5173"]))
  ```

!!! info "Prefer same origin in production"
    Serving the frontend from the backend (same origin) removes CORS,
    simplifies cookies/sessions and closes one more port. Use separate
    origins only in the dev loop.

## Recap

- The tempestweb frontend calls the SDK API with **`tempestweb.native.http`**
  (`request`/`upload`/`poll` → typed `HttpResponse`).
- Frontend **`Idempotency-Key`** + backend **`IdempotencyMiddleware`** = a POST
  safe to retry/replay, without duplicating.
- **`RetryOptions`** adds exponential backoff to safe requests.
- **Same origin** (backend serves the SPA via `make_web_app_router`) needs no
  CORS; for separate origins use `apply_cors`.
- Hosting the build: [Fullstack web](../fullstack-web.md) and [SSR](../ssr.md).
