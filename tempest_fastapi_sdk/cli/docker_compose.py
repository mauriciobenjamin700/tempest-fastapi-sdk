"""Generate a ``docker-compose.yaml`` based on the SDK extras chosen.

When ``tempest new`` scaffolds a service, only the supporting
infrastructure the service will actually use is wired into the
compose file. The mapping from extra → service:

* ``[cache]`` → Redis 7
* ``[queue]`` or ``[tasks]`` → RabbitMQ 3 (management UI exposed)
* ``[minio]`` → MinIO + a one-shot bootstrap container that
  creates the default bucket
* ``[email]`` → MailHog (catches outbound SMTP for dev)

Postgres is always included because the SDK's DB primitives are
core — the scaffolded ``.env`` keeps SQLite as the default URL so
``uv run python main.py`` works out of the box, but the compose
file gives the developer a one-command path to a real Postgres
when they need it.

The service itself is wired in too, as an ``api`` service behind the
``prod`` compose profile: ``docker compose up -d`` still starts only
the infra (the dev loop runs the app on the host), while
``docker compose --profile prod up -d --build`` builds the scaffolded
``Dockerfile`` and runs the app on the compose network, with every
host the ``.env`` points at ``localhost`` re-pointed at the compose
service name. Infra ports are published on ``127.0.0.1`` only, so the
host's dev loop keeps reaching them and nothing outside the host does.

The image tags are pinned to versions known to work with the SDK
at release time — bump intentionally, not by accident. Bumping any
of them should go through the smoke suite first.

The MinIO images are pulled from ``quay.io``, not Docker Hub. MinIO
deleted ``minio/minio`` and ``minio/mc`` from Docker Hub on
2026-09-11, so the unqualified names fail at ``docker compose pull``
with ``pull access denied``. The same release tags are still served
by ``quay.io/minio/*`` as multi-arch manifest lists. This is a
stopgap: the upstream repository is archived, so these images get
no security fixes, and the registry can be withdrawn the same way.
The replacement is an S3-compatible image we publish ourselves.
"""

from __future__ import annotations

POSTGRES_IMAGE: str = "postgres:18-alpine"
REDIS_IMAGE: str = "redis:8-alpine"
RABBITMQ_IMAGE: str = "rabbitmq:4-management-alpine"
MINIO_IMAGE: str = "quay.io/minio/minio:RELEASE.2024-12-13T22-19-12Z"
MINIO_MC_IMAGE: str = "quay.io/minio/mc:RELEASE.2024-11-21T17-21-54Z"
MAILHOG_IMAGE: str = "mailhog/mailhog:v1.0.1"


def _parse_extras(extras: str) -> set[str]:
    """Split a CLI ``--extras`` value into a clean set.

    Args:
        extras (str): Comma-separated extras (``"auth,upload,minio"``).

    Returns:
        set[str]: Lower-cased, whitespace-stripped extras. Empty
        input yields an empty set.
    """
    return {part.strip().lower() for part in extras.split(",") if part.strip()}


def _postgres_block(project_name: str) -> str:
    """Compose snippet for Postgres 18.

    Two facts about the 18+ image worth knowing:

    * The data directory layout changed — the image now expects the
      volume mounted at ``/var/lib/postgresql`` (NOT
      ``/var/lib/postgresql/data``). The cluster creates a
      version-specific subdirectory so ``pg_upgrade --link`` works
      without mount-boundary issues. See
      https://github.com/docker-library/postgres/pull/1259.
      Compose files pointing at the old path crash on first boot
      with "PostgreSQL data in /var/lib/postgresql/data (unused
      mount/volume)".
    * Authentication defaults to ``scram-sha-256`` since 14 — leave
      ``POSTGRES_HOST_AUTH_METHOD`` off so the secure default
      sticks.
    """
    safe = project_name.replace("-", "_")
    return f"""\
  postgres:
    image: {POSTGRES_IMAGE}
    container_name: {project_name}-postgres
    restart: unless-stopped
    environment:
      # Read from .env (see .env.example); the :-default keeps the
      # stack bootable even before you copy .env.example to .env.
      POSTGRES_USER: ${{POSTGRES_USER:-app}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD:-app}}
      POSTGRES_DB: ${{POSTGRES_DB:-{safe}}}
      # Postgres 14+ defaults to scram-sha-256 — leave the explicit
      # method off so the cluster picks the secure default.
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      # Postgres 18+ requires the mount at /var/lib/postgresql
      # (not /var/lib/postgresql/data). Wipe the old volume with
      # `docker compose down -v` when upgrading from 16.
      - postgres-data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${{POSTGRES_USER:-app}} -d ${{POSTGRES_DB:-{safe}}}"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s
"""


