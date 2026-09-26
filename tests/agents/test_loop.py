"""Regression tests for :func:`refine` and :func:`run_until`.

The critic's approval is an exact token, a worker that never finished is
not approvable, and a loop run inside a deadline cannot extend it.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    StopReason,
    refine,
    run_until,
    text_tool,
)
from tempest_fastapi_sdk.agents.schemas import AgentRun
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
)


async def _echo(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Return the text argument."""
    return str(arguments.get("text", ""))


class TestRefineApproval:
    @pytest.mark.asyncio
    async def test_a_prefix_is_not_approval(self) -> None:
        worker = Agent(ScriptedBackend([replies("draft")]), name="worker")
        critic = Agent(
            ScriptedBackend([replies("APPROVED? no — the intro is wrong")]),
            name="critic",
        )
        result = await refine(worker, critic, "write", max_rounds=1)
        assert result.accepted is False
        assert result.iterations[0].critique is not None

    @pytest.mark.asyncio
    async def test_the_exact_token_is_approval(self) -> None:
        worker = Agent(ScriptedBackend([replies("draft")]), name="worker")
        critic = Agent(ScriptedBackend([replies("  approved \n")]), name="critic")
        result = await refine(worker, critic, "write", max_rounds=1)
        assert result.accepted is True

    @pytest.mark.asyncio
    async def test_a_worker_that_did_not_finish_is_not_approved(self) -> None:
        worker = Agent(
            ScriptedBackend(
                [replies_with_tool("noop", {"text": "x"})],
                repeat_last=True,
            ),
            tools=[text_tool("noop", "N.", _echo)],
            budget=AgentBudget(max_steps=2, max_seconds=None),
            name="worker",
        )
        critic = Agent(ScriptedBackend([replies("APPROVED")]), name="critic")
        result = await refine(worker, critic, "write", max_rounds=1)
        assert result.iterations[0].run.stop_reason == StopReason.MAX_STEPS
        assert result.accepted is False


class TestRunUntilKeepsTheInheritedDeadline:
    @pytest.mark.asyncio
    async def test_the_callers_deadline_is_not_overwritten(self) -> None:
        inherited = time.monotonic() + 5
        context = AgentContext(deadline=inherited)
        seen: list[float | None] = []

        def accept(run: AgentRun) -> bool:
            seen.append(run.seconds)
            return True

        agent = Agent(ScriptedBackend([replies("ok")]))
        await run_until(agent, "go", until=accept, context=context)
        assert context.deadline is not None
        assert context.deadline <= inherited
