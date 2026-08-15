"""The SDK side of ``[tool.tempest]`` after the gate moved out.

The table has two owners since v0.226.0: ``typing_strictness`` is read by
``tempest-cli`` (which ships the quality gate) and ``commands`` is read
here. These tests cover what stayed, and that the symbols which moved are
still importable from their old home — a project pinning the SDK must not
have to touch its imports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.config import (
    DEFAULT_TYPING_STRICTNESS,
    TempestConfig,
    find_pyproject,
    load_project_commands,
    load_tempest_config,
)


class TestProjectCommands:
    def test_string_value_becomes_a_single_entry(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ncommands = "src.management"\n'
        )
        assert load_project_commands(tmp_path) == ("src.management",)

    def test_list_value(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ncommands = ["a.cmds", "b.cmds"]\n'
        )
        assert load_project_commands(tmp_path) == ("a.cmds", "b.cmds")

    def test_absent_table_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        assert load_project_commands(tmp_path) == ()

    def test_no_pyproject_is_empty(self, tmp_path: Path) -> None:
        assert load_project_commands(tmp_path) == ()

    def test_invalid_value_raises(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.tempest]\ncommands = 42\n")
        with pytest.raises(ValueError, match="invalid commands"):
            load_project_commands(tmp_path)

    def test_reads_the_nearest_pyproject_walking_up(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ncommands = "src.management"\n'
        )
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert load_project_commands(nested) == ("src.management",)


class TestMovedSymbolsStayImportable:
    """`from tempest_fastapi_sdk.cli.config import ...` must keep working."""

    def test_typing_config_is_re_exported(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ntyping_strictness = "strict"\n'
        )
        config = load_tempest_config(tmp_path)
        assert config.typing_strictness == "strict"
        assert config.mypy_flags() == ["--strict"]

    def test_re_exports_come_from_tempest_cli(self) -> None:
        import tempest_cli

        assert TempestConfig is tempest_cli.TempestConfig
        assert load_tempest_config is tempest_cli.load_tempest_config
        assert find_pyproject is tempest_cli.find_pyproject
        assert DEFAULT_TYPING_STRICTNESS == tempest_cli.DEFAULT_TYPING_STRICTNESS

    def test_the_gate_no_longer_owns_the_commands_key(self) -> None:
        """`commands` is an SDK concept; the shared config must not carry it."""
        assert not hasattr(TempestConfig(), "commands")
