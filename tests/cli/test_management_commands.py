"""Tests for project management-command discovery + mounting."""

import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.commands import mount_project_commands
from tempest_fastapi_sdk.cli.config import load_tempest_config

runner = CliRunner()


def _make_command_module(name: str, *, command_name: str = "greet") -> None:
    """Install a fake module exposing a ``commands`` Typer in sys.modules."""
    module = types.ModuleType(name)
    commands = typer.Typer()

    @commands.command(command_name)
    def _cmd() -> None:
        typer.echo(f"ran {command_name}")

    module.commands = commands  # type: ignore[attr-defined]
    sys.modules[name] = module


def _root_app() -> typer.Typer:
    """A Typer with a placeholder command, so it behaves as a group.

    Typer collapses a single-command app into a nameless command; the
    real ``tempest`` app always has many, so tests add a placeholder to
    reproduce group behavior.
    """
    app = typer.Typer()

    @app.command("_placeholder")
    def _placeholder() -> None:
        typer.echo("placeholder")

    return app


@pytest.fixture
def cleanup_modules() -> Iterator[None]:
    yield
    for name in list(sys.modules):
        if name.startswith("_mgmt_"):
            del sys.modules[name]


class TestMount:
    def test_explicit_module_mounts_and_runs(self, cleanup_modules: None) -> None:
        _make_command_module("_mgmt_ok", command_name="backfill")
        app = _root_app()
        mounted = mount_project_commands(app, modules=("_mgmt_ok",))
        assert "backfill" in mounted

        result = runner.invoke(app, ["backfill"])
        assert result.exit_code == 0
        assert "ran backfill" in result.stdout

    def test_missing_explicit_module_raises(self, cleanup_modules: None) -> None:
        app = typer.Typer()
        with pytest.raises(ImportError):
            mount_project_commands(app, modules=("_mgmt_does_not_exist",))

    def test_module_without_typer_raises(self, cleanup_modules: None) -> None:
        sys.modules["_mgmt_empty"] = types.ModuleType("_mgmt_empty")
        app = typer.Typer()
        with pytest.raises(ValueError, match="no 'commands' or 'app'"):
            mount_project_commands(app, modules=("_mgmt_empty",))

    def test_collision_with_builtin_is_skipped(self, cleanup_modules: None) -> None:
        _make_command_module("_mgmt_clash", command_name="version")
        app = _root_app()

        @app.command("version")
        def _builtin() -> None:
            typer.echo("builtin")

        mounted = mount_project_commands(app, modules=("_mgmt_clash",), warn=False)
        assert "version" not in mounted
        # The built-in still wins.
        result = runner.invoke(app, ["version"])
        assert "builtin" in result.stdout

    def test_absent_candidates_are_silent(self, cleanup_modules: None) -> None:
        app = typer.Typer()
        # No modules given and no conventional module importable → no error.
        mounted = mount_project_commands(app, modules=(), cwd=Path("/tmp"))
        assert mounted == []


class TestConfigCommands:
    def test_string_value(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ncommands = "src.management"\n'
        )
        config = load_tempest_config(tmp_path)
        assert config.commands == ("src.management",)

    def test_list_value(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ncommands = ["a.cmds", "b.cmds"]\n'
        )
        config = load_tempest_config(tmp_path)
        assert config.commands == ("a.cmds", "b.cmds")

    def test_absent_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.tempest]\n")
        config = load_tempest_config(tmp_path)
        assert config.commands == ()

    def test_invalid_value_raises(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.tempest]\ncommands = 42\n")
        with pytest.raises(ValueError, match="invalid commands"):
            load_tempest_config(tmp_path)
