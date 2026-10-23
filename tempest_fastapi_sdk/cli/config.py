"""``[tool.tempest]`` configuration, split between its two owners.

The table serves two unrelated features, and since v0.226.0 each key is
read by whoever uses it:

```toml
[tool.tempest]
typing_strictness = "strict"   # read by tempest-cli (the quality gate)
commands = ["src.commands"]    # read here (project management commands)
```

What the SDK reads lives here and needs only the standard library:
:func:`find_pyproject`, :func:`load_tempest_table` and
:func:`load_project_commands` — the module paths whose ``typer.Typer`` gets
mounted under ``tempest``. That is what lets the ``[cli-ops]`` extra, which
has no ``tempest-cli``, run a project's own commands on a server.

The typing side moved to
[`tempest-cli`](https://pypi.org/project/tempest-cli/) together with the
gate itself. :class:`TempestConfig`, :data:`TypingStrictness`,
:data:`DEFAULT_TYPING_STRICTNESS` and :func:`load_tempest_config` are still
importable from here, resolved lazily from ``tempest_cli.config``: without
the ``[cli]`` extra, reading one raises an ``ImportError`` carrying the
install line, and nothing else in this module is affected.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.cli.entrypoint import raise_for_missing_cli_extra

if TYPE_CHECKING:
    from tempest_cli.config import (
        DEFAULT_TYPING_STRICTNESS as DEFAULT_TYPING_STRICTNESS,
    )
    from tempest_cli.config import TempestConfig as TempestConfig
    from tempest_cli.config import TypingStrictness as TypingStrictness
    from tempest_cli.config import load_tempest_config as load_tempest_config

PYPROJECT_FILENAME: str = "pyproject.toml"
"""File :func:`find_pyproject` looks for in each directory.

Ported from tempest-cli's ``find_pyproject``.
"""

TEMPEST_TABLE_PATH: tuple[str, ...] = ("tool", "tempest")
"""Keys leading from the parsed ``pyproject.toml`` to ``[tool.tempest]``.

Ported from tempest-cli's ``load_tempest_config``.
"""

_GATE_EXPORTS: frozenset[str] = frozenset(
    {
        "DEFAULT_TYPING_STRICTNESS",
        "TempestConfig",
        "TypingStrictness",
        "load_tempest_config",
    }
)
"""Names re-exported lazily from ``tempest_cli.config`` (the ``[cli]`` extra)."""


def find_pyproject(start: Path | None = None) -> Path | None:
    """Locate the nearest ``pyproject.toml`` walking up from ``start``.

    Ported from tempest-cli's ``find_pyproject``, so finding the project
    does not need the ``[cli]`` extra.

    Args:
        start (Path | None): Directory to begin the search. Defaults to
            the current working directory.

    Returns:
        Path | None: The path to the first ``pyproject.toml`` found in
        ``start`` or an ancestor, or ``None`` when none exists.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / PYPROJECT_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_tempest_table(start: Path | None = None) -> tuple[Path | None, dict[str, Any]]:
    """Read ``[tool.tempest]`` from the nearest ``pyproject.toml``.

    Args:
        start (Path | None): Directory to begin the search. Defaults to
            the current working directory.

    Returns:
        tuple[Path | None, dict[str, Any]]: The ``pyproject.toml`` read
        (``None`` when none was found) and its ``[tool.tempest]`` table
        (empty when the file or the table is absent).

    Raises:
        tomllib.TOMLDecodeError: When the file found is not valid TOML.
    """
    pyproject = find_pyproject(start)
    if pyproject is None:
        return None, {}
    with pyproject.open("rb") as handle:
        table: Any = tomllib.load(handle)
    for key in TEMPEST_TABLE_PATH:
        table = table.get(key, {}) if isinstance(table, dict) else {}
    return pyproject, table if isinstance(table, dict) else {}


def _coerce_commands(value: object, *, source: str) -> tuple[str, ...]:
    """Validate the ``[tool.tempest] commands`` value.

    Accepts a single module path string or a list of them; ``None``
    (absent) yields an empty tuple.

    Args:
        value (object): The raw value read from the TOML table.
        source (str): Human-readable origin used in the error message.

    Returns:
        tuple[str, ...]: The module import paths.

    Raises:
        ValueError: When the value is not a string or list of strings.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError(
        f"{source}: invalid commands {value!r}; expected a string or a "
        "list of module import paths."
    )


def load_project_commands(start: Path | None = None) -> tuple[str, ...]:
    """Read ``[tool.tempest] commands`` from the nearest ``pyproject.toml``.

    Args:
        start (Path | None): Directory to begin the search. Defaults to
            the current working directory.

    Returns:
        tuple[str, ...]: Import paths of modules exposing a
        ``typer.Typer`` to mount under the ``tempest`` CLI. Empty means
        "use the conventional candidates" (``src.commands`` /
        ``app.commands`` / ``commands``).

    Raises:
        ValueError: When the key is present but is neither a string nor
            a list of strings.
    """
    pyproject, table = load_tempest_table(start)
    if pyproject is None:
        return ()
    return _coerce_commands(table.get("commands"), source=str(pyproject))


def __getattr__(name: str) -> Any:
    """Resolve the quality gate's config names from ``tempest_cli.config``.

    Args:
        name (str): The attribute requested.

    Returns:
        Any: The symbol of the same name in ``tempest_cli.config``.

    Raises:
        ImportError: When the ``[cli]`` extra is not installed.
        AttributeError: For any other attribute name.
    """
    if name not in _GATE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        module = importlib.import_module("tempest_cli.config")
    except ModuleNotFoundError as exc:
        raise_for_missing_cli_extra(exc)
        raise
    return getattr(module, name)


__all__: list[str] = [
    "DEFAULT_TYPING_STRICTNESS",
    "PYPROJECT_FILENAME",
    "TEMPEST_TABLE_PATH",
    "TempestConfig",
    "TypingStrictness",
    "find_pyproject",
    "load_project_commands",
    "load_tempest_config",
    "load_tempest_table",
]
