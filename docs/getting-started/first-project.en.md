# Your first project

You have `uv` ([page 1](uv.md)) and you know how to pick a Python version ([page 2](python-versions.md)). Now let's create a project from scratch, install the SDK and bring up an API that actually answers — in fewer than ten commands.

There are two paths. Do **A** first: it is slower, but it is where you learn what each file does. **B** is the shortcut you will use on every real project afterwards.

## Path A — from scratch, one piece at a time

### 1. Create the project

```bash
uv init my-api --python 3.13
cd my-api
```

`uv` writes the skeleton and initializes a Git repository:

```text
my-api/
├── .git/
├── .gitignore
├── .python-version      # 3.13 — the version pinned for this checkout
├── README.md
├── main.py              # a sample "hello world"
└── pyproject.toml       # the project's name, version and dependencies
```

The freshly created `pyproject.toml`:

```toml
[project]
name = "my-api"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.13"
dependencies = []
```

!!! info "`pyproject.toml` is the project's identity"
    It is the standard file of the modern Python ecosystem (defined by [PEP 621](https://peps.python.org/pep-0621/)). Everything the project needs in order to be installed and resolved lives there — there is no `requirements.txt` in a `uv` workflow.

### 2. Install the SDK

```bash
uv add "tempest-fastapi-sdk[sqlite]" uvicorn
```

`uv` resolves, downloads, creates `.venv/`, writes `uv.lock` and updates `pyproject.toml`:

```toml
dependencies = [
    "tempest-fastapi-sdk[sqlite]>=0.172.1",
    "uvicorn>=0.52.0",
]
```

!!! note "Why `[sqlite]`, and why `uvicorn`?"
    `[sqlite]` brings the async driver `aiosqlite` — the SDK does not pick a database for you (full table in **[Installation »](../installation.md)**). `uvicorn` is the server that runs the application; `fastapi` already ships with the SDK, no need to add it.

### 3. Write the API

Replace the contents of `main.py` with this complete file:

```python
# main.py
from fastapi import FastAPI

from tempest_fastapi_sdk import BaseSchema, register_exception_handlers
from tempest_fastapi_sdk.exceptions import NotFoundException


class TaskSchema(BaseSchema):
    """A single task exposed by the API."""

    id: int
    title: str
    done: bool = False


app: FastAPI = FastAPI(title="My first API")
register_exception_handlers(app)

TASKS: dict[int, TaskSchema] = {
    1: TaskSchema(id=1, title="Install uv", done=True),
    2: TaskSchema(id=2, title="Write my first endpoint"),
}


@app.get("/tasks")
async def list_tasks() -> list[TaskSchema]:
    """Return every task."""
    return list(TASKS.values())


@app.get("/tasks/{task_id}")
async def get_task(task_id: int) -> TaskSchema:
    """Return one task by id.

    Raises:
        NotFoundException: When no task carries the given id.
    """
    task = TASKS.get(task_id)
    if task is None:
        raise NotFoundException("Task not found")
    return task
```

### 4. Start the server

```bash
uv run uvicorn main:app --reload --port 8000
```

```text
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

!!! tip "Notice you never activated `.venv`?"
    `uv run` does it for you — and syncs the environment first if `pyproject.toml` changed. That is the habit to build: prefix everything with `uv run`.

### 5. See it working

Open <http://127.0.0.1:8000/docs> in the browser: FastAPI generates the interactive documentation on its own, from the types you annotated. You can call the endpoints from there.

From the terminal, in another tab:

```bash
curl http://127.0.0.1:8000/tasks
```

```json
[
  {"id": 1, "title": "Install uv", "done": true},
  {"id": 2, "title": "Write my first endpoint", "done": false}
]
```

Now ask for a task that does not exist:

```bash
curl -i http://127.0.0.1:8000/tasks/42
```

```text
HTTP/1.1 404 Not Found
content-type: application/json

{"detail": "Task not found", "code": "NOT_FOUND", "details": {}}
```

### What the SDK did for you

Three lines of the example carry all the weight:

| Line | Effect |
| --- | --- |
| `class TaskSchema(BaseSchema)` | a Pydantic schema pre-configured with the project's conventions (reads from ORM objects, strips strings, validates on assignment) |
| `register_exception_handlers(app)` | any `AppException` raised in any layer becomes a JSON response in the same envelope, with the right status |
| `raise NotFoundException(...)` | you raise a domain exception; translating it to HTTP 404 is the handler's job, not your endpoint's |

Without the SDK, the third item becomes `raise HTTPException(status_code=404, detail=...)` scattered across routers, and the error shape changes from endpoint to endpoint. That single envelope is what lets a client (React, Flutter) handle errors in exactly one place.

## Path B — the real-project shortcut

A real service has layers (router → controller → service → repository), settings, migrations, Docker. Typing all that by hand every time is waste: the `tempest` CLI generates the whole skeleton.

```bash
# 1. install the CLI in isolation (it does not enter the project)
uv tool install "tempest-fastapi-sdk[all]"

# 2. scaffold the service
tempest new my-api
cd my-api

# 3. install the generated dependencies and run the tests
uv sync
uv run pytest
```

What comes out:

```text
my-api/
├── main.py                 # one-liner calling run() from src.server
├── pyproject.toml
├── .env.example
└── src/
    ├── server.py           # programmatic uvicorn + importable app
    ├── api/                # routers, dependencies, app factory
    ├── controllers/        # thin orchestration over the services
    ├── services/           # business logic
    ├── schemas/            # request/response DTOs
    ├── db/
    │   ├── models/
    │   └── repositories/
    └── core/               # settings + constants + exceptions
```

Bring the service up:

```bash
uv run python main.py
```

!!! warning "The global CLI and the project are different environments"
    `uv tool install` puts the `tempest` command in an environment of its own, available from any directory. The generated project has **its** `pyproject.toml` and **its** `.venv`, created by `uv sync`. Do not mix a global `pip install` with the project's `uv sync`: you end up with two SDK installs and debug the wrong one.

## Day to day, in five commands

| Command | When to use it |
| --- | --- |
| `uv add <package>` | you need a new library |
| `uv sync` | you just cloned the repository, or someone changed the dependencies |
| `uv run <command>` | run anything (server, tests, a script) |
| `uv run pytest` | run the test suite |
| `uv lock --upgrade` | refresh the versions pinned in `uv.lock` |

## Recap

- `uv init` creates, `uv add` installs, `uv run` executes — in that order.
- `.venv` is created and maintained by `uv`; activating it by hand is optional.
- `BaseSchema` + `register_exception_handlers` + domain exceptions already give you an API with a consistent error contract.
- For a real project, `tempest new` generates the entire layered layout.

From here you have two good paths:

- **[Architecture »](../architecture.md)** — why router → controller → service → repository, and what lives in each layer.
- **[Tutorial »](../tutorial.md)** — the complete *Users* feature, from model to paginated endpoint.

And whenever you want to go deeper on any tool in the stack: **[Official reference docs »](references.md)**.

## Official documentation

| Resource | Link |
| --- | --- |
| Working on projects (`uv`) | <https://docs.astral.sh/uv/guides/projects/> |
| Managing dependencies (`uv`) | <https://docs.astral.sh/uv/concepts/projects/dependencies/> |
| FastAPI first steps | <https://fastapi.tiangolo.com/tutorial/first-steps/> |
| Pydantic models | <https://docs.pydantic.dev/latest/concepts/models/> |
| Running Uvicorn | <https://www.uvicorn.org/> |
