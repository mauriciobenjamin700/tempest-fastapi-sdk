"""Tests for the docker-compose generator."""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from tempest_fastapi_sdk.cli.docker_compose import (
    env_block_for,
    generate,
)

ALL_EXTRAS: str = "auth,cache,queue,tasks,minio,email,upload"
"""Every extra that wires an infra service, plus two that wire none."""

INFRA_FOR_EXTRA: dict[str, str] = {
    "cache": "redis",
    "queue": "rabbitmq",
    "tasks": "rabbitmq",
    "minio": "minio",
}
"""Extra -> the compose service the ``api`` must wait on to be healthy."""


def _services(extras: str, *, port: int = 8000) -> dict[str, Any]:
    """Parse the generated compose and return its ``services`` mapping.

    Args:
        extras (str): Extras handed to :func:`generate`.
        port (int): App port handed to :func:`generate`.

    Returns:
        dict[str, Any]: The parsed ``services`` block.
    """
    parsed: dict[str, Any] = yaml.safe_load(generate("svc", extras, port=port))
    services: dict[str, Any] = parsed["services"]
    return services


def _localhost_keys(extras: str) -> set[str]:
    """Return every ``.env.example`` key whose value points at ``localhost``.

    Commented lines count too (``# DATABASE_URL=...``): the scaffold tells
    the developer to uncomment them, so the container must not inherit
    them either.

    Args:
        extras (str): Extras handed to :func:`env_block_for`.

    Returns:
        set[str]: The variable names.
    """
    pattern = re.compile(r"^#?\s*([A-Z_]+)=\S*(?:localhost|127\.0\.0\.1)")
    return {
        match.group(1)
        for line in env_block_for(extras).splitlines()
        if (match := pattern.match(line))
    }


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
        assert "127.0.0.1:5672:5672" in out
        assert "127.0.0.1:15672:15672" in out
        assert "rabbitmq-data" in out

    def test_tasks_extra_also_adds_rabbitmq(self) -> None:
        out = generate("svc", "tasks")
        assert "rabbitmq:" in out

    def test_queue_and_tasks_dont_duplicate_rabbitmq(self) -> None:
        out = generate("svc", "queue,tasks")
        # "rabbitmq:" (the service) appears once; "rabbitmq-data:" (the
        # volume) also matches but is a different string.
        assert len(re.findall(r"^  rabbitmq:$", out, flags=re.MULTILINE)) == 1

    def test_minio_extra_adds_minio_and_bootstrap(self) -> None:
        out = generate("svc", "minio")
        assert "minio:" in out
        assert "minio-bootstrap:" in out
        assert "mc mb -p local/uploads" in out
        assert "127.0.0.1:9000:9000" in out
        assert "127.0.0.1:9001:9001" in out

    def test_email_extra_adds_mailhog(self) -> None:
        out = generate("svc", "email")
        assert "mailhog:" in out
        assert "127.0.0.1:1025:1025" in out
        assert "127.0.0.1:8025:8025" in out

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


class TestProdProfile:
    def test_api_service_is_behind_the_prod_profile(self) -> None:
        api = _services("")["api"]
        assert api["profiles"] == ["prod"]
        assert api["build"] == "."
        assert api["env_file"] == ".env"
        assert api["restart"] == "unless-stopped"

    def test_only_the_api_service_carries_a_profile(self) -> None:
        services = _services(ALL_EXTRAS)
        profiled = {name for name, body in services.items() if "profiles" in body}
        assert profiled == {"api"}

    @pytest.mark.parametrize("extras", ["", "cache", "queue,tasks", ALL_EXTRAS])
    def test_api_environment_has_no_localhost(self, extras: str) -> None:
        environment: dict[str, str] = _services(extras)["api"]["environment"]
        for key, value in environment.items():
            assert "localhost" not in str(value), key
            assert "127.0.0.1" not in str(value) or key == "SERVER_HOST", key

    @pytest.mark.parametrize("extras", ["", "cache", "queue", "tasks", ALL_EXTRAS])
    def test_every_localhost_host_from_env_is_overridden(self, extras: str) -> None:
        environment: dict[str, str] = _services(extras)["api"]["environment"]
        expected = _localhost_keys(extras) - {"SMTP_HOST", "SMTP_FROM_ADDR"}
        assert expected <= set(environment), expected - set(environment)

    def test_smtp_is_left_to_the_env_file(self) -> None:
        environment: dict[str, str] = _services(ALL_EXTRAS)["api"]["environment"]
        assert not any(key.startswith("SMTP_") for key in environment)

    def test_hosts_point_at_compose_service_names(self) -> None:
        environment: dict[str, str] = _services(ALL_EXTRAS)["api"]["environment"]
        assert environment["DATABASE_URL"].startswith("postgresql+asyncpg://")
        assert "@postgres:5432/" in environment["DATABASE_URL"]
        assert environment["REDIS_URL"] == "redis://redis:6379/0"
        assert "@rabbitmq:5672/" in environment["RABBITMQ_URL"]
        assert "@rabbitmq:5672/" in environment["TASKIQ_BROKER_URL"]
        assert environment["MINIO_ENDPOINT"] == "minio:9000"

    def test_bind_is_pinned_inside_the_container(self) -> None:
        environment: dict[str, str] = _services("", port=9123)["api"]["environment"]
        assert environment["SERVER_HOST"] == "0.0.0.0"
        assert environment["SERVER_PORT"] == "9123"

    def test_database_credentials_follow_the_postgres_service(self) -> None:
        out = generate("my-api", "")
        assert (
            "DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-app}:"
            "${POSTGRES_PASSWORD:-app}@postgres:5432/${POSTGRES_DB:-my_api}"
        ) in out

    @pytest.mark.parametrize(
        "extras", ["", "cache", "queue", "tasks", "minio", "email", ALL_EXTRAS]
    )
    def test_one_depends_on_per_extra_service(self, extras: str) -> None:
        depends_on: dict[str, dict[str, str]] = _services(extras)["api"]["depends_on"]
        chosen = {part.strip() for part in extras.split(",") if part.strip()}
        expected = {"postgres"} | {
            service for extra, service in INFRA_FOR_EXTRA.items() if extra in chosen
        }
        healthy = {
            name
            for name, rule in depends_on.items()
            if rule["condition"] == "service_healthy"
        }
        assert healthy == expected
        assert "mailhog" not in depends_on

    def test_minio_bucket_bootstrap_completes_before_the_api(self) -> None:
        depends_on: dict[str, dict[str, str]] = _services("minio")["api"]["depends_on"]
        assert depends_on["minio-bootstrap"] == {
            "condition": "service_completed_successfully"
        }

    def test_every_healthy_dependency_has_a_healthcheck(self) -> None:
        services = _services(ALL_EXTRAS)
        for name, rule in services["api"]["depends_on"].items():
            if rule["condition"] == "service_healthy":
                assert "healthcheck" in services[name], name

    def test_port_is_published_and_probed(self) -> None:
        api = _services("", port=9123)["api"]
        assert api["ports"] == ["9123:9123"]
        probe = " ".join(api["healthcheck"]["test"])
        assert "http://127.0.0.1:9123/health/readiness" in probe
        assert "urllib.request" in probe
        assert "curl" not in probe

    def test_infra_ports_bind_loopback_only(self) -> None:
        services = _services(ALL_EXTRAS)
        for name, body in services.items():
            if name == "api":
                continue
            for mapping in body.get("ports", []):
                assert str(mapping).startswith("127.0.0.1:"), (name, mapping)


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
