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

The image tags are pinned to versions known to work with the SDK
at release time. Bumping any of them should go through the smoke
suite first.
"""

from __future__ import annotations

# Pinned image tags — bump intentionally, not by accident.
POSTGRES_IMAGE: str = "postgres:16-alpine"
REDIS_IMAGE: str = "redis:7-alpine"
RABBITMQ_IMAGE: str = "rabbitmq:3-management-alpine"
MINIO_IMAGE: str = "minio/minio:RELEASE.2024-12-13T22-19-12Z"
MINIO_MC_IMAGE: str = "minio/mc:RELEASE.2024-11-21T17-21-54Z"
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
    """Compose snippet for Postgres."""
    safe = project_name.replace("-", "_")
    return f"""\
  postgres:
    image: {POSTGRES_IMAGE}
    container_name: {project_name}-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: {safe}
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d {safe}"]
      interval: 5s
      timeout: 5s
      retries: 10
"""


def _redis_block(project_name: str) -> str:
    """Compose snippet for Redis."""
    return f"""\
  redis:
    image: {REDIS_IMAGE}
    container_name: {project_name}-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
"""


def _rabbitmq_block(project_name: str) -> str:
    """Compose snippet for RabbitMQ with management UI."""
    return f"""\
  rabbitmq:
    image: {RABBITMQ_IMAGE}
    container_name: {project_name}-rabbitmq
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: guest
      RABBITMQ_DEFAULT_PASS: guest
    ports:
      - "5672:5672"     # AMQP
      - "15672:15672"   # Management UI — http://localhost:15672 (guest/guest)
    volumes:
      - rabbitmq-data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 10s
      timeout: 10s
      retries: 6
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
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"   # S3 API
      - "9001:9001"   # Web console — http://localhost:9001
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
      mc alias set local http://minio:9000 minioadmin minioadmin &&
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
      - "1025:1025"   # SMTP
      - "8025:8025"   # Web UI — http://localhost:8025
"""


def generate(project_name: str, extras: str) -> str:
    """Render a ``docker-compose.yaml`` matching the chosen extras.

    Args:
        project_name (str): Scaffolded project name. Used as a
            prefix in container names so multiple SDK services on
            the same host don't collide.
        extras (str): Comma-separated SDK extras the caller picked
            via ``tempest new --extras``. Triggers the
            corresponding service blocks.

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

    header = (
        f"# docker-compose.yaml — generated by `tempest new` for "
        f"`{project_name}`.\n"
        "#\n"
        "# Boot the entire stack:   docker compose up -d\n"
        "# Tear it down (keep data): docker compose down\n"
        "# Tear it down (wipe data): docker compose down -v\n"
        "#\n"
        "# Only services backing the SDK extras you chose are wired\n"
        "# in here. Add others manually as the service grows.\n"
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
        "\n# Postgres (uncomment to switch from the default SQLite URL)\n"
        "# DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app\n"
    )

    if "cache" in extras_set:
        blocks.append(
            "\n# Redis (cache + IdempotencyMiddleware Redis store)\n"
            "REDIS_URL=redis://localhost:6379/0\n"
        )

    if extras_set & {"queue", "tasks"}:
        blocks.append(
            "\n# RabbitMQ (FastStream queue / TaskIQ broker)\n"
            "RABBITMQ_URL=amqp://guest:guest@localhost:5672/\n"
            "TASKIQ_BROKER_URL=amqp://guest:guest@localhost:5672/\n"
        )

    if "minio" in extras_set:
        blocks.append(
            "\n# MinIO / S3-compatible object storage\n"
            "MINIO_ENDPOINT=localhost:9000\n"
            "MINIO_ACCESS_KEY=minioadmin\n"
            "MINIO_SECRET_KEY=minioadmin\n"
            "MINIO_SECURE=false\n"
            "MINIO_REGION=us-east-1\n"
            "MINIO_DEFAULT_BUCKET=uploads\n"
        )

    if "email" in extras_set:
        blocks.append(
            "\n# SMTP via MailHog (dev catch-all — UI at http://localhost:8025)\n"
            "EMAIL_HOST=localhost\n"
            "EMAIL_PORT=1025\n"
            "EMAIL_FROM=noreply@localhost\n"
            "EMAIL_USE_TLS=false\n"
            "EMAIL_USE_STARTTLS=false\n"
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
