"""The ``openapi-errors`` and ``lint-schemas`` steps of ``tempest check``.

``tempest check`` lives in ``tempest-cli``, which knows nothing about
FastAPI; the SDK hands it extra steps (:class:`tempest_cli.CheckStep`) that
run between ``mypy`` and ``pytest``. ``lint-layers`` was the first. This
module holds the next two, so a project gets the same answer from
``tempest check`` that ``tempest openapi-errors --check`` and
``tempest lint-schemas`` give on their own.

Each step finds the project from the ``check`` target the same way the
command it mirrors does:

* the project root is the target, or its parent when the target is the
  source root itself (``tempest check src``);
* ``openapi-errors`` scans ``src/`` or ``app/``
  (:func:`~tempest_fastapi_sdk.cli.openapi_errors.default_source_paths`),
  statically, without importing anything;
* ``lint-schemas`` imports ``src.schemas`` or ``app.schemas``
  (:func:`~tempest_fastapi_sdk.cli.schema_lint.default_schemas_module`) in
  a **child process** run from the project root, so the project's modules
  never enter the process that goes on to run ``pytest``, and ``ENV``
  defaults to ``test`` there (the value the generated ``conftest.py``
  pins) when the shell did not set one.

A project without the thing a step checks — no ``src/``/``app/``, or no
``schemas`` package — is not a failure: the step prints why it skipped
and passes. Each step can be switched off under ``[tool.tempest]`` with
:data:`OPENAPI_ERRORS_CONFIG_KEY` / :data:`LINT_SCHEMAS_CONFIG_KEY` set to
``false``, for a project migrating one gate at a time.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TextIO

from tempest_fastapi_sdk.cli.config import load_tempest_table
from tempest_fastapi_sdk.cli.openapi_errors import (
    RouteFinding,
    analyze_paths,
    default_source_paths,
)
from tempest_fastapi_sdk.cli.schema_lint import default_schemas_module

SOURCE_ROOT_NAMES: tuple[str, ...] = ("src", "app")
"""Directory names that mark a ``check`` target as the source root itself."""

OPENAPI_ERRORS_CONFIG_KEY: str = "check_openapi_errors"
"""``[tool.tempest]`` key; ``false`` drops the ``openapi-errors`` step."""

LINT_SCHEMAS_CONFIG_KEY: str = "check_lint_schemas"
"""``[tool.tempest]`` key; ``false`` drops the ``lint-schemas`` step."""

CHILD_ENVIRONMENT_DEFAULTS: dict[str, str] = {"ENV": "test"}
"""Variables the ``lint-schemas`` child gets when the shell did not set them.

Importing ``src.schemas`` imports ``src/__init__.py``, which builds the
settings, and a settings class composing ``EnvironmentSettings`` refuses
to build without ``ENV``. ``test`` is what the generated ``conftest.py``
pins for the suite that runs right after this step.
"""

LINT_SCHEMAS_ENV_CONFIG_KEY: str = "lint_schemas_env"
"""``[tool.tempest]`` table of variables the ``lint-schemas`` child gets.

