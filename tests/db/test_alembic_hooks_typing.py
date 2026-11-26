"""The revision hooks type-check where Alembic takes them (#840).

``context.configure(process_revision_directives=...)`` is typed by
Alembic as ``Callable[[MigrationContext, str | Iterable[str | None] |
Iterable[str], list[MigrationScript]], None]``. Callback parameters are
contravariant, so a hook that narrows ``revision`` (the enum hooks shipped
``str | tuple[str, ...]``) is an ``arg-type`` error under
``mypy --strict`` in the consumer's ``env.py``; the SDK alias
``ProcessRevisionDirectives`` erased ``revision`` and ``directives`` to
``Any``, so the same hooks passed only when wrapped in ``compose_hooks``.

The consumer snippet below wires every hook straight into ``configure``
and through ``compose_hooks``, and converts between the SDK alias and
Alembic's ``ProcessRevisionDirectiveFn`` in both directions. Its last
two hooks keep the shapes that shipped (a narrowed ``revision``, and a
``directives`` only ``list[Any]`` accepted), so the run proves it fires.

The in-process run checks against the Alembic in the lock; the
``network`` matrix reruns mypy in a subprocess under the floor read from
``pyproject.toml`` and a fixed newer release, overlaid with
``uv run --no-sync --with`` and ``python -m mypy`` (the ``mypy`` script's
shebang would import the lock's Alembic).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parents[2]

NEWER_ALEMBIC: str = "1.20.1"
"""The newest Alembic measured when the hook types were fixed."""

CONSUMER_SNIPPET: str = '''\
"""A consumer env.py wiring the SDK hooks into Alembic."""

from collections.abc import Iterable

from alembic import context
from alembic.operations.ops import MigrationScript
from alembic.runtime.environment import ProcessRevisionDirectiveFn
from alembic.runtime.migration import MigrationContext

from tempest_fastapi_sdk.db import (
    DEFAULT_REVISION_HOOKS,
    compose_hooks,
    sqlite_enum_checks,
    sync_enum_types,
)
from tempest_fastapi_sdk.db.alembic_hooks import (
    ProcessRevisionDirectives,
    backfill_non_nullable_defaults,
    reorder_base_columns_first,
)
from tempest_fastapi_sdk.db.enum_migrations import (
    create_enum_types_on_add_column,
    drop_enum_types_on_downgrade,
)


def alembic_typed(
    context: MigrationContext,
    revision: str | Iterable[str | None] | Iterable[str],
    directives: list[MigrationScript],
) -> None:
    """A consumer hook typed with Alembic's own revision union."""


def run() -> None:
    """Wire each hook the ways the docs show."""
    context.configure(process_revision_directives=sync_enum_types)
    context.configure(process_revision_directives=sqlite_enum_checks)
    context.configure(process_revision_directives=create_enum_types_on_add_column)
    context.configure(process_revision_directives=drop_enum_types_on_downgrade)
    context.configure(process_revision_directives=reorder_base_columns_first)
    context.configure(process_revision_directives=backfill_non_nullable_defaults)
    context.configure(
        process_revision_directives=compose_hooks(*DEFAULT_REVISION_HOOKS)
    )
    context.configure(
        process_revision_directives=compose_hooks(
            reorder_base_columns_first,
            backfill_non_nullable_defaults,
            sync_enum_types,
            alembic_typed,
        )
    )


theirs: ProcessRevisionDirectiveFn = compose_hooks(sync_enum_types)
ours: ProcessRevisionDirectives = theirs


def narrowed(
    context: MigrationContext,
    revision: str | tuple[str, ...],
    directives: list[MigrationScript],
) -> None:
    """The revision type the enum hooks shipped with before #840."""


context.configure(process_revision_directives=narrowed)  # expected: arg-type


def wrong_directives(
    context: MigrationContext,
    revision: str | Iterable[str | None],
    directives: list[int],
) -> None:
    """A hook whose directives are not Alembic's, which ``Any`` let through."""