def _redis_block(project_name: str) -> str:
    """Compose snippet for Redis 8 (tri-licensed since 8.0).

    Protected mode is off by default in Docker — fine for the
    compose-internal network but always set a password before
    exposing the port outside the host.
    """
    return f"""\
  redis:
    image: {REDIS_IMAGE}
    container_name: {project_name}-redis
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 5s
"""


def _rabbitmq_block(project_name: str) -> str:
    """Compose snippet for RabbitMQ 4 with management plugin.

    ``RABBITMQ_DEFAULT_USER`` / ``RABBITMQ_DEFAULT_PASS`` are
    deprecated in the docker entrypoint script but still honored
    by the broker — keep them until 5.x lands.
    """
    return f"""\
  rabbitmq:
    image: {RABBITMQ_IMAGE}
    container_name: {project_name}-rabbitmq
    restart: unless-stopped
    environment:
      # Read from .env (see .env.example); :-default keeps dev bootable.
      RABBITMQ_DEFAULT_USER: ${{RABBITMQ_DEFAULT_USER:-guest}}
      RABBITMQ_DEFAULT_PASS: ${{RABBITMQ_DEFAULT_PASS:-guest}}
      RABBITMQ_DEFAULT_VHOST: ${{RABBITMQ_DEFAULT_VHOST:-/}}
    ports:
      - "127.0.0.1:5672:5672"     # AMQP
      - "127.0.0.1:15672:15672"   # Management UI — http://localhost:15672 (guest/guest)
    volumes:
      - rabbitmq-data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 10s
      timeout: 10s
      retries: 6
      start_period: 30s
"""


def _minio_blocks(project_name: str) -> str:
    """Compose snippets for MinIO + bucket bootstrap container."""
    return f"""\
  minio:
    image: {MINIO_IMAGE}
    container_name: {project_name}-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      # Read from .env (see .env.example); :-default keeps dev bootable.
      MINIO_ROOT_USER: ${{MINIO_ROOT_USER:-minioadmin}}
      MINIO_ROOT_PASSWORD: ${{MINIO_ROOT_PASSWORD:-minioadmin}}
    ports:
      - "127.0.0.1:9000:9000"   # S3 API
      - "127.0.0.1:9001:9001"   # Web console — http://localhost:9001
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 5s
      retries: 10

  minio-bootstrap:
    image: {MINIO_MC_IMAGE}
    container_name: {project_name}-minio-bootstrap
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 ${{MINIO_ROOT_USER:-minioadmin}} ${{MINIO_ROOT_PASSWORD:-minioadmin}} &&
      mc mb -p local/uploads &&
      echo 'bucket ready'
      "
"""


def _mailhog_block(project_name: str) -> str:
    """Compose snippet for MailHog dev SMTP server."""
    return f"""\
  mailhog:
    image: {MAILHOG_IMAGE}
    container_name: {project_name}-mailhog
    restart: unless-stopped
    ports:
      - "127.0.0.1:1025:1025"   # SMTP
      - "127.0.0.1:8025:8025"   # Web UI — http://localhost:8025
"""