Importing the schemas builds the settings, and under ``ENV=test``
``EnvironmentSettings`` discards the ``.env`` — so a ``Settings`` with a
required field and no default (a secret such as ``ADMIN_SECRET_KEY``, which
must not carry a placeholder) refused to import, and ``tempest check`` exited
2 while ``tempest lint-schemas`` alone passed (#736). The suite solves the same
thing in ``conftest.py``, which the child never runs. Values here apply only
to the child: they override :data:`CHILD_ENVIRONMENT_DEFAULTS` and lose to a
variable the shell exports, so a real value is never replaced by a lint
placeholder::

    [tool.tempest]
    lint_schemas_env = { ADMIN_SECRET_KEY = "lint-only" }
"""

_LINT_SCHEMAS_CHILD: str = (
    "import sys\n"
    "from tempest_fastapi_sdk.cli.schema_lint import run_lint_schemas\n"
    "raise SystemExit(run_lint_schemas(sys.argv[1], root=sys.argv[2]))\n"
)
"""Program the ``lint-schemas`` child runs: module and root come as argv."""


def check_project_root(target: str) -> Path:
    """Return the project root a ``check`` target points at.

    Args:
        target (str): The path given to ``tempest check`` (``.`` by default).

    Returns:
        Path: The parent of a source-root target (``src`` / ``app``), the
        directory of a file target, ``target`` itself otherwise.
    """
    path = Path(target)
    if path.is_file():
        path = path.parent
    if path.name in SOURCE_ROOT_NAMES:
        return path.parent
    return path


def step_enabled(root: Path, key: str) -> bool:
    """Read an on/off switch for a step from ``[tool.tempest]``.

    Args:
        root (Path): The project root; the nearest ``pyproject.toml`` from
            it is read.
        key (str): The ``[tool.tempest]`` key.

    Returns:
        bool: The configured value, ``True`` when absent.

    Raises:
        ValueError: When the key holds anything but a boolean.
        tomllib.TOMLDecodeError: When the ``pyproject.toml`` is not TOML.
    """
    pyproject, table = load_tempest_table(root)
    value = table.get(key, True)
    if not isinstance(value, bool):
        raise ValueError(
            f"{pyproject}: [tool.tempest] {key} must be true or false, got {value!r}."
        )
    return value


def _enabled_or_exit(root: Path, key: str, output: TextIO) -> int | None:
    """Resolve a step's switch, translating a bad value into an exit code.

    Args:
        root (Path): The project root.
        key (str): The ``[tool.tempest]`` key.
        output (TextIO): Where a skip notice or a config error goes.

    Returns:
        int | None: ``None`` when the step should run; ``0`` when it is
        switched off; ``2`` when the configured value is invalid.
    """
    try:
        enabled = step_enabled(root, key)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        output.write(f"{exc}\n")
        return 2
    if enabled:
        return None
    output.write(f"skipped: [tool.tempest] {key} = false.\n")
    return 0


def render_findings(findings: list[RouteFinding], output: TextIO) -> None:
    """Write the drift report of ``openapi-errors`` as plain text.

    Args:
        findings (list[RouteFinding]): The routes with drift.
        output (TextIO): Where the report goes.
    """
    undocumented_total = 0
    for finding in findings:
        output.write(
            f"{finding.location}  {finding.route.method} {finding.route.path}\n"
        )
        if finding.undocumented:
            undocumented_total += len(finding.undocumented)
            output.write(f"  undocumented: {', '.join(finding.undocumented)}\n")
        if finding.unreachable:
            output.write(f"  unreachable:  {', '.join(finding.unreachable)}\n")
    output.write(
        f"{len(findings)} route(s) with drift, "
        f"{undocumented_total} undocumented exception(s).\n"
    )


def run_openapi_errors_step(target: str, *, stream: TextIO | None = None) -> int:
    """Run ``openapi-errors --check`` as a step of ``tempest check``.

    Fails on both directions, as ``--check`` does: an exception reachable
    from a route but not declared, and one declared but never reachable.

    Args:
        target (str): The ``check`` target.
        stream (TextIO | None): Where the report goes. ``None`` means
            standard output.

    Returns:
        int: ``0`` when every route matches its flow, when the project has
        no ``src/``/``app/``, or when the step is switched off; ``1`` on
        drift; ``2`` for an invalid ``[tool.tempest]`` value.
    """
    output = stream or sys.stdout
    root = check_project_root(target)
    gated = _enabled_or_exit(root, OPENAPI_ERRORS_CONFIG_KEY, output)
    if gated is not None:
        return gated
    sources = default_source_paths(root)
    if not sources:
        output.write(f"skipped: no src/ or app/ under {root}, no routes to check.\n")
        return 0
    findings = analyze_paths(sources)
    if not findings:
        output.write(
            "Every route's declared errors match its flow "
            f"({', '.join(str(source) for source in sources)}).\n"
        )
        output.flush()
        return 0
    render_findings(findings, output)
    output.flush()
    return 1


def lint_schemas_environment(root: Path) -> dict[str, str]:
    """Read the variables ``[tool.tempest] lint_schemas_env`` sets.

    Args:
        root (Path): The project root; the nearest ``pyproject.toml`` from
            it is read.

    Returns:
        dict[str, str]: Variable name to value; empty when the key is absent.

    Raises:
        ValueError: When the key is not a table of strings.
        tomllib.TOMLDecodeError: When the ``pyproject.toml`` is not TOML.
    """
    pyproject, table = load_tempest_table(root)
    value = table.get(LINT_SCHEMAS_ENV_CONFIG_KEY, {})
    if not isinstance(value, dict) or not all(
        isinstance(item, str) for item in value.values()
    ):
        raise ValueError(
            f"{pyproject}: [tool.tempest] {LINT_SCHEMAS_ENV_CONFIG_KEY} must be a "
            f"table of strings, got {value!r}."
        )
    return {str(name): item for name, item in value.items()}


def _import_hint(module: str, environment: dict[str, str]) -> str:
    """Explain the environment a failed ``lint-schemas`` import ran with.

    Args:
        module (str): The schemas module that did not import.
        environment (dict[str, str]): The child's environment.

    Returns:
        str: The hint printed after the traceback.
    """
    env = environment.get("ENV", "<unset>")
    discarded = (
        " (ENV=test discards the .env, so a required setting without a "
        "default is missing)"
        if env == "test"
        else ""
    )
    return (
        f"\nhint: {module} was imported in a child process with ENV={env}"
        f"{discarded}. Give the child the settings it needs under "
        f'[tool.tempest] {LINT_SCHEMAS_ENV_CONFIG_KEY} = {{ NAME = "value" }}, '
        f"or drop the step with {LINT_SCHEMAS_CONFIG_KEY} = false.\n"
    )


def run_lint_schemas_step(target: str, *, stream: TextIO | None = None) -> int:
    """Run ``lint-schemas`` as a step of ``tempest check``.

    The import happens in a child process of the current interpreter, run
    from the project root. Its environment is, lowest precedence first,
    :data:`CHILD_ENVIRONMENT_DEFAULTS`, the
    :data:`LINT_SCHEMAS_ENV_CONFIG_KEY` table, then the shell. Its output is
    relayed to ``stream``; a failed import is followed by a hint naming the
    ``ENV`` it ran with and the two ways out.

    Args:
        target (str): The ``check`` target.
        stream (TextIO | None): Where the report goes. ``None`` means
            standard output.

    Returns:
        int: ``0`` when every field is documented, when the project has no
        schemas package, or when the step is switched off; ``1`` for an
        undocumented field; ``2`` when the package does not import or a
        ``[tool.tempest]`` value is invalid.
    """
    output = stream or sys.stdout
    root = check_project_root(target).resolve()
    gated = _enabled_or_exit(root, LINT_SCHEMAS_CONFIG_KEY, output)
    if gated is not None:
        return gated
    module = default_schemas_module(root)
    package = root.joinpath(*module.split("."))
    if not (package.is_dir() or package.with_suffix(".py").is_file()):
        output.write(f"skipped: no {module} package under {root}.\n")
        return 0
    try:
        configured = lint_schemas_environment(root)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        output.write(f"{exc}\n")
        return 2
    environment = {**CHILD_ENVIRONMENT_DEFAULTS, **configured, **os.environ}
    completed = subprocess.run(
        [sys.executable, "-c", _LINT_SCHEMAS_CHILD, module, str(root)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    output.write(completed.stdout)
    output.write(completed.stderr)
    if completed.returncode == 2 and f"cannot import {module}" in completed.stdout:
        output.write(_import_hint(module, environment))
    output.flush()
    return completed.returncode


__all__: list[str] = [
    "CHILD_ENVIRONMENT_DEFAULTS",
    "LINT_SCHEMAS_CONFIG_KEY",
    "LINT_SCHEMAS_ENV_CONFIG_KEY",
    "OPENAPI_ERRORS_CONFIG_KEY",
    "SOURCE_ROOT_NAMES",
    "check_project_root",
    "lint_schemas_environment",
    "render_findings",
    "run_lint_schemas_step",
    "run_openapi_errors_step",
    "step_enabled",
]
