"""Tests for ``[tool.tempest]`` config loading and strictness mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.config import (
    DEFAULT_TYPING_STRICTNESS,
    TempestConfig,
    find_pyproject,
    load_tempest_config,
)


class TestStrictnessMapping:
    def test_lenient_adds_nothing(self) -> None:
        config = TempestConfig(typing_strictness="lenient")
        assert config.ruff_ann_select() == []
        assert config.mypy_flags() == []

    def test_standard_requires_annotations(self) -> None:
        config = TempestConfig(typing_strictness="standard")
        assert "ANN001" in config.ruff_ann_select()
        assert "ANN201" in config.ruff_ann_select()
        assert "--disallow-untyped-defs" in config.mypy_flags()

    def test_strict_uses_mypy_strict(self) -> None:
        config = TempestConfig(typing_strictness="strict")
        assert config.mypy_flags() == ["--strict"]

    def test_any_never_flagged_at_any_level(self) -> None:
        for level in ("lenient", "standard", "strict"):
            config = TempestConfig(typing_strictness=level)  # type: ignore[arg-type]
            assert "ANN401" not in config.ruff_ann_select()

    def test_default_level(self) -> None:
        assert TempestConfig().typing_strictness == DEFAULT_TYPING_STRICTNESS


class TestLoadTempestConfig:
    def test_reads_typing_strictness(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ntyping_strictness = "strict"\n'
        )
        config = load_tempest_config(tmp_path)
        assert config.typing_strictness == "strict"

    def test_missing_table_falls_back_to_default(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        config = load_tempest_config(tmp_path)
        assert config.typing_strictness == DEFAULT_TYPING_STRICTNESS

    def test_no_pyproject_falls_back_to_default(self, tmp_path: Path) -> None:
        config = load_tempest_config(tmp_path)
        assert config.typing_strictness == DEFAULT_TYPING_STRICTNESS

    def test_invalid_value_raises(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ntyping_strictness = "bogus"\n'
        )
        with pytest.raises(ValueError, match="invalid typing_strictness"):
            load_tempest_config(tmp_path)

    def test_find_pyproject_walks_up(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_pyproject(nested) == tmp_path / "pyproject.toml"

    def test_find_pyproject_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert find_pyproject(tmp_path) is None