def _api_block(project_name: str, extras_set: set[str], port: int) -> str:
    """Compose snippet for the service itself, gated behind the ``prod`` profile.

    ``docker compose up -d`` leaves this service out, so the dev loop is
    unchanged: infra in containers, the app on the host with reload.
    ``docker compose --profile prod up -d --build`` builds the scaffolded
    ``Dockerfile`` and runs the app inside the compose network.

    The ``.env`` the scaffold writes points every backend at
    ``localhost``, which is right for the host process and wrong inside a
    container, where ``localhost`` is the API container itself. Compose
    gives ``environment:`` precedence over ``env_file:``, so the block
    re-points each host :func:`env_block_for` writes for the chosen
    extras at the compose service name. ``SMTP_HOST`` is the deliberate
    exception: MailHog is a dev catch-all, and production mail goes
    through the real SMTP server configured in ``.env``.

    ``SERVER_HOST`` / ``SERVER_PORT`` are pinned too, because ``.env``
    carries the host-side ``SERVER_HOST=127.0.0.1`` and would otherwise
    override the image's ``0.0.0.0`` and make the app unreachable from
    the published port.

    The health probe hits ``/health/readiness`` (the scaffold mounts
    ``make_health_router``, which serves ``/health/liveness`` and
    ``/health/readiness`` but nothing at ``/health``) through
    ``urllib``, since ``python:3.13-slim`` ships no ``curl``. Readiness
    answers ``503`` while the database is unreachable, and ``urlopen``
    raises on it, so ``healthy`` means the app reached Postgres.

    Args:
        project_name (str): Scaffolded project name, used for the
            container name and the default ``POSTGRES_DB``.
        extras_set (set[str]): Parsed extras deciding which hosts are
            re-pointed and which services the API waits on.
        port (int): Port the app listens on inside the container and
            publishes on the host.

    Returns:
        str: The ``api`` service block.
    """
    safe = project_name.replace("-", "_")
    environment: list[str] = [
        "      SERVER_HOST: 0.0.0.0",
        f'      SERVER_PORT: "{port}"',
        "      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-app}:"
        f"${{POSTGRES_PASSWORD:-app}}@postgres:5432/${{POSTGRES_DB:-{safe}}}",
    ]
    depends_on: list[str] = [
        "      postgres:\n        condition: service_healthy",
    ]
    if "cache" in extras_set:
        environment.append("      REDIS_URL: redis://redis:6379/0")
        depends_on.append("      redis:\n        condition: service_healthy")
    if extras_set & {"queue", "tasks"}:
        amqp = (
            "amqp://${RABBITMQ_DEFAULT_USER:-guest}:"
            "${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/"
        )
        environment.append(f"      RABBITMQ_URL: {amqp}")
        environment.append(f"      TASKIQ_BROKER_URL: {amqp}")
        depends_on.append("      rabbitmq:\n        condition: service_healthy")
    if "minio" in extras_set:
        environment.append("      STORAGE_ENDPOINT: minio:9000")
        depends_on.append("      minio:\n        condition: service_healthy")
        depends_on.append(
            "      minio-bootstrap:\n        condition: service_completed_successfully"
        )
    probe = (
        "import urllib.request; urllib.request.urlopen("
        f"'http://127.0.0.1:{port}/health/readiness', timeout=3)"
    )
    return (
        "  api:\n"
        "    # Only under `docker compose --profile prod up -d --build`;\n"
        "    # plain `docker compose up -d` keeps running just the infra.\n"
        '    profiles: ["prod"]\n'
        "    build: .\n"
        f"    container_name: {project_name}-api\n"
        "    restart: unless-stopped\n"
        "    env_file: .env\n"
        "    environment:\n"
        "      # environment: wins over env_file. .env points every backend\n"
        "      # at localhost (right for the host, wrong in a container), so\n"
        "      # the hosts are re-pointed at the compose service names here.\n"
        "      # SMTP_* is left to .env on purpose: MailHog is dev-only and\n"
        "      # production mail goes through the real SMTP server.\n"
        + "\n".join(environment)
        + "\n"
        "    ports:\n"
        f'      - "{port}:{port}"\n'
        "    depends_on:\n" + "\n".join(depends_on) + "\n"
        "    healthcheck:\n"
        "      # python:3.13-slim has no curl; readiness is 503 until the DB answers.\n"
        f'      test: ["CMD", "python", "-c", "{probe}"]\n'
        "      interval: 10s\n"
        "      timeout: 5s\n"
        "      retries: 6\n"
        "      start_period: 20s\n"
    )


def generate(project_name: str, extras: str, *, port: int = 8000) -> str:
    """Render a ``docker-compose.yaml`` matching the chosen extras.

    Args:
        project_name (str): Scaffolded project name. Used as a
            prefix in container names so multiple SDK services on
            the same host don't collide.
        extras (str): Comma-separated SDK extras the caller picked
            via ``tempest new --extras``. Triggers the
            corresponding service blocks.
        port (int): Port the app listens on. Feeds the ``api`` service
            under the ``prod`` profile (published port and health
            probe); the infra blocks do not use it.

    Returns:
        str: YAML body, ready to write at ``docker-compose.yaml``.
    """
    extras_set = _parse_extras(extras)

    services: list[str] = [_postgres_block(project_name)]
    volumes: list[str] = ["postgres-data"]

    if "cache" in extras_set:
        services.append(_redis_block(project_name))
        volumes.append("redis-data")

    if extras_set & {"queue", "tasks"}:
        services.append(_rabbitmq_block(project_name))
        volumes.append("rabbitmq-data")

    if "minio" in extras_set:
        services.append(_minio_blocks(project_name))
        volumes.append("minio-data")

    if "email" in extras_set:
        services.append(_mailhog_block(project_name))

    services.append(_api_block(project_name, extras_set, port))

    header = (
        f"# docker-compose.yaml — generated by `tempest new` for "
        f"`{project_name}`.\n"
        "#\n"
        "# Dev  (infra only, app on the host): docker compose up -d\n"
        "# Prod (infra + app in containers):   docker compose --profile prod up -d --build\n"
        "# Tear it down (keep data): docker compose --profile prod down\n"
        "# Tear it down (wipe data): docker compose --profile prod down -v\n"
        "#\n"
        "# Infra ports are published on 127.0.0.1 only: the host's dev\n"
        "# loop reaches them, nothing outside the host does. The api\n"
        "# service talks to them over the compose network by name.\n"
        "#\n"
        "# Only services backing the SDK extras you chose are wired\n"
        "# in here. Add others manually as the service grows.\n"
        "#\n"
        "# Credentials are NOT hardcoded — they resolve from the .env\n"
        "# file next to this compose (see .env.example). The :-default\n"
        "# in each ${VAR:-default} keeps the stack bootable before you\n"
        "# copy .env.example to .env; set real secrets in .env for any\n"
        "# non-throwaway deploy.\n"
        "\n"
        "services:\n"
    )

    volumes_section = ""
    if volumes:
        volumes_section = "\nvolumes:\n" + "".join(
            f"  {name}:\n" for name in sorted(volumes)
        )

    return header + "\n".join(services) + volumes_section


