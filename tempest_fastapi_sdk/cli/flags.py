"""``tempest flags`` — read and flip feature flags from the terminal.

A flag exists to be moved without a deploy. With the Redis backend that
move lived in ``redis-cli HSET feature_flags <name> 1``, which is the
kind of command that gets a digit wrong at the wrong hour. These four
speak through :class:`RedisFeatureFlagBackend`, so the value the service
reads and the value the CLI writes are encoded by the same code.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.cli.project import resolve_redis_url

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

flags_app: typer.Typer = typer.Typer(
    name="flags",
    help="Read and flip feature flags (Redis-backed).",
    no_args_is_help=True,
)

_URL_OPTION: Any = typer.Option(
    "",
    "--redis-url",
    help="Redis URL. Defaults to REDIS_URL, then the project's settings.",
)
_KEY_OPTION: Any = typer.Option(
    "feature_flags",
    "--key",
    help="Redis hash key holding the flags. Must match the service's.",
)


async def _with_backend(
    url: str,
    key: str,
    operation: Callable[[Any], Awaitable[Any]],
) -> Any:
    """Run ``operation`` against a connected flag backend.

    Args:
        url (str): The Redis URL.
        key (str): The Redis hash key holding the flags.
        operation (Callable[[Any], Awaitable[Any]]): Coroutine function
            taking the backend and returning the command's result.

    Returns:
        Any: Whatever ``operation`` returned.
    """
    from tempest_fastapi_sdk.cache import AsyncRedisManager
    from tempest_fastapi_sdk.flags import RedisFeatureFlagBackend

    manager = AsyncRedisManager(url)
    await manager.connect()
    try:
        return await operation(RedisFeatureFlagBackend(manager.client, key=key))
    finally:
        await manager.disconnect()


def _run(url: str, key: str, operation: Callable[[Any], Awaitable[Any]]) -> Any:
    """Execute a backend operation, reporting a connection failure.

    Args:
        url (str): The Redis URL.
        key (str): The Redis hash key.
        operation (Callable[[Any], Awaitable[Any]]): The operation.

    Returns:
        Any: The operation's result.

    Raises:
        typer.Exit: Exit code 2 when Redis cannot be reached, with the
            driver's own message — "connection refused" names the fix,
            "an error occurred" does not.
    """
    try:
        return asyncio.run(_with_backend(url, key, operation))
    except Exception as exc:
        typer.echo(f"error: Redis at {url} failed: {exc}", err=True)
        raise typer.Exit(2) from exc


@flags_app.command("list")
def flags_list(
    redis_url: str = _URL_OPTION,
    key: str = _KEY_OPTION,
) -> None:
    """Print every flag the backend holds, with its value.

    A backend holding no flag prints ``(no flags)`` and exits 0: a
    service whose flags are all still at their code default is an
    ordinary state, not a failure.
    """
    url = resolve_redis_url(redis_url or None)
    values: dict[str, bool] = _run(url, key, lambda backend: backend.all())
    if not values:
        typer.echo("(no flags)")
        return
    width = max(len(name) for name in values)
    for name in sorted(values):
        typer.echo(f"{name.ljust(width)}  {'on' if values[name] else 'off'}")


@flags_app.command("get")
def flags_get(
    name: str = typer.Argument(..., help="Flag name."),
    redis_url: str = _URL_OPTION,
    key: str = _KEY_OPTION,
) -> None:
    """Print one flag's stored value.

    Raises:
        typer.Exit: Exit code 1 when the backend holds no value for the
            flag — "unset" is a different answer from "off", and the
            service resolves it to the caller's default.
    """
    url = resolve_redis_url(redis_url or None)
    value: bool | None = _run(url, key, lambda backend: backend.get(name))
    if value is None:
        typer.echo(f"{name} unset")
        raise typer.Exit(1)
    typer.echo(f"{name} {'on' if value else 'off'}")


@flags_app.command("enable")
def flags_enable(
    name: str = typer.Argument(..., help="Flag name."),
    redis_url: str = _URL_OPTION,
    key: str = _KEY_OPTION,
) -> None:
    """Turn a flag on for every process reading this backend."""
    url = resolve_redis_url(redis_url or None)
    _run(url, key, lambda backend: backend.set(name, True))
    typer.echo(f"{name} on")


@flags_app.command("disable")
def flags_disable(
    name: str = typer.Argument(..., help="Flag name."),
    redis_url: str = _URL_OPTION,
    key: str = _KEY_OPTION,
) -> None:
    """Turn a flag off for every process reading this backend."""
    url = resolve_redis_url(redis_url or None)
    _run(url, key, lambda backend: backend.set(name, False))
    typer.echo(f"{name} off")


__all__: list[str] = [
    "flags_app",
]
