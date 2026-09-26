"""Regression tests for :func:`~tempest_fastapi_sdk.agents.run_structured`.

Each class pins a defect that shipped in the structured path: a copy of the
agent that dropped its skills, an answer tool that did not end the run, and
an answer that skipped moderation or survived a blocked or truncated run.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    Skill,
    StopReason,
    run_structured,
    text_tool,
)
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema


class Answer(BaseSchema):
    """The structured answer used across these tests."""

    headline: str


async def _echo(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Return the text argument."""
    return str(arguments.get("text", ""))


class _Verdict:
    """What a moderator returns."""

    def __init__(self, *, flagged: bool, labels: list[str]) -> None:
        """Store the verdict."""
        self.flagged = flagged
        self.labels = labels


class _WordModerator:
    """Flag any text containing a banned word."""

    def __init__(self, word: str) -> None:
        """Configure the banned word."""
        self.word = word

    async def check(self, text: str) -> _Verdict:
        """Flag text containing the banned word."""
        if self.word in text:
            return _Verdict(flagged=True, labels=["banned"])
        return _Verdict(flagged=False, labels=[])


class TestSkillsSurviveTheScopedCopy:
    @pytest.mark.asyncio
    async def test_a_skill_tool_is_callable_in_a_structured_run(self) -> None:
        skill = Skill(
            name="weather",
            description="Look up weather.",
            tools=[text_tool("forecast", "Forecast.", _echo)],
        )
        backend = ScriptedBackend(
            [
                replies_with_tool("load_skill", {"name": "weather"}),
                replies_with_tool("forecast", {"text": "sunny"}),
                replies_with_tool("final_answer", {"headline": "sunny"}),
            ],
        )
        run = await run_structured(Agent(backend, skills=[skill]), "go", Answer)
        forecast = next(step for step in run.steps if step.name == "forecast")
        assert forecast.error is None
        assert forecast.output == "sunny"
        assert run.data is not None


class TestFinalAnswerEndsTheRun:
    @pytest.mark.asyncio
    async def test_the_model_is_not_asked_again_after_final_answer(self) -> None:
        backend = ScriptedBackend(
            [
                replies_with_tool("final_answer", {"headline": "done"}),
                replies("an extra turn that should never happen"),
            ],
        )
        run = await run_structured(Agent(backend), "go", Answer)
        assert backend.calls == 1
        assert run.stop_reason == StopReason.COMPLETED
        assert run.data == Answer(headline="done")

    @pytest.mark.asyncio
    async def test_an_invalid_answer_keeps_the_loop_going(self) -> None:
        backend = ScriptedBackend(
            [
                replies_with_tool("final_answer", {}),
                replies_with_tool("final_answer", {"headline": "fixed"}),
            ],
        )
        run = await run_structured(Agent(backend), "go", Answer)
        assert backend.calls == 2
        assert run.data == Answer(headline="fixed")
        assert run.steps[1].error is not None
        assert "headline" in run.steps[1].error


class TestStructuredDataIsModerated:
    @pytest.mark.asyncio
    async def test_flagged_data_is_withheld(self) -> None:
        backend = ScriptedBackend(
            [replies_with_tool("final_answer", {"headline": "forbidden thing"})],
        )
        agent = Agent(backend, moderator=_WordModerator("forbidden"))  # type: ignore[arg-type]
        run = await run_structured(agent, "go", Answer)
        assert run.stop_reason == StopReason.BLOCKED
        assert run.data is None
        assert run.parse_error is not None

    @pytest.mark.asyncio
    async def test_a_truncated_run_returns_no_data(self) -> None:
        backend = ScriptedBackend(
            [
                {
                    "content": '{"headline": "partial"}',
                    "tool_calls": [
                        {"function": {"name": "noop", "arguments": {"text": "x"}}},
                    ],
                },
            ],
            repeat_last=True,
        )
        looping = Agent(
            backend,
            tools=[text_tool("noop", "N.", _echo)],
            budget=AgentBudget(max_steps=2, max_seconds=None),
        )
        run = await run_structured(looping, "go", Answer)
        assert run.stop_reason == StopReason.MAX_STEPS
        assert run.output == '{"headline": "partial"}'
        assert run.data is None
        assert run.parse_error is not None
        assert "max_steps" in run.parse_error


class TestExtractionHonoursTheDeadline:
    @pytest.mark.asyncio
    async def test_the_extractor_inherits_the_callers_deadline(self) -> None:
        backend = ScriptedBackend(
            [
                replies("The headline is: ok"),
                replies_with_tool("final_answer", {"headline": "ok"}),
            ],
        )
        context = AgentContext(deadline=time.monotonic() + 30)
        seen: list[float | None] = []
        original = Agent.run

        async def spy(
            self: Agent,
            goal: str,
            *,
            context: AgentContext | None = None,
        ) -> Any:
            seen.append(context.deadline if context is not None else None)
            return await original(self, goal, context=context)

        Agent.run = spy  # type: ignore[method-assign]
        try:
            run = await run_structured(Agent(backend), "go", Answer, context=context)
        finally:
            Agent.run = original  # type: ignore[method-assign]
        assert run.data == Answer(headline="ok")
        assert len(seen) == 2
        assert seen[1] is not None
        assert context.deadline is not None
        assert seen[1] <= context.deadline
