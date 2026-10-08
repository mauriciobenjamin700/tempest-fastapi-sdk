"""Guard that the ``tempest`` CLI stays out of the base install.

Up to v0.307.0 the base dependencies carried ``tempest-cli``, which requires
``ruff>=0.8.0`` at runtime: every service importing the SDK installed a
formatter in production, and a project pinning ``ruff<0.8`` in its dev group
could not lock at all (measured with ``uv lock`` on a copy of a real
service). v0.308.0 moved the CLI into the ``[cli]`` extra, together with
``typer`` and ``click``, which nothing outside ``tempest_fastapi_sdk/cli/``
imports.

Two ways it comes back, one check each:

- **The requirement returns to the base list** — a new CLI dependency added
  to ``dependencies`` instead of ``cli``. Read from ``pyproject.toml``.
- **Base code imports a CLI module at module level** — a top-level
  ``import typer`` in a module the base package loads makes the extra
  mandatory again, with no error until a consumer without it imports that
  module. Read from the AST: outside ``tempest_fastapi_sdk/cli/``, and in
  the ``cli`` modules the rest of the package imports (``src_layers`` today),
  an import of ``tempest_cli`` / ``typer`` / ``click`` must sit inside a
  function.

Blind spot: an allowed ``cli`` module that imports *another* ``cli`` module
which imports ``typer`` is not followed. The subprocess tests in
``tests/cli/test_entrypoint.py`` cover the paths that exist.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from tempest_fastapi_sdk.cli.entrypoint import CLI_EXTRA_MODULES

ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root."""

PACKAGE_ROOT: Path = ROOT / "tempest_fastapi_sdk"
"""The package whose modules are scanned."""

CLI_ROOT: Path = PACKAGE_ROOT / "cli"
"""The CLI package, where importing the extra at module level is allowed."""

CLI_DISTRIBUTIONS: frozenset[str] = frozenset({"tempest-cli", "typer", "click"})
"""Distributions that belong to the ``[cli]`` extra and never to the base."""

ALWAYS_IMPORTABLE_CLI_MODULES: frozenset[str] = frozenset({"__init__", "entrypoint"})
"""``cli`` modules loaded before the extra is known to be there.

``__init__`` runs on any import of a submodule; ``entrypoint`` is what the
console script loads to decide whether the extra is installed.
"""


def _module_level_cli_imports(source: str) -> list[str]:
    """Find module-level imports of a ``[cli]`` module.

    Imports nested in a function or class body are lazy and not reported;
    imports at module level, including inside ``try`` / ``if`` blocks, are.

    Args:
        source (str): Python source of one module.

    Returns:
        list[str]: ``line: module`` for each offending import.
    """
    offenders: list[str] = []
    pending: list[ast.AST] = list(ast.parse(source).body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            if name.partition(".")[0] in CLI_EXTRA_MODULES:
                offenders.append(f"{node.lineno}: {name}")
        pending.extend(ast.iter_child_nodes(node))
    return offenders


def _cli_modules_imported_by_the_base() -> set[str]:
    """Name the ``cli`` modules that code outside ``cli/`` imports.

    Returns:
        set[str]: Module stems under ``tempest_fastapi_sdk/cli/``.
    """
    stems: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        if CLI_ROOT in path.parents:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                prefix = "tempest_fastapi_sdk.cli."
                if node.module.startswith(prefix):
                    stems.add(node.module[len(prefix) :].partition(".")[0])
    return stems


def _guarded_files() -> list[Path]:
    """Collect every module that must import without the extra.

    Returns:
        list[Path]: Sorted paths, for a stable test id.
    """
    allowed_cli = ALWAYS_IMPORTABLE_CLI_MODULES | _cli_modules_imported_by_the_base()
    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if CLI_ROOT not in path.parents or path.stem in allowed_cli
    )


