"""Console-script entry point that checks for the CLI's extra first.

The ``tempest`` command is optional since v0.308.0: ``tempest-cli`` (the
quality gate behind ``tempest check``) requires ``ruff`` at runtime, so
keeping it in the base package made every service that imports the SDK
ship a formatter to production and inherit its version floor.

Two extras install it:

- ``[cli-ops]`` — ``typer`` and ``click`` only. Enough for every command
  that operates a deployed service (``doctor``, ``db``, ``shell``,
  ``user``, ``routes``, the project's own commands), so a server image can
  carry ``tempest`` without ``ruff``.
- ``[cli]`` — ``[cli-ops]`` plus ``tempest-cli``, which adds the quality
  gate (``lint``, ``fix``, ``format``, ``fmt-check``, ``type``, ``test``,
  ``check``, ``pr-prompt``). The development and CI install.

This module is what ``[project.scripts]`` points at, and it imports only
the standard library. Without ``typer`` / ``click`` the script prints the
install line and exits with code 2 instead of a ``ModuleNotFoundError``
traceback; with them, control passes to
:func:`tempest_fastapi_sdk.cli.main.main`.
"""

from __future__ import annotations

import importlib
import sys

CLI_OPS_EXTRA_MODULES: tuple[str, ...] = ("typer", "click")
"""Top-level modules the ``[cli-ops]`` extra installs.

One per requirement of ``[project.optional-dependencies] cli-ops``. They are
what the ``tempest`` script needs to start at all; a missing one means
neither ``[cli-ops]`` nor ``[cli]`` is installed.
"""

CLI_GATE_MODULES: tuple[str, ...] = ("tempest_cli",)
"""Top-level modules ``[cli]`` adds on top of ``[cli-ops]``.

Only the quality-gate commands import them; a missing one means the
environment has ``[cli-ops]``, which is the intended production install.
"""

CLI_EXTRA_MODULES: tuple[str, ...] = (*CLI_GATE_MODULES, *CLI_OPS_EXTRA_MODULES)
"""Top-level modules the ``[cli]`` extra installs, ``[cli-ops]`` included."""

CLI_EXTRA_HINT: str = (
    "The tempest CLI needs the optional [cli-ops] or [cli] extra. Install it with:\n"
    '  uv add "tempest-fastapi-sdk[cli-ops]"   (on a server, without ruff)\n'
    '  uv add --dev "tempest-fastapi-sdk[cli]"   (in development, with the gate)\n'
    '  uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)'
)
"""Install instruction shown when ``typer`` / ``click`` are missing."""

CLI_GATE_HINT: str = (
    "The quality gate (lint, fix, format, fmt-check, type, test, check, "
    "pr-prompt) needs the optional [cli] extra, which adds tempest-cli and "
    "ruff. Install it with:\n"
    '  uv add --dev "tempest-fastapi-sdk[cli]"   (in a project)\n'
    '  uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)'
)
"""Install instruction shown when only ``tempest-cli`` is missing."""

CLI_EXTRA_EXIT_CODE: int = 2
"""Exit status of ``tempest`` without the extra, the SDK's usage-error code."""


def missing_cli_modules() -> list[str]:
    """List the modules the ``tempest`` script needs that cannot be imported.

    Only :data:`CLI_OPS_EXTRA_MODULES` are checked: ``tempest-cli`` is
    optional at startup, and its absence only turns the quality-gate
    commands into the :data:`CLI_GATE_HINT`.

    Each module is imported rather than looked up with
    ``importlib.util.find_spec``: the CLI imports them right after this
    check anyway, and an import is the question that matters — a module can
    have a spec and still fail to load.

    Returns:
        list[str]: Names from :data:`CLI_OPS_EXTRA_MODULES` that are not
        installed, in declaration order. Empty when either extra is
        installed.

    Raises:
        ModuleNotFoundError: When an installed module fails because of some
            *other* missing module — a broken install, which the install
            hint would not fix.
    """
    missing: list[str] = []
    for name in CLI_OPS_EXTRA_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
            missing.append(name)
    return missing


def raise_for_missing_cli_extra(error: ModuleNotFoundError) -> None:
    """Turn a failed import of a CLI extra's module into the install hint.

    Called from the ``except`` clause around a CLI module's imports. A
    missing module that belongs to some other package is left alone, so
    the caller re-raises the original error unchanged.

    Args:
        error (ModuleNotFoundError): The error the import raised.

    Raises:
        ImportError: With :data:`CLI_GATE_HINT` when the missing module is
            ``tempest_cli`` or a submodule of it, and with
            :data:`CLI_EXTRA_HINT` when it is ``typer`` / ``click`` or a
            submodule of one.
    """
    missing = (error.name or "").partition(".")[0]
    if missing in CLI_GATE_MODULES:
        raise ImportError(CLI_GATE_HINT) from error
    if missing in CLI_OPS_EXTRA_MODULES:
        raise ImportError(CLI_EXTRA_HINT) from error


def main() -> None:
    """Run the ``tempest`` CLI, or explain how to install it.

    Raises:
        SystemExit: With :data:`CLI_EXTRA_EXIT_CODE` when neither
            ``[cli-ops]`` nor ``[cli]`` is installed. Otherwise the exit
            status is whatever :func:`tempest_fastapi_sdk.cli.main.main`
            produces.
    """
    missing = missing_cli_modules()
    if missing:
        print(f"error: missing {', '.join(missing)}. {CLI_EXTRA_HINT}", file=sys.stderr)
        raise SystemExit(CLI_EXTRA_EXIT_CODE)
    from tempest_fastapi_sdk.cli.main import main as run_cli

    run_cli()
