"""Console-script entry point that checks for the ``[cli]`` extra first.

The ``tempest`` command is the optional ``[cli]`` extra since v0.308.0:
``tempest-cli`` (the quality gate behind ``tempest check``) requires
``ruff`` at runtime, so keeping it in the base package made every service
that imports the SDK ship a formatter to production and inherit its
version floor. ``typer`` and ``click`` moved with it, because nothing
outside :mod:`tempest_fastapi_sdk.cli` imports them.

This module is what ``[project.scripts]`` points at, and it imports only
the standard library. Without the extra the script prints the install
line and exits with code 2 instead of a ``ModuleNotFoundError`` traceback;
with it, control passes to :func:`tempest_fastapi_sdk.cli.main.main`.
"""

from __future__ import annotations

import importlib
import sys

CLI_EXTRA_MODULES: tuple[str, ...] = ("tempest_cli", "typer", "click")
"""Top-level modules the ``[cli]`` extra installs.

One per requirement of ``[project.optional-dependencies] cli``; a missing
one means the extra is not installed.
"""

CLI_EXTRA_HINT: str = (
    "The tempest CLI needs the optional [cli] extra. Install it with:\n"
    '  uv add --dev "tempest-fastapi-sdk[cli]"   (in a project)\n'
    '  uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)'
)
"""Install instruction shown when the ``[cli]`` extra is missing."""

CLI_EXTRA_EXIT_CODE: int = 2
"""Exit status of ``tempest`` without the extra, the SDK's usage-error code."""


def missing_cli_modules() -> list[str]:
    """List the ``[cli]`` extra's modules that cannot be imported.

    Each module is imported rather than looked up with
    ``importlib.util.find_spec``: the CLI imports all three right after
    this check anyway, and an import is the question that matters — a
    module can have a spec and still fail to load.

    Returns:
        list[str]: Names from :data:`CLI_EXTRA_MODULES` that are not
        installed, in declaration order. Empty when the extra is installed.

    Raises:
        ModuleNotFoundError: When an installed extra module fails because
            of some *other* missing module — a broken install, which the
            install hint would not fix.
    """
    missing: list[str] = []
    for name in CLI_EXTRA_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
            missing.append(name)
    return missing


def raise_for_missing_cli_extra(error: ModuleNotFoundError) -> None:
    """Turn a failed import of a ``[cli]`` module into the install hint.

    Called from the ``except`` clause around a CLI module's imports. A
    missing module that belongs to some other package is left alone, so
    the caller re-raises the original error unchanged.

    Args:
        error (ModuleNotFoundError): The error the import raised.

    Raises:
        ImportError: With :data:`CLI_EXTRA_HINT` when the missing module
            is one of :data:`CLI_EXTRA_MODULES` or a submodule of one.
    """
    missing = (error.name or "").partition(".")[0]
    if missing in CLI_EXTRA_MODULES:
        raise ImportError(CLI_EXTRA_HINT) from error


def main() -> None:
    """Run the ``tempest`` CLI, or explain how to install it.

    Raises:
        SystemExit: With :data:`CLI_EXTRA_EXIT_CODE` when the ``[cli]``
            extra is missing. With the extra installed, the exit status is
            whatever :func:`tempest_fastapi_sdk.cli.main.main` produces.
    """
    missing = missing_cli_modules()
    if missing:
        print(f"error: missing {', '.join(missing)}. {CLI_EXTRA_HINT}", file=sys.stderr)
        raise SystemExit(CLI_EXTRA_EXIT_CODE)
    from tempest_fastapi_sdk.cli.main import main as run_cli

    run_cli()
