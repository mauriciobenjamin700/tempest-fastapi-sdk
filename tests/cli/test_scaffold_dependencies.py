"""The scaffold wires controllers through factories and settings through ``Depends``.

``docs/architecture.md`` teaches three things the template used not to
ship: controller factories in ``src/api/dependencies/controllers.py``
received by ``Depends``, a ``get_settings`` that ``dependency_overrides``
can replace, and the ``get_mailer`` / ``get_storage`` providers for the
``[email]`` / ``[minio]`` extras, which came commented out.

Each generated app runs in a child interpreter rather than in-process:
importing a scaffolded ``src`` package registers its models on the SDK's
declarative base, and three projects under the same package name would
collide in one process.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

_PROBE = textwrap.dedent(
    """
    from fastapi.testclient import TestClient

    import src.api.dependencies as dependencies
    from src.api.app import create_app
    from src.core.settings import Settings, get_settings

    app = create_app()
    with TestClient(app) as client:
        print("PROBE", client.get("/api/").json()["service"])
        app.dependency_overrides[get_settings] = lambda: Settings(TITLE="swapped")
        print("PROBE", client.get("/api/").json()["service"])
    for name in ("get_mailer", "get_storage"):
        provider = getattr(dependencies, name, None)
        listed = name in dependencies.__all__
        kind = type(provider()).__name__ if provider is not None else "-"
        print("PROBE", f"{name}={kind} listed={listed}")
    """,
)
"""Serves ``/api/`` before and after overriding ``get_settings``, then
reports which resource providers the ``src.api.dependencies`` package
exposes and what they return."""


def _scaffold(tmp_path: Path, name: str, extras: str) -> Path:
    """Run ``tempest new`` and return the project root.

    Args:
        tmp_path (Path): Parent directory.
        name (str): Project name.
        extras (str): Value for ``--extras``; empty for the default set.

    Returns:
        Path: The generated project.
    """
    arguments = ["new", name, "--path", str(tmp_path)]
    if extras:
        arguments += ["--extras", extras]
    result = runner.invoke(cli_app, arguments)
    assert result.exit_code == 0, result.stdout + result.stderr
    return tmp_path / name


def _probe(project: Path, tmp_path: Path) -> list[str]:
    """Run :data:`_PROBE` inside the generated project.

    Args:
        project (Path): The generated project root.
        tmp_path (Path): Where the probe's SQLite file goes.

    Returns:
        list[str]: The lines the probe printed, without the ``PROBE``
        prefix that sets them apart from the app's JSON log lines.
    """
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}"
    env["LOG_DIR"] = ""
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return [
        line.removeprefix("PROBE ")
        for line in completed.stdout.splitlines()
        if line.startswith("PROBE ")
    ]


def test_router_receives_its_controller_from_a_factory(tmp_path: Path) -> None:
    """``controllers.py`` holds the factory and the router declares ``Depends``."""
    project = _scaffold(tmp_path, "demo", "")
    factories = project / "src" / "api" / "dependencies" / "controllers.py"
    assert factories.is_file()
    tree = ast.parse(factories.read_text(encoding="utf-8"))
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "get_service_info_controller"
    )
    assert [ast.unparse(default) for default in factory.args.defaults] == [
        "Depends(get_settings)",
    ]

    router = (project / "src" / "api" / "routers" / "__init__.py").read_text(
        encoding="utf-8",
    )
    assert "Depends(get_service_info_controller)" in router
    assert "ServiceInfoController(" not in router

    package = (project / "src" / "api" / "dependencies" / "__init__.py").read_text(
        encoding="utf-8",
    )
    assert "get_service_info_controller as get_service_info_controller" in package
    assert '"get_service_info_controller"' in package


@pytest.mark.parametrize(
    ("extras", "mailer", "storage"),
    [
        ("", "get_mailer=- listed=False", "get_storage=- listed=False"),
        ("email", "get_mailer=EmailUtils listed=True", "get_storage=- listed=False"),
        (
            "minio",
            "get_mailer=- listed=False",
            "get_storage=AsyncMinIOClient listed=True",
        ),
    ],
)
def test_settings_override_and_resource_providers_follow_the_extras(
    tmp_path: Path,
    extras: str,
    mailer: str,
    storage: str,
) -> None:
    """``get_settings`` is swappable; mailer and storage exist only with their extra."""
    project = _scaffold(tmp_path, "demo", extras)
    resources = (project / "src" / "api" / "dependencies" / "resources.py").read_text(
        encoding="utf-8",
    )
    assert "# from tempest_fastapi_sdk import" not in resources
    assert "@@" not in resources

    assert _probe(project, tmp_path) == ["demo", "swapped", mailer, storage]
