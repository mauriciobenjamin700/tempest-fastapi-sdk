"""``tempest cache`` — check and invalidate the service's Redis cache.

Invalidating a namespace by hand means knowing how the ``@cached``
decorator names its registry sets, which is exactly the knowledge that
goes stale. These commands call :class:`CacheInvalidator`, the same code
the service calls, so the keys deleted here are the keys written there.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.cli.project import resolve_redis_url

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

cache_app: typer.Typer = typer.Typer(
    name="cache",
    help="Check and invalidate the Redis cache.",
    no_args_is_help=True,
)

_URL_OPTION: Any = typer.Option(
    "",
    "--redis-url",
    help="Redis URL. Defaults to REDIS_URL, then the project's settings.",
)
_PREFIX_OPTION: Any = typer.Option(
    "",
    "--key-prefix",
    help="The key_prefix the @cached decorators use, so registries line up.",
)


async def _with_manager(url: str, operation: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run ``operation`` against a connected Redis manager.

    Args:
        url (str): The Redis URL.
        operation (Callable[[Any], Awaitable[Any]]): Coroutine function
            taking the manager and returning the command's result.

    Returns:
        Any: Whatever ``operation`` returned.
    """
    from tempest_fastapi_sdk.cache import AsyncRedisManager

    manager = AsyncRedisManager(url)
    await manager.connect()
    try:
        return await operation(manager)
    finally:
        await manager.disconnect()


def _run(url: str, operation: Callable[[Any], Awaitable[Any]]) -> Any:
    """Execute a Redis operation, reporting a connection failure.

    Args:
        url (str): The Redis URL.
        operation (Callable[[Any], Awaitable[Any]]): The operation.

    Returns:
        Any: The operation's result.

    Raises:
        typer.Exit: Exit code 2 when Redis cannot be reached, carrying
            the driver's own message.
    """
    try:
        return asyncio.run(_with_manager(url, operation))
    except Exception as exc:
        typer.echo(f"error: Redis at {url} failed: {exc}", err=True)
        raise typer.Exit(2) from exc


def format_stats(info: dict[str, Any], dbsize: int) -> list[str]:
    """Render an ``INFO`` payload as the lines ``stats`` prints.

    Kept separate from the command because the fake Redis the tests run
    against does not implement ``INFO``: the formatting — including the
    division that has no answer before the first lookup — is what can go
    wrong, and this is the part a test can reach.

    Args:
        info (dict[str, Any]): The server's ``INFO`` mapping.
        dbsize (int): How many keys the selected database holds.

    Returns:
        list[str]: One line per reported figure.
    """
    hits = int(info.get("keyspace_hits", 0))
    misses = int(info.get("keyspace_misses", 0))
    total = hits + misses
    ratio = f"{hits / total:.1%}" if total else "n/a (no lookup yet)"
    return [
        f"server      {info.get('redis_version', 'unknown')}",
        f"keys        {dbsize}",
        f"memory      {info.get('used_memory_human', 'unknown')}",
        f"clients     {info.get('connected_clients', 'unknown')}",
        f"hit ratio   {ratio} ({hits} hit / {misses} miss, since restart)",
    ]


@cache_app.command("ping")
def cache_ping(redis_url: str = _URL_OPTION) -> None:
    """Report whether the configured Redis answers ``PING``.

    Raises:
        typer.Exit: Exit code 1 when the server is reachable but does
            not answer, so the command is usable as a readiness gate.
    """
    url = resolve_redis_url(redis_url or None)
    alive: bool = _run(url, lambda manager: manager.health_check())
    if not alive:
        typer.echo(f"error: no PONG from {url}.", err=True)
        raise typer.Exit(1)
    typer.echo(f"PONG from {url}")


@cache_app.command("stats")
def cache_stats(redis_url: str = _URL_OPTION) -> None:
    """Print key count, memory use and hit ratio of the cache server.

    The hit ratio is the server's lifetime one (``keyspace_hits`` over
    hits plus misses, both counted since the last restart), not this
    application's — Redis does not attribute either to a caller.
    """
    url = resolve_redis_url(redis_url or None)

    async def _collect(manager: Any) -> dict[str, Any]:
        info = await manager.client.info()
        size = await manager.client.dbsize()
        return {"info": info, "dbsize": size}

    payload = _run(url, _collect)
    for line in format_stats(payload["info"], payload["dbsize"]):
        typer.echo(line)


@cache_app.command("flush")
def cache_flush(
    namespace: str = typer.Option(
        "",
        "--namespace",
        "-n",
        help="Invalidate every entry written under this namespace.",
    ),
    tag: str = typer.Option(
        "",
        "--tag",
        "-t",
        help="Invalidate every entry carrying this tag.",
    ),
    key: str = typer.Option(
        "",
        "--key",
        help="Invalidate one exact cache key.",
    ),
    everything: bool = typer.Option(
        False,
        "--all",
        help="Flush the whole database. Needs --yes.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm --all, which deletes keys this service does not own.",
    ),
    redis_url: str = _URL_OPTION,
    key_prefix: str = _PREFIX_OPTION,
) -> None:
    """Invalidate cache entries by namespace, tag, key — or all of them.

    ``--all`` is ``FLUSHDB``: it deletes every key in the database,
    including sessions, rate-limit counters and feature flags when they
    share it. The targeted options go through ``CacheInvalidator``,
    which deletes only what the ``@cached`` registries recorded.

    Raises:
        typer.Exit: Exit code 2 when no target is given, or when
            ``--all`` arrives without ``--yes``.
    """
    url = resolve_redis_url(redis_url or None)
    if everything:
        if not yes:
            typer.echo(
                "error: --all runs FLUSHDB and deletes every key in the "
                "database, cache or not. Pass --yes to confirm.",
                err=True,
            )
            raise typer.Exit(2)
        _run(url, lambda manager: manager.client.flushdb())
        typer.echo(f"Flushed the whole database at {url}.")
        return

    if not any((namespace, tag, key)):
        typer.echo(
            "error: nothing to invalidate. Pass --namespace, --tag, --key or --all.",
            err=True,
        )
        raise typer.Exit(2)

    async def _invalidate(manager: Any) -> int:
        from tempest_fastapi_sdk.cache import CacheInvalidator

        invalidator = CacheInvalidator(manager, key_prefix=key_prefix)
        deleted = 0
        if namespace:
            deleted += await invalidator.invalidate_namespace(namespace)
        if tag:
            deleted += await invalidator.invalidate_tag(tag)
        if key:
            deleted += await invalidator.invalidate_keys(key)
        return deleted

    deleted: int = _run(url, _invalidate)
    typer.echo(f"Invalidated {deleted} key(s).")


__all__: list[str] = [
    "cache_app",
    "format_stats",
]
