# CLI


Installing `tempest-fastapi-sdk[cli]` (or `[cli-ops]`, on a server) exposes a `tempest` console script. It does two jobs: bootstrap a new layered service from the SDK's preferred skeleton, and run the four quality gates (`ruff check`, `ruff format`, `mypy`, `pytest`) without copy-pasting the same commands into every project.

```bash
tempest --help                                  # list every command
tempest --version                               # show the SDK version
```

!!! info "The CLI is the `[cli]` extra (since 0.308.0)"
    `tempest` lives in an extra rather than in the base package. In
    development, install it the way you use it (for a server, see
    [`[cli-ops]`](#server-vs-development-cli-ops-and-cli)):

    ```bash
    uv tool install "tempest-fastapi-sdk[cli]"   # global command
    uv add --dev "tempest-fastapi-sdk[cli]"      # in the project's dev group
    ```

    The reason is `ruff`: the gate (`tempest check`) comes from
    `tempest-cli`, which requires `ruff>=0.8.0` at runtime. In the base
    package, every service importing the SDK shipped a formatter to
    production, and a project pinning `ruff<0.8` in its dev group could not
    even resolve its lock.

    Without either extra the script is still installed and explains what is
    missing, exiting with code 2 and no traceback:

    ```text
    $ tempest --help
    error: missing typer, click. The tempest CLI needs the optional [cli-ops] or [cli] extra. Install it with:
      uv add "tempest-fastapi-sdk[cli-ops]"   (on a server, without ruff)
      uv add --dev "tempest-fastapi-sdk[cli]"   (in development, with the gate)
      uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)
    ```

    `import tempest_fastapi_sdk` needs no extra at all; reading
    `tempest_fastapi_sdk.cli.app` (or importing `tempest_fastapi_sdk.cli.main`)
    without `typer` raises `ImportError` with that instruction. Importing
    `.lint` or `.pr_prompt`, or reading `TempestConfig` /
    `load_tempest_config` from `tempest_fastapi_sdk.cli.config`, without
    `tempest-cli` raises `ImportError` naming `[cli]`.

!!! tip "Usage error? The full help shows up with it"
    When you type an unknown command, an invalid option, or forget a
    required argument, `tempest` prints that command's **complete**
    `--help` (every parameter, default and description) **before** the
    error line — instead of Click's terse `Try '... --help'`. You fix
    it on the spot, without re-running with `--help`.

    ```bash
    tempest user create            # forgot --email
    # ... full `user create` help (every option) ...
    # Error: Missing option '--email' / '-e'.
    ```

### Server vs development: `[cli-ops]` and `[cli]`

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

### Scaffold a new service

```bash
tempest new my_service                          # scaffold under ./my_service
tempest new my_service --path ~/projects        # custom parent dir
# default HOST/PORT in .env.example and SDK extras pinned in pyproject.toml
tempest new my_service --bind-host 0.0.0.0 --bind-port 9090 --extras upload
tempest new my_service --force                  # overwrite existing dir
```

The skeleton matches the layered architecture documented in [Architecture »](../architecture.md):

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
│   │   └── dependencies/    # auth.py + resources.py (db, get_session, get_mailer/get_storage per extra) + controllers.py (providers)
│   ├── controllers/system/  # ServiceInfoController — one package per context, one class per module
│   ├── services/            # business logic, in services/<context>/
│   ├── schemas/             # Pydantic DTOs
│   ├── scripts/             # composition.py — the composition root (build_*)
│   ├── core/                # settings.py + enums.py + types.py + exceptions.py
│   ├── db/
│   │   ├── configs/names.py # table names behind one symbol
│   │   ├── models/users/    # UserModel — only with [auth] (or [admin])
│   │   └── repositories/
│   ├── queue/               # only with the [queue] extra — broker + handlers
│   ├── tasks/               # only with [tasks] or [tasks-redis] — queue, names, dispatch, worker + an example task
│   ├── ui/                  # only with the [ssr] extra — pages/layout/components/styles
│   └── utils/
└── tests/
    ├── conftest.py          # one SQLite per pytest-xdist worker
    ├── test_smoke.py        # asserts /api/ and /health/liveness boot
    └── test_schemas.py      # every exported schema field documented (assert_schema_metadata)
```

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
    [`read_project_version`](../../../reference/#tempest_fastapi_sdk.settings.version.read_project_version).
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

### Extras-driven `docker-compose.yaml`

Since v0.25.0 the scaffold generates a `docker-compose.yaml` carrying **only** the supporting services the chosen extras actually need — no ZooKeeper, no Kafka, nothing you won't use.

| Extra | Container | Port(s) published on `127.0.0.1` |
|-------|-----------|----------------------------------|
| (always) | `postgres:18-alpine` | 5432 |
| `[cache]` / `[tasks-redis]` | `redis:8-alpine` | 6379 |
| `[queue]` / `[tasks]` | `rabbitmq:4-management-alpine` | 5672 (AMQP) + 15672 (UI) |
| `[minio]` | `quay.io/minio/minio` + bootstrap mc | 9000 (API) + 9001 (Console) |
| `[email]` | `mailhog/mailhog` | 1025 (SMTP) + 8025 (UI) |

Every infra port is published on **loopback only** (`"127.0.0.1:5432:5432"`):
the process you run on the host still reaches Postgres at `localhost:5432`,
and nothing outside the machine does. The API does not need those ports —
inside the compose it talks to the infra by service name (see the
[`prod` profile](#the-prod-profile-the-api-inside-the-compose)).

Example — service using cache + S3 uploads + emails:

```bash
tempest new my_service --extras auth,cache,minio,email
```

Generates:

- `postgres`, `redis`, `minio` (+ `minio-bootstrap` creating the `uploads` bucket), `mailhog`
- `.env.example` with `REDIS_URL`, `MINIO_ROOT_*` (the container), `STORAGE_*` (the app), `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_USE_TLS=false` (MailHog is plain — no STARTTLS)

!!! info "Credentials come from `.env`, not hardcoded in the compose"
    As of v0.37.0, no credential is written straight into
    `docker-compose.yaml`. Each `environment:` block uses the
    `${VAR:-default}` form, and Docker Compose resolves `VAR` from the
    `.env` next to the compose file. The `:-default` keeps the stack
    bootable before you copy `.env.example` to `.env` — but set real
    secrets in `.env` for any non-throwaway deploy. Variables read by
    compose: `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`,
    `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS` /
    `RABBITMQ_DEFAULT_VHOST`, `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`
    — all with their defaults already in `.env.example`.

Boot only the infra — the dev loop, with the app running on the host with reload:

```bash
docker compose up -d
uv run python main.py
```

Tear down keeping volumes:

```bash
docker compose --profile prod down
```

Tear down wiping volumes:

```bash
docker compose --profile prod down -v
```

!!! warning "`down` without `--profile prod` leaves the API up"
    A bare `docker compose down` does not see the `api` service: it stops the
    infra, leaves the `my_service-api` container running, and the network
    ends in `Resource is still in use`. Passing `--profile prod` to `down`
    works in both cases — with or without the API running.

#### The `prod` profile: the API inside the compose

The compose also carries the API itself, as an `api` service behind
`profiles: ["prod"]`. Without the profile it does not exist —
`docker compose up -d` keeps starting just the infra, as above. With the
profile, the project's `Dockerfile` is built and the app runs **on the
compose network**, next to the infra:

```bash
cp .env.example .env
docker compose --profile prod up -d --build --wait
curl http://127.0.0.1:8000/health/readiness
```

```json
{"status":"ready","checks":{"database":true},"version":"0.1.0"}
```

For `tempest new my_service --extras cache,tasks`, the generated block is:

```yaml
services:
  api:
    # Only under `docker compose --profile prod up -d --build`;
    # plain `docker compose up -d` keeps running just the infra.
    profiles: ["prod"]
    build: .
    container_name: my_service-api
    restart: unless-stopped
    env_file: .env
    environment:
      # environment: wins over env_file. .env points every backend
      # at localhost (right for the host, wrong in a container), so
      # the hosts are re-pointed at the compose service names here.
      # SMTP_* is left to .env on purpose: MailHog is dev-only and
      # production mail goes through the real SMTP server.
      SERVER_HOST: 0.0.0.0
      SERVER_PORT: "8000"
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-app}:${POSTGRES_PASSWORD:-app}@postgres:5432/${POSTGRES_DB:-my_service}
      REDIS_URL: redis://redis:6379/0
      RABBITMQ_URL: amqp://${RABBITMQ_DEFAULT_USER:-guest}:${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/
      TASKIQ_BROKER_URL: amqp://${RABBITMQ_DEFAULT_USER:-guest}:${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
    healthcheck:
      # python:3.13-slim has no curl; readiness is 503 until the DB answers.
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/readiness', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 20s
```

Piece by piece:

- **`environment:` rewrites the hosts.** The `.env` the scaffold writes
  points everything at `localhost` — right for the host process, wrong
  inside a container, where `localhost` is the API container itself.
  Compose gives `environment:` precedence over `env_file:`, so every host
  `.env.example` points at `localhost` is swapped for the service name, only
  for the extras you chose:

    | Variable | Value inside the compose | When |
    |----------|--------------------------|------|
    | `DATABASE_URL` | `postgresql+asyncpg://…@postgres:5432/<db>` | always |
    | `REDIS_URL` | `redis://redis:6379/0` | `[cache]` |
    | `RABBITMQ_URL` / `TASKIQ_BROKER_URL` | `amqp://…@rabbitmq:5672/` | `[queue]` / `[tasks]` |
    | `TASKIQ_BROKER_URL` | `redis://redis:6379/0` | `[tasks-redis]` (wins over the `amqp://` of `[tasks]`) |
    | `STORAGE_ENDPOINT` | `minio:9000` | `[minio]` |

    Without that rewrite the API does not crash — it boots **against the
    wrong thing**. The same image run with only `--env-file .env` answered
    `{"status":"ready","checks":{"database":true}}` with `200` and created
    `/app/app.db` inside the container: the `.env` default `DATABASE_URL` is
    SQLite, and Postgres was never touched.

- **`SERVER_HOST: 0.0.0.0` and `SERVER_PORT`.** The `Dockerfile` already
  sets `SERVER_HOST=0.0.0.0`, but the `.env` carries
  `SERVER_HOST=127.0.0.1` and beats the image `ENV` — uvicorn comes up on
  `http://127.0.0.1:8000` inside the container and the published port does
  not answer. Pinning both in `environment:` fixes it. This does not
  contradict the templates' `127.0.0.1` bind: that one is for the process
  on the host; inside the container the bind must accept the compose
  network.
- **`depends_on` with `condition: service_healthy`**, one per extra service
  (all of them have a `healthcheck`). With `[minio]`, the API also waits for
  `minio-bootstrap` to finish (`service_completed_successfully`), so the
  `uploads` bucket exists before the first request.
- **`healthcheck` on `/health/readiness`, via `urllib`.**
  `python:3.13-slim` has no `curl`. The scaffold mounts
  `make_health_router`, which serves `/health/liveness` and
  `/health/readiness` — **`/health` alone answers `404`**. Readiness answers
  `503` while the database is unreachable, and `urlopen` raises on it, so
  `healthy` means the app reached Postgres. It checks **only the
  database**: Redis, RabbitMQ and MinIO are not in the scaffold's payload.
- **`env_file: .env` is required under the profile.** Without `.env`,
  `docker compose --profile prod config` fails with
  `env file .../.env not found`; the dev `docker compose up -d` keeps
  working without it.

!!! info "`prod` profile decisions"
    - **Compose default network**, no `networks:` block. Service names
      already resolve on it; a named network would only matter if another
      compose had to join this one.
    - **Infra on `127.0.0.1` only, API on every interface.** The dev loop on
      the host still reaches the database and broker; in production they
      are not exposed outside the machine. The API port is published
      normally — it is the one serving traffic.
    - **MailHog without a profile, and SMTP stays with `.env`.** MailHog
      starts in both modes (the dev loop does not change), but the API's
      `environment:` does **not** rewrite `SMTP_*`: inside the container,
      the `.env.example`'s `SMTP_HOST=localhost` does not reach MailHog. In
      production set the real SMTP server in `.env`; to test email against
      MailHog inside the compose, use `SMTP_HOST=mailhog` in `.env`.
    - **`asyncpg` is a dependency of the generated project.** The
      scaffold's `pyproject.toml` declares `asyncpg>=0.30.0` next to
      `aiosqlite`, because the image installs only what `pyproject.toml`
      declares and the `prod` profile always uses `postgresql+asyncpg://`.

##### Migrations under the `prod` profile

The scaffold ships `alembic.ini`, `alembic/` and a `baseline` revision (the
tables of the models the extras brought — `users` with `[auth]`, an empty
`upgrade()` without it), and the lifespan of the generated `src/api/app.py`
calls `AlembicHelper(...).sync_schema_async()` before `db.connect()`. So the
compose needs no migration service: the API migrates Postgres as it starts.

```bash
cp .env.example .env
docker compose --profile prod up -d --build --wait
curl http://127.0.0.1:8000/health/readiness
```

Measured with `tempest new my_service --extras admin,postgres`: the API
answered `/health/readiness` 200, Postgres held `users` and `alembic_version`
at the `baseline` revision, and restarting the API ran no upgrade again.
`sync_schema_async` refuses a destructive migration (dropping a table, column
or constraint) and handles an empty database, one created before Alembic and
one already migrated. With several replicas starting together, turn it off
with `DATABASE_MIGRATE_ON_STARTUP=false` and run
`docker compose --profile prod run --rm api tempest db upgrade` as a job.

A new model gets its migration with `tempest db revision -m "..."`; the next
start applies it.

Image tags are pinned by the SDK — bump them through `pyproject.toml` of the SDK, not on a per-project basis. Current versions (v0.26.0+): `postgres:18-alpine`, `redis:8-alpine`, `rabbitmq:4-management-alpine`.

### Regenerating `docker-compose.yaml` in an existing project

When you change installed extras (`uv add "tempest-fastapi-sdk[minio]"`) or the SDK bumps image versions, regenerate with:

```bash
tempest generate --docker                        # read extras from local pyproject.toml
tempest generate --docker --extras cache,minio   # force explicit extras
tempest generate --docker --name my-svc          # override container-name prefix
tempest generate --docker --force                # overwrite an existing compose file
```

The command reads ``[project] name`` + extras from the current directory's `pyproject.toml` (pass `--path` for another). It refuses to overwrite without `--force` so hand edits don't get clobbered. The `.env.example` addendum is idempotent — re-running does not duplicate service blocks.

### Dockerfile to containerize the app

!!! tip "Fullstack: the SPA is detected and built in a Node stage"
    If the project holds a frontend — a `package.json` under `web/`,
    `frontend/`, `client/` or `ui/` — the generated Dockerfile gains a Node
    stage that installs and builds the SPA, and only the resulting `dist/` is
    copied into the final image. Neither `node_modules` nor the Node toolchain
    reaches the runtime.

    ```text
    Regenerated Dockerfile
    Regenerated .dockerignore
      SPA stage: builds web/ and copies web/dist into the image.
    ```

    | Option | Effect |
    | --- | --- |
    | *(none)* | Detect by `package.json`. An empty directory does not count |
    | `--spa-dir apps-web` | An unconventional layout |
    | `--no-spa` | Backend-only image even with a frontend present |

    Serve the result with [`make_spa_router("web/dist")`](react-spa.md),
    included **after** every API router. A project with no frontend gets no
    `spa` stage and no `COPY --from=spa`: the Dockerfile carries only the
    `builder` and final stages shown below.


Since v0.71.0, `tempest new` also generates a ready-to-build **`Dockerfile`** + **`.dockerignore`**. The `Dockerfile` is **multi-stage** and uses [uv](https://docs.astral.sh/uv/):

- **`builder` stage** — installs dependencies into `/app/.venv` (a cached layer that only re-runs when `pyproject.toml` / `uv.lock` change), then installs the project.
- **final stage** — copies only the venv + the source, creates `/app/logs` and hands `/app` to the **non-root** user (`app`, uid 1000), which can then write `logs/` and the SQLite `app.db`, and exposes the configured port.

The file generated for `my_service`, pasted from the generator's output:

```dockerfile
# Dockerfile — generated by `tempest new` for my_service.
#
# Multi-stage build using uv (https://docs.astral.sh/uv/):
#   * builder stage installs dependencies into /app/.venv
#   * final stage copies only the venv + source, runs as a non-root user
#
# Build:  docker build -t my_service .
# Run:    docker run --rm -p 8000:8000 --env-file .env my_service
# Or, with the infra:  docker compose --profile prod up -d --build
#
# The image binds SERVER_HOST=0.0.0.0 (see the final stage) so the app is
# reachable from outside the container even without a .env file. The `api`
# service in docker-compose.yaml (prod profile) builds this image and
# re-points DATABASE_URL & co. from the .env's localhost to the compose
# service names; a bare `docker run --env-file .env` does not, so it only
# reaches the infra if you edit those hosts yourself.

# ---- builder ----------------------------------------------------------------
FROM python:3.13-slim AS builder

# uv ships as a static binary; pin the tag in production for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy bytecode-compiled packages instead of symlinking — the venv must be
# self-contained when copied across stages.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (without the project) so this layer is cached
# and only re-runs when pyproject.toml / uv.lock change. uv.lock is optional
# in a fresh scaffold — the glob keeps the COPY from failing when it is absent.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

# Now copy the source and install the project itself.
COPY . .
RUN uv sync --no-dev

# ---- final ------------------------------------------------------------------
FROM python:3.13-slim

# Run as an unprivileged user. /app is owned by it so the app can write
# logs/ and the default SQLite app.db without root.
RUN useradd --create-home --uid 1000 app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

# WORKDIR created /app as root before the COPY, and `COPY --chown` only sets
# ownership on the copied *contents* — not on the pre-existing /app directory
# node itself. So the app user cannot create new entries (the runtime logs/
# dir, the default SQLite app.db, etc.) inside /app and the app crashes at
# startup with `PermissionError: [Errno 13] ... 'logs'`. Chown the directory
# and pre-create logs/ so the SDK's file logging (LOG_DIR=logs) works.
RUN mkdir -p /app/logs && chown -R app:app /app

# Put the venv on PATH so `python` resolves to the project interpreter.
# SERVER_HOST=0.0.0.0 binds all interfaces inside the container so the app
# is reachable from the host (overridable via .env / `environment:`).
ENV PATH="/app/.venv/bin:$PATH" \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000

USER app
EXPOSE 8000

CMD ["python", "main.py"]
```

Build and run:

```bash
docker build -t my_service .
docker run --rm -p 8000:8000 --env-file .env my_service
```

!!! info "The image binds to `0.0.0.0` by default"
    The final stage sets `ENV SERVER_HOST=0.0.0.0` so the app is
    reachable from outside the container even without a `.env`. Locally
    the scaffold keeps `SERVER_HOST=127.0.0.1` (internal service) — the
    container overrides it to `0.0.0.0` because the bind there must
    accept external connections. A bare `--env-file .env` does **not**
    wire the image to the infra in `docker-compose.yaml`: the `.env`
    points at `localhost`. Use the
    [`prod` profile](#the-prod-profile-the-api-inside-the-compose) for that.

    But a variable from `--env-file` **beats** the image's `ENV`: a `.env`
    copied from `.env.example` carries `SERVER_HOST=127.0.0.1` and puts the
    bind back inside the container. Drop `SERVER_HOST` from the `.env` that
    goes to the container, or set `SERVER_HOST=0.0.0.0` in it.

!!! tip "App + infra in one command"
    The generated `docker-compose.yaml` already builds this image in the
    `api` service, behind the `prod` profile:
    `docker compose --profile prod up -d --build`. See
    [the `prod` profile](#the-prod-profile-the-api-inside-the-compose).

#### Regenerating the Dockerfile — `tempest generate --dockerfile`

```bash
tempest generate --dockerfile                    # Dockerfile + .dockerignore
tempest generate --dockerfile --name my-svc      # override the name in the comments
tempest generate --dockerfile --force            # overwrite existing files
tempest generate --docker --dockerfile --src     # everything in one shot
```

The `EXPOSE` / `SERVER_PORT` port is read from `SERVER_PORT` in `.env` (or `.env.example`), falling back to `8000` when absent. Like the other generators, it refuses to overwrite without `--force`.

### Generating the `src` layers from extras — `tempest generate --src`

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
| `[queue]` | `queue/__init__.py` (`RabbitBroker` + `AsyncQueueManager` + `get_broker`), `queue/handlers.py` (example subscriber) |
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

### Run the service — `tempest serve`

```bash
tempest serve                              # host/port/reload from the settings
tempest serve --reload                     # turn reload on for this run
tempest serve --host 0.0.0.0 --port 9000   # override
tempest serve --workers 4                  # several processes (no reload)
tempest serve --app src.api.app:create_app # app outside the conventional spots
```

The scaffolded `main.py` already boots uvicorn programmatically; this command
is for the project that does not have that file yet, or for overriding host
and port without editing `.env`. Resolution is `run_server`'s: flag > the
project's settings > the SDK default (`127.0.0.1`, `8000`, no reload).

!!! note "uvicorn gets the *string*, never the object"
    The reloader re-imports the target in the child process. Handing it the
    already-imported instance works — and silently turns `--reload` into
    nothing. So the command imports the candidate once to validate it, and
    passes `"src.server:app"` along.

`--reload` together with `--workers` exits 2: uvicorn cannot honour both.
And `--no-reload` really does turn it off, even with `SERVER_RELOAD=true` in
`.env` — the flag has three states (on, off, "you decide").

### Interactive console — `tempest shell`

A REPL with the project already loaded, so "how many orders does this user
have?" does not cost a throwaway script.

```bash
tempest shell
```

```text
tempest shell — Python 3.11.12, top-level await enabled
available: WidgetModel, db, select, session, settings, text
session: open on sqlite+aiosqlite:///./app.db
>>> rows = await session.execute(select(WidgetModel))
>>> rows.scalars().all()
[<WidgetModel id=...>]
```

What the prompt holds: `settings` (the project's instance), every mapped
model **the project defines** (the SDK's `BaseModel` stays out — a query
against the base class is never what you meant), `select`, `text`, the
`AsyncDatabaseManager` as `db`, and an already-open `session`.

!!! info "Top-level `await`, on purpose"
    The console compiles with `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` and runs the
    coroutine **on the same loop** the session was opened on. Exposing a
    `run(coro)` helper would look equivalent and is not: a session driven
    from a second loop raises `MissingGreenlet` at the first lazy load.

`--no-db` starts without connecting — useful when the database is down and
you only want to inspect settings and models.

### Database — `tempest db`

Alembic wrapper backed by ``AlembicHelper`` — your project's ``alembic.ini`` + ``env.py`` stay the source of truth.

``DATABASE_URL`` resolution order:

1. ``--database-url`` flag.
2. ``DATABASE_URL`` env var.
3. Settings on the project's ``core/settings.py``, under either code root
   (``src`` or ``app``). **Any** ``pydantic_settings.BaseSettings`` instance
   qualifies — the names ``settings`` and ``config`` are tried first, then the
   module is scanned by type — **or** the result of a zero-arg factory that
   returns one (``get_settings()``, or any callable annotated to return
   ``BaseSettings``).
4. ``DATABASE_URL`` in the project's ``.env``.
5. ``sqlalchemy.url`` from ``alembic.ini``.

!!! tip "The instance name no longer matters, and the factory counts"
    Up to 0.280.0 only the name ``settings`` was accepted, and the failure was
    an ``ImportError`` swallowed by ``except Exception: return None``. A
    service naming its instance ``config`` got *"no database URL. Pass
    --database-url, set DATABASE_URL, or run inside a project with
    src/core/settings.py"* — pointing at two conditions the project already
    satisfied. Today the instance's **type** decides, the documented
    **factory** (``get_settings()``) is accepted, ``.env`` is read, and when
    nothing resolves each attempt reports why on stderr:

    ```text
    note: importing src.core.settings failed: ImportError("cannot import name ...")
    error: no database URL. Pass --database-url, set DATABASE_URL, put it in
           .env, or expose settings with a DATABASE_URL on
           src/core/settings.py (or app/core/settings.py).
    ```

    The same discovery is shared by ``tempest db``, ``tempest doctor``,
    ``tempest user`` and the generated ``env.py`` — a project whose settings
    exist only as ``get_settings()`` now migrates outside the CLI too.

```bash
tempest db init                                  # create alembic.ini + alembic/env.py
tempest db revision -m "init users table"        # autogenerate by default
tempest db revision -m "manual change" --manual  # empty file you'll edit
tempest db upgrade                               # alembic upgrade head
tempest db upgrade <rev>                         # upgrade to a specific revision
tempest db downgrade                             # roll back one step
tempest db downgrade <rev>                       # roll back to a specific revision
tempest db current                               # print the applied revision
tempest db history                               # revisions newest → oldest
tempest db history -v                            # with full message body
tempest db stamp head                            # mark the DB without running migrations
tempest db squash -m "init" --yes                # collapse history into 1 migration
tempest db backup                                # dump to backups/<db>_<ts>.<ext>
tempest db backup -o dump.sql                    # plain SQL (Postgres) by extension
tempest db restore dump.dump --yes               # restore (clean + recreate)
tempest db seed                                  # runs src.db.seeds:seed
tempest db seed --seed src.db.fixtures:demo      # custom callable
```

!!! danger "`stamp head` does not adopt an existing database"
    `stamp` writes the pointer without running anything. Against a schema
    Alembic did **not** build, stamping `head` records revisions that never ran
    as applied: the database keeps its old columns while `current`, `upgrade`
    and `history` all answer that it is up to date, and the failure surfaces
    weeks later in production. The right revision for adoption is the **base** —
    see [Migrations »](migrations.en.md), which ships `AlembicHelper.adopt()`
    and `sync_schema()` so the choice is not made by hand.

#### When Alembic refuses the command

An Alembic error describing the **state of the database** — not a bug — comes
out as one line, with what to do about it:

```console
$ tempest db revision -m "new field" --autogenerate
error: the database is behind head — a migration is pending. Run `tempest db upgrade`, then try again.
       (TEMPEST_DEBUG=1 for the full traceback)
$ echo $?
1
```

Four conditions get advice: the database is behind head, the history has more
than one head, the revision is not in `alembic/versions/`, and two revisions
are not on the same branch. Any other message is printed **exactly as Alembic
wrote it** — a wrong hint would be worse than the raw words.

!!! tip "Want the traceback back?"
    `TEMPEST_DEBUG=1 tempest db revision ...` re-raises untouched. Use it when
    the error is **not** one of the conditions above and you need to see where
    it came from.

!!! note "Only the CLI translates"
    Calling `AlembicHelper.revision(...)` directly, from a script or a
    lifespan, still raises `alembic.util.exc.CommandError`. Code wants the
    typed exception; a person at a terminal wants the sentence.

#### Collapse the history — `tempest db squash`

Over time the `alembic/versions/` directory grows without bound — every schema tweak adds another file Alembic must walk on every `upgrade`. `squash` resets that history to a **single root migration** describing the **current** schema, while keeping existing databases usable.

!!! danger "Destructive — run it against a development database"
    `squash` runs `downgrade base` on the configured database (DROPs every table) so it can autogenerate the full schema into a single file. That's why it requires `--yes`. Make sure `DATABASE_URL` points at a dev database before running.

The flow is:

1. Capture the current `head` (used to name the backup directory).
2. `downgrade base` — empty the database so autogenerate sees an empty schema.
3. Move the old revisions into `alembic/versions/_squashed_<oldhead>/` (a subdirectory Alembic ignores). Pass `--no-backup` to delete them instead.
4. Autogenerate **one** root migration from `BaseModel.metadata`.
5. `upgrade head` recreates the schema and stamps the new revision.

```bash
tempest db squash -m "init" --yes               # recoverable backup (default)
tempest db squash -m "init" --yes --no-backup   # delete the old files
```

!!! warning "Production databases are not touched"
    `squash` only touches the configured database. After deploying the collapsed tree, mark production databases as migrated **without** recreating tables:

    ```bash
    tempest db stamp head
    ```

!!! tip "Manual squash (keep your data) → `stamp --purge`"
    If you unify migrations **by hand** (delete `versions/`, write one baseline migration, keep the database with its data), `alembic_version` still points at the old revision — which no longer exists in the tree. A plain `stamp` fails with `Can't locate revision`. Use `--purge` to clear the stale pointer and record the new baseline:

    ```bash
    tempest db stamp init_schema --purge
    ```

**Recap:** `squash` swaps an ever-growing history for one clean initial migration; `stamp` reconciles databases already at the final schema (use `--purge` when the recorded revision no longer exists). Recoverable backup by default — Git is your second net.

#### Backup and restore — `tempest db backup` / `tempest db restore`

Snapshot the database to a file and back. The strategy differs per dialect, but the CLI is the same:

- **PostgreSQL** — `pg_dump` / `pg_restore`. The format comes from the file extension: `.dump` → custom (`pg_dump -Fc`, compressed, restored with `pg_restore`), `.sql` → plain (`psql`). Force it with `--plain` / `--custom`. Requires the PostgreSQL client tools on `PATH`.
- **SQLite** — copies the database file.

!!! warning "Prerequisite: PostgreSQL client tools"
    `backup` / `restore` against a **PostgreSQL** database depend on the `pg_dump`, `pg_restore` and `psql` binaries being on your `PATH`. They do **not** ship with the Python package — they are installed by your operating system. Without them the CLI fails with a clear message (`'pg_dump' not found on PATH`).

    **SQLite needs nothing** — the backup is a stdlib file copy.

    === "Debian / Ubuntu"
        ```bash
        sudo apt-get update && sudo apt-get install -y postgresql-client
        ```

    === "Fedora / RHEL"
        ```bash
        sudo dnf install -y postgresql
        ```

    === "Arch"
        ```bash
        sudo pacman -S postgresql
        ```

    === "macOS (Homebrew)"
        ```bash
        brew install libpq && brew link --force libpq
        ```

    === "Windows"
        ```powershell
        choco install postgresql   # Chocolatey
        scoop install postgresql   # or Scoop
        ```

    !!! tip "Verify the install"
        ```bash
        pg_dump --version && pg_restore --version && psql --version
        ```
        Match the client major version to the server (a newer `pg_dump` reads older servers, but not the other way around).

```bash
tempest db backup                       # backups/<db>_<YYYYMMDD-HHMMSS>.dump
tempest db backup -o snapshot.sql       # plain SQL (Postgres) by extension
tempest db backup -o snap.dump --custom # force the custom format
tempest db restore snapshot.sql --yes   # restore (psql)
tempest db restore snap.dump --yes      # restore (pg_restore --clean --if-exists)
```

!!! danger "Restore overwrites the target database"
    By default the restore is **clean + recreate**: existing objects are dropped before being recreated, so the result is a faithful copy of the backup (`pg_restore --clean --if-exists`; plain drops/recreates the `public` schema; SQLite overwrites the file). That's why it requires `--yes`. Pass `--no-clean` to apply the dump on top of the current schema.

!!! info "The Postgres password never leaks into `ps`"
    The URL is parsed into `-h/-p/-U/-d` and the password is passed via `PGPASSWORD` in the subprocess environment — never on the command line.

**Recap:** `backup` takes a snapshot (format by extension on Postgres, file copy on SQLite); `restore --yes` brings it back, cleaning the target by default.

#### Catch model drift — `tempest db check`

`tempest db revision --autogenerate` records a diff when you ask for one.
Nothing notices when somebody edits a model and **forgets** to ask — until
the deploy that needed the column.

```bash
tempest db check
```

No drift:

```text
No drift: the models match the migration tree.
```

With drift, it names the operations a revision would contain and exits 1:

```text
error: New upgrade operations detected: [('add_column', ...'colour'...)]
       Run `tempest db revision -m "<message>"` to record them.
```

!!! tip "This is a CI step"
    Run it after `tempest db upgrade` in the pipeline. The comparison needs
    the database at `head` — when it is behind, Alembic says so and the
    message is passed through instead of being reported as drift.

#### Seed the database — `tempest db seed`

Runs a project seed callable inside a managed session (commit on success, rollback on error). The callable takes a positional `AsyncSession` and may be sync or async; what it inserts is up to you — the SDK only wires the session lifecycle. Defaults to importing `src.db.seeds:seed`.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CategoryModel


async def seed(session: AsyncSession) -> int:
    """Seed initial categories. Return the count (optional)."""
    session.add_all([CategoryModel(name="Books"), CategoryModel(name="Games")])
    await session.flush()
    return 2
```

When the callable returns an `int`, the CLI prints the count: `Seeded via src.db.seeds:seed (2 rows).`

### Users — `tempest user`

Seed and list users using the project's concrete ``UserModel`` (default ``src.db.models:UserModel``). Bootstraps the first admin without manual SQL.

```bash
# Create a regular user
tempest user create --email ana@example.com --password strong-pass-12 --no-admin

# Create an admin (can log into /admin)
tempest user create --email admin@local --password admin-pass-12 --admin

# Read the password interactively (never lands in shell history)
tempest user create --email admin@local --admin

# Custom model outside the scaffolded layout — MUST be a BaseUserModel subclass
tempest user create --email x@y --password pass-12-chars --model myapp.models.user:UserModel

# Columns YOUR UserModel adds — repeatable, one per column
tempest user create --email ana@example.com --password strong-pass-12 \
    --set display_name=Ana --set locale=pt-BR

# Promote / demote an existing user (toggles is_admin)
tempest user promote --email ana@example.com    # becomes admin
tempest user revoke  --email ana@example.com    # back to a regular account

# Replace an existing user's password (prompts twice) and revoke
# their sessions along with it
tempest user set-password --email ana@example.com

# Refresh-token table outside src.db.models
tempest user set-password --email ana@example.com \
    --refresh-token-model myapp.models:UserRefreshTokenModel

# Replace the password and leave the sessions alive (explicit opt-out)
tempest user set-password --email ana@example.com --keep-sessions

# First privileged account born with MFA + audit event in the same transaction
tempest user create --email staff@example.com --admin --require-mfa \
    --audit-model src.db.models:AuditLogModel --event staff.bootstrapped

# Account that lost the phone and the recovery codes: remove the second
# factor, end the sessions and audit it in the same transaction
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel

# List
tempest user list                                # everyone
tempest user list --admin                        # admins only
```

!!! info "Accounts with MFA: `--require-mfa`, `set-password` and `mfa-reset`"
    `create --require-mfa` enrolls the second factor **before** the commit:
    it prints the `otpauth://` URI and the secret, asks for a code from the
    Authenticator app (up to 3 tries) and writes only with a valid one —
    without it, it exits with code 1 and no row is written. It needs
    `MFAMixin` on the `UserModel` and the recovery-code table
    (`--recovery-code-model`, default `src.db.models:UserRecoveryCodeModel`);
    the recovery codes are printed once, after the commit. `--audit-model` (a
    `BaseAuditLogModel` with `AuditRequestMixin`) writes `record_event` in the
    same transaction, named by `--event` (default `user.created`); without it,
    the command says on stderr that no event was recorded.

    `set-password` on an account with MFA active (and `AUTH_MFA_ENABLED` on,
    as at login) asks for the code — TOTP or recovery code — and changes the
    password through `UserAuthService.change_password_with_mfa`. Step by step
    in the [MFA recipe](mfa.md#bootstrapping-the-first-privileged-account).

    Whoever lost the phone **and** the recovery codes has no code to give:
    `mfa-reset` is the way back — see
    [Recovering an account that lost its second factor](#mfa-reset) below.

!!! tip "A `UserModel` with a required column of its own: use `--set`"
    `create` fills `email`, `hashed_password`, `is_admin` and
    `is_active`. Every **other** column of your `UserModel` goes in via
    `--set <column>=<value>`, validated against the mapped columns and
    converted to the column type (bool, int, float, `Decimal`, UUID,
    ISO-8601 date/time, enum by value, and JSON).

    ```bash
    tempest user create -e newbie@example.com -p 'strong-pass-12' --no-admin
    # error: UserModel requires a value for: display_name.
    #        Pass each one as --set <column>=<value>.
    ```

    A `NOT NULL` column with no default that you did not pass is
    **prompted for** in an interactive terminal (one prompt per column)
    and becomes an exit-code-2 error without a TTY — instead of the raw
    `IntegrityError` the database used to raise. An unknown key also
    exits 2, listing the accepted columns; `--set email=` /
    `--set hashed_password=` / `--set is_admin=` are refused, pointing at
    the flag that owns them (`--email`, `--password`,
    `--admin/--no-admin`).

    An insert the database refuses (a duplicate email, most often) exits
    with code 1 and the database's own message —
    `error: could not insert user: …` — instead of a traceback.

!!! tip "Without `--admin`/`--no-admin`, create asks"
    When you pass **neither** `--admin` nor `--no-admin` in an
    interactive terminal, `tempest user create` prompts
    `Should this user be an administrator? [y/N]`. Non-interactive runs
    (CI, pipes, scripts) skip the prompt and create a regular user
    (`is_admin=False`) — pass `--admin` explicitly to create an admin
    without a TTY.

`tempest user promote` / `tempest user revoke` find the user by email (case-insensitive) and only flip `is_admin`. When no user matches the email they exit with code 1 and a `no user found` message.

#### Changing a password — `tempest user set-password`

The operator's half of the reset flow: the account nobody can mail a link to, the first admin locked out of a fresh environment, the credential rotated after a report. It finds the user by email (case-insensitive), hashes the new plaintext with the model's own `set_password` — the very hash the login endpoint verifies — and exits with code 1 and `no user found` when nobody matches.

```bash
tempest user set-password --email ana@example.com
# Password:
# Confirm:
# Password updated for ana@example.com (id=6f0c...)
# Revoked 2 active refresh token(s).
```

!!! check "Changing the password revokes the sessions — by default"
    Changing the hash does not, on its own, end any session: an access
    token already issued stays valid until it expires, and a DB-backed
    refresh token can still be exchanged for a fresh one — exactly the
    window the reset exists to close. So the revocation is **not a flag
    you have to remember**: the command resolves
    `src.db.models:UserRefreshTokenModel` (the conventional name of the
    opt-in refresh-token table — `tempest new` does **not** scaffold it;
    whoever turns the table on writes the model under that name in
    `src/db/models/`) and revokes the user's refresh tokens **in the same
    transaction** that writes the new hash.

    - Table somewhere else? `--refresh-token-model myapp.models:UserRefreshTokenModel`.
      A spec you typed that does not resolve is an exit-code-2 error — it
      never degrades silently into "revoked nothing".
    - Do not want the revocation? `--keep-sessions`, which is refused
      with code 2 when it comes together with `--refresh-token-model`
      (the two flags contradict each other).
    - A project that never wired the opt-in refresh-token table has
      nothing to revoke, and the command says so on `stderr`:
      `note: no refresh-token model at 'src.db.models:UserRefreshTokenModel', so no session was revoked. Pass --refresh-token-model if yours lives elsewhere.`

    The count is of the sessions **this** run killed — an already
    revoked row is not counted again, so running it twice prints
    `Revoked 0 active refresh token(s).`

!!! tip "The same thing, from code"
    `await service.revoke_user_sessions(session, user_id=user.id)` is the
    administrative counterpart of `revoke_refresh_token`:
    logout-everywhere addressed by id, for when you do not hold the token
    (a "sign out everywhere" endpoint, a support agent locking a
    compromised account). It returns how many rows it revoked, and `0`
    when the project does not use `refresh_token_model` — stateless JWTs
    carry no row to flip.

    ```python
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk import UserAuthService


    async def force_logout(
        user_id: UUID,
        session: AsyncSession,
        service: UserAuthService,
    ) -> int:
        """Revoke every session of a user. Returns how many fell."""
        killed = await service.revoke_user_sessions(session, user_id=user_id)
        await session.commit()
        return killed
    ```

!!! info "The password policy is your project's, not the CLI's"
    `create` and `set-password` both check the plaintext with
    `check_password_policy` against the three `AUTH_PASSWORD_*` fields of
    the project's `src.core.settings` — and against the `PasswordPolicy`
    defaults (minimum 12 characters, maximum 72 **bytes**) when the
    project does not compose `AuthSettings`. It is the same ruler
    `signup` and the admin panel use, so a password the CLI accepts is
    always one the account owner can log in with.

    The ceiling is measured in **UTF-8 bytes**, the unit bcrypt counts:
    40 accented characters are 80 bytes and exit with code 2 and
    `error: password must be at most 72 bytes.` — before anything is
    written to the database.

``DATABASE_URL`` resolves the same way as ``tempest db`` (env var > settings instance or factory > ``.env`` > ``alembic.ini``).

#### Recovering an account that lost its second factor — `tempest user mfa-reset` { #mfa-reset }

An account with MFA active whose owner lost the phone **and** the recovery codes: `set-password` asks for the code, and there is no code to give. `mfa-reset` removes the factor **without proof** — so it asks for confirmation and records who did it.

```bash
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel
# Remove the second factor of staff@example.com? It will log in with the password alone until it enrolls again. [y/N]: y
# MFA reset for staff@example.com (id=2ced...)
# Revoked 2 active refresh token(s).

# Outside a terminal (CI, a script): --yes is required
tempest user mfa-reset --email staff@example.com --yes \
    --audit-model src.db.models:AuditLogModel --event support.mfa_recovered

# Remove the factor and leave the sessions alive (explicit opt-out)
tempest user mfa-reset --email staff@example.com --yes --keep-sessions
```

- It runs `UserAuthService.mfa_reset`: clears `totp_secret`, `totp_enabled_at` and `totp_last_step` and deletes the recovery codes (`--recovery-code-model`, default `src.db.models:UserRecoveryCodeModel`; without a table, it exits with code 2 naming the flag).
- **It revokes the sessions by default**, in the same transaction, with the same rules as `set-password`: `--refresh-token-model` (default `src.db.models:UserRefreshTokenModel`), `--keep-sessions` as the explicit opt-out, and both together refused with code 2.
- `--audit-model` (with `AuditRequestMixin`) writes `record_event` in the **same transaction**, named by `--event` (default `user.mfa_reset`); if the audit write fails, the reset rolls back with it. Without `--audit-model`, the command says on stderr that no event was recorded. `--event` without `--audit-model` exits with code 2.
- Without confirmation nothing changes: outside a terminal, without `--yes`, it exits with code 1; in a terminal, answering `n` exits with 1 too.
- An account **without MFA active** exits with code 2 (`has no MFA active, nothing to reset.`); a `UserModel` without `MFAMixin` does too, before the database is opened. An unknown email exits with 1 and `no user found`.

After the reset the account logs in with the password alone until it enrolls again, and `set-password` stops asking for a code. The MFA recipe walks the path, including the direct call from code: [An account that lost its second factor](mfa.md#mfa-reset).

#### Inspect, deactivate and delete

`list` answers "who exists". The four below answer the rest.

```bash
# The whole row, including the columns YOUR model adds
tempest user show --email ana@example.com
tempest user show --email ana@example.com --json

# Turn access off (reversible) and drop the sessions with it
tempest user deactivate --email ana@example.com
tempest user activate --email ana@example.com

# That user's sessions (refresh tokens)
tempest user sessions --email ana@example.com
tempest user sessions --email ana@example.com --revoke

# Delete the row (irreversible)
tempest user delete --email ana@example.com --yes
```

`show` **redacts `hashed_password`**: no question this command answers needs
the hash, and printing it puts a credential in terminal scrollback and in CI
logs.

!!! warning "`deactivate` revokes sessions, and that is not optional"
    Flipping `is_active=False` ends nothing on its own: a refresh token
    already issued stays exchangeable, and the account you just turned off
    keeps minting fresh access tokens. So the revocation runs in the **same
    transaction** as the flag — the rule `set-password` already follows.

`sessions` without `--revoke` prints one line per token (id, family, created,
expires, state). A user with no session prints an empty table and exits 0 —
"not logged in anywhere" is an answer, not a failure. The `--revoke` count is
the number of sessions **that run** killed, so a second run prints
`Revoked 0`.

!!! danger "`delete` is irreversible, and takes the tokens with it"
    The scaffolded refresh-token table carries a `ForeignKey` with no
    `ON DELETE CASCADE`, so deleting a user with a live token would fail with
    an integrity error naming a constraint rather than the cause. The command
    removes the tokens in the same transaction. Outside an interactive
    terminal, `--yes` is required. When what you mean is "this person should
    not be able to log in any more", the command is `deactivate`.

!!! note "`deactivate` blocks login, it is not a ban"
    A deactivated account cannot log in, but its holder can reactivate it on
    their own: `POST /auth/activation/request` mails a fresh activation link
    to the account's address, and opening it makes the account active again.
    That is intended — whoever holds the mailbox gets back in. To close the
    door for good, pass `refuses_email_link` to `UserAuthService` with a
    predicate matching that account: activation and reset by e-mail are then
    refused for it (see the [MFA recipe](mfa.md#email-link-refusal)).

### Secrets — `tempest secrets`

Four commands, ordered by how much they touch your `.env`:

| Command | Writes to `.env`? | When to use it |
| --- | --- | --- |
| `generate` | never | grab a value to paste into a secret manager |
| `init` | only what is empty or a placeholder | right after `tempest new` |
| `rotate` | always, with a backup | the secret leaked or went stale |
| `vapid` | the Web Push key pair | turn on browser notifications |

#### Generate a loose value — `tempest secrets generate`

Touches no file at all: it prints and stops.

```bash
# One secret, bare value
tempest secrets generate

# Three at once
tempest secrets generate --count 3

# Labelled, ready to paste into a .env or a deploy form
tempest secrets generate --keys JWT_SECRET,TOKEN_SECRET

# JSON, to pipe into a secret manager
tempest secrets generate --keys JWT_SECRET --json
```

Output of the third one:

```text
JWT_SECRET=_Ptx1Cq4lUAK4EJX7GAnXMp115d6i92I_rrTDJ3sS59noFLm3N1wTWL-F0OhCkbr
TOKEN_SECRET=qNsIyp8hp4BZll_BzcDS7omceBNB4a4EI9JVWvIQEg_R5VveTY0IuLBiogp0uRBa
```

!!! tip
    `--count` and `--keys` say the same thing (how many secrets come out), so
    passing both is a usage error rather than one of them silently winning.

#### Fill a freshly scaffolded `.env` — `tempest secrets init`

`tempest new` writes `JWT_SECRET=change-me-change-me-change-me-32` and an
empty `TOKEN_SECRET=` — exactly the two cases `tempest check-config`
reports as `security.W001` / `security.W004`. `init` replaces only those:

```bash
tempest secrets init
```

```text
set JWT_SECRET (was placeholder)
set TOKEN_SECRET (was empty)
```

A key that already holds a real value is **kept**, so running it again does
nothing:

```text
kept JWT_SECRET (already set)
kept TOKEN_SECRET (already set)
Nothing to do.
```

That is what separates `init` from `rotate`: `init` is safe to run at any
moment — including from a setup script — because it never destroys a secret
in use. Replacing a real value is explicit opt-in, and then it backs the
file up first:

```bash
tempest secrets init --force
```

#### Rotate — `tempest secrets rotate`

Generates and rewrites the matching `.env` lines **in place** — backing up
the old file first — and leaves every other line untouched.

```bash
# Rotate JWT_SECRET and TOKEN_SECRET in .env (writes .env.bak)
tempest secrets rotate

# Just print the new values (writes nothing)
tempest secrets rotate --print

# Custom keys and file
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod

# More entropy, no backup
tempest secrets rotate --length 64 --no-backup
```

!!! warning
    Rotating `JWT_SECRET` invalidates every token signed with the old value: users are logged out and pending reset/activation links stop working. Rotate during a maintenance window and restart the service to load the new values.

#### Web Push keys — `tempest secrets vapid`

Web Push does not use a random string: it uses a P-256 key pair. The browser
subscribes with the public key and the server signs every push with the
private one, so the two values are born together and cannot come out of
`generate`.

```bash
# Writes VAPID_PUBLIC_KEY + VAPID_PRIVATE_KEY into .env
tempest secrets vapid

# With the contact carried in the VAPID JWT's `sub` claim
tempest secrets vapid --subject mailto:ops@example.com

# Print only
tempest secrets vapid --print
```

These are exactly the fields `WebPushSettings` reads, in the shape
`pywebpush` reads back: the private key is the 32-byte scalar and the public
key is the 65-byte uncompressed point, both base64url without padding.

!!! danger
    Replacing a pair that already exists **invalidates every active
    subscription**: a browser that subscribed with the old public key will not
    accept a push signed with the new private one, and each client has to
    subscribe again. That is why the command refuses to overwrite without
    `--force`.

#### Recap

- `generate` prints, `init` fills what is missing, `rotate` replaces
  everything, `vapid` mints the Web Push pair.
- `init` is idempotent and never loses a secret in use — it is the command
  for a project's first day.
- `rotate` and `vapid --force` are destructive in different ways: one logs
  users out, the other drops push subscriptions.
- Every file these commands write ends up `0600`.

### Queue and tasks — `tempest queue` / `tempest tasks`

```bash
# Publish a message through the project's broker
tempest queue publish orders.paid '{"order_id": 7}' --json
tempest queue handlers                     # channels this service consumes

# Background tasks
tempest tasks list                         # registered names
tempest tasks run src.tasks:send_welcome --arg ana@example.com --kwarg retries=2
```

`publish` connects, publishes and **closes** — in that order, in one
process. Closing is the step a throwaway script forgets, and it is the one
that flushes the publish before the command returns.

!!! tip "A task's name is not its function's name"
    TaskIQ registers under `<module>:<function>` (`src.tasks:send_welcome`),
    so the name `run` takes is not the one in the source file. `list` is what
    tells you which is which — and it imports `<root>.tasks.jobs` first,
    because a task exists on the broker only after the module declaring it
    has been imported.

`tasks run` **enqueues**; it does not execute. The worker runs the job, so a
command that returns successfully means the message was accepted, not that
the job succeeded.

Without `--broker` / `--queue`, both probe `<root>.queue:broker` and
`<root>.tasks:tq` — the names `tempest generate --src` writes. When nothing
resolves, every attempt is listed with its cause before exit 2.

### Email — `tempest email test`

The pair that costs the most time in SMTP setup is STARTTLS (port 587,
`SMTP_USE_TLS`) against implicit TLS (port 465, `SMTP_USE_SSL`), and the only
honest way to learn which one a server wants is to connect and send.

```bash
tempest email test --to you@example.com
tempest email test --to you@example.com --subject "deploy ok" --body "..."
tempest email test --to you@example.com --html      # exercises the multipart path
```

It builds the client with `EmailUtils(**settings.email_kwargs())` — the same
construction the service performs — so a message that arrives here arrives
from the app. A refusal exits 1 carrying the server's own reply, plus the
line that recalls the TLS pair.

A project that composes no `EmailSettings` exits 2 saying so: composing only
the mixins you use is ordinary, not broken.

### Object store — `tempest storage`

```bash
tempest storage check                       # does the endpoint answer? bucket there?
tempest storage ls                          # keys in the default bucket
tempest storage ls reports/ --flat          # only the immediate level
tempest storage put ./note.pdf --key notes/2026-09.pdf
tempest storage get notes/2026-09.pdf --out ./downloaded.pdf
tempest storage presign notes/2026-09.pdf --expires 900
tempest storage rm notes/2026-09.pdf
```

Everything goes through `AsyncMinIOClient(**settings.storage_kwargs())`, so the
endpoint, credentials, region and — the one that matters most — the **public
endpoint** used to sign URLs are the service's own.

!!! warning "A URL signed with the internal endpoint is valid and useless"
    Signing against `minio:9000` produces a correct URL your user's browser
    cannot resolve. Set `STORAGE_PUBLIC_ENDPOINT`; `presign` prints on `stderr`
    which host it signed for, precisely so that mistake surfaces before the
    URL reaches a client.

An empty bucket lists nothing and exits 0 — "no object" is a result, not a
failure, the same convention the SDK's repositories follow.

### Feature flags — `tempest flags`

A flag exists to be moved without a deploy. With the Redis backend that move
lived in `redis-cli HSET feature_flags <name> 1` — the kind of command that
gets a digit wrong at the wrong hour.

```bash
tempest flags list                       # everything the backend holds
tempest flags enable new-checkout        # on for every process reading it
tempest flags disable new-checkout
tempest flags get new-checkout           # the stored value
tempest flags list --key ff:staging      # another hash, another environment
```

```text
new-checkout  on
legacy-upload off
```

All four speak through `RedisFeatureFlagBackend`, so the value the service
reads and the value the CLI writes go through the same encoding code.

!!! note "`off` and `unset` are different answers"
    `get` exits **1** when the backend holds no value (`unset`) and **0** when
    it holds `off`. The difference matters: an unset flag resolves to the
    *default* the caller passed in code, while `off` overrides that default.

The URL comes from `--redis-url` > `REDIS_URL` > the project's settings. A
Redis that is down exits 2 carrying the driver's own message — "connection
refused" names the fix, "an error occurred" does not.

### Cache — `tempest cache`

```bash
tempest cache ping                       # PONG, or exit 1
tempest cache stats                      # version, keys, memory, hit ratio
tempest cache flush --namespace orders   # invalidate what @cached registered
tempest cache flush --tag user:42
tempest cache flush --key "cache:orders:list:abc"
tempest cache flush --all --yes          # FLUSHDB
```

The targeted options go through `CacheInvalidator` — the same code the
service calls — so the keys deleted here are the keys `@cached` wrote there.
Pass the same `--key-prefix` those decorators use, or the registries will not
line up.

!!! danger "`--all` is `FLUSHDB`, not 'clear the cache'"
    It deletes **every** key in the selected database: sessions, rate-limit
    counters and feature flags included, when they share the Redis. That is
    why it demands `--yes`. When in doubt, invalidate by namespace.

!!! info "The hit ratio is the server's, not your application's"
    `keyspace_hits` / `keyspace_misses` are Redis counters since the last
    restart, summed across every client. Redis does not attribute a hit to a
    caller — the number answers "is this server earning its keep", not "is my
    endpoint X".

### Models — `tempest model`

Analyze, benchmark, convert and quantize ONNX models. Needs the
`[modelops-onnx]` extra, except `hardware`, which only reports what this
host can measure:

```bash
tempest model analyze models/classify.onnx
tempest model bench models/classify.onnx --dim height=224 --dim width=224
tempest model quantize models/classify.onnx models/classify.int8.onnx
tempest model export-ort models/classify.int8.onnx -o dist/mobile -t arm
tempest model hardware
```

| Command | What it does |
| --- | --- |
| `analyze` | Parameters, size, opset and shapes, without running the model. |
| `bench` | Latency (median/IQR/p95/p99), RAM, GPU and energy over N repetitions. |
| `optimize` | Persists ONNX Runtime's graph optimizations into a new `.onnx`. |
| `quantize` | Dynamic int8 quantization. |
| `export-ort` | Converts to `.ort` plus `.required_operators.config`. |
| `hardware` | What the host runs and which energy sampler is available. |

`analyze`, `bench` and `hardware` accept `--json`, which makes them usable
as a CI step. A missing extra exits 2 with the install line, never a
traceback. Details in [Modelops](modelops.md).

### Diagnostics — `tempest doctor`

`check-config` reads the settings and reasons about them; it never opens a
socket. That is the right trade for a fast gate and the wrong one for "why
does the service not come up", where the answer is almost always a dependency
that is down, unreachable from this host, or holding credentials nobody
updated.

```bash
tempest doctor
tempest doctor --json            # for scripts / deployment health checks
tempest doctor --timeout 2       # tighten the wait on each network probe
```

```text
OK    python         3.11.12 (/srv/app/.venv/bin/python)
OK    sdk            tempest-fastapi-sdk 0.293.0
OK    settings       Settings
OK    database       postgresql+asyncpg://app:***@db:5432/app
FAIL  redis          ConnectionError: Error 111 connecting to cache:6379
SKIP  rabbitmq       no RABBITMQ_URL
SKIP  smtp           SMTP_HOST is still the mixin default
SKIP  minio          the [minio] extra is not installed
OK    config checks  no error (2 warning(s))
```

Every line is a **real connection**, not a deduction. It exits 1 when any
check fails, so it works as a deployment smoke test.

!!! warning "`skip` is not `ok`, on purpose"
    A capability the project never configured reads `skip`. A green line for a
    Redis nobody set up is the kind of reassurance that costs an outage.

    The mixin default counts as unconfigured: `EmailSettings` ships
    `SMTP_HOST=localhost` and `StorageSettings` ships
    `STORAGE_ENDPOINT=localhost:9000`, so a service composing the mixin without
    using the capability would look configured. The comparison is against
    `model_fields[field].default` — the same ruler `check_secrets` uses.

The last line runs the static check registry (the one behind `check-config`),
so one command answers both halves of "is this deployment sane".

### Routes — `tempest routes`

Lists the URLs the application **actually** serves.

```bash
tempest routes
```

```text
METHOD  PATH                    NAME          SCHEMA  GUARDS
GET     /api/items/{item_id}    read_item     yes     require_admin
POST    /api/items/             create_item   yes     require_admin
GET     /health                 health        yes     -
GET     /painel                 painel        no      -
```

Four things in that table reading the code does not hand you:

- **A mounted router shows up.** Since FastAPI 0.141.1, `include_router`
  keeps an `_IncludedRouter` entry instead of flattening the routes onto the
  application — so `{r.path for r in app.routes}` contains **none** of the
  included router's paths. The command reads the same expansion the OpenAPI
  generator walks.
- **Prefixes already resolved.** The path is the router's plus every
  `include_router` above it.
- **Routes outside the schema are listed too**, with `SCHEMA=no` — an HTML
  page and an internal endpoint serve traffic, and they are exactly what an
  answer taken from `openapi.json` hides.
- **`GUARDS` shows the dependency inherited from the include**, not only the
  one declared on the route. That is how you confirm the
  `Depends(require_admin)` on `include_router` reached everything it should.

Filters and format:

```bash
tempest routes --match /api/orders        # only paths containing this
tempest routes --method post,delete       # only these methods
tempest routes --all                      # include /docs, /redoc, /openapi.json
tempest routes --json                     # JSON array, for scripting
tempest routes --app src.server:app       # app outside the conventional spots
```

Without `--app`, the command probes `src.server:app`,
`src.api.app:create_app`, `main:app` (and the same under `app/`). When
nothing resolves it **lists what it tried and why** before exiting 2. A
`--match` that matches nothing exits 1, so a typo in a script fails instead
of reading as an application with no routes.

### Errors documented in OpenAPI — `tempest openapi-errors`

Compares, per route, the `AppException`s the flow can raise against what the
route declared in `error_responses(...)` / `@raises(...)`:

```bash
tempest openapi-errors                          # advisory report (exit 0)
tempest openapi-errors --check                  # exit 1 on drift (CI gate)
tempest openapi-errors --path src --path libs   # explicit directories, repeatable
tempest openapi-errors --check --allow-unreachable   # fail only on undocumented
tempest openapi-errors --fix --dry-run          # diff of the missing declarations
tempest openapi-errors --fix                    # write (needs a clean git tree)
```

Without `--path` it scans `./src` or `./app` — whichever exists. The analysis is
static (`ast`, without importing the application) and walks `router ->
controller -> service -> repository`, reading both `raise` statements and the
docstrings' `Raises:` sections.

```text
src/api/routers/jobs.py:15  POST /{service_id}/candidates
  undocumented: CandidateAlreadyExistsException, ServiceFullException
1 route(s) with drift, 2 undocumented exception(s).
```

`--fix` closes the gap by writing `responses=error_responses(...)` into the route
(plus whatever imports are missing), extending an existing declaration instead of
replacing it. It only ever adds: `unreachable` findings are never removed, since
reachability cannot see a dynamic raise.

Details and limitations in the [Errors in OpenAPI »](openapi-errors.md) recipe.

---

### Permission guards — `tempest permissions`

Reads the `@requires` guard contract straight off the source (`ast`, without
importing the application) and reports what the decorator cannot see at import
time:

```bash
tempest permissions                    # informative report (exit 0)
tempest permissions --check            # exit 1 on any error (CI gate)
tempest permissions --check --strict   # fail on warnings too
tempest permissions --path src --path libs
```

```text
src/api/routers/orders.py:41  delete_order
  error: guard-foreign-exception: guard 'order_owner' raises ValueError, which is
    not an AppException subclass; the API layer answers it as HTTP 500 without an
    error code
2 finding(s), 1 error(s).
```

Errors: a guard raising outside the `AppException` hierarchy, a predicate-style
guard (`-> bool`), wrong arity, an `async` guard on a sync function, a route with
no user parameter. Warnings: a guard that never denies, a missing annotation, a
guard the checker could not resolve (lambda, duplicated name, definition outside
the scanned paths).

Details in the [Permission guards (`@requires`) »](permission-guards.md) recipe.

---

### Integrations — `tempest integrations`

```bash
tempest integrations list                    # what ships + the credential state here
tempest integrations verify openpix          # one real authenticated call
tempest integrations verify mercado-pago
tempest integrations verify zap --base-url http://localhost:3000
```

In a project fresh out of `tempest new`, with no credential in the `.env`:

```text
openpix       tempest_fastapi_sdk.integrations.payment.openpix:OpenPixClient
              OPENPIX_APP_ID not set
mercado-pago  tempest_fastapi_sdk.integrations.payment.mercado_pago:MercadoPagoClient
              MERCADOPAGO_ACCESS_TOKEN not set
stripe        tempest_fastapi_sdk.integrations.payment.stripe:StripeClient
              no settings mixin (build the client yourself)
zap           tempest_fastapi_sdk.integrations.messaging.zap:ZapClient
              no settings mixin (build the client yourself)
```

`verify` builds the client with `HTTPClient(**settings.<provider>_kwargs())`
and makes **the cheapest authenticated read** each API offers: OpenPix's
`GET /company` and Mercado Pago's `GET /users/me` — neither moves money nor
creates a record. A bad credential exits 1 carrying the provider's refusal.

!!! note "That endpoint is a hand-kept table — with a guard"
    Choosing "the cheapest call" per provider is a human decision, and
    generated endpoint names change. A test asserts each named method still
    exists on the generated client **and** takes no required argument, so an
    upstream rename breaks a test instead of the terminal of whoever is
    on call.

The two providers the SDK ships no settings mixin for behave differently:

- **zap** needs `--base-url`, because the SDK does not know where your
  instance lives. `verify` calls `health`, which takes no credential, so it
  measures **reachability**, not authorization. Without `--base-url` it
  exits 2; an instance that is down exits 1:

    ```text
    error: zap has no settings mixin, so the CLI does not know where it lives. Pass --base-url.
    error: zap refused the call — ConnectTimeout:
    ```

- **stripe** has no `verify`: the API offers no authenticated read that
  neither spends money nor creates a record. The command exits 2, with or
  without `--base-url`:

    ```text
    error: stripe exposes no cheap authenticated read, so there is nothing the CLI can call without spending money or creating a record. Construct the client in your service.
    ```

### Agents — `tempest agents`

```bash
tempest agents tools --agent src.agents:agent     # which tools it holds
tempest agents run "summarise the report" --agent src.agents:agent --trace
```

`tools` calls no model — no token is spent, and it works with a backend that
has no credentials configured. It is the quick answer to "does this agent
configuration actually see the tool I just registered?".

!!! warning "The exit code follows `succeeded`, not 'raised nothing'"
    A run cut short by its budget (steps or seconds) **still returns text**.
    Treating that text as an answer is the mistake this command refuses to
    make for you: `run` exits 1 and prints the stop reason (`max_steps`,
    `max_seconds`) on `stderr`, with the output on `stdout` either way.

There is no scaffolded `agents` layer, so `--agent` is nearly always needed —
and the command says so rather than pretending a convention exists.

### Integration client — `tempest openapi-client`

Generates Pydantic schemas + a typed HTTP client from a third party's OpenAPI
specification:

```bash
tempest openapi-client https://api.vendor.com/openapi.json --name vendor
tempest openapi-client ./vendor/spec.yaml --name vendor --force
tempest openapi-client <spec> -H "Authorization: Bearer $TOKEN" --schemas-only
```

Writes `<src|app>/integrations/<name>/` with `schemas.py` (one class per
component, with the spec's `title`/`description`/`examples` filled in) and
`client.py` (one `async` method per operation, over an injected `HTTPClient`).

```text
  + src/integrations/vendor/__init__.py
  + src/integrations/vendor/client.py
  + src/integrations/vendor/schemas.py
4 schema(s), 12 operation(s).
```

Details, OpenAPI coverage and limitations in the
[Integration client (OpenAPI) »](openapi-client.md) recipe.

---

### Export the contract — `tempest openapi-export`

`openapi-client` consumes somebody else's spec; this one writes **yours**.

```bash
# Document on stdout
tempest openapi-export

# To a file (JSON by default)
tempest openapi-export --out contracts/openapi.json

# YAML (needs the [openapi] extra)
tempest openapi-export --out contracts/openapi.yaml --yaml
```

Two things come out of that. The first is feeding a generator — `tempest
openapi-client` itself, a TypeScript client generator, a mock server —
**without booting the service**. The second is committing the document, which
turns a contract change into something the reviewer sees:

```bash
tempest openapi-export --out contracts/openapi.json --check
```

```text
error: contracts/openapi.json is out of date:
  + DELETE /api/items/{item_id}
  - GET /api/legacy
  ~ /api/items/{item_id} changed
Run 'tempest openapi-export --out <file>' to refresh it.
```

Exit 1 when it differs, 0 when it is current. Operations added and removed
come first because those are the ones that break consumers; a change inside
an operation reads as `~ <path> changed`. "The spec changed" is not a
reviewable message — which is why the command names what moved.

!!! tip "In CI"
    Run `--check` in the same job as the tests. The day somebody changes a
    status code, a required field or a path, the PR goes red with the exact
    line instead of the client finding out in production.

### PR description with AI — `tempest pr-prompt`

The branch is finally green and the step everyone skips is left: writing the
Pull Request description. Any assistant writes a good one — what it lacks are
the two things that live in the repository: the **template** the team agreed on
and the **diff** the branch actually produced.

`tempest pr-prompt` assembles both into a single prompt and writes it to
`stdout`, so it pipes straight into whichever assistant you run:

```bash
tempest pr-prompt                               # compares against main, prompt on stdout
tempest pr-prompt develop                       # another base
tempest pr-prompt | claude -p                   # pipe it into the assistant
tempest pr-prompt --out pr_prompt.txt           # write a file to paste from
```

The prompt carries three blocks:

1. **The PR template.** The repository's own wins — it is the contract that
   project's reviewers read. These are looked up, in order:
   `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
   `.github/PULL_REQUEST_TEMPLATE/pull_request_template.md`,
   `.gitlab/merge_request_templates/default.md`, `docs/pull_request_template.md`,
   `.pull_request_template.md` and `pull_request_template.md`. With none of
   them, the SDK's bundled template is used.
2. **The rules** that stop the model from returning the template with its
   placeholders still in it: no undecided `Yes/No`, no `_italic instruction_`,
   no section dropped, and no invented migration, env var or dependency that is
   absent from the diff.
3. **The branch context**: commit subjects, the `--name-status` list of changed
   files, and an excerpt of each file's patch.

!!! info "The diff is the one the forge shows"
    Patches are read as `base...head` (three dots) — the merge-base diff, which
    is what GitHub/GitLab display on the pull request. With two dots, every
    commit that landed on `base` after your branch started would be attributed
    to you.

!!! info "What is complete and what is sampled"
    The **commit** list and the **changed-file** list always go in whole — the
    model always knows *what* changed. What is bounded are the **diff
    excerpts**: by default the 10 files with the most changed lines, each cut
    at 1500 characters. So what gets sampled is *how* it changed.

Excerpts are bounded because the whole diff of a large branch does not fit in
the context and is mostly noise (lock file, changelog, generated migration).
Files enter **ordered by changed lines**, not alphabetically — otherwise the
budget is spent on `.github/` and `CHANGELOG.md` before it reaches the file the
PR is actually about:

```bash
tempest pr-prompt --full                        # every file, whole patch
tempest pr-prompt --max-files 20                # 20 files with a patch (default: 10)
tempest pr-prompt --max-files 0                 # file list only, no patch
tempest pr-prompt --max-chars 4000              # more patch per file (default: 1500)
```

`--full` lifts both bounds at once — use it on a branch small enough to send
whole. It **refuses** to run alongside `--max-files` / `--max-chars` (exit `2`):
silently overriding a number you typed would be worse than complaining.

!!! check "Nothing is dropped silently"
    A truncated patch carries the `excerpt cut` mark, and the files left
    without one become an explicit line in the prompt (`N more changed
    file(s)…`). The model reads a partial context as partial instead of taking
    the fragment for the whole change. The cut also respects line boundaries,
    so half a diff line never survives — the model would read it as code that
    does not exist.

The summary (which template was picked, commit/file counts) goes to `stderr`,
so the pipe stays clean:

```text
template: .github/pull_request_template.md
7 commit(s), 12 changed file(s), 10 excerpt(s), 2 file(s) without a patch.
```

Other options:

```bash
tempest pr-prompt --head feat/other             # describe another branch, no checkout
tempest pr-prompt --lang en                     # rules and bundled template in English
tempest pr-prompt -t docs/my_template.md        # explicit template, wins over all
tempest pr-prompt -p ../other-repo              # run against another repository
```

!!! tip "A missing base falls back to `origin/<base>`"
    A fresh clone usually has no local `main`, only `origin/main`. When the base
    does not resolve, the command tries `origin/<base>` before failing. When
    neither exists it exits with code `2` naming the ref it could not find. When
    the comparison holds no commit and no file, it exits `1` — almost always a
    wrong base.

---

### Quality gates

!!! info "These commands come from `tempest-cli` (v0.226.0)"
    The quality gate has nothing to do with FastAPI — it is `ruff`,
    `mypy` and `pytest`. Since v0.226.0 it lives in its own package,
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), which the SDK's
    `[cli]` extra declares and the SDK mounts on its CLI.

    **Nothing changes for you**: `tempest check` is the same command,
    with the same flags and the same `[tool.tempest] typing_strictness`.
    What changes is that people **not** using FastAPI can now install
    the gate alone:

    ```bash
    uv add --dev tempest-cli
    tempest-cli check
    ```

    Why split it: reaching those four commands meant installing FastAPI,
    SQLAlchemy, Alembic and Pydantic — 38.7 MB of dependencies and about
    0.5 s of import time per invocation, measured, for commands that
    touch none of it.

    The two never diverge because there is one implementation: the SDK
    calls `register_commands(app)` from `tempest_cli`. Importing
    `tempest_fastapi_sdk.cli.lint` or `.pr_prompt` keeps working and
    returns the same functions.

The lint commands shell out to the project's tooling, looking for the executable in this order:

1. the activated `$VIRTUAL_ENV`;
2. the nearest `.venv`, walking up from the working directory — the project's environment, even when it was never activated;
3. the directory of the interpreter running `tempest` itself;
4. `PATH`, skipping a version-manager shim (pyenv/asdf) that dispatches nowhere;
5. `uv run --with <tool> <tool>`, when `uv` is on `PATH`.

The project comes before the CLI's own environment on purpose: under `uv tool install` the CLI carries its own `ruff`, and a project that pins another version still runs the one it pinned.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix-only . + ruff format . + ruff check .   (writes)
tempest fix --unsafe                            # also apply ruff's --unsafe-fixes
tempest format                                  # ruff format .          (writes)
tempest fmt-check                               # ruff format --check .   (read-only)
tempest type                                    # mypy .
tempest test                                    # pytest -n auto -p no:cacheprovider  (parallel, the default)
tempest test tests/api/                         # with a path filter
tempest test -w 4                               # four workers
tempest test --serial                           # serially (--lf / --ff, a debugger)
tempest check                                   # lint + fmt-check + type + lint-layers + test, stops at first failure
tempest check --rule IMP002 --strict            # the same gate, lint-layers on that rule only and warnings failing
```

`test` and `check` run the suite in parallel by default since `tempest-cli`
0.6.0 (the `[cli]` extra declares `tempest-cli>=0.7.0`); without pytest-xdist
in pytest's environment, they print a notice and run serially. When to use
it, one database per worker and what to do with a test that fails only in
parallel are in the
[testing recipe](testing.md#the-suite-in-parallel-the-tempest-test-default).

`tempest fix` is the one-shot "organize the project" pass — sorts and dedupes imports, drops unused imports, normalizes string quotes, removes trailing whitespace, then runs `ruff format` to align indentation, line length, blank lines and trailing newlines. Run it before pushing when CI keeps catching style nits.

!!! info "The report comes from the formatted file (`tempest-cli` 0.5.1)"
    `tempest fix` runs three passes: `ruff check --fix-only`, `ruff format`
    and finally `ruff check`, which is where the report and the exit code
    come from. A violation the formatter resolved no longer shows up or
    fails the command. Whatever is left after formatting still exits
    non-zero, so CI keeps failing on the real leftovers.

!!! tip "Long comments and strings are shortened, with an AST proof (`tempest-cli` 0.5.1)"
    `ruff format` never breaks a comment or a string literal — same as
    Black. After it, `tempest fix` wraps each `E501` comment at the limit
    (a trailing comment moves above its line) and splits each plain string
    literal at its spaces into implicitly concatenated parts inside
    parentheses. Every edit is kept only when the file's AST is identical
    before and after, so the program cannot change. With
    `line-length = 88`, this:

    ```python
    """Long lines."""

    MESSAGE: str = "this is a deliberately long plain string literal that goes well beyond the configured line length limit"


    def f() -> int:
        """Return one."""
        return 1  # this trailing comment is deliberately long so that it overflows the configured line length
    ```

    becomes this:

    ```python
    """Long lines."""

    MESSAGE: str = (
        "this is a deliberately long plain string literal that goes well beyond the "
        "configured line length limit"
    )


    def f() -> int:
        """Return one."""
        # this trailing comment is deliberately long so that it overflows the configured
        # line length
        return 1
    ```

    Tool pragmas (`noqa`, `type: ignore`, `fmt:`, …), f-strings,
    triple-quoted strings and words longer than a line are left alone. For
    those lines — and for what the formatter has nowhere to break, such as
    a dotted name — `tempest fix` names the overflowing token, its kind and
    the action that resolves it, and splits the leftovers into those with
    no autofix and those `tempest fix --unsafe` would fix. A long f-string,
    for instance, comes out like this (exit 1):

    ```text
    1 line(s) still too long after ruff format — the line-length setting was applied; the formatter has nowhere to break these:
      src/longs.py:4 [string] ` this is a deliberately long f-string literal that goes well beyond the limit`
        ruff format does not split string literals, and fix could not split this one safely (an f-string, a triple-quoted string, or a word longer than a line). Break it into implicitly concatenated parts inside parentheses.
    1 residual violation(s): 1 with no autofix (edit by hand).
    ```

Every command returns the underlying tool's exit code, so `tempest check` is safe to wire into CI (`tempest check || exit 1`) or pre-commit hooks. When none of the five steps finds the tool, the wrapper exits with `127` instead of failing silently, naming what to install:

```text
error: 'ruff' was not found in this project's environment, on PATH, and 'uv' is unavailable to run it. Install it with 'uv add --dev ruff' — or the whole set with 'uv add --dev "tempest-cli[tools]"' — and retry.
```

### Layer rules — `tempest lint-layers`

The `CLAUDE.md` that `tempest new` writes says how a service and a
repository are shaped. Prose is not a gate: a service with ten services
without `BaseService`, repositories wrapping the SDK's `list`, and loose
functions in a `services/feeds.py` passed `ruff`, `mypy` and `pytest`
clean (#520). `tempest lint-layers` is the gate for those rules (and for the enum naming convention) — and
`tempest check` runs it as a step of its own, after `mypy` and before
`pytest`:

```bash
tempest lint-layers            # the project in the current directory (reads src/ or app/)
tempest lint-layers src        # the source directory itself
```

It reads the code with `ast` and **never imports the project**, so it runs
even with a broken import. Each violation is one `path:line: CODE message`
line, and the message says what to change:

| Code | Where | What it flags |
| --- | --- | --- |
| `SVC001` | `services/` | a public class that does not inherit `BaseService` (directly, through another project service, or through a `*Service` imported from `tempest_fastapi_sdk`) |
| `SVC002` | `services/` | a second public class in the module, or any module-level function — a helper goes to `utils/` |
| `SVC003` | `services/` | an `__init__` that only calls `super().__init__` with its own parameters and/or stores `self.repository` |
| `REPO001` | `repositories/` | a method whose body is only `return [await] self.<BaseRepository method>(...)` |
| `ENUM001` | all source | a public class inheriting `BaseStrEnum` (directly or through another project class) whose name does not end in `Enum` — the message names the expected name; renaming needs no migration, because `enum_type_name` drops one trailing `Enum` (#518) |
| `RTR001` | `routers/` | a call to a `*Controller` or `*Service` class (a name starting upper case) anywhere in the module — route body, helper function, module level. The construction goes to a provider in `api/dependencies/controllers.py` and reaches the route through `Depends` |
| `RTR002` | `routers/` | a route (`@<router>.get` / `post` / `put` / `patch` / `delete` / `head` / `options` / `trace` / `api_route`) whose return annotation or `response_model=` names an ORM model, at any depth (`list[ProductModel]`, `Optional[ProductModel]`, a union with `None`) |

The three cases of #520, in a project generated by `tempest new`, come out
of `tempest check` like this (an excerpt of the real output; exit code
`1`):

```text
$ mypy --disallow-untyped-defs --disallow-incomplete-defs .
Success: no issues found in 29 source files
$ lint-layers
src/db/repositories/news.py:18: REPO001 `NewsRepository.list_all` only forwards to BaseRepository.list; delete it and call `repository.list(...)` with those arguments at the call site.
src/services/feeds.py:9: SVC002 module-level function `page_envelope` in services/; move it to utils/, or make it a method of the service that uses it.
src/services/feeds.py:24: SVC001 `NewsService` does not inherit BaseService; subclass `BaseService[Repository, ResponseSchema]` and drop what it reimplements. A service with no repository opts out with `# tempest: no-base-service` on the class line; a type that is not a service belongs in schemas/ or core/.
src/services/feeds.py:27: SVC003 `NewsService.__init__` only stores the repository, which BaseService already does; delete it.
src/services/feeds.py:37: SVC002 `TrendService` shares this module with `NewsService`; one public class per module in services/ — move it to its own file.
14 layer finding(s).
```

The project as `tempest new` generates it passes clean, and stays clean
with the `CLAUDE.md` `ProductService` / `ProductRepository` / product
router examples copied into it — the suite's test does exactly that.

!!! tip "A service without a repository: explicit opt-out"
    A service that has no repository to hand to `BaseService` (a static
    catalogue read from a file, a facade over an external API) marks its
    `class` line with `# tempest: no-base-service`. The marker takes the
    class out of `SVC001` and `SVC003`; `SVC002` still applies.

    ```python
    # src/services/catalogue.py
    import json
    from pathlib import Path


    class CatalogueService:  # tempest: no-base-service
        """Static catalogue read from a JSON file — there is no repository."""

        def __init__(self, path: Path) -> None:
            self.items: list[dict[str, str]] = json.loads(path.read_text())
    ```

    It is a comment, not a class attribute, because it is an instruction
    to the checker and nothing at runtime reads it — the same reason
    `# noqa` is a comment.

!!! info "What `REPO001` lets through on purpose"
    A method that **overrides** a `BaseRepository` name (`delete`
    forwarding to `soft_delete`) is a policy, not a wrapper: whoever calls
    `service.delete` now deletes logically. And a method that calls
    another method defined on the same class is not a wrapper of the SDK.

#### Router: controller through `Depends`, response as a schema

The two `routers/` rules cover the deviations a router shows most:
building the controller by hand inside the route, and returning the ORM
row. This router, written into a `tempest new` project, does both:

```python
# src/api/routers/product.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.resources import get_session
from src.controllers import ProductController
from src.db.models import ProductModel
from src.db.repositories import ProductRepository
from src.services import ProductService

router: APIRouter = APIRouter(prefix="/products", tags=["products"])


@router.get("/{product_id}")
async def get_product(
    product_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProductModel:
    """Read a product."""
    controller = ProductController(ProductService(ProductRepository(session)))
    return await controller.get_by_id(product_id)
```

`tempest lint-layers` exits with code `1` and three lines (real output):

```text
src/api/routers/product.py:15: RTR002 route `get_product` returns the ORM model `ProductModel` (return annotation); return the response schema the repository maps it to (`map_to_response`) and annotate the route with that schema.
src/api/routers/product.py:20: RTR001 `ProductController(...)` is built inside a router; move the construction to a provider in api/dependencies/controllers.py and receive it in the route as `Depends(get_...)`. A call that has to stay opts out with `# tempest: router-builds` on its line.
src/api/routers/product.py:20: RTR001 `ProductService(...)` is built inside a router; move the construction to a provider in api/dependencies/services.py and receive it in the route as `Depends(get_...)`. A call that has to stay opts out with `# tempest: router-builds` on its line.
3 layer finding(s).
```

The fix is the shape the project's `CLAUDE.md` teaches: the
`get_product_controller` provider in `src/api/dependencies/controllers.py`
builds the chain, the route takes `controller: ProductController =
Depends(get_product_controller)` and annotates its return with
`ProductResponseSchema`.

**What counts as an ORM model.** A name imported from a module whose path
has a `models` segment (`src.db.models`, `src.db.models.user`), or a
public class defined in a `models/` directory of the project — as long as
the route does not import it from a `schemas` module. A schema that
happens to share a model's name stays clean.

!!! tip "A construction that has to stay in the router: `# tempest: router-builds`"
    The marker is the exception you own in writing, so review reads the
    reason. It covers the calls on that line, not the whole module:

    ```python
    # src/api/routers/reports.py
    from fastapi import APIRouter

    from src.services import ReportService

    report_service: ReportService = ReportService()  # tempest: router-builds
    router: APIRouter = APIRouter(prefix="/reports")
    ```

    `RTR002` has no opt-out: the fix has one shape — return the schema the
    repository's `map_to_response` produces.

!!! warning "What the router rules do not see"
    They read syntax and never import the project, so these stay out: a
    route with no return annotation and no `response_model=`; a model kept
    outside any `models/` directory and imported from a module without
    that segment; the SDK's own ORM bases (`BaseUserModel`) used directly
    in a route; a controller built through an alias
    (`factory = ProductController; factory()`) or a class whose name does
    not end in `Controller` / `Service`.

    **Where `@requires` sits is not a rule.** The
    [permission guards](permission-guards.md) recipe applies `@requires`
    to the route as its first form, and `requires` itself documents route
    handlers, controller methods and service methods as valid places.
    Flagging `@requires` on a route would flag the SDK's own documentation;
    choosing between the two places is review — the criterion is in
    [Architecture › Guards](../architecture.md#guards-route-dependency-or-requires-on-the-method).

!!! note "Requires `tempest-cli>=0.7.0`"
    The step reaches `tempest check` through `register_commands`'
    `extra_steps` (`tempest-cli` 0.5.0), and `check`'s `--rule` / `--strict`
    reach it through `run_with_options` (`tempest-cli` 0.7.0); the `[cli]`
    extra declares `tempest-cli>=0.7.0`, so it comes with it.
    `tempest-cli check` alone, without the SDK, does not know these rules:
    they belong to the SDK, and there `--rule` exits `2`.

#### Layer in a context package (warning)

Three rules check the shape of the four layers — `controllers/`,
`services/`, `repositories/` and `models/`. They enter as **warnings**:

| Code | Where | What it reports |
| --- | --- | --- |
| `LAY001` | `controllers/`, `repositories/`, `models/` | a second class in the module (any name, private ones too) or a module-level function. In `services/` `SVC002` reports it, as an error |
| `LAY002` | all four layers | a module directly at the layer root: `models/route.py` goes to `models/<context>/route.py`. Any context name is fine; `__init__.py` is exempt |
| `REPO002` | `repositories/` | an import of a `*Repository` name from another repository module (an absolute import with a `repositories` segment, or a relative one). A name used only as a class base — the project's base repository — passes |

A `models/route.py` with two models and a repository importing another
print (real output, exit code `0`, since they are only warnings):

```text
src/db/models/route.py:1: LAY002 (warning) models/route.py sits at the root of the layer; move it to models/<context>/route.py (a subpackage per domain context, re-exported by models/__init__.py).
src/db/models/route.py:5: LAY001 (warning) `StopModel` shares this module with `RouteModel`; one class per module in models/ — move it to its own file in the same context package.
src/db/repositories/orders/order.py:1: REPO002 (warning) imports `CustomerRepository` from another repository; a repository reads its own tables (join in the query) and a service combines two repositories.
3 layer finding(s): 0 error(s), 3 warning(s).
```

!!! check "The `tempest new` project passes all of them"
    The scaffold already comes in context packages (`controllers/system/`,
    `db/models/users/`), with relative `__init__` imports, `/api` only in
    `build_api_router` and tests with `Cenário:`/`Esperado:`:
    `tempest lint-layers` reports nothing, not even a warning (measured
    with no extras, with `auth,queue,tasks,ssr` and with
    `admin,email,minio,cache,tasks-redis`; `tests/cli/test_layer_lint.py`
    pins all three).

#### What services and controllers receive (warning)

| Code | Where | What it reports |
| --- | --- | --- |
| `DI001` | `services/`, `controllers/` | an `__init__` parameter annotated with `AsyncSession` (at any depth: a union with `None`, a string annotation). The session reaches the repository; the service receives the repository |
| `DI002` | `services/`, `controllers/` | a service `__init__` taking a `*Service`, or a controller one taking a `*Controller`. Combining two services is the controller's job |
| `DI003` | the whole source | a function or method named `from_session`. Assembly lives in the provider in `api/dependencies/` (inside a request) or in a `build_*` function of the composition root (outside one) |

This service has all three shapes (`tempest lint-layers --rule DI001 --rule
DI002 --rule DI003`, real output):

```python
# src/services/wallet/settlement.py
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.wallet.wallet import WalletService


class SettlementService:
    """Liquida o saldo retido."""

    def __init__(self, session: AsyncSession, wallet_service: WalletService) -> None:
        """Guarda a sessão e o service da carteira."""
        self.session = session
        self.wallet_service = wallet_service

    @classmethod
    def from_session(cls, session: AsyncSession) -> "SettlementService":
        """Monta o service sobre uma sessão."""
        return cls(session, WalletService(session))
```

```text
src/services/wallet/settlement.py:9: DI001 (warning) `SettlementService.__init__` takes the session (`session`); take the repository instead and let the provider in api/dependencies/ build it from the session.
src/services/wallet/settlement.py:9: DI002 (warning) `SettlementService.__init__` takes `WalletService` (`wallet_service`), a service of the same layer; combine the two one layer up, or declare the exception with its reason.
src/services/wallet/settlement.py:15: DI003 (warning) `from_session` assembles the graph inside the class; build it in a provider of api/dependencies/ for a request, or in a `build_*` function of the composition root outside one.
3 layer finding(s): 0 error(s), 3 warning(s).
```

When two services really must go together — in the transport service, the
settlement and the wallet credit had to land in the same transaction —, the
way out is a declared exception, aimed at that one parameter (for the
SDK's wallet, since v0.330.0 the service receives the
[`WalletLedger`](wallet.md#the-ledger-not-the-service-walletledger) and the
exception is no longer needed):

```python
ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "DI002 src/services/wallet/settlement.py::SettlementService.wallet_service": (
        "settlement and credit land in the same transaction"
    ),
}
```

!!! tip "Assembly outside a request"
    Tasks, consumers, agents and seeds have no `Depends`. Instead of every
    class getting a `from_session`, gather the `build_*` functions in a
    composition root and reuse it in all of those places and in the test
    suite. The [agents with a database](agents-db.md) recipe shows the shape.

#### Nested function and private helper (warning)

These two rules read the source **and the suite** (`tests/` beside
`src/`). In the transport service, most occurrences were in the tests.

| Code | What it reports |
| --- | --- |
| `STR001` | a function defined inside another function — including inside a method or a local class. A callback that keeps state becomes a class with `__call__`; one that only binds an argument becomes `functools.partial` over a module-level function |
| `STR002` | a module-level function whose name starts with a single `_` (`__getattr__` and methods pass). It becomes a method of the class that uses it, a public function in `utils/` or, in the suite, a fixture or a public helper module |

With these two files (real output):

```python
# src/utils/retry.py
from collections.abc import Awaitable, Callable


def with_retry(attempts: int) -> Callable[[], Awaitable[None]]:
    """Repeat the call until it succeeds."""

    async def wrapper() -> None:
        """Try again."""

    return wrapper
```

```python
# tests/test_app.py
def _make_payload() -> dict[str, str]:
    """Build the request body."""
    return {"name": "Picos"}
```

```text
src/utils/retry.py:7: STR001 (warning) `with_retry.wrapper` is defined inside a function; a callback that keeps state becomes a class with `__call__`, and one that binds an argument goes through functools.partial over a module-level function.
tests/test_app.py:1: STR002 (warning) module-level `_make_payload` is a helper with no home; make it a method of the class that uses it or a public function in utils/ (in tests: a fixture or a public helper module).
2 layer finding(s): 0 error(s), 2 warning(s).
```

#### Imports between project packages (warning)

| Code | What it reports |
| --- | --- |
| `IMP001` | an `__init__.py` importing a module of its own package with an absolute import. A neighbour is imported relatively: `from .orders import OrderService` |
| `IMP002` | outside an `__init__.py`, an import of a name from the submodule it lives in when a package above already re-exports it. The re-export is resolved transitively, through `from .x import *` too, and the suggestion is the top-most package that re-exports the name **without containing** the importing module — a service is never told to import its own layer's `__init__` |

Both read the source and the suite. With these four files
(`tempest lint-layers --rule IMP001 --rule IMP002`, real output):

```python
# src/services/__init__.py
from src.services.orders import OrderService as OrderService

__all__: list[str] = ["OrderService"]
```

```python
# src/services/orders/__init__.py
from .order import OrderService as OrderService

__all__: list[str] = ["OrderService"]
```

```python
# src/services/orders/order.py
class OrderService:
    """Orders."""
```

```python
# src/controllers/orders/order.py
from src.services.orders.order import OrderService


class OrderController:
    """Orchestrates orders."""

    def __init__(self, service: OrderService) -> None:
        """Keep the service."""
        self.service = service
```

```text
src/controllers/orders/order.py:1: IMP002 (warning) `OrderService` is re-exported by `src.services`; import it from there: `from src.services import OrderService`.
src/services/__init__.py:1: IMP001 (warning) `src.services.orders` is a module of this package; import it relatively in __init__.py: `from .orders import ...`.
2 layer finding(s): 0 error(s), 2 warning(s).
```

!!! warning "When the package would close a cycle"
    Sometimes importing through the layer package closes a cycle: in the
    transport service, tasks and scripts imported `api.dependencies.resources`
    directly, because importing the `api.dependencies` package would close
    a cycle with `src.tasks`.
    That is a case for a declared exception
    (`"IMP002 <path>::<name>": "<reason>"`), not for rewriting the import.

#### Router: one per file, `/api` once (warning)

| Code | Where | What it reports |
| --- | --- | --- |
| `RTR003` | `routers/` | more than one `APIRouter(...)` in the module — one finding per router after the first |
| `RTR004` | the whole source | a `prefix` equal to `/api` (or under it) in `APIRouter(...)` or `include_router(...)` outside a `build_api_router` function |

In the transport service, `/api` was repeated in 22 `include_router` calls.
An orders module with two routers and `/api` baked in prints (real output):

```python
# src/api/routers/orders.py
from fastapi import APIRouter

router: APIRouter = APIRouter(prefix="/api/orders", tags=["orders"])
admin_router: APIRouter = APIRouter(prefix="/api/admin/orders", tags=["admin"])
```

```text
src/api/routers/orders.py:3: RTR004 (warning) prefix `/api/orders` set outside build_api_router(); mount the domain routers under `/api` once, in routers/api.py, and give each router only its own prefix.
src/api/routers/orders.py:4: RTR003 (warning) a second APIRouter in this module (`admin_router`); one router per file in routers/ — move it to its own module.
src/api/routers/orders.py:4: RTR004 (warning) prefix `/api/admin/orders` set outside build_api_router(); mount the domain routers under `/api` once, in routers/api.py, and give each router only its own prefix.
3 layer finding(s): 0 error(s), 3 warning(s).
```

The shape both rules ask for: each domain in its own file, with only its own
prefix (`/orders`, `/admin/orders`), and `/api` set once:

```python
# src/api/routers/api.py
from fastapi import APIRouter

from src.api.routers.admin_orders import router as admin_orders_router
from src.api.routers.orders import router as orders_router


def build_api_router() -> APIRouter:
    """Build the API router: every domain sits under ``/api``."""
    api: APIRouter = APIRouter(prefix="/api")
    api.include_router(orders_router)
    api.include_router(admin_orders_router)
    return api
```

#### The shape of the suite (warning)

The four `TST` rules read the files pytest collects in the project's
`tests/` (`test_*.py` and `*_test.py`):

| Code | What it reports |
| --- | --- |
| `TST001` | a test (module-level `test*` or a `test*` method of a `Test*` class) whose docstring does not carry `Cenário:` **and** `Esperado:` — the situation the test builds and what it proves |
| `TST002` | a test that checks nothing: no `assert`, no `with pytest.raises` / `warns` / `deprecated_call`, no `assert*` call (`mock.assert_awaited_once()`) |
| `TST003` | `pytest.raises(Exception)` or `BaseException`, alone or in a tuple — any bug passes |
| `TST004` | a `parametrize` whose id holds a value drawn at import |

This file has all four (real output):

```python
# tests/test_orders.py
from uuid import uuid4

import pytest


@pytest.mark.parametrize("order_id", [str(uuid4())])
def test_order_is_created(order_id: str) -> None:
    """The order is created."""
    with pytest.raises(Exception):
        int(order_id)


def test_receipt_is_sent() -> None:
    """Cenário: um pedido pago.

    Esperado: o recibo sai sem erro.
    """
    str(uuid4())
```

```text
tests/test_orders.py:6: TST004 (warning) parametrize puts a `uuid4()` drawn at import into the test id; each pytest-xdist worker collects different ids and the run aborts — use fixed literals.
tests/test_orders.py:7: TST001 (warning) `test_order_is_created` docstring lacks Cenário:, Esperado:; say the situation the test builds (Cenário:) and what it proves (Esperado:).
tests/test_orders.py:9: TST003 (warning) pytest.raises(Exception) passes for any bug; name the exception the code raises (and `match=` its message when it matters).
tests/test_orders.py:13: TST002 (warning) `test_receipt_is_sent` checks nothing (no assert, no pytest.raises, no assert_* call); a test that cannot fail proves nothing.
4 layer finding(s): 0 error(s), 4 warning(s).
```

!!! info "When a drawn value breaks `pytest -n`"
    Each pytest-xdist worker imports the module on its own. When a test id
    depends on a drawn value, each worker collects different ids and the
    run aborts with "Different tests were collected". Measured with
    `pytest -n 2`: `random.randint()`, `time.time()`,
    `secrets.token_hex()`, `os.urandom()`, `str(uuid4())`, `uuid4().hex`,
    `f"{uuid4()}"` and `pytest.param(..., id=str(uuid4()))` abort. A **bare**
    `uuid4()` or `datetime.now()` passes — pytest ids those objects by
    position (`v0`, `v1`) —, which is why `TST004` only reports them once
    they become text or sit in `ids=` / `id=`.

#### Warning, error and declared exception

Every rule has a severity. An **error** fails the step (exit code `1`); a
**warning** prints `(warning)` after its code and does not. A new rule
enters as a warning in one release and becomes an error in the next — so
an existing service has a whole release to migrate. To migrate one rule at
a time, select it with `--rule` (repeatable, lower case accepted):

```bash
tempest lint-layers --rule SVC002              # one rule only
tempest lint-layers --rule SVC002 --rule RTR001
tempest lint-layers --strict                   # warnings fail too
```

`tempest check` runs the step without `--rule`. For the gate to fail on
warnings too, turn it on in `pyproject.toml` — the command line's
`--strict`/`--no-strict` wins over the config:

```toml
[tool.tempest]
layers_strict = true
```

**An exception the project keeps lives in one place**, with a reason,
reviewed in the diff like code: the `ARCHITECTURE_EXCEPTIONS` dict in
`tests/architecture_exceptions.py`. The key is `"<CODE> <path>"` (every
finding of that code in the file) or `"<CODE> <path>::<subject>"` (only the
one about that name — the class, `Class.method` or the function). The path
is relative to the project root. The file is read with `ast` too, never
imported:

```python
"""Architecture exceptions the project keeps, each with its reason."""

ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "SVC001 src/services/catalogue/catalogue.py::CatalogueService": (
        "static catalogue, no repository to hand to BaseService"
    ),
    "REPO001 src/db/repositories/legacy/legacy.py": "wrapper kept until 2.0",
}
```

With that file, a `src/services/catalogue/catalogue.py` defining `CatalogueService`
(no `BaseService`) and a module-level function `load_items`, and no
`legacy.py`, `tempest lint-layers` prints (real output, exit code `1`
because of the `SVC002`):

```text
src/services/catalogue/catalogue.py:5: SVC002 module-level function `load_items` in services/; move it to utils/, or make it a method of the service that uses it.
tests/architecture_exceptions.py:7: EXC002 (warning) `REPO001 src/db/repositories/legacy/legacy.py` matches no finding; the code it excused is gone or moved — delete the entry, or fix its path.
2 layer finding(s): 1 error(s), 1 warning(s).
```

`CatalogueService`'s `SVC001` is gone, because the exception covers it. The
`REPO001` entry matches nothing and becomes the `EXC002` warning: an
exception does not outlive the code it excused.

| Code | Severity | What it reports |
| --- | --- | --- |
| `EXC001` | error, always (even under `--rule`) | an entry the checker cannot use: key out of shape, unknown code, empty or non-literal reason, repeated key, file without the dict |
| `EXC002` | warning | an entry that excuses no finding — the code changed or the path is wrong |

`--rule` with an unknown code exits `2` and lists the valid codes.
`tempest check` takes the same two options and hands them to the
`lint-layers` step — `tempest check --rule STR002 --strict` runs the whole
gate with the step filtered and warnings failing (exit code `1` with a
`STR002`, `0` without `--strict`; measured on a `tempest new` project).

!!! tip "Marker or declared exception?"
    The `# tempest: no-base-service` and `# tempest: router-builds` markers
    still work. A declared exception covers any rule, including the ones
    with no marker, and gathers in one file what used to be scattered —
    review reads every reason at once.

### Schema metadata — `tempest lint-schemas`

The client reads the `openapi.json`, and a field without `title`,
`description` and `examples` arrives there with a name and a type only. This
rule cannot come from `ast`: the metadata only exists once Pydantic built
the model. So it **imports** the schemas package (`src.schemas`, or
`app.schemas`) instead of reading the source like `lint-layers`:

```python
# src/schemas/order.py
from pydantic import Field
from tempest_fastapi_sdk import BaseSchema


class AddressSchema(BaseSchema):
    """Delivery address."""

    city: str = Field(title="City", description="Delivery city.")


class OrderCreateSchema(BaseSchema):
    """A new order."""

    total_cents: int
    address: AddressSchema = Field(title="Address", description="Where to deliver.")
```

`tempest lint-schemas` (with `src/schemas/__init__.py` exporting both; real
output, exit code `1`):

```text
src.schemas.order.AddressSchema.city: missing examples
src.schemas.order.OrderCreateSchema.total_cents: missing title, description, examples
2 schema field(s) without documentation.
```

- A **leaf** field (a value: `str`, `int`, an enum, `list[str]`) needs all
  three. A field **holding another schema** (`AddressSchema`,
  `list[ItemSchema]`, `ItemSchema | None`) needs only `title` and
  `description` — its examples are the nested schema's. That is why
  `address` passes.
- Each field is reported once, on the class declaring it, however many
  exported schemas inherit it. The fields `BaseResponseSchema` brings (`id`,
  `created_at`...) are already documented.
- A blank title and `examples=[]` count as missing. A package that does not
  import exits `2` with the traceback.

The same check runs **inside the suite**, which is how the `tempest new`
project enforces it on every `tempest check` — the scaffold already ships
this test:

```python
# tests/test_schemas.py
import src.schemas
from tempest_fastapi_sdk.testing import assert_schema_metadata


def test_exported_schemas_carry_metadata() -> None:
    """Cenário: the schemas ``src.schemas`` exports.

    Esperado: every field carries ``title``, ``description`` and ``examples``.
    """
    assert_schema_metadata(src.schemas)
```

---

## Recap

`tempest` covers the whole cycle — from scaffolding (`new`),
through infra (`generate --docker` / `--dockerfile` / `--src`), migrations
(`db`), users (`user`) and secrets (`secrets`), to the quality gates
(`lint` / `fix` / `type` / `test` / `check`), with the SDK's layer rules (`lint-layers`) inside `check`.

## Next steps

Once the service is generated, head to the related recipes:

- [Database »](database.md) — `BaseRepository`, async sessions and the
  migration flow behind `tempest db`.
- [Queue and tasks »](queue-tasks.md) — the layers `tempest generate
  --src` writes for the `[queue]` (FastStream) and `[tasks]` (TaskIQ)
  extras.
- [Safe deploys »](deploy-safety.md) — destructive migrations and graceful
  shutdown when containerizing with the generated `Dockerfile`.

