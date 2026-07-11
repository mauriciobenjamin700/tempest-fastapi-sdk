# Fullstack web — SSR, WASM e server

O SDK fala com o **tempestweb** de três jeitos, todos fullstack e todos
tipados em Python — sem template, sem JavaScript escrito à mão. Cada um
resolve um cenário diferente:

| Projeto | Onde o Python roda | Build? | Ideal para |
|---------|--------------------|--------|------------|
| **1. SSR + HTMX** | No servidor, por request | Não | Páginas server-rendered, SEO, interatividade progressiva |
| **2. SPA WASM** | No **browser** (Pyodide) | `tempestweb build --mode wasm` | App offline-first, servidor só serve arquivos + API |
| **3. Server-mode** | No servidor, ao vivo | `tempestweb build --mode server` | UI reativa dirigida pelo servidor sobre WebSocket/SSE |

Cada seção é um projeto completo e rodável. Comece pela 1 (a mais
simples) e suba conforme a necessidade.

!!! info "O extra `[ssr]`"
    Tudo aqui mora no extra `[ssr]`:

    ```bash
    pip install "tempest-fastapi-sdk[ssr]"
    ```

    O `tempestweb` é carregado sob demanda — `import tempest_fastapi_sdk`
    nunca exige o extra.

---

## Projeto 1 — SSR + HTMX (sem build)

Uma lista de tarefas fullstack renderizada **no servidor**: páginas
tipadas (`Page`), persistência via `BaseRepository`, e HTMX servido
localmente para adicionar/marcar/excluir sem recarregar a página. Nada é
compilado — cada request devolve HTML.

### O modelo e a persistência

Um `BaseModel` do SDK e o `AsyncDatabaseManager`:

```python
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel


class TaskModel(BaseModel):
    """Um item da lista."""

    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean(), default=False, nullable=False)


db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
```

### Os widgets tipados

Cada tarefa vira um `<li>` com botões que disparam HTMX. Todo widget
aceita `tag=` e `attrs=`, então dá pra emitir HTML semântico e atributos
`hx-*` diretamente:

```python
from tempest_core import Button, Column, Row, Text, Widget


def task_widget(task_id: str, title: str, done: bool) -> Widget:
    """Renderiza uma tarefa como <li> com toggle + excluir."""
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

### A página

Um `Page` tipado declara seus dados como campos e implementa `body()`.
Aqui um formulário HTMX (append no `<ul>`) e a lista:

```python
from tempest_fastapi_sdk.ssr import Page


class TasksPage(Page):
    """O documento completo da lista de tarefas."""

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
                    attrs={"name": "title", "placeholder": "Nova tarefa", "required": "required"},
                ),
                Button(label="Adicionar", attrs={"type": "submit"}),
            ],
        )
        items = [task_widget(i, t, d) for i, t, d in self.tasks]
        return Column(
            tag="main",
            children=[
                Text(content="Tarefas", tag="h1"),
                form,
                Column(tag="ul", attrs={"id": "tasks"}, children=items),
            ],
        )
```

### As rotas

`GET /` devolve o **documento completo** (`htmx=True` injeta o HTMX
local); cada ação devolve só o **fragmento** (`document=False`) que o
HTMX troca no lugar:

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
app.include_router(make_htmx_router())   # serve /_ssr/htmx.js localmente


@app.get("/")
async def index() -> object:
    async with db.get_session_context() as session:
        rows = await BaseRepository(session, model=TaskModel).list()
    page = TasksPage(
        title="Tarefas",
        tasks=[(str(r.id), r.title, r.done) for r in rows],
    )
    return html_response(page, title="Tarefas", htmx=True)


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

Rode com `uvicorn app:app --reload` e abra `http://127.0.0.1:8000`.

!!! check "O que este projeto mostra"
    - **`Page`** tipado + **`html_response`** (documento e fragmento).
    - **`make_htmx_router`** servindo o HTMX embutido — sem CDN, offline,
      amigável a CSP.
    - **`BaseRepository`** para o CRUD, tudo async.
    - Interatividade real (add/toggle/delete) **sem escrever JavaScript**.

---

## Projeto 2 — SPA WASM (Python no browser)

Agora o mesmo Python roda **no browser** via Pyodide. Você compila o
frontend com `tempestweb build --mode wasm` e o SDK serve o `dist/`
estático com `make_web_app_router` — mais uma API JSON do FastAPI por
trás para os dados.

### O app tempestweb (`web/app.py`)

