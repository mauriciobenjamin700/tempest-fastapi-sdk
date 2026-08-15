"""`tempest check` must keep working after the gate moved to tempest-cli.

The commands are registered from ``tempest_cli`` rather than defined
here, so what these tests protect is the seam: the same names, mounted on
the SDK's app, resolving to the shared implementation. A project that
pins the SDK and runs ``tempest check`` in CI must not notice the
extraction.
"""

from __future__ import annotations

import tempest_cli
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli import lint as sdk_lint
from tempest_fastapi_sdk.cli import pr_prompt as sdk_pr_prompt
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

MOVED_COMMANDS: frozenset[str] = frozenset(
    {"lint", "fix", "format", "fmt-check", "type", "test", "check", "pr-prompt"},
)


def _command_names() -> set[str]:
    """Return the command names registered on the SDK's CLI.

    Returns:
        set[str]: Every registered command name.
    """
    return {
        info.name or (info.callback.__name__ if info.callback else "")
        for info in app.registered_commands
    }


def test_every_moved_command_is_still_mounted() -> None:
    assert _command_names() >= MOVED_COMMANDS


def test_sdk_commands_are_still_there_too() -> None:
    """Registering the gate must not displace what the SDK owns."""
    assert {"new", "generate", "openapi-errors", "permissions"} <= _command_names()


def test_check_help_reaches_the_shared_implementation() -> None:
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    assert "--strictness" in result.stdout


def test_runners_are_the_shared_ones_not_a_copy() -> None:
    assert sdk_lint.run_full_check is tempest_cli.run_full_check
    assert sdk_lint.run_ruff_check is tempest_cli.run_ruff_check
    assert sdk_pr_prompt.generate_pr_prompt is tempest_cli.generate_pr_prompt


def test_old_import_paths_still_resolve() -> None:
    """A project pinning the SDK keeps its imports."""
    for name in sdk_lint.__all__:
        assert hasattr(sdk_lint, name), name
    for name in sdk_pr_prompt.__all__:
        assert hasattr(sdk_pr_prompt, name), name
