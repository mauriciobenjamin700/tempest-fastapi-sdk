"""Compatibility re-export of the quality-gate runners.

The gate moved to [`tempest-cli`](https://pypi.org/project/tempest-cli/)
in v0.226.0 — it never depended on FastAPI, and living here meant
reaching it cost 38.7 MB of dependencies and half a second of import
time per invocation.

``tempest lint`` / ``fix`` / ``format`` / ``fmt-check`` / ``type`` /
``test`` / ``check`` keep working exactly as before: the SDK's CLI mounts
the very same commands through
:func:`tempest_cli.main.register_commands`. Importing these runners from
here also keeps working and returns the same functions.

Prefer importing from ``tempest_cli`` directly in new code.
"""

from tempest_cli.lint import resolve_tool as resolve_tool
from tempest_cli.lint import run_full_check as run_full_check
from tempest_cli.lint import run_mypy as run_mypy
from tempest_cli.lint import run_pytest as run_pytest
from tempest_cli.lint import run_ruff_check as run_ruff_check
from tempest_cli.lint import run_ruff_fix as run_ruff_fix
from tempest_cli.lint import run_ruff_format as run_ruff_format

__all__: list[str] = [
    "resolve_tool",
    "run_full_check",
    "run_mypy",
    "run_pytest",
    "run_ruff_check",
    "run_ruff_fix",
    "run_ruff_format",
]
