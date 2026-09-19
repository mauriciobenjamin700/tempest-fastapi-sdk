"""``tempest queue`` — publish into the service's broker, list its handlers.

Publishing a test message used to mean a scratch script that imported
the project's broker, connected it, published and closed it — four
steps, and the last one is the one people forget, which leaves the
message in a connection that never flushed.

The broker is the project's own (``<root>.queue:broker`` by default), so
the exchange, routing and serialization are the ones the consumers
expect.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer

from tempest_fastapi_sdk.cli.project import CODE_ROOTS

queue_app: typer.Typer = typer.Typer(
    name="queue",
    help="Publish messages and inspect the handlers of the project's broker.",
    no_args_is_help=True,
)

_BROKER_OPTION: Any = typer.Option(
    "",
    "--broker",
    help="Import spec of the FastStream broker. Defaults to '<root>.queue:broker'.",
)

_HANDLER_MODULES: tuple[str, ...] = ("queue.handlers", "queue")
"""Modules imported before listing, so decorated handlers register."""


def _load_broker(spec: str) -> Any:
    """Import the project's FastStream broker.

    Args:
        spec (str): Explicit ``module:attr``, empty to use the
            scaffolded ``<root>.queue:broker``.

    Returns:
        Any: The broker object.

    Raises:
        typer.Exit: Exit code 2 when nothing resolves, naming what was
            tried.
    """
    import importlib
    import sys
    from pathlib import Path

    root = Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    candidates = [spec] if spec else [f"{name}.queue:broker" for name in CODE_ROOTS]
    notes: list[str] = []
    for candidate in candidates:
        module_name, _, attr = candidate.partition(":")
        try:
            module = importlib.import_module(module_name)
            return getattr(module, attr)
        except (ImportError, AttributeError) as exc:
            notes.append(f"  {candidate}: {exc}")
    for note in notes:
        typer.echo(note, err=True)
    typer.echo(
        "error: no broker found. Pass --broker 'module:attr', or run inside a "
        "project scaffolded with the [queue] extra.",
        err=True,
    )
    raise typer.Exit(2)


def _import_handler_modules() -> None:
    """Import the modules whose decorators register consumers.

    A subscriber only exists on the broker once the module declaring it
    has been imported, so listing handlers without this reports an empty
    broker on a service that has several.
    """
    import importlib

    for root in CODE_ROOTS:
        for suffix in _HANDLER_MODULES:
            try:
                importlib.import_module(f"{root}.{suffix}")
            except ImportError:
                continue


@queue_app.command("publish")
def queue_publish(
    channel: str = typer.Argument(..., help="Destination channel / queue name."),
    payload: str = typer.Argument(..., help="Message body."),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Parse the payload as JSON and publish the decoded object.",
    ),
    broker_spec: str = _BROKER_OPTION,
) -> None:
    """Publish one message through the project's broker.

    The broker is connected, the message published and the connection
    closed — in that order, in one process, so the publish is flushed
    before the command returns.

    Raises:
        typer.Exit: Exit code 2 when ``--json`` is passed and the
            payload does not parse; code 1 when the broker refuses the
            publish, carrying its own message.
    """
    body: Any = payload
    if as_json:
        try:
            body = json.loads(payload)
        except json.JSONDecodeError as exc:
            typer.echo(
                f"error: --json given but the payload does not parse: {exc}", err=True
            )
            raise typer.Exit(2) from exc

    broker = _load_broker(broker_spec)

    async def _publish() -> None:
        from tempest_fastapi_sdk.queue import AsyncQueueManager

        manager = AsyncQueueManager(broker)
        await manager.connect()
        try:
            await manager.publish(body, channel)
        finally:
            await manager.disconnect()

    try:
        asyncio.run(_publish())
    except Exception as exc:
        typer.echo(f"error: publish failed — {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Published to {channel}.")


@queue_app.command("handlers")
def queue_handlers(broker_spec: str = _BROKER_OPTION) -> None:
    """List the channels this service consumes.

    A broker with no subscriber prints ``(no handler)`` and exits 0 — a
    publish-only service is a normal shape.
    """
    _import_handler_modules()
    broker = _load_broker(broker_spec)
    subscribers = list(getattr(broker, "subscribers", ()))
    if not subscribers:
        typer.echo("(no handler)")
        return
    for subscriber in subscribers:
        channel = (
            getattr(subscriber, "queue", None)
            or getattr(subscriber, "subject", None)
            or getattr(subscriber, "topic", None)
        )
        name = getattr(channel, "name", channel)
        calls = getattr(subscriber, "calls", ())
        handler = ", ".join(
            getattr(getattr(call, "handler", None), "__name__", "?") for call in calls
        )
        typer.echo(f"{name}  {handler or '(unnamed)'}")


__all__: list[str] = [
    "queue_app",
]