O contrato do tempestweb é `make_state` + `view` (o mesmo código roda nos
dois modos — o transporte é escolhido no build):

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

### O build

```bash
cd web
tempestweb build --mode wasm     # gera web/dist/wasm/ (index.html + bootstrap + wasm + sw)
```

O SDK **não** builda — o build fica no fluxo do tempestweb (CLI/CI). O SDK
só serve o `dist/` pronto.

### O host FastAPI

`make_web_app_router` devolve um `APIRouter` que serve a SPA com
**history fallback** (rota client-side desconhecida → `index.html`).
Inclua-o **por último**, para as rotas de API vencerem o catch-all:

```python
from fastapi import APIRouter, FastAPI

from tempest_fastapi_sdk.ssr import make_web_app_router

app = FastAPI()

# 1) Sua API JSON — registrada PRIMEIRO.
api = APIRouter(prefix="/api")


@api.get("/count")
async def count() -> dict[str, int]:
    return {"value": 42}


app.include_router(api)

# 2) A SPA estática — registrada POR ÚLTIMO (catch-all na raiz).
app.include_router(make_web_app_router("web/dist/wasm"))
```

Rode com `uvicorn app:app`. `GET /api/count` responde JSON; qualquer
outra rota entrega a SPA, que roda Python no browser e chama sua API.

!!! warning "Monte na raiz e inclua por último"
    O artefato wasm referencia `/sw.js` na raiz do site, então monte na
    raiz da app. E como o router é um catch-all (`/{resource:path}`),
    inclua-o **depois** dos seus routers de API.

!!! tip "Sem mágica no serving"
    `index.html` e `sw.js` saem sempre `no-cache`; os demais assets usam
    `asset_cache_control`. MIME correto pra `.wasm`/`.mjs`/`.webmanifest`,
    `Service-Worker-Allowed: /` no worker, traversal bloqueado, e
    **nenhum CSP imposto** (o Pyodide precisa de `wasm-unsafe-eval`) —
    passe `security_headers=` pra adicionar o seu.

---

## Projeto 3 — Server-mode (WebSocket/SSE ao vivo)

O **mesmo `web/app.py`** do Projeto 2, agora compilado para rodar **no
servidor**: a UI é dirigida ao vivo sobre WebSocket/SSE. Você compila com
`--mode server` e monta com `build_web_app`.

### O build

```bash
cd web
tempestweb build --mode server   # gera web/dist/server/ (server.py + app.py + static)
```

### O host FastAPI

`build_web_app` carrega o `app.py` do artefato, monta o engine server do
tempestweb (rotas `/ws` + `/sse`), serve o cliente em `/static` e o shell
em `/` — a mesma fiação que o `server.py` gerado faz, in-process. Como o
build server referencia `/ws` na raiz, ele ocupa a raiz; adicione suas
rotas de API **ao próprio app**:

```python
from fastapi import APIRouter

from tempest_fastapi_sdk.ssr import build_web_app

# O app do tempestweb já é um FastAPI — adicione sua API a ele.
app = build_web_app("web/dist/server", title="Contador")

api = APIRouter(prefix="/api")


@api.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


app.include_router(api)
```

Rode com `uvicorn app:app`. O browser abre `/`, conecta em `/ws`, e cada
clique roda o `view` **no servidor** — a árvore é diffada e enviada de
volta. `GET /api/health` continua respondendo normalmente.

!!! info "Qual modo escolher?"
    - **WASM** — o cliente é autônomo (roda offline), o servidor é
      opcional (só API + arquivos). Boot mais pesado (baixa o Pyodide).
    - **Server** — boot instantâneo e o estado vive no servidor (ideal
      pra dados sensíveis ou lógica pesada), mas exige conexão viva.

    O melhor: o `web/app.py` é **idêntico** nos dois — troque só o
    `--mode` do build.

---

## Recap

- **SSR + HTMX** — `Page` + `html_response` + `make_htmx_router`:
  server-rendered, sem build, interatividade via HTMX local.
- **SPA WASM** — `tempestweb build --mode wasm` + `make_web_app_router`:
  Python no browser, servido como estático com history fallback + sua API.
- **Server-mode** — `tempestweb build --mode server` + `build_web_app`:
  UI ao vivo sobre WebSocket/SSE, com sua API no mesmo app.
- **`detect_build_mode(dir)`** distingue um `dist/` wasm de um server.
- O SDK só **serve** um build pronto — compilar fica no tempestweb.
  Detalhes de cada API em [SSR](ssr.md).
