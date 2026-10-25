"""The project ``tempest new`` writes passes its own ruff, format and mypy.

``tests/test_schemas.py`` shipped in the scaffold with an import order the
generated project's ruff refuses (``I001``): every check here ran on the
SDK, none on the project it writes. This runs the three tools the way
``tempest check`` runs them, with the generated ``pyproject.toml``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

TOOLS: tuple[tuple[str, ...], ...] = (
    ("ruff", "check", "."),
    ("ruff", "format", "--check", "."),
    ("mypy", "."),
)
"""The static steps of ``tempest check``, run with the project's config."""


@pytest.mark.parametrize(
    "extras",
    ["", "auth,queue,tasks,ssr,admin,email,minio"],
    ids=["no-extras", "most-extras"],
)
def test_the_generated_project_passes_its_own_tools(
    tmp_path: Path, extras: str
) -> None:
    """Cenário: um projeto novo, sem extras e com quase todos.

    Esperado: ``ruff check``, ``ruff format --check`` e ``mypy`` saem com
    ``0`` sobre ele, com a config que o próprio projeto declara.
    """
    arguments = ["new", "demo", "--path", str(tmp_path)]
    if extras:
        arguments += ["--extras", extras]
    result = runner.invoke(cli_app, arguments)
    assert result.exit_code == 0, result.stdout
    project = tmp_path / "demo"
    bin_dir = Path(sys.executable).parent
    for tool, *args in TOOLS:
        completed = subprocess.run(
            [str(bin_dir / tool), *args],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, (tool, completed.stdout + completed.stderr)
