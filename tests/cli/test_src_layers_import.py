"""Every module ``tempest new`` writes for a layer-bearing extra imports.

The other CLI tests check that the layer files exist and carry the
expected strings, which is how ``queue/__init__.py`` shipped importing
``AsyncBrokerManager`` from the top-level package — a name the root
``tempest_fastapi_sdk`` never exported — and every project scaffolded
with ``--extras queue`` died with ``ImportError`` on its first import.

Each extra is imported in its own interpreter, so the scaffolded ``src``
package never lands in this process's ``sys.modules`` (where it would
collide with the project ``test_scaffold_runtime`` imports) and models
registered by one import never leak into another.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app
from tempest_fastapi_sdk.cli.src_layers import LAYER_FILES

runner = CliRunner()


def _module_names(extra: str) -> list[str]:
    """Return the dotted ``src`` modules the layer of ``extra`` writes.

    Args:
        extra (str): A key of :data:`LAYER_FILES`.

    Returns:
        list[str]: Module names such as ``src.queue.handlers``, sorted.
    """
    names: list[str] = []
    for relative in LAYER_FILES[extra]:
        parts = relative.removesuffix(".py").split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(["src", *parts]))
    return sorted(names)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Scaffold one project carrying every layer-bearing extra.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temporary
            directory factory.

    Yields:
        Path: The generated project root.
    """
    root = tmp_path_factory.mktemp("layers")
    result = runner.invoke(
        cli_app,
        ["new", "demo", "--path", str(root), "--extras", ",".join(LAYER_FILES)],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    yield root / "demo"


@pytest.mark.parametrize("extra", sorted(LAYER_FILES))
def test_every_generated_module_imports(project: Path, extra: str) -> None:
    """Import each module of one extra's layer in a fresh interpreter.

    Args:
        project (Path): The scaffolded project root.
        extra (str): The layer-bearing extra under test.
    """
    modules = _module_names(extra)
    code = "import importlib\n" + "".join(
        f"importlib.import_module({name!r})\n" for name in modules
    )
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{project / 'test.db'}"
    env.pop("TASKIQ_BROKER_URL", None)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"importing {modules} failed:\n{completed.stderr}"
