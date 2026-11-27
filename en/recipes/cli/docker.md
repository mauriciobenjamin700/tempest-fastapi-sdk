# Docker: compose and Dockerfile

Use to generate `docker-compose.yaml` from the extras, regenerate it in an existing project and containerize the app.

Part of the [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli.md) recipe.

## Extras-driven `docker-compose.yaml`

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

### The `prod` profile: the API inside the compose

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

#### Migrations under the `prod` profile

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

## Regenerating `docker-compose.yaml` in an existing project

When you change installed extras (`uv add "tempest-fastapi-sdk[minio]"`) or the SDK bumps image versions, regenerate with:

```bash
tempest generate --docker                        # read extras from local pyproject.toml
tempest generate --docker --extras cache,minio   # force explicit extras
tempest generate --docker --name my-svc          # override container-name prefix
tempest generate --docker --force                # overwrite an existing compose file
```

The command reads ``[project] name`` + extras from the current directory's `pyproject.toml` (pass `--path` for another). It refuses to overwrite without `--force` so hand edits don't get clobbered. The `.env.example` addendum is idempotent — re-running does not duplicate service blocks.

## Dockerfile to containerize the app

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

    Serve the result with [`make_spa_router("web/dist")`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/react-spa.md),
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

### Regenerating the Dockerfile — `tempest generate --dockerfile`

```bash
tempest generate --dockerfile                    # Dockerfile + .dockerignore
tempest generate --dockerfile --name my-svc      # override the name in the comments
tempest generate --dockerfile --force            # overwrite existing files
tempest generate --docker --dockerfile --src     # everything in one shot
```

The `EXPOSE` / `SERVER_PORT` port is read from `SERVER_PORT` in `.env` (or `.env.example`), falling back to `8000` when absent. Like the other generators, it refuses to overwrite without `--force`.
