"""Quality-gate helpers backing ``tempest lint``/``check``/etc."""

from __future__ import annotations

import shutil
import subprocess
import sys

import typer


def _resolve(executable: str) -> list[str] | None:
    """Return an argv prefix invoking ``executable`` or ``None`` when absent.

    Preference order:

    1. ``executable`` available on ``PATH`` directly (already activated venv,
       global install, etc.).
    2. ``uv run <executable>`` when ``uv`` is on the ``PATH`` (handles
       project-local virtualenvs without requiring activation).

    Args:
        executable (str): The command name (``ruff``/``mypy``/``pytest``).

    Returns:
        list[str] | None: argv prefix to extend with extra arguments, or
        ``None`` when no runner could be found.
    """
    direct = shutil.which(executable)
    if direct is not None:
        return [direct]
    uv = shutil.which("uv")
    if uv is not None:
        return [uv, "run", executable]
    return None


def _execute(executable: str, args: list[str]) -> int:
    """Run ``executable args`` and return its exit code.

    Args:
        executable (str): The command to run.
        args (list[str]): Extra arguments to forward.

    Returns:
        int: The child process exit code. Returns ``127`` when neither
        the executable nor ``uv`` is available.
    """
    argv = _resolve(executable)
    if argv is None:
        typer.echo(
            f"error: '{executable}' is not on PATH and 'uv' is unavailable. "
            f"Install it (or activate the project venv) and retry.",
            err=True,
        )
        return 127
    return subprocess.call([*argv, *args])


def run_ruff_check(target: str) -> int:
    """Invoke ``ruff check <target>``.

    Args:
        target (str): The path passed verbatim to ruff.

    Returns:
        int: The ruff exit code.
    """
    return _execute("ruff", ["check", target])


def run_ruff_fix(target: str, *, unsafe: bool = False) -> int:
    """Apply every automatic fix ruff can perform, then format the target.

    Runs in two passes so the second one sees the rewritten file:

    1. ``ruff check --fix [--unsafe-fixes] <target>`` — autofix imports
       (sort + dedupe), remove unused imports, normalize string quotes,
       drop trailing whitespace, fix the rest of the lint rules that
       have safe (or, with ``unsafe=True``, also unsafe) autofixers.
    2. ``ruff format <target>`` — normalize indentation, line length,
       blank lines and trailing newlines.

    Both passes always run. ``ruff check --fix`` exits non-zero whenever
    *any* residual violation remains that it cannot autofix (an
    over-length string/comment, an undefined name, etc.) — even though
    it already rewrote everything it could. Short-circuiting on that
    exit code would skip ``ruff format`` entirely, leaving the file
    un-wrapped and its extra blank lines intact. So the formatter runs
    unconditionally; the lint exit code is surfaced afterwards so CI
    still fails on the leftover issues.

    Args:
        target (str): The path passed verbatim to ruff.
        unsafe (bool): When True, pass ``--unsafe-fixes`` so ruff also
            applies the fixes it would otherwise leave alone.

    Returns:
        int: ``0`` when both passes succeed with nothing left to fix;
        otherwise the lint pass exit code (residual violations), or the
        format pass exit code when the lint pass was clean.
    """
    check_args = ["check", "--fix"]
    if unsafe:
        check_args.append("--unsafe-fixes")
    check_args.append(target)
    check_code = _execute("ruff", check_args)
    format_code = _execute("ruff", ["format", target])
    return check_code or format_code


def run_ruff_format(target: str, *, check: bool) -> int:
    """Invoke ``ruff format`` (write or check-only).

    Args:
        target (str): The path passed verbatim to ruff.
        check (bool): When True, run ``ruff format --check`` (read-only).

    Returns:
        int: The ruff exit code.
    """
    args = ["format"]
    if check:
        args.append("--check")
    args.append(target)
    return _execute("ruff", args)


def run_mypy(target: str) -> int:
    """Invoke ``mypy <target>``.

    Args:
        target (str): The path passed verbatim to mypy.

    Returns:
        int: The mypy exit code.
    """
    return _execute("mypy", [target])


def run_pytest(target: str | None) -> int:
    """Invoke ``pytest`` with an optional target.

    Args:
        target (str | None): Optional pytest path filter. ``None`` runs
            the default test suite.

    Returns:
        int: The pytest exit code.
    """
    args = [target] if target else []
    return _execute("pytest", args)


def run_full_check(target: str) -> int:
    """Run the entire quality gate sequentially.

    Order: ``ruff check`` → ``ruff format --check`` → ``mypy`` → ``pytest``.
    Stops at the first non-zero exit code so failures surface fast.

    Args:
        target (str): The path inspected by ruff/mypy. Pytest always runs
            against the project's configured ``testpaths``.

    Returns:
        int: The first non-zero exit code, or ``0`` when every gate passed.
    """
    steps: list[tuple[str, list[str]]] = [
        ("ruff", ["check", target]),
        ("ruff", ["format", "--check", target]),
        ("mypy", [target]),
        ("pytest", []),
    ]
    for executable, args in steps:
        typer.echo(f"$ {executable} {' '.join(args)}", err=True)
        code = _execute(executable, args)
        if code != 0:
            return code
    return 0


__all__: list[str] = [
    "run_full_check",
    "run_mypy",
    "run_pytest",
    "run_ruff_check",
    "run_ruff_fix",
    "run_ruff_format",
]


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    sys.exit(run_full_check("."))
