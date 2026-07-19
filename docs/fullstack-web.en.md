# Fullstack web — SSR, WASM and server

The SDK talks to **tempestweb** in three ways, all fullstack and all
typed in Python — no template language, no hand-written JavaScript. Each
fits a different scenario:

| Project | Where Python runs | Build? | Best for |
|---------|-------------------|--------|----------|
| **1. SSR + HTMX** | On the server, per request | No | Server-rendered pages, SEO, progressive interactivity |
| **2. WASM SPA** | In the **browser** (Pyodide) | `tempestweb build --mode wasm` | Offline-first apps; server only serves files + API |
| **3. Server-mode** | On the server, live | `tempestweb build --mode server` | Reactive UI driven by the server over WebSocket/SSE |

Each section is a complete, runnable project. Start with 1 (the simplest)
and move up as you need to.

!!! info "The `[ssr]` extra"
    Everything here lives in the `[ssr]` extra:

    ```bash
    uv add "tempest-fastapi-sdk[ssr]"
    ```

    `tempestweb` is loaded on demand — `import tempest_fastapi_sdk` never
    requires the extra.

---

## Project 1 — SSR + HTMX (no build)

A fullstack task list rendered **on the server**: typed pages (`Page`),
persistence via `BaseRepository`, and locally-served HTMX to add/toggle/
delete without a full reload. Nothing is compiled — each request returns
HTML.

### The model and persistence

An SDK `BaseModel` and the `AsyncDatabaseManager`:

```python
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel


class TaskModel(BaseModel):
    """A single to-do item."""

    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean(), default=False, nullable=False)


db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
```

### The typed widgets

Each task becomes an `<li>` with buttons that fire HTMX. Every widget
accepts `tag=` and `attrs=`, so you emit semantic HTML and `hx-*`
attributes directly:

```python
from tempest_core import Button, Column, Row, Text, Widget


def task_widget(task_id: str, title: str, done: bool) -> Widget:
    """Render one task as an <li> with toggle + delete."""
    return Row(
        tag="li",
        attrs={"id": f"task-{task_id}"},
        children=[
            Button(
                label="✓" if done else "○",
                attrs={
                    "hx-post": f"/tasks/{task_id}/toggle",
                    "hx-target": f"#task-{task_id}",
                    "hx-swap": "outerHTML",
                },
            ),
            Text(content=title, tag="s" if done else "span"),
            Button(
                label="✕",
                attrs={
                    "hx-delete": f"/tasks/{task_id}",
                    "hx-target": f"#task-{task_id}",
                    "hx-swap": "outerHTML",
                },
            ),
        ],
    )
```

### The page

A typed `Page` declares its data as fields and implements `body()`. Here
an HTMX form (appends to the `<ul>`) plus the list:

```python
from tempest_fastapi_sdk.ssr import Page


class TasksPage(Page):
    """The full task-list document."""

    tasks: list[tuple[str, str, bool]]   # (id, title, done)

    def body(self) -> Widget:
        form = Column(
            tag="form",
            attrs={
                "hx-post": "/tasks",
                "hx-target": "#tasks",
                "hx-swap": "beforeend",
                "hx-on::after-request": "this.reset()",
            },
            children=[
                Text(
                    content="",
                    tag="input",
                    attrs={"name": "title", "placeholder": "New task", "required": "required"},
                ),
                Button(label="Add", attrs={"type": "submit"}),
            ],
        )
        items = [task_widget(i, t, d) for i, t, d in self.tasks]
        return Column(
            tag="main",
            children=[
                Text(content="Tasks", tag="h1"),
                form,
                Column(tag="ul", attrs={"id": "tasks"}, children=items),
            ],
        )
```

### The routes

`GET /` returns the **full document** (`htmx=True` injects the local
HTMX); each action returns just the **fragment** (`document=False`) that
HTMX swaps in place:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Form

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.ssr import html_response, make_htmx_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
app.include_router(make_htmx_router())   # serves /_ssr/htmx.js locally


@app.get("/")
async def index() -> object:
    async with db.get_session_context() as session:
        rows = await BaseRepository(session, model=TaskModel).list()
    page = TasksPage(
        title="Tasks",
        tasks=[(str(r.id), r.title, r.done) for r in rows],
    )
    return html_response(page, title="Tasks", htmx=True)


@app.post("/tasks")
async def create_task(title: str = Form(...)) -> object:
    async with db.get_session_context() as session:
        repo = BaseRepository(session, model=TaskModel)
        task = await repo.add(TaskModel(title=title))
        await session.commit()
        await session.refresh(task)
    return html_response(
        task_widget(str(task.id), task.title, task.done), document=False
    )


@app.post("/tasks/{task_id}/toggle")
async def toggle_task(task_id: UUID) -> object:
    async with db.get_session_context() as session:
        repo = BaseRepository(session, model=TaskModel)
        task = await repo.get_by_id(task_id)
        task.done = not task.done
        await repo.update(task)
        await session.commit()
    return html_response(
        task_widget(str(task.id), task.title, task.done), document=False
    )


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: UUID) -> object:
    async with db.get_session_context() as session:
        await BaseRepository(session, model=TaskModel).delete(task_id)
        await session.commit()
    return html_response(Text(content="", tag="span"), document=False)
```

Run it with `uvicorn app:app --reload` and open `http://127.0.0.1:8000`.

