"""``openapi-errors --fix`` imports from the package that re-exports (#722).

The fix wrote ``from src.core.exceptions.bad_request import X`` — the module
that defines the class — even when ``src/core/exceptions/__init__.py``
re-exports it and the router already imported from ``src.core.exceptions``.
That breaks the project rule (import from the package; every ``__init__``
re-exports), which ``lint-layers`` reports as ``IMP002``, and nothing else in
the pipeline caught it.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

INIT = '''"""Domain exceptions."""

from .bad_request import InvalidAudioException
from .not_found import ServiceNotFoundException

__all__: list[str] = ["InvalidAudioException", "ServiceNotFoundException"]
'''

BAD_REQUEST = '''"""400 exceptions."""

from tempest_fastapi_sdk import BadRequestException


class InvalidAudioException(BadRequestException):
    """The audio could not be read."""

    code: str = "INVALID_AUDIO"


class HiddenException(BadRequestException):
    """Defined here and not re-exported by the package."""

    code: str = "HIDDEN"
'''

NOT_FOUND = '''"""404 exceptions."""

from tempest_fastapi_sdk import NotFoundException


class ServiceNotFoundException(NotFoundException):
    """The service does not exist."""

    code: str = "SERVICE_NOT_FOUND"
'''

SERVICE = '''"""Audio service."""

from src.core.exceptions import InvalidAudioException, ServiceNotFoundException
from src.core.exceptions.bad_request import HiddenException


class AudioService:
    """Business logic for audio."""

    async def find(self, service_id: str) -> None:
        """Find a service.

        Raises:
            ServiceNotFoundException: If it does not exist.
        """
        raise ServiceNotFoundException()

    async def transcribe(self, service_id: str) -> None:
        """Transcribe.

        Raises:
            InvalidAudioException: If the audio is unreadable.
        """
        raise InvalidAudioException()

    async def hidden(self, service_id: str) -> None:
        """Do the hidden thing.

        Raises:
            HiddenException: Always.
        """
        raise HiddenException()
'''

ROUTER_WITH_PACKAGE_IMPORT = '''"""Audio routes."""

from fastapi import APIRouter
from tempest_fastapi_sdk import error_responses

from src.core.exceptions import (
    ServiceNotFoundException,
)
from src.services.audio import AudioService

router = APIRouter(prefix="/api/audio")


@router.post(
    "/{service_id}",
    responses=error_responses(ServiceNotFoundException),
)
async def transcribe(service_id: str) -> dict[str, str]:
    """Transcribe an upload."""
    service = AudioService()
    await service.find(service_id)
    await service.transcribe(service_id)
    return {}
'''

BARE_ROUTER = '''"""Audio routes."""

from fastapi import APIRouter

from src.services.audio import AudioService

router = APIRouter(prefix="/api/audio")


@router.post("/{service_id}")
async def transcribe(service_id: str) -> dict[str, str]:
    """Transcribe an upload."""
    service = AudioService()
    await service.transcribe(service_id)
    await service.hidden(service_id)
    return {}
'''


def _git(root: Path, *args: str) -> None:
    """Run a git command inside ``root``.

    Args:
        root (Path): Repository directory.
        *args (str): Command arguments after ``git``.
    """
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _project(root: Path, router: str) -> Path:
    """Lay out a project whose exceptions are a package, and commit it.

    Args:
        root (Path): Where to create it.
        router (str): The router module's source.

    Returns:
        Path: The router file.
    """
    exceptions = root / "src" / "core" / "exceptions"
    exceptions.mkdir(parents=True)
    (root / "src" / "services").mkdir(parents=True)
    (root / "src" / "api" / "routers").mkdir(parents=True)
    (exceptions / "__init__.py").write_text(INIT)
    (exceptions / "bad_request.py").write_text(BAD_REQUEST)
    (exceptions / "not_found.py").write_text(NOT_FOUND)
    (root / "src" / "services" / "audio.py").write_text(SERVICE)
    target = root / "src" / "api" / "routers" / "audio.py"
    target.write_text(router)
    _git(root, "init", "-q", ".")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return target


def _imports(source: str) -> list[tuple[str, list[str]]]:
    """List the module's ``from X import ...`` statements.

    Args:
        source (str): The module source.

    Returns:
        list[tuple[str, list[str]]]: ``(module, names)`` per statement.
    """
    return [
        (node.module or "", [alias.name for alias in node.names])
        for node in ast.parse(source).body
        if isinstance(node, ast.ImportFrom)
    ]


def _fix(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run ``tempest openapi-errors --fix`` in ``root``.

    Args:
        root (Path): Project root.
        monkeypatch (pytest.MonkeyPatch): Used to chdir.

    Returns:
        str: The command output.
    """
    monkeypatch.chdir(root)
    return CliRunner().invoke(app, ["openapi-errors", "--fix"]).output


class TestPackageImport:
    def test_joins_the_existing_package_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The issue's case: one block from the package, no submodule import."""
        router = _project(tmp_path, ROUTER_WITH_PACKAGE_IMPORT)

        _fix(tmp_path, monkeypatch)
        imports = _imports(router.read_text())

        modules = [module for module, _ in imports]
        assert "src.core.exceptions.bad_request" not in modules
        assert modules.count("src.core.exceptions") == 1
        names = dict(imports)["src.core.exceptions"]
        assert sorted(names) == ["InvalidAudioException", "ServiceNotFoundException"]

    def test_new_import_names_the_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file with no import from the package gets one, from the package."""
        router = _project(tmp_path, BARE_ROUTER)

        _fix(tmp_path, monkeypatch)
        imports = dict(_imports(router.read_text()))

        assert "InvalidAudioException" in imports["src.core.exceptions"]

    def test_a_name_the_package_does_not_reexport_keeps_its_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Climbing stops where the re-export stops; no import is invented."""
        router = _project(tmp_path, BARE_ROUTER)

        _fix(tmp_path, monkeypatch)
        imports = dict(_imports(router.read_text()))

        assert "HiddenException" in imports["src.core.exceptions.bad_request"]
        assert "HiddenException" not in imports.get("src.core.exceptions", [])

    def test_the_rewritten_router_still_parses_and_declares_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _project(tmp_path, ROUTER_WITH_PACKAGE_IMPORT)

        _fix(tmp_path, monkeypatch)
        source = router.read_text()

        ast.parse(source)
        assert "InvalidAudioException" in source.split("responses=")[1]