compose_hooks(wrong_directives)  # expected: arg-type
'''
"""Every hook wired into ``configure``; only the two marked lines may fail."""

EXPECTED_ERRORS: set[tuple[int, str]] = {
    (number, "arg-type")
    for number, line in enumerate(CONSUMER_SNIPPET.splitlines(), start=1)
    if line.endswith("# expected: arg-type")
}
"""The lines that must be ``arg-type`` errors.

The narrowed ``revision`` is the shape the enum hooks shipped; the wrong
``directives`` is accepted only while ``ProcessRevisionDirectives`` erases
the parameter to ``list[Any]``.
"""

ERROR_LINE: re.Pattern[str] = re.compile(r":(\d+): error: .*\[([a-z-]+)\]$")


def _write_snippet(directory: Path) -> Path:
    """Write :data:`CONSUMER_SNIPPET` as a module under ``directory``.

    Args:
        directory (Path): Where to write the module.

    Returns:
        Path: The written module.
    """
    module = directory / "consumer_env.py"
    module.write_text(CONSUMER_SNIPPET, encoding="utf-8")
    return module


def _mypy_args(module: Path, cache_name: str) -> list[str]:
    """Build the strict mypy command line for ``module``.

    Args:
        module (Path): The snippet module to check.
        cache_name (str): A suffix keeping this run's cache apart.

    Returns:
        list[str]: The arguments after ``mypy``.
    """
    digest: str = hashlib.sha256(str(ROOT).encode("utf-8")).hexdigest()[:12]
    cache: Path = Path(tempfile.gettempdir()) / f"tempest-mypy-{digest}-{cache_name}"
    return [
        str(module),
        "--strict",
        "--no-error-summary",
        "--hide-error-context",
        "--no-color-output",
        "--show-error-codes",
        "--cache-dir",
        str(cache),
    ]


def _errors(stdout: str) -> set[tuple[int, str]]:
    """Collect ``(line, code)`` for every mypy error in ``stdout``.

    Args:
        stdout (str): mypy's standard output.

    Returns:
        set[tuple[int, str]]: One entry per reported error.
    """
    found: set[tuple[int, str]] = set()
    for line in stdout.splitlines():
        match = ERROR_LINE.search(line)
        if match is not None:
            found.add((int(match.group(1)), match.group(2)))
    return found


def _alembic_floor() -> str:
    """Read the Alembic floor declared in ``pyproject.toml``.

    Returns:
        str: The version after ``alembic>=``.
    """
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    for requirement in project["dependencies"]:
        match = re.fullmatch(r"alembic>=([0-9.]+)", requirement)
        if match is not None:
            return match.group(1)
    raise AssertionError("pyproject.toml declares no alembic>= floor")


def test_hooks_type_check_against_the_locked_alembic(tmp_path: Path) -> None:
    """Every hook goes into ``configure``; the shipped shapes still fail.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    module = _write_snippet(tmp_path)
    stdout, _stderr, _status = mypy_api.run(
        [
            *_mypy_args(module, "alembic-hooks"),
            "--python-executable",
            sys.executable,
        ]
    )
    assert _errors(stdout) == EXPECTED_ERRORS, stdout


@pytest.mark.network
@pytest.mark.parametrize("version", [_alembic_floor(), NEWER_ALEMBIC])
def test_hooks_type_check_across_alembic_versions(
    tmp_path: Path,
    version: str,
) -> None:
    """The same snippet under the declared floor and a newer Alembic.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        version (str): The Alembic release overlaid on the project.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not installed")
    overlay = ["uv", "run", "--no-sync", "--with", f"alembic=={version}"]
    probe = subprocess.run(
        [*overlay, "python", "-c", "import alembic; print(alembic.__version__)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert probe.stdout.strip() == version, probe.stderr
    module = _write_snippet(tmp_path)
    result = subprocess.run(
        [
            *overlay,
            "python",
            "-m",
            "mypy",
            *_mypy_args(module, f"alembic-hooks-{version}"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert _errors(result.stdout) == EXPECTED_ERRORS, result.stdout
