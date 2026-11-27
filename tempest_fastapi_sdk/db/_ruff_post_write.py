"""Alembic post-write hook that formats a new revision with ruff, when ruff exists.

:meth:`tempest_fastapi_sdk.db.AlembicHelper.init` writes an ``alembic.ini``
whose ``[post_write_hooks]`` runs this module through Alembic's ``module``
hook type (``python -m tempest_fastapi_sdk.db._ruff_post_write <path>``).
The dotted path is therefore persisted in every generated ``alembic.ini``
and must not move.

Alembic's ``exec`` hook type, used before, spawns ``ruff`` straight from
``PATH``. On a ``[cli-ops]`` install (the server variant, without ruff)
that either raised ``FileNotFoundError`` out of ``tempest db revision``
after the revision file was already written, or, behind a pyenv shim,
printed ``pyenv: ruff: command not found`` for each hook. This module
looks a working ruff up first and stays silent when there is none, so
the revision is created without a false error; when ruff is available it
runs
``ruff format`` and then ``ruff check --fix`` on the revision, in that
order, exactly like the previous pair of hooks.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Sequence

RUFF_STEPS: tuple[tuple[str, ...], ...] = (
    ("format", "--quiet"),
    ("check", "--fix", "--quiet"),
)
"""Ruff sub-commands run on the revision, formatter first.

The formatter wraps long lines and strips trailing whitespace before the
linter runs, so ``check --fix`` does not report what ``format`` would fix.
"""


def find_ruff() -> list[str] | None:
    """Return the argv prefix that really runs ruff, or ``None`` without one.

    Candidates, in order: ``ruff`` on ``PATH`` (what the previous ``exec``
    hooks ran, so a project that formatted its revisions keeps the same
    ruff), then ``<current interpreter> -m ruff`` when the wheel is
    importable (``tempest`` invoked by absolute path from a venv whose
    ``bin/`` is not on ``PATH``). Each one is accepted only after
    ``--version`` exits ``0``: a pyenv shim resolves through
    :func:`shutil.which` and then fails with ``command not found``.

    ``tempest_fastapi_sdk.cli.openapi_fix.ruff_runner`` answers the same
    question but is not reused: it lives under ``cli/`` (the ``db`` package
    does not import the CLI) and its last candidate, ``uv run ruff``, may
    sync the project's environment as a side effect, which a post-write
    hook running on a server must not do.

    Returns:
        list[str] | None: The argv prefix for ruff, or ``None`` when no
        working ruff is available.
    """
    candidates: list[list[str]] = []
    on_path = shutil.which("ruff")
    if on_path is not None:
        candidates.append([on_path])
    if importlib.util.find_spec("ruff") is not None:
        candidates.append([sys.executable, "-m", "ruff"])
    for candidate in candidates:
        probe = subprocess.run(
            [*candidate, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Format and lint-fix one revision file with ruff, if ruff exists.

    Ruff's own exit status is ignored, as Alembic's ``exec`` hook ignored
    it: a lint finding that ``--fix`` cannot repair is printed by ruff and
    does not undo the revision.

    Args:
        argv (Sequence[str] | None): Arguments after the module name;
            defaults to ``sys.argv[1:]``. Exactly one is expected, the
            revision file Alembic just wrote.

    Returns:
        int: ``0`` when the file was processed or ruff is absent, ``2``
        on a wrong number of arguments.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(
            "usage: python -m tempest_fastapi_sdk.db._ruff_post_write <revision.py>",
            file=sys.stderr,
        )
        return 2
    ruff = find_ruff()
    if ruff is None:
        return 0
    for step in RUFF_STEPS:
        subprocess.run([*ruff, *step, args[0]], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
