# Frontend tempestweb + backend tempest-fastapi-sdk

**Qual problema resolve:** você quer um frontend rico em Python (tempestweb)
falando com um backend FastAPI do SDK — dados via HTTP tipado, sem escrever
JavaScript nem duplicar tipos.

**Quando recorrer:** já tem (ou quer) uma API do SDK e precisa de uma UI que
a consuma; ou está montando um app fullstack todo em Python.

Se você ainda não sabe **como hospedar** o frontend compilado, leia antes
[Fullstack web](../fullstack-web.md) e a seção
[Servir um build](../ssr.md#servir-um-build-compilado-do-tempestweb) —
esta receita foca no **glue**: o frontend chamando o backend.

## O cenário

O caminho mais simples é **mesma origem**: o backend do SDK serve o frontend
compilado *e* expõe a API sob `/api`. Sem CORS, sem host separado.

```text
navegador ──GET /──────────────▶ SPA (make_web_app_router)
          ──GET/POST /api/… ───▶ API JSON (routers do SDK)
```

O frontend tempestweb roda no browser (WASM) ou no servidor (server-mode);
nos dois casos ele chama a API com o cliente HTTP **tipado** do tempestweb.

## O backend (SDK)

!!! info "Instalação"
    A API e os primitivos HTTP já vêm com `tempest-fastapi-sdk`. Servir o
    build do frontend com `make_web_app_router` depende do extra `[ssr]` —
    `uv add "tempest-fastapi-sdk[ssr]"` (traz `tempestweb`).

Uma API de tarefas com `BaseRepository`, `IdempotencyMiddleware` para
deduplicar POSTs, e o build do frontend servido por `make_web_app_router`
(incluído **por último**):

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
    store=MemoryIdempotencyStore(),   # Redis em produção
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


app.include_router(api)                              # 1) API primeiro
app.include_router(make_web_app_router("web/dist/wasm"))  # 2) frontend por último
```

## O frontend (tempestweb)

O `web/app.py` do tempestweb (contrato `make_state` + `view`). O handler é
`async` e chama a API com `tempestweb.native.http.request` — um cliente
tipado que devolve um `HttpResponse` (`status`, `ok`, `json_body`, …):

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
        # Idempotency-Key torna o POST seguro pra retry/replay offline.
        await http.request(
            "POST",
            "/api/tasks",
            json={"title": "Nova tarefa"},
            idempotency_key=http.generate_idempotency_key(),
        )
        await load()

    return Column(
        children=[
            Button(label="Recarregar", on_click=load, key="reload"),
            Button(label="Adicionar", on_click=add, key="add"),
            Text(
                content="Carregando…" if app.state.loading
                else f"{len(app.state.tasks)} tarefa(s)",
                key="status",
            ),
            *[
                Text(content=t["title"], key=t["id"])
                for t in app.state.tasks
            ],
        ],
    )
```

Compile o frontend e o backend serve tudo:

```bash
cd web && tempestweb build --mode wasm     # gera web/dist/wasm/
cd .. && uvicorn app:app                    # serve SPA em / e API em /api
```

!!! tip "Handlers `async` no view"
    Um `on_click` pode ser `async`: aguarde a request e chame
    `app.set_state(...)` quando os dados chegarem — o runtime reagenda o
    render. É o padrão para I/O no frontend.

## Idempotência de ponta a ponta

Aqui as duas metades se encaixam: o `request(..., idempotency_key=...)` do
frontend manda o header **`Idempotency-Key`**, e o `IdempotencyMiddleware`
do backend **deduplica** — se a resposta já completou uma vez, um retry (ou
replay offline) recebe a mesma resposta, sem criar tarefa duplicada.

```python
# frontend: gera a chave uma vez, reusa em cada tentativa
key = http.generate_idempotency_key()
await http.request("POST", "/api/tasks", json={"title": "X"}, idempotency_key=key)
```

Veja [Idempotência](idempotency.md) para o lado do servidor (TTL, store
Redis, quais métodos são cobertos).

## Retry tipado

Instabilidade de rede é normal num cliente. `RetryOptions` liga backoff
exponencial nas requests seguras (métodos idempotentes, ou qualquer método
com `idempotency_key`):

```python
from tempestweb.native.http import RetryOptions

res = await http.request(
    "GET",
    "/api/tasks",
    retry=RetryOptions(attempts=4, base_delay=0.2, factor=2.0),
)
```

## CORS: mesma origem vs. origem separada

- **Mesma origem** (o backend serve o frontend, como acima) → **não precisa
  de CORS**. `/api/...` e a SPA vêm do mesmo host.
- **Origens separadas** (ex.: `tempestweb dev` num host, a API noutro) → o
  browser exige CORS. Ligue no backend:

  ```python
  from tempest_fastapi_sdk import CORSSettings, apply_cors

  apply_cors(app, CORSSettings(allow_origins=["http://localhost:5173"]))
  ```

!!! info "Prefira mesma origem em produção"
    Servir o frontend pelo próprio backend (mesma origem) elimina o CORS,
    simplifica cookies/sessão e fecha uma porta a menos. Use origens
    separadas só no loop de dev.

## Recap

- O frontend tempestweb chama a API do SDK com **`tempestweb.native.http`**
  (`request`/`upload`/`poll` → `HttpResponse` tipado).
- **`Idempotency-Key`** do frontend + **`IdempotencyMiddleware`** do backend
  = POST seguro pra retry/replay, sem duplicar.
- **`RetryOptions`** dá backoff exponencial nas requests seguras.
- **Mesma origem** (backend serve a SPA via `make_web_app_router`) dispensa
  CORS; para origens separadas use `apply_cors`.
- Hospedagem do build: [Fullstack web](../fullstack-web.md) e [SSR](../ssr.md).