def env_block_for(extras: str) -> str:
    """Render extra ``.env.example`` lines covering the wired services.

    Args:
        extras (str): The same comma-separated extras passed to
            :func:`generate`.

    Returns:
        str: Trailing ``.env.example`` content (one block per
        service). Empty string when no service-specific vars apply.
    """
    extras_set = _parse_extras(extras)
    blocks: list[str] = []

    blocks.append(
        "\n# Postgres container credentials — read by docker compose.\n"
        "# Change these before exposing port 5432 outside the host.\n"
        "POSTGRES_USER=app\n"
        "POSTGRES_PASSWORD=app\n"
        "# POSTGRES_DB defaults to the project name; uncomment to override.\n"
        "# POSTGRES_DB=app\n"
        "# Uncomment to switch the host-run app from the default SQLite URL\n"
        "# (host/port/db must match the credentials above; asyncpg is\n"
        "# already a dependency). The prod profile's api service overrides\n"
        "# this with the in-network postgres host on its own.\n"
        "# DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app\n"
    )

    if "cache" in extras_set:
        blocks.append(
            "\n# Redis (cache + IdempotencyMiddleware Redis store)\n"
            "REDIS_URL=redis://localhost:6379/0\n"
        )

    if extras_set & {"queue", "tasks"}:
        blocks.append(
            "\n# RabbitMQ container credentials — read by docker compose.\n"
            "RABBITMQ_DEFAULT_USER=guest\n"
            "RABBITMQ_DEFAULT_PASS=guest\n"
            "RABBITMQ_DEFAULT_VHOST=/\n"
            "# Connection URLs consumed by the app (must match the creds above)\n"
            "RABBITMQ_URL=amqp://guest:guest@localhost:5672/\n"
            "TASKIQ_BROKER_URL=amqp://guest:guest@localhost:5672/\n"
        )

    if "minio" in extras_set:
        blocks.append(
            "\n# MinIO container credentials — read by docker compose.\n"
            "MINIO_ROOT_USER=minioadmin\n"
            "MINIO_ROOT_PASSWORD=minioadmin\n"
            "# Connection settings consumed by the app (keys must match the\n"
            "# root credentials above for the bundled single-user setup)\n"
            "STORAGE_ENDPOINT=localhost:9000\n"
            "STORAGE_ACCESS_KEY=minioadmin\n"
            "STORAGE_SECRET_KEY=minioadmin\n"
            "STORAGE_SECURE=false\n"
            "STORAGE_REGION=us-east-1\n"
            "STORAGE_DEFAULT_BUCKET=uploads\n"
        )

    if "email" in extras_set:
        blocks.append(
            "\n# SMTP via MailHog (dev catch-all — UI at http://localhost:8025)\n"
            "# Names match EmailSettings; MailHog speaks plain SMTP, so both\n"
            "# TLS toggles are off (STARTTLS on a plain server would crash).\n"
            "SMTP_HOST=localhost\n"
            "SMTP_PORT=1025\n"
            "SMTP_FROM_ADDR=noreply@localhost\n"
            "SMTP_USE_TLS=false\n"
            "SMTP_USE_SSL=false\n"
        )

    return "".join(blocks)


__all__: list[str] = [
    "MAILHOG_IMAGE",
    "MINIO_IMAGE",
    "MINIO_MC_IMAGE",
    "POSTGRES_IMAGE",
    "RABBITMQ_IMAGE",
    "REDIS_IMAGE",
    "env_block_for",
    "generate",
]
