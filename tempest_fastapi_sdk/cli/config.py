"""``[tool.tempest]`` configuration read from ``pyproject.toml``.

The CLI quality gates (``tempest lint`` / ``fix`` / ``type`` / ``check``)
let a project dial how strictly typing is enforced without editing the
gate commands themselves. The single knob is ``typing_strictness`` under
``[tool.tempest]``:

```toml
[tool.tempest]
typing_strictness = "strict"  # lenient | standard | strict
```

Each level layers *additional* ruff and mypy flags on top of whatever
``[tool.ruff]`` / ``[tool.mypy]`` already declares -- it never relaxes
the project's own configuration. ``Any`` is always a valid annotation,
so ANN401 is never enabled at any level: the levels enforce that things
*are* annotated, never that they avoid ``Any``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

TypingStrictness = Literal["lenient", "standard", "strict"]
"""Allowed values for ``[tool.tempest] typing_strictness``."""

DEFAULT_TYPING_STRICTNESS: TypingStrictness = "standard"
"""Level applied when the key is absent or no ``pyproject.toml`` is found."""

# ANN rules layered onto ruff per level. ANN401 (Any) is intentionally
# never included -- ``Any`` is a valid annotation. ANN002/ANN003
# (``*args`` / ``**kwargs``) are also left out as noise.
_RUFF_ANN_BY_LEVEL: dict[TypingStrictness, list[str]] = {
    "lenient": [],
    "standard": ["ANN001", "ANN201", "ANN202", "ANN205", "ANN206"],
    "strict": [
        "ANN001",
        "ANN201",
        "ANN202",
        "ANN204",
        "ANN205",
        "ANN206",
    ],
}

# mypy flags layered on per level (additive over [tool.mypy]).
_MYPY_FLAGS_BY_LEVEL: dict[TypingStrictness, list[str]] = {
    "lenient": [],
    "standard": ["--disallow-untyped-defs", "--disallow-incomplete-defs"],
    "strict": ["--strict"],
}


@dataclass(frozen=True)
class TempestConfig:
    """Resolved ``[tool.tempest]`` settings.

    Attributes:
        typing_strictness (TypingStrictness): How strictly the CLI gates
            enforce typing. One of ``"lenient"``, ``"standard"``,
            ``"strict"``.
    """

    typing_strictness: TypingStrictness = DEFAULT_TYPING_STRICTNESS

    def ruff_ann_select(self) -> list[str]:
        """Return the ANN rule codes to add to ruff for this level.

        Returns:
            list[str]: Rule codes for ``--extend-select`` (empty for
            ``"lenient"``). ANN401 is never present.
        """
        return list(_RUFF_ANN_BY_LEVEL[self.typing_strictness])

    def mypy_flags(self) -> list[str]:
        """Return the extra mypy flags to add for this level.

        Returns:
            list[str]: Flags layered on top of the project's
            ``[tool.mypy]`` config (empty for ``"lenient"``).
        """
        return list(_MYPY_FLAGS_BY_LEVEL[self.typing_strictness])


def find_pyproject(start: Path | None = None) -> Path | None:
    """Locate the nearest ``pyproject.toml`` walking up from ``start``.

    Args:
        start (Path | None): Directory to begin the search. Defaults to
            the current working directory.

    Returns:
        Path | None: The path to the first ``pyproject.toml`` found in
        ``start`` or an ancestor, or ``None`` when none exists.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _coerce_strictness(value: object, *, source: str) -> TypingStrictness:
    """Validate a raw ``typing_strictness`` value.

    Args:
        value (object): The raw value read from the TOML table.
        source (str): Human-readable origin used in the error message.

    Returns:
        TypingStrictness: The validated level.

    Raises:
        ValueError: When ``value`` is not one of the allowed levels.
    """
    allowed = get_args(TypingStrictness)
    if value not in allowed:
        allowed_str = ", ".join(repr(level) for level in allowed)
        raise ValueError(
            f"{source}: invalid typing_strictness {value!r}; "
            f"expected one of {allowed_str}."
        )
    return value  # type: ignore[return-value]


def load_tempest_config(start: Path | None = None) -> TempestConfig:
    """Load ``[tool.tempest]`` from the nearest ``pyproject.toml``.

    Args:
        start (Path | None): Directory to begin the search. Defaults to
            the current working directory.

    Returns:
        TempestConfig: The resolved config. Falls back to defaults when
        no ``pyproject.toml`` or no ``[tool.tempest]`` table is found.

    Raises:
        ValueError: When ``typing_strictness`` is present but invalid.
    """
    pyproject = find_pyproject(start)
    if pyproject is None:
        return TempestConfig()
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    table = data.get("tool", {}).get("tempest", {})
    if "typing_strictness" not in table:
        return TempestConfig()
    strictness = _coerce_strictness(
        table["typing_strictness"],
        source=str(pyproject),
    )
    return TempestConfig(typing_strictness=strictness)


__all__: list[str] = [
    "DEFAULT_TYPING_STRICTNESS",
    "TempestConfig",
    "TypingStrictness",
    "find_pyproject",
    "load_tempest_config",
]
