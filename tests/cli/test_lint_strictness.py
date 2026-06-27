"""Tests that lint helpers inject the configured strictness flags."""

from __future__ import annotations

from unittest.mock import patch

from tempest_fastapi_sdk.cli import lint
from tempest_fastapi_sdk.cli.config import TempestConfig


class TestRuffAnnInjection:
    def test_standard_adds_extend_select(self) -> None:
        config = TempestConfig(typing_strictness="standard")
        with patch.object(lint, "_execute", return_value=0) as fake:
            lint.run_ruff_check("src/", config=config)
        args = fake.call_args.args[1]
        assert "--extend-select" in args
        assert "ANN001" in args[args.index("--extend-select") + 1]

    def test_lenient_omits_extend_select(self) -> None:
        config = TempestConfig(typing_strictness="lenient")
        with patch.object(lint, "_execute", return_value=0) as fake:
            lint.run_ruff_check("src/", config=config)
        assert "--extend-select" not in fake.call_args.args[1]


class TestMypyFlagInjection:
    def test_strict_passes_strict_flag(self) -> None:
        config = TempestConfig(typing_strictness="strict")
        with patch.object(lint, "_execute", return_value=0) as fake:
            lint.run_mypy("src/", config=config)
        args = fake.call_args.args[1]
        assert args == ["--strict", "src/"]

    def test_lenient_passes_only_target(self) -> None:
        config = TempestConfig(typing_strictness="lenient")
        with patch.object(lint, "_execute", return_value=0) as fake:
            lint.run_mypy("src/", config=config)
        assert fake.call_args.args[1] == ["src/"]
