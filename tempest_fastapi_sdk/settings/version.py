"""Read a project's version from its ``pyproject.toml``.

A service scaffolded by ``tempest new`` declares its version once, in
``[project].version`` of the ``pyproject.toml`` it owns. That value is
what the OpenAPI document and ``/health/readiness`` should publish — but
``importlib.metadata.version`` cannot reach it: the generated project is
not installed as a distribution (``[tool.uv] package = false``), so it
has no metadata to read. :func:`read_project_version` parses the file
directly instead, which also makes the value available at import time in
a settings module.

The scaffolded ``Settings.VERSION`` wires this helper as its
``default_factory`` so there is a single source of truth: bumping
``[project].version`` changes the published contract, and the ``VERSION``
environment variable still overrides it through pydantic-settings.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

__all__: list[str] = ["read_project_version"]

VERSION_KEY: str = "version"
"""The key read from the ``[project]`` table."""


def _candidate_paths(start: Path) -> list[Path]:
    """List the ``pyproject.toml`` candidates the upward search visits.

    Args:
        start (Path): The directory the search begins at.

    Returns:
        list[Path]: ``pyproject.toml`` under ``start`` and each of its
        ancestors, nearest first.
    """
    current = start.resolve()
    return [directory / "pyproject.toml" for directory in (current, *current.parents)]


def read_project_version(pyproject_path: Path | None = None) -> str:
    """Return the project version declared in a ``pyproject.toml``.

    With no argument the search starts at the current working directory
    and walks up to the filesystem root, stopping at the first
    ``pyproject.toml`` it finds — the same resolution ``find_pyproject``
    uses. Pass an explicit ``pyproject_path`` when the answer must not
    depend on the working directory: the scaffolded settings module
    anchors it on its own location, so the version is the project's even
    when the app is started from somewhere else.

    Args:
        pyproject_path (Path | None): The file to read. Defaults to the
            nearest ``pyproject.toml`` above the current working
            directory.

    Returns:
        str: The value of ``[project].version``.

    Raises:
        FileNotFoundError: When no ``pyproject.toml`` is found. The
            message lists every path the search visited, so a miss names
            what it looked for and where.
        ValueError: When the file has no ``[project].version`` — an
            absent ``[project]`` table, an absent key, or a project that
            declares the version through ``dynamic``. The message names
            the file and the missing key.
    """
    if pyproject_path is None:
        candidates = _candidate_paths(Path.cwd())
        pyproject_path = next(
            (candidate for candidate in candidates if candidate.is_file()),
            None,
        )
        if pyproject_path is None:
            searched = "\n".join(f"  - {candidate}" for candidate in candidates)
            raise FileNotFoundError(
                "No pyproject.toml found walking up from "
                f"{Path.cwd().resolve()} to the filesystem root. Searched:\n"
                f"{searched}\nPass pyproject_path explicitly if the file "
                "lives elsewhere."
            )
    elif not pyproject_path.is_file():
        raise FileNotFoundError(
            f"{pyproject_path}: no such file. Pass a path to an existing "
            "pyproject.toml with [project].version."
        )

    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project")
    if not isinstance(project, dict) or VERSION_KEY not in project:
        raise ValueError(
            f"{pyproject_path}: missing [project].version. Declare "
            'version = "x.y.z" under [project], or point the helper at a '
            "file that does. A project that resolves its version through "
            'dynamic = ["version"] has none to read.'
        )
    version = project[VERSION_KEY]
    if not isinstance(version, str):
        raise ValueError(
            f"{pyproject_path}: [project].version is "
            f"{type(version).__name__}, expected a string."
        )
    return version
