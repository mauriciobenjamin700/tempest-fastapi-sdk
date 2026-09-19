"""``tempest doctor`` — check what this service can actually reach.

``tempest check-config`` reads the settings and reasons about them; it
never opens a socket. That is the right trade for a fast gate and the
wrong one for "why does the service not come up", where the answer is
almost always a dependency that is down, unreachable from this host, or
configured with credentials nobody updated.

Every check here connects for real. A capability the project did not
configure is reported as skipped, not as healthy — a green line for a
Redis that was never set up would be the kind of reassurance that costs
an outage.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
from dataclasses import dataclass
from typing import Any

import typer

from tempest_fastapi_sdk.cli.project import load_project_settings

_OK: str = "ok"
_FAIL: str = "fail"
_SKIP: str = "skip"


@dataclass(frozen=True)
class CheckOutcome:
    """One line of the report.

    Attributes:
        name (str): What was checked.
        status (str): ``ok``, ``fail`` or ``skip``.
        detail (str): The evidence — a version, an endpoint, or the
            error the attempt raised.
    """

    name: str
    status: str
    detail: str


def _python_check() -> CheckOutcome:
    """Report the interpreter running the CLI.

    Returns:
        CheckOutcome: Always ``ok``; the version is the evidence.
    """
    return CheckOutcome(
        "python",
        _OK,
        f"{platform.python_version()} ({sys.executable})",
    )


def _sdk_check() -> CheckOutcome:
    """Report the installed SDK version.

    Returns:
        CheckOutcome: Always ``ok``; a broken install could not have
        reached this line.
    """
    from tempest_fastapi_sdk import __version__

    return CheckOutcome("sdk", _OK, f"tempest-fastapi-sdk {__version__}")


def _settings_check(settings: Any) -> CheckOutcome:
    """Report whether the project's settings could be imported.

    Args:
        settings (Any): The loaded settings instance, or ``None``.

    Returns:
        CheckOutcome: ``ok`` naming the class, or ``skip`` when the
        working directory is not a Tempest project.
    """
    if settings is None:
        return CheckOutcome(
            "settings",
            _SKIP,
            "no <root>/core/settings.py here — run inside the project root",
        )
    return CheckOutcome("settings", _OK, type(settings).__name__)


def _is_untouched_default(settings: Any, field: str, env_var: str) -> bool:
    """Return whether a settings field still holds the mixin's default.

    ``EmailSettings`` defaults ``SMTP_HOST`` to ``localhost`` and
    ``MinIOSettings`` defaults ``MINIO_ENDPOINT`` to
    ``localhost:9000``, so a project that composes the mixin without
    using the capability looks configured. Connecting anyway would
    report a failure the operator never asked for — the same reasoning
    ``check_secrets`` uses when it compares a secret against
    ``model_fields[name].default``.

    Args:
        settings (Any): The project's settings instance.
        field (str): The field name to inspect.
        env_var (str): The environment variable that would override it.

    Returns:
        bool: True when the environment is silent and the value equals
        the declared default.
    """
    if os.environ.get(env_var):
        return False
    fields = getattr(type(settings), "model_fields", {})
    declared = fields.get(field)
    if declared is None:
        return False
    return bool(getattr(settings, field, None) == declared.default)


def _database_check(settings: Any) -> CheckOutcome:
    """Connect to the database and run its health check.

    Args:
        settings (Any): The project's settings, or ``None``.

    Returns:
        CheckOutcome: ``ok`` with the redacted URL, ``fail`` with the
        driver's message, or ``skip`` when no URL is configured.
    """
    url = os.environ.get("DATABASE_URL") or getattr(settings, "DATABASE_URL", "")
    if not url:
        return CheckOutcome("database", _SKIP, "no DATABASE_URL")

    async def _probe() -> tuple[bool, str]:
        from tempest_fastapi_sdk import AsyncDatabaseManager

        manager = AsyncDatabaseManager(url)
        await manager.connect()
        try:
            return (await manager.health_check(), manager.db_url_safe)
        finally:
            await manager.disconnect()

    try:
        alive, safe_url = asyncio.run(_probe())
    except Exception as exc:
        return CheckOutcome("database", _FAIL, f"{type(exc).__name__}: {exc}")
    return CheckOutcome(
        "database",
        _OK if alive else _FAIL,
        safe_url if alive else f"{safe_url} did not answer SELECT 1",
    )


def _redis_check(settings: Any, timeout: float) -> CheckOutcome:
    """Connect to Redis and send ``PING``.

    Args:
        settings (Any): The project's settings, or ``None``.
        timeout (float): Seconds to wait for the connection.

    Returns:
        CheckOutcome: ``ok``, ``fail`` with the driver's message, or
        ``skip`` when Redis is not configured or the extra is absent.
    """
    url = os.environ.get("REDIS_URL") or getattr(settings, "REDIS_URL", "")
    if not url:
        return CheckOutcome("redis", _SKIP, "no REDIS_URL")

    async def _probe() -> bool:
        from tempest_fastapi_sdk.cache import AsyncRedisManager

        manager = AsyncRedisManager(
            url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        await manager.connect()
        try:
            return await manager.health_check()
        finally:
            await manager.disconnect()

    try:
        alive = asyncio.run(_probe())
    except ImportError:
        return CheckOutcome("redis", _SKIP, "the [cache] extra is not installed")
    except Exception as exc:
        return CheckOutcome("redis", _FAIL, f"{type(exc).__name__}: {exc}")
    return CheckOutcome("redis", _OK if alive else _FAIL, url)


def _rabbitmq_check(settings: Any) -> CheckOutcome:
    """Open the broker connection and close it again.

    FastStream brokers expose no generic ping, so what is measured is
    the start handshake: a broker that connects and closes is one the
    service can publish through.

    Args:
        settings (Any): The project's settings, or ``None``.

    Returns:
        CheckOutcome: ``ok``, ``fail`` with the error, or ``skip`` when
        RabbitMQ is not configured or the extra is absent.
    """
    url = os.environ.get("RABBITMQ_URL") or getattr(settings, "RABBITMQ_URL", "")
    if not url:
        return CheckOutcome("rabbitmq", _SKIP, "no RABBITMQ_URL")

    async def _probe() -> bool:
        from faststream.rabbit import RabbitBroker

        from tempest_fastapi_sdk.queue import AsyncQueueManager

        manager = AsyncQueueManager(RabbitBroker(url))
        await manager.connect()
        try:
            return bool(await manager.health_check())
        finally:
            await manager.disconnect()

    try:
        started = asyncio.run(_probe())
    except ImportError:
        return CheckOutcome("rabbitmq", _SKIP, "the [queue] extra is not installed")
    except Exception as exc:
        return CheckOutcome("rabbitmq", _FAIL, f"{type(exc).__name__}: {exc}")
    return CheckOutcome("rabbitmq", _OK if started else _FAIL, url)


def _smtp_check(settings: Any, timeout: float) -> CheckOutcome:
    """Open the SMTP connection without sending anything.

    Args:
        settings (Any): The project's settings, or ``None``.
        timeout (float): Seconds to wait for the connection.

    Returns:
        CheckOutcome: ``ok`` naming host and port, ``fail`` with the
        error, or ``skip`` when the project composes no
        ``EmailSettings``.
    """
    host = getattr(settings, "SMTP_HOST", "")
    port = getattr(settings, "SMTP_PORT", 0)
    if not host:
        return CheckOutcome("smtp", _SKIP, "no SMTP_HOST")
    if _is_untouched_default(settings, "SMTP_HOST", "SMTP_HOST"):
        return CheckOutcome("smtp", _SKIP, "SMTP_HOST is still the mixin default")

    async def _probe() -> None:
        import aiosmtplib

        client = aiosmtplib.SMTP(hostname=host, port=int(port), timeout=timeout)
        await client.connect()
        await client.quit()

    try:
        asyncio.run(_probe())
    except ImportError:
        return CheckOutcome("smtp", _SKIP, "the [email] extra is not installed")
    except Exception as exc:
        return CheckOutcome(
            "smtp", _FAIL, f"{host}:{port} — {type(exc).__name__}: {exc}"
        )
    return CheckOutcome("smtp", _OK, f"{host}:{port}")


def _minio_check(settings: Any) -> CheckOutcome:
    """List the buckets, which is the cheapest authenticated call.

    Args:
        settings (Any): The project's settings, or ``None``.

    Returns:
        CheckOutcome: ``ok`` with the bucket count, ``fail`` with the
        error, or ``skip`` when MinIO is not configured.
    """
    endpoint = getattr(settings, "MINIO_ENDPOINT", "")
    if not endpoint:
        return CheckOutcome("minio", _SKIP, "no MINIO_ENDPOINT")
    if _is_untouched_default(settings, "MINIO_ENDPOINT", "MINIO_ENDPOINT"):
        return CheckOutcome("minio", _SKIP, "MINIO_ENDPOINT is still the mixin default")
    try:
        from minio import Minio

        client = Minio(
            endpoint,
            access_key=getattr(settings, "MINIO_ACCESS_KEY", ""),
            secret_key=getattr(settings, "MINIO_SECRET_KEY", ""),
            secure=bool(getattr(settings, "MINIO_SECURE", True)),
        )
        buckets = client.list_buckets()
    except ImportError:
        return CheckOutcome("minio", _SKIP, "the [minio] extra is not installed")
    except Exception as exc:
        return CheckOutcome("minio", _FAIL, f"{endpoint} — {type(exc).__name__}: {exc}")
    return CheckOutcome("minio", _OK, f"{endpoint} ({len(buckets)} bucket(s))")


def _config_checks(settings: Any) -> CheckOutcome:
    """Run the registered configuration checks over the settings.

    This is the static half — the same registry ``tempest check-config``
    walks — folded in so one command answers both halves of "is this
    deployment sane".

    Args:
        settings (Any): The project's settings, or ``None``.

    Returns:
        CheckOutcome: ``ok`` when nothing reaches error level, ``fail``
        with the count and the first message otherwise, or ``skip``
        without settings.
    """
    if settings is None:
        return CheckOutcome("config checks", _SKIP, "no settings to check")
    from tempest_fastapi_sdk.checks import run_checks

    messages = run_checks(settings)
    errors = [message for message in messages if message.is_serious()]
    if errors:
        return CheckOutcome(
            "config checks",
            _FAIL,
            f"{len(errors)} error(s), first: {errors[0].message}",
        )
    warnings = [message for message in messages if not message.is_serious()]
    return CheckOutcome(
        "config checks",
        _OK,
        f"no error ({len(warnings)} warning(s))",
    )


def doctor_command(
    timeout: float = typer.Option(
        10.0,
        "--timeout",
        min=0.1,
        help=(
            "Seconds to wait for each network probe. Honoured by the Redis "
            "and SMTP checks; the database and object-store clients use "
            "their own driver timeouts."
        ),
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the report as a JSON array instead of a table.",
    ),
) -> None:
    """Connect to everything this service depends on and report back.

    Unlike ``tempest check-config``, which only reads the settings, each
    line here is the result of a real connection. A dependency the
    project never configured reads ``skip``, never ``ok``.

    Raises:
        typer.Exit: Exit code 1 when any check failed, so the command
            works as a deployment smoke test.
    """
    settings = load_project_settings()
    outcomes = [
        _python_check(),
        _sdk_check(),
        _settings_check(settings),
        _database_check(settings),
        _redis_check(settings, timeout),
        _rabbitmq_check(settings),
        _smtp_check(settings, timeout),
        _minio_check(settings),
        _config_checks(settings),
    ]

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "name": outcome.name,
                        "status": outcome.status,
                        "detail": outcome.detail,
                    }
                    for outcome in outcomes
                ],
                indent=2,
            )
        )
    else:
        width = max(len(outcome.name) for outcome in outcomes)
        for outcome in outcomes:
            typer.echo(
                f"{outcome.status.upper().ljust(4)}  "
                f"{outcome.name.ljust(width)}  {outcome.detail}"
            )

    failures = [outcome for outcome in outcomes if outcome.status == _FAIL]
    if failures:
        typer.echo(
            f"{len(failures)} check(s) failed: "
            f"{', '.join(outcome.name for outcome in failures)}",
            err=True,
        )
        raise typer.Exit(1)


__all__: list[str] = [
    "CheckOutcome",
    "doctor_command",
]
