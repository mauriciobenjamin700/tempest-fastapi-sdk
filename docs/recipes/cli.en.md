# CLI


Installing `tempest-fastapi-sdk` exposes a `tempest` console script. It does two jobs: bootstrap a new layered service from the SDK's preferred skeleton, and run the four quality gates (`ruff check`, `ruff format`, `mypy`, `pytest`) without copy-pasting the same commands into every project.

```bash
tempest --help                                  # list every command
tempest --version                               # show the SDK version
```

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

#### Scaffold a new service

```bash
tempest new my_service                          # scaffold under ./my_service
tempest new my_service --path ~/projects        # custom parent dir
tempest new my_service \
    --bind-host 0.0.0.0 \                       # default HOST in .env.example
    --bind-port 9090 \                          # default PORT in .env.example
    --extras auth,upload                        # pinned SDK extras
tempest new my_service --force                  # overwrite existing dir
```

The skeleton matches the layered architecture documented in this README:

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # pins tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # TITLE/VERSION/HOST/PORT/DATABASE_URL/JWT_SECRET/CORS_ORIGINS
├── docker-compose.yaml      # services keyed to the chosen extras
├── .gitignore
├── README.md
├── src/
│   ├── server.py            # uvicorn.run() + module-level FastAPI app
│   ├── api/
│   │   ├── app.py           # create_app() wires SDK middleware + handlers
│   │   ├── routers/         # placeholder business router
│   │   └── dependencies/    # auth.py (require_token) + factories
│   ├── controllers/         # orchestration between services
│   ├── services/            # business logic
│   ├── schemas/             # Pydantic DTOs
│   ├── core/                # settings.py + exceptions.py
│   ├── db/
│   │   ├── models/
│   │   └── repositories/
│   └── utils/
└── tests/
    └── test_smoke.py        # asserts /api/ and /health/liveness boot
```

The generated `pyproject.toml` pins the current SDK version (`tempest-fastapi-sdk[auth,admin]>=<version>` by default — change with `--extras`). The scaffolded `.env.example` uses the v0.8.0 settings naming (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), and `src/server.py` delegates to `tempest_fastapi_sdk.run_server` so uvicorn is imported lazily and tests can import the app without it. Validation rules: the project name must match `^[a-z][a-z0-9_]*$` and cannot collide with a Python keyword, so `tempest new Bad-Name` and `tempest new class` exit with code 2 before any file is written.

!!! tip "API title / version come from `.env`"
    Since v0.48.0 the scaffolded `Settings` carries `TITLE`, `VERSION`
    and `DESCRIPTION`, and `src/api/app.py` consumes them
    (`FastAPI(title=settings.TITLE, version=settings.VERSION,
    description=settings.DESCRIPTION)`, `make_health_router(version=
    settings.VERSION)` and `AdminSite(title=f"{settings.TITLE} admin")`).
    Tune the title shown in Swagger/ReDoc and the `/admin` header from
    `.env`, no code edits:

    ```bash
    TITLE=My API
    VERSION=1.2.0
    DESCRIPTION=Payments API for product X.
    ```

### Extras-driven `docker-compose.yaml`

Since v0.25.0 the scaffold generates a `docker-compose.yaml` carrying **only** the supporting services the chosen extras actually need — no ZooKeeper, no Kafka, nothing you won't use.

| Extra | Container | Exposed port(s) |
|-------|-----------|-----------------|
| (always) | `postgres:18-alpine` | 5432 |
| `[cache]` | `redis:8-alpine` | 6379 |
| `[queue]` / `[tasks]` | `rabbitmq:4-management-alpine` | 5672 (AMQP) + 15672 (UI) |
| `[minio]` | `minio/minio` + bootstrap mc | 9000 (API) + 9001 (Console) |
| `[email]` | `mailhog/mailhog` | 1025 (SMTP) + 8025 (UI) |

Example — service using cache + S3 uploads + emails:

```bash
tempest new my_service --extras auth,cache,minio,email
```

Generates:

- `postgres`, `redis`, `minio` (+ `minio-bootstrap` creating the `uploads` bucket), `mailhog`
- `.env.example` with `REDIS_URL`, `MINIO_*`, `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_USE_TLS=false` (MailHog is plain — no STARTTLS)

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

Boot it all:

```bash
docker compose up -d
```

Tear down keeping volumes:

```bash
docker compose down
```

Tear down wiping volumes:

```bash
docker compose down -v
```

Image tags are pinned by the SDK — bump them through `pyproject.toml` of the SDK, not on a per-project basis. Current versions (v0.26.0+): `postgres:18-alpine`, `redis:8-alpine`, `rabbitmq:4-management-alpine`.

### Database — `tempest db`

Alembic wrapper backed by ``AlembicHelper`` — your project's ``alembic.ini`` + ``env.py`` stay the source of truth.

``DATABASE_URL`` resolution order:

1. ``--database-url`` flag.
2. ``DATABASE_URL`` env var.
3. ``src.core.settings.settings.DATABASE_URL`` (when run from a scaffolded project root).
4. ``sqlalchemy.url`` from ``alembic.ini``.

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
tempest db seed                                  # runs src.db.seeds:seed
tempest db seed --seed src.db.fixtures:demo      # custom callable
```

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

