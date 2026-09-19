"""Tests for ``tempest serve``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_SERVER_MODULE = """
from fastapi import FastAPI

app = FastAPI()
"""

_FACTORY_MODULE = """
from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI()
"""


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture what ``serve`` hands to :func:`run_server` without booting it."""
    recorded: list[dict[str, Any]] = []

    def fake_run_server(target: Any, **kwargs: Any) -> None:
        recorded.append({"target": target, **kwargs})

    monkeypatch.setattr("tempest_fastapi_sdk.run_server", fake_run_server)
    return recorded


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand inside a project exposing ``src.server:app``."""
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "server.py").write_text(_SERVER_MODULE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]


class TestSpec:
    def test_hands_uvicorn_the_import_string(
        self,
        service: Path,
        calls: list[dict[str, Any]],
    ) -> None:
        """uvicorn re-imports the target in the worker; an instance kills reload."""
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert calls[0]["target"] == "src.server:app"
        assert isinstance(calls[0]["target"], str)

    def test_factory_is_declared(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[dict[str, Any]],
    ) -> None:
        (tmp_path / "factory_mod.py").write_text(_FACTORY_MODULE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["serve", "--app", "factory_mod:create_app"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert calls[0]["factory"] is True

    def test_plain_app_does_not_declare_a_factory(
        self,
        service: Path,
        calls: list[dict[str, Any]],
    ) -> None:
        runner.invoke(app, ["serve"])
        assert "factory" not in calls[0]

    def test_unresolvable_app_exits_two(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[dict[str, Any]],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 2
        assert not calls


class TestOverrides:
    def test_host_and_port(
        self,
        service: Path,
        calls: list[dict[str, Any]],
    ) -> None:
        runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9100"])
        assert calls[0]["host"] == "0.0.0.0"
        assert calls[0]["port"] == 9100

    def test_reload_is_tri_state(
        self,
        service: Path,
        calls: list[dict[str, Any]],
    ) -> None:
        """--no-reload must be able to override a settings value of True."""
        runner.invoke(app, ["serve"])
        runner.invoke(app, ["serve", "--reload"])
        runner.invoke(app, ["serve", "--no-reload"])
        assert [call["reload"] for call in calls] == [None, True, False]

    def test_workers_with_reload_is_refused(
        self,
        service: Path,
        calls: list[dict[str, Any]],
    ) -> None:
        result = runner.invoke(app, ["serve", "--reload", "--workers", "4"])
        assert result.exit_code == 2
        assert "--reload runs a single process" in result.stderr
        assert not calls

    def test_workers_alone_is_forwarded(
        self,
        service: Path,
        calls: list[dict[str, Any]],
    ) -> None:
        runner.invoke(app, ["serve", "--workers", "4"])
        assert calls[0]["workers"] == 4
