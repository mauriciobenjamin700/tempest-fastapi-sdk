"""Discovery + mounting of project-defined management commands.

Django lets an app register ``manage.py <command>``; this brings the
same to the ``tempest`` CLI. A service exposes a ``typer.Typer`` of its
own commands in a discovered module and they appear as first-class
``tempest <command>`` entries — sharing the SDK's help rendering and
error handling.

**Convention.** Put commands in ``src/commands.py`` (or ``app/commands.py``
/ ``commands.py``) exposing a Typer named ``commands`` (or ``app``):

```python
import typer

commands: typer.Typer = typer.Typer()


@commands.command("backfill")
def backfill(dry_run: bool = False) -> None:
    \"\"\"Backfill denormalized counters.\"\"\"
    ...
```

Then ``tempest backfill --dry-run`` runs it. Override the location with
``[tool.tempest] commands = "src.management"`` (string or list) in
``pyproject.toml``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import typer

#: Module paths tried when ``[tool.tempest] commands`` is not set.
DEFAULT_COMMAND_MODULES: tuple[str, ...] = (
    "src.commands",
    "app.commands",
    "commands",
)

#: Attribute names looked up on a command module, in priority order.
_TYPER_ATTRS: tuple[str, ...] = ("commands", "app")


def _load_command_typer(module_name: str) -> typer.Typer | None:
    """Import ``module_name`` and return its command Typer, if any.

    Args:
        module_name (str): The module import path.

    Returns:
        typer.Typer | None: The first ``commands`` / ``app`` attribute
        that is a ``typer.Typer``, or ``None`` when the module has none.

    Raises:
        ImportError: When the module cannot be imported.
    """
    import typer

    module = importlib.import_module(module_name)
    for attr in _TYPER_ATTRS:
        candidate = getattr(module, attr, None)
        if isinstance(candidate, typer.Typer):
            return candidate
    return None


def _existing_names(app: typer.Typer) -> set[str]:
    """Return the command / group names already registered on ``app``.

    Args:
        app (typer.Typer): The root Typer application.

    Returns:
        set[str]: Names that a project command must not shadow.
    """
    names: set[str] = set()
    for info in app.registered_commands:
        name = info.name or (
            info.callback.__name__.replace("_", "-") if info.callback else None
        )
        if name:
            names.add(name)
    for group in app.registered_groups:
        if group.name:
            names.add(group.name)
    return names


def _command_name(info: Any) -> str | None:
    """Derive the CLI name of a Typer command info.

    Args:
        info (Any): A Typer ``CommandInfo``.

    Returns:
        str | None: The explicit name, the callback name with
        underscores hyphenated, or ``None`` when undeterminable.
    """
    if info.name:
        return str(info.name)
    if info.callback is not None:
        return str(info.callback.__name__).replace("_", "-")
    return None


def mount_project_commands(
    app: typer.Typer,
    *,
    modules: tuple[str, ...] = (),
    cwd: Path | None = None,
    warn: bool = True,
) -> list[str]:
    """Discover project command modules and mount them onto ``app``.

    When ``modules`` is given (from ``[tool.tempest] commands``), each is
    required — an import failure or a missing Typer raises. Otherwise the
    conventional candidates are tried best-effort and silently skipped
    when absent.

    A project command whose name collides with a built-in ``tempest``
    command is skipped (with a warning) so the SDK's commands always win.

    Args:
        app (typer.Typer): The root Typer application to extend.
        modules (tuple[str, ...]): Explicit module paths; empty uses
            :data:`DEFAULT_COMMAND_MODULES` best-effort.
        cwd (Path | None): Directory to add to ``sys.path`` so the
            project is importable. Defaults to the current directory.
        warn (bool): Whether to print a stderr warning on a skipped
            collision.

    Returns:
        list[str]: The names of the commands and groups mounted.

    Raises:
        ImportError: When an explicitly-configured module cannot import.
        ValueError: When an explicitly-configured module exposes no
            command Typer.
    """
    root = str((cwd or Path.cwd()).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)

    explicit = bool(modules)
    targets = modules or DEFAULT_COMMAND_MODULES
    mounted: list[str] = []

    for module_name in targets:
        try:
            project_typer = _load_command_typer(module_name)
        except ImportError:
            if explicit:
                raise
            continue
        if project_typer is None:
            if explicit:
                raise ValueError(
                    f"Module {module_name!r} exposes no 'commands' or 'app' "
                    "typer.Typer for tempest to mount."
                )
            continue

        taken = _existing_names(app)
        for info in project_typer.registered_commands:
            name = _command_name(info)
            if name and name in taken:
                if warn:
                    import typer

                    typer.secho(
                        f"tempest: skipping project command {name!r} — it "
                        "collides with a built-in command.",
                        err=True,
                        fg="yellow",
                    )
                continue
            app.registered_commands.append(info)
            if name:
                mounted.append(name)
        for group in project_typer.registered_groups:
            if group.name and group.name in taken:
                if warn:
                    import typer

                    typer.secho(
                        f"tempest: skipping project group {group.name!r} — it "
                        "collides with a built-in command.",
                        err=True,
                        fg="yellow",
                    )
                continue
            app.registered_groups.append(group)
            if group.name:
                mounted.append(group.name)

    return mounted


__all__: list[str] = [
    "DEFAULT_COMMAND_MODULES",
    "mount_project_commands",
]
