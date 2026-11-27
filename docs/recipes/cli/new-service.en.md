---
description: "Use to choose between `[cli-ops]` and `[cli]`, scaffold a new service with `tempest new` and bring layers or an old project to the standard with `tempest generate`."
---

# Install and scaffold a service

Use to choose between `[cli-ops]` and `[cli]`, scaffold a new service and bring an existing project to the standard (`tempest new`, `tempest generate`).

Part of the [CLI](../cli.md) recipe.

## Server vs development: `[cli-ops]` and `[cli]`

`tempest` does two kinds of work, and they live in different environments:

- **Operating the service** — `tempest doctor`, `tempest db`,
  `tempest shell`, `tempest user`, `tempest routes` and the project's own
  commands (`[tool.tempest] commands`). You want these **on the server**,
  with its configuration and its network.
- **The quality gate** — `lint`, `fix`, `format`, `fmt-check`, `type`,
  `test`, `check` and `pr-prompt`. That is `ruff`, `mypy` and `pytest`, and
  it lives **in development and CI**.

That is why there are two extras:

| Extra | Brings | Where to use it |
| --- | --- | --- |
| `[cli-ops]` | `typer`, `click` | runtime dependency, in the production image |
| `[cli]` | `[cli-ops]` + `tempest-cli` (which requires `ruff`) | the `dev` group and `uv tool install` |

`[cli]` contains `[cli-ops]`, so a project declares both without conflict:

```toml
[project]
dependencies = [
    "tempest-fastapi-sdk[cli-ops,postgres]",
]

[dependency-groups]
dev = [
    "tempest-fastapi-sdk[cli]",
]
```

With `uv sync --no-dev` (what the generated `Dockerfile` runs), the image has
`tempest` and neither `tempest-cli` nor `ruff`. In a clean venv with only
`[cli-ops]`, `import ruff` and `import tempest_cli` fail, and the rest works:

```text
$ tempest doctor
OK    python         3.12.7 (...)
OK    sdk            tempest-fastapi-sdk <version>
SKIP  settings       no <root>/core/settings.py here — run inside the project root
...
$ tempest staff-create ana@example.com      # from [tool.tempest] commands
staff created: ana@example.com
```

The gate commands stay listed in `tempest --help` — with the help line
`Quality gate command; needs the [cli] extra (tempest-cli and ruff).` — and,
when called, explain what is missing instead of vanishing into a
`No such command`. They exit with code 2, no traceback, whatever arguments
you passed:

```text
$ tempest lint
error: tempest lint is not available. The quality gate (lint, fix, format, fmt-check, type, test, check, pr-prompt) needs the optional [cli] extra, which adds tempest-cli and ruff. Install it with:
  uv add --dev "tempest-fastapi-sdk[cli]"   (in a project)
  uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)
```

!!! tip "Precompile the bytecode in the image: `UV_COMPILE_BYTECODE=1`"
    `uv` does not compile `.pyc` files on install, so Python compiles each
    module the first time it imports it. If the container runs as a user that
    cannot write to `site-packages` — the usual case in an image with a
    non-root user —, the `.pyc` files are never written and **every** run pays
    that compilation, not just the first. Measured with
    `tempest-fastapi-sdk[cli-ops]==0.326.0`, `tempest --help`, 3 runs of each:

    | Install | 1st run | Later runs |
    | --- | --- | --- |
    | without `UV_COMPILE_BYTECODE` | ~0.34 s | ~0.14 s |
    | without, read-only `site-packages` | ~0.34 s | ~0.34 s |
    | with `UV_COMPILE_BYTECODE=1` | ~0.14 s | ~0.14 s |

    The `Dockerfile` that `tempest new` writes already sets the variable. In
    a Dockerfile of your own, or with `uv pip install` / `uv tool install`,
    set it on the install step, so the compilation happens once, at build
    time:

    ```dockerfile
    ENV UV_COMPILE_BYTECODE=1
    RUN uv sync --locked --no-dev
    ```

    The mechanism is the same for every module the app imports at startup,
    not only for `tempest`.

!!! info "Technical details"
    Reading `[tool.tempest]` to find the project and its `commands`
    (`find_pyproject`, `load_tempest_table`, `load_project_commands` in
    `tempest_fastapi_sdk.cli.config`) is the SDK's own code, standard library
    only, ported from `tempest-cli`. `typing_strictness` and the
    `ruff`/`mypy` flags still belong to `tempest-cli` and load only when a
    gate command runs.

## Scaffold a new service

```bash
tempest new my_service                          # scaffold under ./my_service
tempest new my_service --path ~/projects        # custom parent dir
# default HOST/PORT in .env.example and SDK extras pinned in pyproject.toml
tempest new my_service --bind-host 0.0.0.0 --bind-port 9090 --extras upload
tempest new my_service --force                  # overwrite existing dir
```