!!! check "What this project shows"
    - Typed **`Page`** + **`html_response`** (document and fragment).
    - **`make_htmx_router`** serving the bundled HTMX — no CDN, offline,
      CSP-friendly.
    - **`BaseRepository`** for the CRUD, all async.
    - Real interactivity (add/toggle/delete) **without writing any
      JavaScript**.

---

## Project 2 — WASM SPA (Python in the browser)

Now the same Python runs **in the browser** via Pyodide. You compile the
frontend with `tempestweb build --mode wasm` and the SDK serves the
static `dist/` with `make_web_app_router` — plus a FastAPI JSON API behind
it for the data.

### The tempestweb app (`web/app.py`)

The tempestweb contract is `make_state` + `view` (the same code runs in
both modes — the transport is chosen at build time):

```python
from __future__ import annotations

from dataclasses import dataclass

from tempest_core import App, Button, Column, Row, Style, Text, Widget
from tempest_core.style import Edge


@dataclass
class State:
    value: int = 0


def make_state() -> State:
    return State()


def view(app: App[State]) -> Widget:
    return Column(
        style=Style(gap=8.0, padding=Edge.all(16)),
        children=[
            Text(content=f"Count: {app.state.value}", key="label"),
            Row(
                style=Style(gap=4.0),
                children=[
                    Button(label="-", key="dec",
                           on_click=lambda: app.set_state(
                               lambda s: setattr(s, "value", s.value - 1))),
                    Button(label="+", key="inc",
                           on_click=lambda: app.set_state(
                               lambda s: setattr(s, "value", s.value + 1))),
                ],
            ),
        ],
    )
```

### The build

```bash
cd web
tempestweb build --mode wasm     # emits web/dist/wasm/ (index.html + bootstrap + wasm + sw)
```

The SDK does **not** build — building stays in the tempestweb CLI/CI
flow. The SDK only serves the finished `dist/`.

### The FastAPI host

`make_web_app_router` returns an `APIRouter` that serves the SPA with a
**history fallback** (unknown client-side route → `index.html`). Include
it **last**, so the API routes beat the catch-all:

```python
from fastapi import APIRouter, FastAPI

from tempest_fastapi_sdk.ssr import make_web_app_router

app = FastAPI()

# 1) Your JSON API — registered FIRST.
api = APIRouter(prefix="/api")


@api.get("/count")
async def count() -> dict[str, int]:
    return {"value": 42}


app.include_router(api)

# 2) The static SPA — registered LAST (root catch-all).
app.include_router(make_web_app_router("web/dist/wasm"))
```

Run it with `uvicorn app:app`. `GET /api/count` returns JSON; any other
route serves the SPA, which runs Python in the browser and calls your API.

!!! warning "Mount at the root, include last"
    The wasm artifact references `/sw.js` at the site root, so mount it at
    the app root. And because the router is a catch-all
    (`/{resource:path}`), include it **after** your API routers.

!!! tip "No magic in the serving"
    `index.html` and `sw.js` are always `no-cache`; other assets use
    `asset_cache_control`. Correct MIME for `.wasm`/`.mjs`/`.webmanifest`,
    `Service-Worker-Allowed: /` on the worker, traversal blocked, and
    **no imposed CSP** (Pyodide needs `wasm-unsafe-eval`) — pass
    `security_headers=` to add your own.

---

## Project 3 — Server-mode (live WebSocket/SSE)

The **same `web/app.py`** as Project 2, now compiled to run **on the
server**: the UI is driven live over WebSocket/SSE. You compile with
`--mode server` and mount with `build_web_app`.

### The build

```bash
cd web
tempestweb build --mode server   # emits web/dist/server/ (server.py + app.py + static)
```

### The FastAPI host

`build_web_app` loads the artifact's `app.py`, wires the tempestweb
server engine (`/ws` + `/sse` routes), serves the client under `/static`
and the shell at `/` — the same wiring the generated `server.py` does,
in-process. Since the server build references `/ws` at the root, it owns
the root; add your API routes **to that app**:

```python
from fastapi import APIRouter

from tempest_fastapi_sdk.ssr import build_web_app

# The tempestweb app is already a FastAPI — add your API to it.
app = build_web_app("web/dist/server", title="Counter")

api = APIRouter(prefix="/api")


@api.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


app.include_router(api)
```

Run it with `uvicorn app:app`. The browser opens `/`, connects to `/ws`,
and every click runs the `view` **on the server** — the tree is diffed
and sent back. `GET /api/health` still answers normally.

!!! info "Which mode?"
    - **WASM** — the client is self-contained (runs offline), the server
      is optional (just API + files). Heavier boot (downloads Pyodide).
    - **Server** — instant boot and state lives on the server (great for
      sensitive data or heavy logic), but needs a live connection.

    The best part: `web/app.py` is **identical** in both — just swap the
    build `--mode`.

---

## Recap

- **SSR + HTMX** — `Page` + `html_response` + `make_htmx_router`:
  server-rendered, no build, interactivity via local HTMX.
- **WASM SPA** — `tempestweb build --mode wasm` + `make_web_app_router`:
  Python in the browser, served as static with a history fallback + your
  API.
- **Server-mode** — `tempestweb build --mode server` + `build_web_app`:
  live UI over WebSocket/SSE, with your API on the same app.
- **`detect_build_mode(dir)`** tells a wasm `dist/` from a server one.
- The SDK only **serves** a finished build — compiling stays in
  tempestweb. Per-API details in [SSR](ssr.md).
