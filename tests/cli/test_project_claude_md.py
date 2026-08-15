"""The scaffolded ``CLAUDE.md`` must be correct, not just present.

It is the file every AI agent reads before touching a generated project,
so a stale symbol or a broken example there teaches the wrong thing at
scale. These tests parse the document the CLI actually writes: every
Python block must compile, and every SDK symbol it imports must exist.
"""

from __future__ import annotations

import ast
import importlib
import re
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
_SDK_IMPORT = re.compile(
    r"^from (tempest_fastapi_sdk[\w.]*) import (.+)$", re.MULTILINE
)


@pytest.fixture(scope="module")
def claude_md(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Scaffold a project and return its rendered ``CLAUDE.md``.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temporary
            directory factory.

    Returns:
        str: The rendered document.
    """
    tmp_path = tmp_path_factory.mktemp("claude-md")
    result = runner.invoke(
        cli_app,
        ["new", "demo", "--path", str(tmp_path), "--extras", "ssr,auth"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    return (tmp_path / "demo" / "CLAUDE.md").read_text(encoding="utf-8")


def test_is_written_and_fully_rendered(claude_md: str) -> None:
    assert claude_md.startswith("# CLAUDE.md — demo")
    assert "__PROJECT_NAME__" not in claude_md
    assert "__SDK_DEP__" not in claude_md
    assert "__SDK_EXTRAS__" not in claude_md
    assert "tempest-fastapi-sdk[ssr,auth]" in claude_md


def test_every_python_block_parses(claude_md: str) -> None:
    """Blocks nested in a list are dedented first, as a reader would copy them."""
    blocks = _BLOCK.findall(claude_md)
    assert len(blocks) >= 8
    for index, block in enumerate(blocks):
        source = textwrap.dedent(block)
        try:
            ast.parse(source)
        except SyntaxError as exc:  # pragma: no cover - failure path
            pytest.fail(f"block {index} does not parse: {exc}\n{source}")


def test_every_sdk_symbol_it_imports_exists(claude_md: str) -> None:
    """A renamed export must fail here, not in a user's project."""
    checked = 0
    for module_name, names in _SDK_IMPORT.findall(claude_md):
        module = importlib.import_module(module_name)
        for name in (item.strip() for item in names.split(",")):
            assert hasattr(module, name), f"{module_name}.{name} does not exist"
            checked += 1
    assert checked >= 10


def test_states_the_rules_that_keep_services_uniform(claude_md: str) -> None:
    for rule in (
        "map_to_response",
        "BasePaginationSchema",
        "register_exception_handlers",
        "HTTPException",
        "tempest check",
        "Do not reimplement",
        "__all__",
    ):
        assert rule in claude_md


def test_documents_the_ui_layer(claude_md: str) -> None:
    for symbol in ("form_for", "parse_form", "STYLESHEET", "Stack", "body()"):
        assert symbol in claude_md


def test_readme_points_at_it(tmp_path: Path) -> None:
    result = runner.invoke(cli_app, ["new", "svc", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    readme = (tmp_path / "svc" / "README.md").read_text(encoding="utf-8")
    assert "CLAUDE.md" in readme
