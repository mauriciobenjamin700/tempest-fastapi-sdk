"""Tests for ``tempest agents``.

The agent is a real :class:`Agent` built on a scripted backend, so what
is measured is the command's contract — the tool listing costs no model
call, and the exit code follows ``AgentRun.succeeded`` rather than "no
exception raised".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_AGENT_MODULE = '''
"""Agent wired to a scripted backend that answers in one step."""

from typing import Any

from pydantic import BaseModel

from tempest_fastapi_sdk.agents import Agent, tool


class AddArgs(BaseModel):
    """Arguments the add tool takes."""

    a: int
    b: int


class _ScriptedBackend:
    """Backend returning a fixed final answer."""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def generate(self, *args: Any, **kwargs: Any) -> str:
        return self.answer

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return self.answer


@tool("add", "Add two numbers together.")
async def add(args: AddArgs, context: Any) -> int:
    return args.a + args.b


agent = Agent(
    _ScriptedBackend("42"),
    tools=[add],
    system_prompt="You are a calculator.",
    name="calc",
)
'''


@pytest.fixture(autouse=True)
def forget_project_modules() -> None:
    """Drop the tmp project's package and restore ``sys.path``."""
    original_path = list(sys.path)
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    yield
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    sys.path[:] = original_path


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand inside a project exposing ``src.agents:agent``."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "agents.py").write_text(_AGENT_MODULE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestTools:
    def test_lists_the_tools_without_calling_the_model(
        self,
        service: Path,
    ) -> None:
        """No token is spent: the backend here would answer '42' if called."""
        result = runner.invoke(app, ["agents", "tools"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "add" in result.stdout
        assert "42" not in result.stdout

    def test_json_output(self, service: Path) -> None:
        import json

        result = runner.invoke(app, ["agents", "tools", "--json"])
        assert result.exit_code == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload[0]["name"] == "add"

    def test_missing_agent_says_there_is_no_convention(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["agents", "tools"])
        assert result.exit_code == 2
        assert "--agent" in result.stderr


_FAKE_RUNS_MODULE = '''
"""Agents whose runs end in each of the two ways the CLI distinguishes."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Step:
    tool: str


@dataclass
class _Run:
    output: str
    succeeded: bool
    stop_reason: str
    steps: list[_Step] = field(default_factory=list)


class _Agent:
    def __init__(self, run: _Run) -> None:
        self._run = run
        self.tools: list[Any] = []

    async def run(self, goal: str) -> _Run:
        return self._run


completed = _Agent(
    _Run("the answer", True, "completed", [_Step("add"), _Step("final_answer")]),
)
truncated = _Agent(_Run("half an answer", False, "max_steps"))
'''


class TestRun:
    @pytest.fixture
    def runs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Stand inside a project exposing two pre-built runs."""
        (tmp_path / "fake_runs.py").write_text(_FAKE_RUNS_MODULE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_successful_run_prints_the_output(self, runs: Path) -> None:
        result = runner.invoke(
            app,
            ["agents", "run", "add 1 and 2", "--agent", "fake_runs:completed"],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "the answer" in result.stdout
        assert "completed" in result.stderr

    def test_budget_truncated_run_exits_one(self, runs: Path) -> None:
        """A truncated run still carries text; treating it as an answer is the bug."""
        result = runner.invoke(
            app,
            ["agents", "run", "add 1 and 2", "--agent", "fake_runs:truncated"],
        )
        assert result.exit_code == 1
        assert "half an answer" in result.stdout
        assert "max_steps" in result.stderr

    def test_trace_lists_the_steps(self, runs: Path) -> None:
        result = runner.invoke(
            app,
            [
                "agents",
                "run",
                "add 1 and 2",
                "--agent",
                "fake_runs:completed",
                "--trace",
            ],
        )
        assert "[1] add" in result.stderr
        assert "[2] final_answer" in result.stderr
