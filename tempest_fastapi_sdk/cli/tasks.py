"""``tempest tasks`` — list the service's background tasks and enqueue one.

Firing a task by hand meant importing the project's ``TaskQueue``,
connecting it, calling ``.kiq`` and disconnecting. The interesting part
is the name: TaskIQ registers a task under ``<module>:<function>``, so
the name to pass is not the one in the source file, and ``list`` is
what tells you which is which.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer

from tempest_fastapi_sdk.cli.project import CODE_ROOTS, ensure_project_on_path

tasks_app: typer.Typer = typer.Typer(
    name="tasks",
    help="List the project's background tasks and enqueue one.",
    no_args_is_help=True,
)

_QUEUE_OPTION: Any = typer.Option(
    "",
    "--queue",
    help="Import spec of the TaskQueue. Defaults to '<root>.tasks:tq'.",
)

_JOB_MODULES: tuple[str, ...] = ("tasks.jobs", "tasks")
"""Modules imported before listing, so decorated tasks register."""


def _load_queue(spec: str) -> Any:
    """Import the project's ``TaskQueue``.

    Args:
        spec (str): Explicit ``module:attr``, empty to use the
            scaffolded ``<root>.tasks:tq``.

    Returns:
        Any: The task queue.

    Raises:
        typer.Exit: Exit code 2 when nothing resolves, naming what was
            tried.
    """
    import importlib

    ensure_project_on_path()
    candidates = [spec] if spec else [f"{name}.tasks:tq" for name in CODE_ROOTS]
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
        "error: no TaskQueue found. Pass --queue 'module:attr', or run inside "
        "a project scaffolded with the [tasks] extra.",
        err=True,
    )
    raise typer.Exit(2)


def _import_job_modules() -> None:
    """Import the modules whose decorators register tasks.

    A task exists on the broker only once its module has been imported.
    Listing without this reports an empty queue on a service that has
    several.
    """
    import importlib

    ensure_project_on_path()
    for root in CODE_ROOTS:
        for suffix in _JOB_MODULES:
            try:
                importlib.import_module(f"{root}.{suffix}")
            except ImportError:
                continue


def _registered(queue: Any) -> dict[str, Any]:
    """Return the tasks registered on a queue's broker.

    Args:
        queue (Any): The project's ``TaskQueue``.

    Returns:
        dict[str, Any]: ``task name -> decorated task``.
    """
    tasks: dict[str, Any] = queue.broker.get_all_tasks()
    return tasks


@tasks_app.command("list")
def tasks_list(queue_spec: str = _QUEUE_OPTION) -> None:
    """Print every registered task name, with the function behind it.

    A queue with no task prints ``(no task)`` and exits 0.
    """
    _import_job_modules()
    queue = _load_queue(queue_spec)
    tasks = _registered(queue)
    if not tasks:
        typer.echo("(no task)")
        return
    width = max(len(name) for name in tasks)
    for name in sorted(tasks):
        function = getattr(tasks[name], "original_func", None)
        typer.echo(f"{name.ljust(width)}  {getattr(function, '__name__', '?')}")


@tasks_app.command("run")
def tasks_run(
    name: str = typer.Argument(
        ..., help="Task name, as 'tempest tasks list' prints it."
    ),
    args: list[str] = typer.Option(
        [],
        "--arg",
        "-a",
        help="Positional argument, repeatable. Strings unless --json is passed.",
    ),
    kwargs: list[str] = typer.Option(
        [],
        "--kwarg",
        "-k",
        metavar="NAME=VALUE",
        help="Keyword argument, repeatable.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Parse every argument value as JSON (numbers, lists, objects).",
    ),
    queue_spec: str = _QUEUE_OPTION,
) -> None:
    """Enqueue one task through the project's broker.

    This enqueues; it does not execute. The worker runs the task, so a
    command that returns successfully means the message was accepted,
    not that the job succeeded.

    Raises:
        typer.Exit: Exit code 1 when no task carries that name; code 2
            when a ``--kwarg`` has no ``=`` or a value does not parse as
            JSON.
    """
    _import_job_modules()
    queue = _load_queue(queue_spec)
    tasks = _registered(queue)
    task = tasks.get(name)
    if task is None:
        typer.echo(
            f"error: no task named {name!r}. Run 'tempest tasks list' to see "
            "the registered names.",
            err=True,
        )
        raise typer.Exit(1)

    def _value(raw: str) -> Any:
        if not as_json:
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            typer.echo(
                f"error: --json given but {raw!r} does not parse: {exc}", err=True
            )
            raise typer.Exit(2) from exc

    positional = [_value(item) for item in args]
    keyword: dict[str, Any] = {}
    for item in kwargs:
        key, separator, raw = item.partition("=")
        if not separator:
            typer.echo(f"error: --kwarg expects NAME=VALUE, got {item!r}.", err=True)
            raise typer.Exit(2)
        keyword[key] = _value(raw)

    async def _enqueue() -> Any:
        await queue.connect()
        try:
            return await task.kiq(*positional, **keyword)
        finally:
            await queue.disconnect()

    try:
        result = asyncio.run(_enqueue())
    except Exception as exc:
        typer.echo(f"error: enqueue failed — {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc

    task_id = getattr(result, "task_id", None)
    typer.echo(f"Enqueued {name}" + (f" (task_id={task_id})" if task_id else ""))


__all__: list[str] = [
    "tasks_app",
]
