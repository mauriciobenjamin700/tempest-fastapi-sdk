# CLI


Installing `tempest-fastapi-sdk` exposes a `tempest` console script. It does two jobs: bootstrap a new layered service from the SDK's preferred skeleton, and run the four quality gates (`ruff check`, `ruff format`, `mypy`, `pytest`) without copy-pasting the same commands into every project.

```bash
tempest --help                                  # list every command
tempest --version                               # show the SDK version
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
├── .env.example             # HOST/PORT/DATABASE_URL/JWT_SECRET/CORS_ORIGINS
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

### Extras-driven `docker-compose.yaml`

Since v0.25.0 the scaffold generates a `docker-compose.yaml` carrying **only** the supporting services the chosen extras actually need — no ZooKeeper, no Kafka, nothing you won't use.

| Extra | Container | Exposed port(s) |
|-------|-----------|-----------------|
| (always) | `postgres:16-alpine` | 5432 |
| `[cache]` | `redis:7-alpine` | 6379 |
| `[queue]` / `[tasks]` | `rabbitmq:3-management-alpine` | 5672 (AMQP) + 15672 (UI) |
| `[minio]` | `minio/minio` + bootstrap mc | 9000 (API) + 9001 (Console) |
| `[email]` | `mailhog/mailhog` | 1025 (SMTP) + 8025 (UI) |

Example — service using cache + S3 uploads + emails:

```bash
tempest new my_service --extras auth,cache,minio,email
```

Generates:

- `postgres`, `redis`, `minio` (+ `minio-bootstrap` creating the `uploads` bucket), `mailhog`
- `.env.example` with `REDIS_URL`, `MINIO_*`, `EMAIL_HOST=localhost`, `EMAIL_PORT=1025`

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
```

### Users — `tempest user`

Seed and list users using the project's concrete ``UserModel`` (default ``src.db.models:UserModel``). Bootstraps the first admin without manual SQL.

```bash
# Create a regular user
tempest user create --email ana@example.com --password strong-pass-12

# Create an admin (can log into /admin)
tempest user create --email admin@local --password admin-pass-12 --admin

# Read the password interactively (never lands in shell history)
tempest user create --email admin@local --admin

# Custom model outside the scaffolded layout — MUST be a BaseUserModel subclass
tempest user create --email x@y --password pass-12-chars --model myapp.models.user:UserModel

# List
tempest user list                                # everyone
tempest user list --admin                        # admins only
```

``DATABASE_URL`` resolves the same way as ``tempest db`` (env var > settings > alembic.ini).

### Regenerating `docker-compose.yaml` in an existing project

When you change installed extras (`uv add "tempest-fastapi-sdk[minio]"`) or the SDK bumps image versions, regenerate with:

```bash
tempest generate --docker                        # read extras from local pyproject.toml
tempest generate --docker --extras cache,minio   # force explicit extras
tempest generate --docker --name my-svc          # override container-name prefix
tempest generate --docker --force                # overwrite an existing compose file
```

The command reads ``[project] name`` + extras from the current directory's `pyproject.toml` (pass `--path` for another). It refuses to overwrite without `--force` so hand edits don't get clobbered. The `.env.example` addendum is idempotent — re-running does not duplicate service blocks.

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

