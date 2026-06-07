"""Tests for the docker-compose generator."""

from __future__ import annotations

from tempest_fastapi_sdk.cli.docker_compose import (
    env_block_for,
    generate,
)


class TestGenerate:
    def test_minimal_extras_yields_only_postgres(self) -> None:
        out = generate("svc", "")
        assert "postgres" in out
        assert "image: postgres:" in out
        # No other services
        assert "redis:" not in out
        assert "rabbitmq:" not in out
        assert "minio:" not in out
        assert "mailhog:" not in out
        # Volume declared
        assert "volumes:" in out
        assert "postgres-data" in out

    def test_cache_extra_adds_redis(self) -> None:
        out = generate("svc", "auth,cache")
        assert "redis:" in out
        assert "redis-data" in out
        assert "rabbitmq:" not in out

    def test_queue_extra_adds_rabbitmq(self) -> None:
        out = generate("svc", "queue")
        assert "rabbitmq:" in out
        assert "5672:5672" in out
        assert "15672:15672" in out  # management UI
        assert "rabbitmq-data" in out

    def test_tasks_extra_also_adds_rabbitmq(self) -> None:
        out = generate("svc", "tasks")
        assert "rabbitmq:" in out

    def test_queue_and_tasks_dont_duplicate_rabbitmq(self) -> None:
        out = generate("svc", "queue,tasks")
        # "rabbitmq:" (the service) appears once; "rabbitmq-data:" (the
        # volume) also matches but is a different string.
        assert out.count("  rabbitmq:") == 1

    def test_minio_extra_adds_minio_and_bootstrap(self) -> None:
        out = generate("svc", "minio")
        assert "minio:" in out
        assert "minio-bootstrap:" in out
        assert "mc mb -p local/uploads" in out
        assert "9000:9000" in out
        assert "9001:9001" in out

    def test_email_extra_adds_mailhog(self) -> None:
        out = generate("svc", "email")
        assert "mailhog:" in out
        assert "1025:1025" in out
        assert "8025:8025" in out

    def test_all_extras_wire_everything(self) -> None:
        out = generate("svc", "auth,cache,queue,tasks,minio,email,upload")
        assert "postgres:" in out
        assert "redis:" in out
        assert "rabbitmq:" in out
        assert "minio:" in out
        assert "mailhog:" in out

    def test_container_names_carry_project_prefix(self) -> None:
        out = generate("my-api", "cache,minio")
        assert "container_name: my-api-postgres" in out
        assert "container_name: my-api-redis" in out
        assert "container_name: my-api-minio" in out

    def test_postgres_db_name_sanitizes_hyphens(self) -> None:
        out = generate("my-cool-api", "")
        # POSTGRES_DB must be a valid identifier (no dashes); it is now
        # exposed as the :-default of a .env-driven substitution.
        assert "POSTGRES_DB: ${POSTGRES_DB:-my_cool_api}" in out
        assert "my-cool-api}" not in out

    def test_credentials_resolve_from_env_not_hardcoded(self) -> None:
        out = generate("svc", "queue,minio")
        # Every credential must be a .env-driven ${VAR:-default}, never
        # a bare literal baked into the compose file.
        assert "POSTGRES_USER: ${POSTGRES_USER:-app}" in out
        assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-app}" in out
        assert "RABBITMQ_DEFAULT_USER: ${RABBITMQ_DEFAULT_USER:-guest}" in out
        assert "RABBITMQ_DEFAULT_PASS: ${RABBITMQ_DEFAULT_PASS:-guest}" in out
        assert "MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}" in out
        assert "MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}" in out
        # No bare hardcoded credential lines remain.
        assert "POSTGRES_USER: app" not in out
        assert "RABBITMQ_DEFAULT_USER: guest" not in out
        assert "MINIO_ROOT_USER: minioadmin" not in out

    def test_volumes_section_alphabetized(self) -> None:
        out = generate("svc", "cache,queue,minio")
        # Walk into the top-level ``volumes:`` block (the one preceded
        # by ``\nvolumes:\n``, not the per-service ``volumes:`` keys).
        volumes_section = out.rsplit("\nvolumes:\n", 1)[1]
        listed = [
            line.strip().rstrip(":")
            for line in volumes_section.splitlines()
            if line.startswith("  ") and line.strip().endswith(":")
        ]
        assert listed == sorted(listed)


class TestEnvBlockFor:
    def test_postgres_url_always_commented(self) -> None:
        out = env_block_for("")
        assert "# DATABASE_URL=postgresql+asyncpg" in out

    def test_redis_url_when_cache(self) -> None:
        assert "REDIS_URL" in env_block_for("cache")
        assert "REDIS_URL" not in env_block_for("auth")

    def test_rabbitmq_url_when_queue(self) -> None:
        assert "RABBITMQ_URL" in env_block_for("queue")
        assert "TASKIQ_BROKER_URL" in env_block_for("tasks")
        assert "RABBITMQ_URL" not in env_block_for("cache")

    def test_minio_block_when_minio(self) -> None:
        out = env_block_for("minio")
        assert "MINIO_ROOT_USER" in out
        assert "MINIO_ROOT_PASSWORD" in out
        assert "MINIO_ENDPOINT" in out
        assert "MINIO_ACCESS_KEY" in out
        assert "MINIO_DEFAULT_BUCKET" in out

    def test_postgres_credentials_always_present(self) -> None:
        out = env_block_for("")
        assert "POSTGRES_USER=app" in out
        assert "POSTGRES_PASSWORD=app" in out

    def test_rabbitmq_credentials_when_queue(self) -> None:
        out = env_block_for("queue")
        assert "RABBITMQ_DEFAULT_USER=guest" in out
        assert "RABBITMQ_DEFAULT_PASS=guest" in out

    def test_email_block_when_email(self) -> None:
        out = env_block_for("email")
        # Must use the SMTP_* names EmailSettings actually reads — the old
        # EMAIL_* names were silently ignored, leaving SMTP_USE_TLS at its
        # True default and crashing STARTTLS against plain MailHog.
        assert "SMTP_HOST=localhost" in out
        assert "SMTP_PORT=1025" in out
        assert "SMTP_FROM_ADDR=noreply@localhost" in out
        # MailHog is plain SMTP: STARTTLS (SMTP_USE_TLS) must be off.
        assert "SMTP_USE_TLS=false" in out
        assert "SMTP_USE_SSL=false" in out
        assert "EMAIL_HOST" not in out
        assert "EMAIL_USE_STARTTLS" not in out