The skeleton matches the layered architecture documented in [Architecture »](../../architecture.md):

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # pins tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # TITLE/DESCRIPTION, SERVER_*, LOG_*, DATABASE_URL, JWT_* (with [auth]), TOKEN_SECRET, CORS_*
├── alembic.ini              # paths anchored on %(here)s
├── alembic/                 # env.py + versions/ with the baseline revision
├── docker-compose.yaml      # services keyed to the chosen extras
├── Dockerfile               # multi-stage with uv, non-root user
├── .dockerignore
├── .gitignore
├── README.md
├── CLAUDE.md                # project rules for AI agents and humans
├── src/
│   ├── __init__.py          # re-exports run from .server
│   ├── server.py            # module-level app + run() via run_server
│   ├── api/
│   │   ├── app.py           # create_app() wires middleware, handlers and build_api_router()
│   │   ├── routers/         # api.py (build_api_router, the only /api) + service_info.py (GET /api/)
│   │   ├── docs/            # base.py (EndpointDoc) + service_info.py — OpenAPI text out of the router
│   │   └── dependencies/    # auth.py + resources.py (db, get_session, get_mailer/get_storage per extra) + controllers.py (providers)
│   ├── controllers/system/  # ServiceInfoController — one package per context, one class per module
│   ├── services/            # business logic, in services/<context>/
│   ├── schemas/             # Pydantic DTOs
│   ├── scripts/             # composition.py — the composition root (build_*)
│   ├── core/
│   │   ├── settings.py
│   │   ├── constants/       # per-domain constants + messages/{errors,successes}.py
│   │   ├── enums/           # one module per domain
│   │   ├── types/           # Protocol, alias, NamedTuple — one module per domain
│   │   └── exceptions/      # one module per status: bad_request, unauthorized, forbidden, not_found, conflict
│   ├── db/
│   │   ├── configs/         # names.py + connection.py (make_db_manager) + migrations.py (run_migrations)
│   │   ├── models/users/    # UserModel — only with [auth] (or [admin])
│   │   └── repositories/
│   ├── queue/               # only with the [queue] extra — broker + handlers
│   ├── tasks/               # only with [tasks] or [tasks-redis] — queue, names, dispatch, worker + an example task
│   ├── ui/                  # only with the [ssr] extra — pages/layout/components/styles
│   └── utils/
└── tests/
    ├── conftest.py          # one SQLite per pytest-xdist worker
    ├── integration/system/  # test_service_info.py: /api/, settings override, /health/liveness
    └── unit/
        ├── architecture/    # lint_layers, analyze_paths, assert_schema_metadata
        └── core/            # test_exceptions_contract.py: status, code, message and field frozen
