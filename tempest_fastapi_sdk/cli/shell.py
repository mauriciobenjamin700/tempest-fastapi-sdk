"""``tempest shell`` — an async REPL with the project already loaded.

Answering "how many orders does this user have?" should not cost a
throwaway script that re-imports the settings, builds an engine, opens a
session and remembers to close it. This opens that session once and
hands it to an interactive console.

The console compiles with ``ast.PyCF_ALLOW_TOP_LEVEL_AWAIT`` and runs
the resulting coroutine on the same loop the session belongs to, so
``await session.execute(...)`` works at the prompt. Exposing a
``run(coro)`` helper instead would look equivalent and is not: a session
driven from a second loop raises ``MissingGreenlet`` at the first lazy
load.
"""

from __future__ import annotations

import ast
import asyncio
import code
import inspect
import sys
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.cli.project import (
    CODE_ROOTS,
    ensure_project_on_path,
    load_project_settings,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import CodeType

_MODEL_MODULES: tuple[str, ...] = ("db.models", "models")
"""Module suffixes probed under each code root for the project's models."""


class _AsyncConsole(code.InteractiveConsole):
    """Interactive console that awaits top-level coroutines.

    The ``PyCF_ALLOW_TOP_LEVEL_AWAIT`` flag is set on the compiler the
    base class already owns, which is how ``python -m asyncio`` does it;
    overriding the compile step instead would have to re-implement the
    incomplete-input detection that makes multi-line blocks work.

    Attributes:
        loop (asyncio.AbstractEventLoop): The loop every awaited
            statement runs on — the same one the injected session was
            opened on.
    """

    def __init__(
        self,
        namespace: dict[str, Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Initialize the console.

        Args:
            namespace (dict[str, Any]): Names available at the prompt.
            loop (asyncio.AbstractEventLoop): Loop for awaited code.
        """
        super().__init__(namespace)
        self.loop: asyncio.AbstractEventLoop = loop
        self.compile.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT

    def runcode(self, code_object: CodeType) -> None:
        """Execute one compiled statement, awaiting it when needed.

        Args:
            code_object (CodeType): The statement to run.
        """
        try:
            result = eval(code_object, self.locals)
            if inspect.iscoroutine(result):
                result = self.loop.run_until_complete(result)
            if result is not None:
                self.locals["_"] = result
                sys.displayhook(result)
        except SystemExit:
            raise
        except BaseException:
            self.showtraceback()


def _import_project_models(root: Path) -> dict[str, Any]:
    """Collect the mapped models the project defines.

    Args:
        root (Path): The project root.

    Only classes *defined by the project* are returned: a models module
    re-exports the SDK's own ``BaseModel``, and offering it at the prompt
    next to the real models invites a query against the base class.

    Returns:
        dict[str, Any]: ``ClassName -> model class`` for every mapped
        class found, empty when the project has no models module.
    """
    import importlib

    from sqlalchemy.orm import DeclarativeBase

    for code_root in CODE_ROOTS:
        for suffix in _MODEL_MODULES:
            dotted = f"{code_root}.{suffix}"
            try:
                module = importlib.import_module(dotted)
            except ImportError:
                continue
            found = {
                name: value
                for name, value in vars(module).items()
                if isinstance(value, type)
                and issubclass(value, DeclarativeBase)
                and hasattr(value, "__tablename__")
                and value.__module__.startswith(f"{code_root}.")
            }
            if found:
                return found
    return {}


def _database_url(settings: Any) -> str | None:
    """Pick the database URL for the shell session.

    Args:
        settings (Any): The project's settings instance, or ``None``.

    Returns:
        str | None: The URL, or ``None`` when neither the environment
        nor the settings carry one.
    """
    import os

    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    candidate = getattr(settings, "DATABASE_URL", None)
    return candidate if isinstance(candidate, str) and candidate else None


def _banner(namespace: dict[str, Any], *, database: str | None) -> str:
    """Describe what the prompt was handed.

    Args:
        namespace (dict[str, Any]): The console namespace.
        database (str | None): Redacted URL of the open session, or
            ``None`` when no session could be opened.

    Returns:
        str: The banner printed above the prompt.
    """
    names = ", ".join(sorted(namespace)) or "(nothing)"
    lines = [
        f"tempest shell — Python {sys.version.split()[0]}, top-level await enabled",
        f"available: {names}",
    ]
    lines.append(f"session: open on {database}" if database else "session: none")
    return "\n".join(lines)


async def _open_session(url: str) -> tuple[Any, Any]:
    """Connect to the database and open one session.

    Args:
        url (str): The database URL.

    Returns:
        tuple[Any, Any]: ``(manager, session)``.
    """
    from tempest_fastapi_sdk import AsyncDatabaseManager

    manager = AsyncDatabaseManager(url)
    await manager.connect()
    session = await manager.get_session()
    return (manager, session)


def shell_command(
    no_db: bool = typer.Option(
        False,
        "--no-db",
        help="Skip the database connection and start the prompt without a session.",
    ),
) -> None:
    """Open an async REPL with the project's settings, models and session.

    The prompt starts with ``settings``, every mapped model class,
    ``select`` and — unless ``--no-db`` is passed — an open
    ``session``. ``await`` works at the top level.

    Raises:
        typer.Exit: Exit code 2 when a database connection was expected
            but the URL resolves to nothing.
    """

    from sqlalchemy import select, text

    root = ensure_project_on_path()
    settings = load_project_settings(root)
    namespace: dict[str, Any] = {
        "settings": settings,
        "select": select,
        "text": text,
    }
    namespace.update(_import_project_models(root))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    manager: Any = None
    session: Any = None
    database: str | None = None
    try:
        if not no_db:
            url = _database_url(settings)
            if url is None:
                typer.echo(
                    "error: no database URL. Set DATABASE_URL, expose a "
                    "settings instance carrying one, or pass --no-db.",
                    err=True,
                )
                raise typer.Exit(2)
            manager, session = loop.run_until_complete(_open_session(url))
            namespace["session"] = session
            namespace["db"] = manager
            database = manager.db_url_safe

        console = _AsyncConsole(namespace, loop)
        console.interact(banner=_banner(namespace, database=database), exitmsg="")
    finally:
        if session is not None:
            loop.run_until_complete(session.close())
        if manager is not None:
            loop.run_until_complete(manager.disconnect())
        loop.close()
        asyncio.set_event_loop(None)


__all__: list[str] = [
    "shell_command",
]
