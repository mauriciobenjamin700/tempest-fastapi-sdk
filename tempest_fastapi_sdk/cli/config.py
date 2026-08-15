"""``[tool.tempest]`` configuration, split between its two owners.

The table serves two unrelated features, and since v0.226.0 each key is
read by whoever uses it:

```toml
[tool.tempest]
typing_strictness = "strict"   # read by tempest-cli (the quality gate)
commands = ["src.commands"]    # read here (project management commands)
```

The typing side moved to
[`tempest-cli`](https://pypi.org/project/tempest-cli/) together with the
gate itself; :class:`TempestConfig`, :data:`TypingStrictness`,
:func:`load_tempest_config` and :func:`find_pyproject` are re-exported
below so existing imports keep working.

What stays here is :func:`load_project_commands` — the module paths
whose ``typer.Typer`` gets mounted under ``tempest``, which is an SDK
concept and has no meaning in a framework-agnostic gate.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from tempest_cli.config import DEFAULT_TYPING_STRICTNESS as DEFAULT_TYPING_STRICTNESS
from tempest_cli.config import TempestConfig as TempestConfig
from tempest_cli.config import TypingStrictness as TypingStrictness
from tempest_cli.config import find_pyproject as find_pyproject
from tempest_cli.config import load_tempest_config as load_tempest_config


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
    pyproject = find_pyproject(start)
    if pyproject is None:
        return ()
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    table = data.get("tool", {}).get("tempest", {})
    return _coerce_commands(table.get("commands"), source=str(pyproject))


__all__: list[str] = [
    "DEFAULT_TYPING_STRICTNESS",
    "TempestConfig",
    "TypingStrictness",
    "find_pyproject",
    "load_project_commands",
    "load_tempest_config",
]
