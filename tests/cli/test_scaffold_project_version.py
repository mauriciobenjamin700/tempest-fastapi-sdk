"""The scaffolded ``Settings`` reads its version from the generated ``pyproject.toml``.

Bumping ``[project].version`` in a project created by ``tempest new`` must
change the published contract — ``app.openapi()["info"]["version"]`` and
``GET /health/readiness`` — without editing ``src/core/settings.py``, and
the ``VERSION`` environment variable must still win. The generated
settings module anchors the path on its own location, so the value is the
project's even when the process starts from another directory.

Everything runs against a real scaffold, in its own ``src`` package, so
the module cache is cleared between tests.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import tempest_fastapi_sdk
from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

_VERSION_LINE = re.compile(r'^version = ".*"$', re.MULTILINE)
"""The single ``version = "..."`` line in a generated ``pyproject.toml``."""

GENERATED_VERSION = "7.7.7"
"""Version written into the scaffold, distinct from the ``0.1.0`` default."""


def _scaffold(tmp_path: Path, version: str = GENERATED_VERSION) -> Path:
    """Run ``tempest new`` and set the generated project version.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        version (str): The version to write into the generated
            ``pyproject.toml``.

    Returns:
        Path: The scaffolded project root.
    """
    result = runner.invoke(cli_app, ["new", "demo", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    project = tmp_path / "demo"
    pyproject = project / "pyproject.toml"
    pyproject.write_text(
        _VERSION_LINE.sub(
            f'version = "{version}"',
            pyproject.read_text(encoding="utf-8"),
            count=1,
        ),
        encoding="utf-8",
    )
    return project


def _forget_src_modules() -> None:
    """Drop the generated ``src`` package from the module cache.

    Other CLI tests scaffold their own project under the same top-level
    ``src`` name; a cached copy would shadow the project under test.
    """
    for name in [
        module
        for module in list(sys.modules)
        if module == "src" or module.startswith("src.")
    ]:
        del sys.modules[name]


def _disconnect_scaffold_database() -> None:
    """Dispose the engine the scaffolded ``resources.db`` opened, if any."""
    import asyncio

    resources = sys.modules.get("src.api.dependencies.resources")
    database = getattr(resources, "db", None)
    if database is not None:
        asyncio.run(database.disconnect())


def _no_logging(**_: Any) -> None:
    """Replace ``configure_logging`` so importing the app leaves no ``logs/``."""


@pytest.fixture()
def generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Scaffold a project, importable, with a temporary database.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        monkeypatch (pytest.MonkeyPatch): Fixture applying the env vars and
            the ``sys.path`` entry, both reverted at teardown.

    Yields:
        Path: The generated project root.
    """
    project = _scaffold(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("DATABASE_MIGRATE_ON_STARTUP", "false")
    monkeypatch.delenv("VERSION", raising=False)
    _forget_src_modules()
    monkeypatch.syspath_prepend(str(project))
    importlib.invalidate_caches()
    try:
        yield project
    finally:
        _disconnect_scaffold_database()
        _forget_src_modules()
        importlib.invalidate_caches()


def test_openapi_and_readiness_use_the_pyproject_version(
    generated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bumping the pyproject version changes both published surfaces."""
    monkeypatch.setattr(tempest_fastapi_sdk, "configure_logging", _no_logging)
    app_module = importlib.import_module("src.api.app")
    app = app_module.create_app()

    assert app.openapi()["info"]["version"] == GENERATED_VERSION

    with TestClient(app) as client:
        body = client.get("/health/readiness").json()
    assert body["version"] == GENERATED_VERSION
    assert body["status"] == "ready"


def test_version_env_var_wins_over_pyproject(
    generated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``VERSION`` in the environment overrides the file."""
    monkeypatch.setenv("VERSION", "9.9.9")
    settings = importlib.import_module("src.core.settings").settings
    assert settings.VERSION == "9.9.9"


def test_version_is_read_regardless_of_cwd(
    generated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The value comes from the project, not from the working directory."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    settings = importlib.import_module("src.core.settings").settings
    assert settings.VERSION == GENERATED_VERSION


def test_generated_settings_reads_version_in_a_subprocess(
    generated: Path, tmp_path: Path
) -> None:
    """Across a process boundary, started from another directory."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    environment = {key: value for key, value in os.environ.items() if key != "VERSION"}
    environment["PYTHONPATH"] = str(generated)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.core.settings import settings; print(settings.VERSION)",
        ],
        cwd=elsewhere,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == GENERATED_VERSION


def test_generated_env_example_does_not_pin_the_version(generated: Path) -> None:
    """The version is declared once, in ``pyproject.toml``."""
    lines = (generated / ".env.example").read_text(encoding="utf-8").splitlines()
    assert not [line for line in lines if line.startswith("VERSION=")]