def _distribution(requirement: str) -> str:
    """Return the canonical distribution name of a PEP 508 requirement.

    Args:
        requirement (str): The requirement string.

    Returns:
        str: The lower-cased name.
    """
    return Requirement(requirement).name.lower()


def _base_cli_requirements(pyproject: dict[str, object]) -> list[str]:
    """Find ``[cli]`` distributions in the base dependency list.

    Args:
        pyproject (dict[str, object]): The parsed ``pyproject.toml``.

    Returns:
        list[str]: Offending requirements, empty when the base is clean.
    """
    project = pyproject["project"]
    assert isinstance(project, dict)
    return [
        line
        for line in project["dependencies"]
        if _distribution(line) in CLI_DISTRIBUTIONS
    ]


def _pyproject() -> dict[str, object]:
    """Parse the repository's ``pyproject.toml``.

    Returns:
        dict[str, object]: The parsed table.
    """
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class TestPyproject:
    """Where the CLI's requirements are declared."""

    def test_base_has_no_cli_requirement(self) -> None:
        """``dependencies`` lists none of ``tempest-cli``, ``typer``, ``click``."""
        assert _base_cli_requirements(_pyproject()) == []

    def test_cli_extra_declares_every_cli_distribution(self) -> None:
        """``[cli]`` is exactly the three distributions."""
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        extra = project["optional-dependencies"]["cli"]
        assert {_distribution(line) for line in extra} == CLI_DISTRIBUTIONS

    def test_all_carries_the_cli_extra(self) -> None:
        """``[all]`` keeps installing the CLI, with the same floors."""
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        extras = project["optional-dependencies"]
        assert set(extras["cli"]) <= set(extras["all"])

    def test_script_points_at_the_entrypoint(self) -> None:
        """``tempest`` loads the module that checks for the extra first."""
        project = _pyproject()["project"]
        assert isinstance(project, dict)
        assert (
            project["scripts"]["tempest"] == "tempest_fastapi_sdk.cli.entrypoint:main"
        )

    def test_check_fires_on_the_shipped_dependency_list(self) -> None:
        """The v0.307.0 base list, with ``tempest-cli`` in it, is refused."""
        shipped: dict[str, object] = {
            "project": {
                "dependencies": [
                    "alembic>=1.19.1",
                    "tempest-cli>=0.4.0",
                    "click>=8.5.0",
                    "typer>=0.27.2",
                ]
            }
        }
        assert _base_cli_requirements(shipped) == [
            "tempest-cli>=0.4.0",
            "click>=8.5.0",
            "typer>=0.27.2",
        ]


class TestModuleLevelImports:
    """Modules the base package loads never import the extra eagerly."""

    def test_src_layers_is_in_scope(self) -> None:
        """``openapi.generate`` imports ``cli.src_layers``, so it is guarded."""
        assert "src_layers" in _cli_modules_imported_by_the_base()

    @pytest.mark.parametrize(
        "path",
        _guarded_files(),
        ids=lambda path: str(path.relative_to(ROOT)),
    )
    def test_no_module_level_cli_import(self, path: Path) -> None:
        """The module imports ``tempest_cli`` / ``typer`` / ``click`` lazily.

        Args:
            path (Path): Module that must import without the extra.
        """
        assert _module_level_cli_imports(path.read_text(encoding="utf-8")) == []

    def test_check_fires_on_the_shipped_cli_init(self) -> None:
        """The eager ``cli/__init__.py`` chain of v0.307.0 is reported.

        That file imported ``cli.config``, which imported ``tempest_cli`` at
        module level; the shape below is the import that broke the base.
        """
        shipped = (
            "from tempest_cli.config import TempestConfig as TempestConfig\n"
            "try:\n"
            "    import typer\n"
            "except ModuleNotFoundError:\n"
            "    raise\n"
            "def lazy() -> None:\n"
            "    import click\n"
        )
        assert sorted(_module_level_cli_imports(shipped)) == [
            "1: tempest_cli.config",
            "3: typer",
        ]