```

Why each directory exists, and what enforces each one, is in
[Service standard](../../service-standard.md).

!!! tip "The generated `CLAUDE.md` is the project's contract"
    Every new project ships with a `CLAUDE.md` pinning the rules that
    keep services alike: the dependency direction between layers, the
    **exact order** of the seven steps a new domain follows (schema →
    model → repository → service → controller → provider → router), the
    table of what **not** to reimplement because the SDK already ships
    it, and a definition of done that ends in `tempest check`.

    It exists for an AI agent to read before writing the first line — so
    its examples are verified in the SDK's own CI:
    `tests/cli/test_scaffold_runtime.py` writes the example domain into a
    scaffolded project and runs it (POST 201, duplicate 409 with the
    right `code`, paginated listing in the SDK envelope). A renamed
    symbol breaks that test, not someone's project.

The generated `pyproject.toml` pins the current SDK version with the extras you asked for, and only those (`tempest-fastapi-sdk>=<version>` without `--extras`). `admin` and `auth` are **opt-in**: `--extras auth` gives the project the `UserModel`, `JWTSettings` on `Settings` and the `JWT_*` block in `.env.example`; `--extras admin` also makes `src/api/app.py` mount the admin panel — and brings `auth` along, because the panel's login checks the password through `PasswordUtils`. With neither, the service starts with no user table, no panel and no `bcrypt`/`PyJWT` in the lock: `--extras postgres` pins just `[postgres]`, `--extras admin,cache` pins `[admin,auth,cache]`, and duplicates are dropped with the list sorted alphabetically. Up to 0.312.x `admin` and `auth` were always pinned. The `dev` group gets `tempest-fastapi-sdk[cli]>=<version>`, so `tempest` runs in the project without entering the image: the generated `Dockerfile` installs with `uv sync --no-dev`. The scaffolded `.env.example` uses the v0.8.0 settings naming (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), and `src/server.py` delegates to `tempest_fastapi_sdk.run_server` so uvicorn is imported lazily and tests can import the app without it. Validation rules: the project name must match `^[a-z][a-z0-9_]*$` and cannot collide with a Python keyword, so `tempest new Bad-Name` and `tempest new class` exit with code 2 before any file is written.

!!! tip "API title, description and version"
    Since v0.48.0 the scaffolded `Settings` carries `TITLE`, `VERSION`
    and `DESCRIPTION`, and `src/api/app.py` consumes them
    (`FastAPI(title=settings.TITLE, version=settings.VERSION,
    description=settings.DESCRIPTION)`, `make_health_router(version=
    settings.VERSION)` and, with `[admin]`, `AdminSite(title=f"{settings.TITLE} admin")`).

    The version has a **single source**: `Settings.VERSION` comes from
    `[project].version` in `pyproject.toml`, read by
    [`read_project_version`](../../../../reference/admin-data-api/settings/#tempest_fastapi_sdk.settings.version.read_project_version).
    The path is anchored on `settings.py` itself (not the cwd), so the
    value is the project's even when it runs from another directory;
    bumping the release is editing `pyproject.toml`, and
    `/health/readiness` and the OpenAPI `info.version` follow. Title and
    description stay in `.env`, and the `VERSION` env var still overrides
    the file when you need to publish a version other than the committed
    one:

    ```bash
    TITLE=My API
    DESCRIPTION=Payments API for product X.
    # VERSION=1.2.0  # optional: overrides [project].version
    ```

## Generating the `src` layers from extras — `tempest generate --src`

The always-present layers (`api`, `controllers`, `services`, `schemas`, `db`, `core`, `utils`) ship in the scaffold. The layers that only make sense with a specific extra — `[queue]` (FastStream) and `[tasks]` (TaskIQ) — are **not** part of the base skeleton: dropping empty placeholder packages in every service contradicts the layout rules. When you add one of those extras to an existing project (`uv add "tempest-fastapi-sdk[queue]"`), generate the matching layer with:

```bash
tempest generate --src                           # read extras from local pyproject.toml
tempest generate --src --extras tasks            # force explicit extras
tempest generate --src --force                   # overwrite existing files
tempest generate --docker --src                  # compose + layers in one shot
```

Extra → generated layer mapping:

| Extra | Files created (under `src/` or `app/`) |
|-------|------------------------------------------|
| `[queue]` | `queue/broker.py` (`RabbitBroker` + `AsyncQueueManager` + `get_broker`), `queue/__init__.py` (re-exports only), `queue/handlers.py` (example subscriber) |
| `[tasks]` / `[tasks-redis]` | `tasks/queue.py` (`tq = TaskQueue.from_settings(settings)` + `get_task_queue`), `tasks/names.py` (`EXAMPLE_TASK_NAME`, `EXAMPLE_SCHEDULE`; imports nothing from the project), `tasks/example.py` (registration only: `@tq.cron(EXAMPLE_SCHEDULE, name=EXAMPLE_TASK_NAME)` calling `scripts/example.py`), `tasks/dispatch.py` (`TaskDispatcher`: the API enqueues by name, with `enqueue_by_name`), `tasks/worker.py` (imports the tasks; `broker`/`scheduler` for `taskiq worker src.tasks.worker:broker`), `tasks/__init__.py` (re-exports queue, names and dispatcher — never a task) |

Both extras generate the same layer: the transport comes from `TASKIQ_BROKER_URL` at runtime. With `[tasks-redis]`, `tempest new` also declares `TASKIQ_BROKER_URL` on `Settings` with a `redis://localhost:6379/0` default and writes the same URL to `.env.example`, because the mixin's default is `amqp://`, which selects RabbitMQ and needs `[tasks]`. If the URL still points at a transport whose extra is not installed, the `ImportError` from `TaskQueue.from_settings` names `TASKIQ_BROKER_URL`, the value it read (password masked) and the URL shape of the other transport.

The source root (`src` or `app`) is auto-detected, and generated imports (`from src.queue import broker`) already point at it. The operation is **idempotent**: existing files are **kept** unless you pass `--force`, so a hand-edited handler is never clobbered silently — a sibling file that doesn't exist yet is still written. Extras with no associated layer (e.g. just `[cache]`) generate nothing and the command says so.

!!! note "`tempest new` already generates the chosen extras' layers"
    A `tempest new my_service --extras auth,queue` already ships
    `src/queue/` — `generate --src` is for when you add the extra
    **after** creating the project.

After scaffolding:

```bash
cd my_service
uv sync                                         # installs SDK + dev tools
cp .env.example .env
uv run python main.py                           # serves on the configured HOST:PORT
uv run pytest                                   # the bundled smoke test
```

## Bring an older project to the standard — `tempest generate --standard`

Writes the pieces of the [service standard](../../service-standard.md) a project older than it lacks — `core/constants/`, `core/enums/`, `core/types/`, the `tests/unit/` guards —, turns a flat `core/exceptions.py` into the `core/exceptions/` package with one module per status, and freezes the exception contract in `tests/unit/core/test_exceptions_contract.py`. An existing file is skipped without `--force`; the migration refuses, writing nothing, when the module holds anything besides imports, classes and `__all__`. The walkthrough is in [An existing project](../../service-standard.md#an-existing-project-tempest-generate-standard).

```bash
tempest generate --standard
```