# Promote / demote an existing user (toggles is_admin)
tempest user promote --email ana@example.com    # becomes admin
tempest user revoke  --email ana@example.com    # back to a regular account

# List
tempest user list                                # everyone
tempest user list --admin                        # admins only
```

!!! tip "Without `--admin`/`--no-admin`, create asks"
    When you pass **neither** `--admin` nor `--no-admin` in an
    interactive terminal, `tempest user create` prompts
    `Should this user be an administrator? [y/N]`. Non-interactive runs
    (CI, pipes, scripts) skip the prompt and create a regular user
    (`is_admin=False`) — pass `--admin` explicitly to create an admin
    without a TTY.

`tempest user promote` / `tempest user revoke` find the user by email (case-insensitive) and only flip `is_admin`. When no user matches the email they exit with code 1 and a `no user found` message.

``DATABASE_URL`` resolves the same way as ``tempest db`` (env var > settings > alembic.ini).

### Secrets — `tempest secrets`

Generate and rotate application secrets (`JWT_SECRET` / `TOKEN_SECRET` by default), rewriting the matching `.env` lines **in place** — backing up the old file first — and leaving every other line untouched.

```bash
# Rotate JWT_SECRET and TOKEN_SECRET in .env (writes .env.bak)
tempest secrets rotate

# Just print the new values (writes nothing) — pipe into a secret manager
tempest secrets rotate --print

# Custom keys and file
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod

# More entropy, no backup
tempest secrets rotate --length 64 --no-backup
```

!!! warning
    Rotating `JWT_SECRET` invalidates every token signed with the old value: users are logged out and pending reset/activation links stop working. Rotate during a maintenance window and restart the service to load the new values.

### Regenerating `docker-compose.yaml` in an existing project

When you change installed extras (`uv add "tempest-fastapi-sdk[minio]"`) or the SDK bumps image versions, regenerate with:

```bash
tempest generate --docker                        # read extras from local pyproject.toml
tempest generate --docker --extras cache,minio   # force explicit extras
tempest generate --docker --name my-svc          # override container-name prefix
tempest generate --docker --force                # overwrite an existing compose file
```

The command reads ``[project] name`` + extras from the current directory's `pyproject.toml` (pass `--path` for another). It refuses to overwrite without `--force` so hand edits don't get clobbered. The `.env.example` addendum is idempotent — re-running does not duplicate service blocks.

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
| `[queue]` | `queue/__init__.py` (broker + `AsyncBrokerManager` + `get_broker`), `queue/handlers.py` (example subscriber) |
| `[tasks]` | `tasks/__init__.py` (broker + `AsyncTaskBrokerManager` + `get_task_manager`), `tasks/jobs.py` (example task) |

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

#### Quality gates

The lint commands shell out to the project's tooling. They look for the executable on `PATH` first, and otherwise fall back to `uv run <tool>` so a project-local virtualenv works without manual activation.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix . + ruff format .   (writes)
tempest fix --unsafe                            # also apply ruff's --unsafe-fixes
tempest format                                  # ruff format .          (writes)
tempest fmt-check                               # ruff format --check .   (read-only)
tempest type                                    # mypy .
tempest test                                    # pytest
tempest test tests/api/                         # pytest with a path filter
tempest check                                   # lint + fmt-check + type + test, stops at first failure
```

`tempest fix` is the one-shot "organize the project" pass — sorts and dedupes imports, drops unused imports, normalizes string quotes, removes trailing whitespace, then runs `ruff format` to align indentation, line length, blank lines and trailing newlines. Run it before pushing when CI keeps catching style nits.

!!! info "`ruff format` always runs — even with leftover errors"
    `ruff check --fix` exits non-zero whenever **any** violation it
    cannot autofix is left (an over-length string/comment, an undefined
    name, …). `tempest fix` runs `ruff format` anyway, so a single
    unfixable line never blocks formatting the whole file — long **code**
    lines still get wrapped and extra blank lines removed. The lint exit
    code is still surfaced afterwards, so CI keeps failing on the real
    leftovers.

!!! warning "Long strings and comments are never wrapped"
    Neither `ruff format` nor `tempest fix` wraps long **string literals
    or comments** — same behavior as Black. Those `E501` lines stay and
    must be shortened by hand or silenced with `# noqa: E501`.

Every command returns the underlying tool's exit code, so `tempest check` is safe to wire into CI (`tempest check || exit 1`) or pre-commit hooks. When neither the executable nor `uv` is on `PATH`, the wrapper prints `error: '<tool>' is not on PATH and 'uv' is unavailable` and exits with `127` instead of failing silently.

