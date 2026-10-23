"""The SDK side of ``[tool.tempest]`` after the gate moved out.

The table has two owners since v0.226.0: ``typing_strictness`` is read by
``tempest-cli`` (which ships the quality gate) and ``commands`` is read
here. These tests cover what stayed, and that the symbols which moved are
still importable from their old home — a project pinning the SDK must not
have to touch its imports.

Since v0.324.0 finding the project is the SDK's own code again, ported from
``tempest-cli`` so the ``[cli-ops]`` extra (no ``tempest-cli``) can mount the
project's commands. The parity tests below fail on drift upstream.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tempest_cli.config

from tempest_fastapi_sdk.cli.config import (
    DEFAULT_TYPING_STRICTNESS,
    PYPROJECT_FILENAME,
    TEMPEST_TABLE_PATH,
    TempestConfig,
    find_pyproject,
    load_project_commands,
    load_tempest_config,
    load_tempest_table,
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
        assert DEFAULT_TYPING_STRICTNESS == tempest_cli.DEFAULT_TYPING_STRICTNESS

    def test_unknown_name_is_an_attribute_error(self) -> None:
        import tempest_fastapi_sdk.cli.config as config

        with pytest.raises(AttributeError, match="no_such_name"):
            config.no_such_name  # noqa: B018

    def test_the_gate_no_longer_owns_the_commands_key(self) -> None:
        """`commands` is an SDK concept; the shared config must not carry it."""
        assert not hasattr(TempestConfig(), "commands")


class TestPortedFromTempestCli:
    """``find_pyproject`` and the table lookup, owned by the SDK since v0.324.0."""

    def test_pyproject_filename_is_pinned(self) -> None:
        assert PYPROJECT_FILENAME == "pyproject.toml"

    def test_table_path_is_pinned(self) -> None:
        assert TEMPEST_TABLE_PATH == ("tool", "tempest")

    def test_find_pyproject_is_the_sdk_own(self) -> None:
        """Not a re-export: it must run without ``tempest_cli``."""
        assert find_pyproject is not tempest_cli.config.find_pyproject
        assert find_pyproject.__module__ == "tempest_fastapi_sdk.cli.config"

    @pytest.mark.parametrize("layout", ["here", "parent", "none"])
    def test_find_pyproject_matches_upstream(self, tmp_path: Path, layout: str) -> None:
        """Same answer as ``tempest_cli.config.find_pyproject``.

        Args:
            tmp_path (Path): Scratch root.
            layout (str): Where the ``pyproject.toml`` sits.
        """
        start = tmp_path / "a" / "b"
        start.mkdir(parents=True)
        if layout == "here":
            (start / "pyproject.toml").write_text("")
        elif layout == "parent":
            (tmp_path / "pyproject.toml").write_text("")
        ours = find_pyproject(start)
        upstream = tempest_cli.config.find_pyproject(start)
        if layout == "none" and upstream is not None:
            pytest.skip(f"a pyproject.toml above tmp_path: {upstream}")
        assert ours == upstream

    def test_table_matches_upstream_strictness(self, tmp_path: Path) -> None:
        """The table read here is the one ``tempest-cli`` resolves."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ntyping_strictness = "lenient"\ncommands = "x"\n'
        )
        pyproject, table = load_tempest_table(tmp_path)
        assert pyproject == tmp_path / "pyproject.toml"
        assert table == {"typing_strictness": "lenient", "commands": "x"}
        upstream = tempest_cli.config.load_tempest_config(tmp_path)
        assert upstream.typing_strictness == table["typing_strictness"]

    def test_absent_table_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
        assert load_tempest_table(tmp_path) == (tmp_path / "pyproject.toml", {})

    def test_non_table_tool_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('tool = "oops"\n')
        assert load_tempest_table(tmp_path) == (tmp_path / "pyproject.toml", {})
