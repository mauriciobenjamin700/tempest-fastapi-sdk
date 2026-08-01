"""Tests for delegation and the autonomous loops."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    AgentTool,
    LoopResult,
    StepKind,
    StopReason,
    ToolResult,
    agent_tool,
    refine,
    run_until,
    succeeded,
    team_tools,
    text_tool,
)
from tempest_fastapi_sdk.agents.schemas import AgentArtifact, AgentRun


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": arguments}}


class Scripted:
    """Replays fixed replies; repeats the last one once exhausted."""

    def __init__(self, replies: list[dict[str, Any]], *, repeat: bool = False) -> None:
        self.replies = list(replies)
        self.repeat = repeat
        self.prompts: list[str] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if messages and messages[1]["role"] == "user":
            self.prompts.append(messages[1]["content"])
        if not self.replies:
            return {"content": "done", "tool_calls": []}
        if self.repeat and len(self.replies) == 1:
            return self.replies[0]
        return self.replies.pop(0)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        if messages and messages[1]["role"] == "user":
            self.prompts.append(messages[1]["content"])
        if not self.replies:
            return "done"
        reply = self.replies[0] if self.repeat else self.replies.pop(0)
        return str(reply.get("content", ""))


async def _echo(arguments: dict[str, Any], _context: AgentContext) -> str:
    return f"found:{arguments.get('text', '')}"


def _drawing_tool(filename: str = "out.png") -> AgentTool:
    async def handler(
        _arguments: dict[str, Any],
        _context: AgentContext,
    ) -> ToolResult:
        return ToolResult(
            text=f"drew {filename}",
            artifacts=[
                AgentArtifact(
                    name=filename,
                    media_type="image/png",
                    data=b"\x89PNG",
                )
            ],
        )

    return AgentTool(
        name="draw",
        description="Draw.",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )


def _specialist(name: str, answer: str = "specialist answer") -> Agent:
    return Agent(
        Scripted(
            [
                {"content": "", "tool_calls": [_call("echo", text="data")]},
                {"content": answer, "tool_calls": []},
            ],
        ),
        tools=[text_tool("echo", "Look up.", _echo)],
        name=name,
    )


class TestDelegation:
    @pytest.mark.asyncio
    async def test_child_answer_reaches_the_parent(self) -> None:
        child = _specialist("researcher", "PIX is instant payments")
        parent = Agent(
            Scripted(
                [
                    {
                        "content": "",
                        "tool_calls": [_call("ask_researcher", goal="what is PIX")],
                    },
                    {"content": "Brief written.", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child)],
            name="writer",
        )
        run = await parent.run("write a brief")
        assert run.output == "Brief written."
        delegation = next(s for s in run.steps if s.kind == StepKind.AGENT)
        assert "PIX is instant payments" in delegation.output

    @pytest.mark.asyncio
    async def test_child_trace_is_nested_not_lost(self) -> None:
        child = _specialist("researcher")
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_researcher", goal="x")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child)],
            name="writer",
        )
        run = await parent.run("go")
        delegation = next(s for s in run.steps if s.kind == StepKind.AGENT)
        assert delegation.agent == "researcher"
        assert [c.name for c in delegation.children] == ["chat", "echo", "chat"]
        assert delegation.total_steps == 4

    @pytest.mark.asyncio
    async def test_delegation_is_its_own_step_kind(self) -> None:
        child = _specialist("researcher")
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_researcher", goal="x")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child), text_tool("echo", "Echo.", _echo)],
            name="writer",
        )
        run = await parent.run("go")
        kinds = [s.kind for s in run.steps]
        assert StepKind.AGENT in kinds
        assert run.steps[1].kind == StepKind.AGENT

    @pytest.mark.asyncio
    async def test_child_artifacts_bubble_up_namespaced(self) -> None:
        child = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("draw")]},
                    {"content": "drawn", "tool_calls": []},
                ],
            ),
            tools=[_drawing_tool("chart.png")],
            name="illustrator",
        )
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_illustrator", goal="x")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child)],
            name="writer",
        )
        run = await parent.run("go")
        assert [a.name for a in run.artifacts] == ["illustrator/chart.png"]
        assert run.artifact("illustrator/chart.png").data == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_two_specialists_cannot_clobber_each_other(self) -> None:
        left = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("draw")]},
                    {"content": "ok", "tool_calls": []},
                ],
            ),
            tools=[_drawing_tool("report.png")],
            name="alpha",
        )
        right = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("draw")]},
                    {"content": "ok", "tool_calls": []},
                ],
            ),
            tools=[_drawing_tool("report.png")],
            name="beta",
        )
        parent = Agent(
            Scripted(
                [
                    {
                        "content": "",
                        "tool_calls": [
                            _call("ask_alpha", goal="x"),
                            _call("ask_beta", goal="y"),
                        ],
                    },
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=team_tools([left, right]),
            name="boss",
        )
        run = await parent.run("go")
        assert sorted(a.name for a in run.artifacts) == [
            "alpha/report.png",
            "beta/report.png",
        ]

    @pytest.mark.asyncio
    async def test_artifacts_can_be_kept_private(self) -> None:
        child = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("draw")]},
                    {"content": "ok", "tool_calls": []},
                ],
            ),
            tools=[_drawing_tool("secret.png")],
            name="worker",
        )
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_worker", goal="x")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child, share_artifacts=False)],
            name="boss",
        )
        run = await parent.run("go")
        assert run.artifacts == []

    @pytest.mark.asyncio
    async def test_a_truncated_child_is_flagged_not_hidden(self) -> None:
        child = Agent(
            Scripted([{"content": "partial", "tool_calls": [_call("echo", text="x")]}]),
            tools=[text_tool("echo", "Echo.", _echo)],
            budget=AgentBudget(max_steps=2, max_seconds=None),
            name="slowpoke",
        )
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_slowpoke", goal="x")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child)],
            name="boss",
        )
        run = await parent.run("go")
        delegation = next(s for s in run.steps if s.kind == StepKind.AGENT)
        assert "stopped: max_steps" in delegation.output

    @pytest.mark.asyncio
    async def test_missing_goal_is_a_tool_error(self) -> None:
        child = _specialist("researcher")
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_researcher")]},
                    {"content": "recovered", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child)],
            name="boss",
        )
        run = await parent.run("go")
        failed = next(s for s in run.steps if s.error)
        assert "goal" in failed.error
        assert run.succeeded is True

    def test_tool_name_and_description_default_from_the_agent(self) -> None:
        tool = agent_tool(_specialist("researcher"))
        assert tool.name == "ask_researcher"
        assert "researcher" in tool.description

    def test_team_tools_from_a_mapping_keeps_descriptions(self) -> None:
        alpha = _specialist("alpha")
        beta = _specialist("beta")
        tools = team_tools({alpha: "Does alpha things.", beta: "Does beta things."})
        assert [t.name for t in tools] == ["ask_alpha", "ask_beta"]
        assert tools[0].description == "Does alpha things."


class TestDepthGuard:
    @pytest.mark.asyncio
    async def test_recursion_is_refused_with_a_readable_reason(self) -> None:
        inner = _specialist("inner")
        middle = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_inner", goal="x")]},
                    {"content": "middle done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(inner, max_depth=1)],
            name="middle",
        )
        outer = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_middle", goal="x")]},
                    {"content": "outer done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(middle, max_depth=1)],
            name="outer",
        )
        run = await outer.run("go")
        delegation = next(s for s in run.steps if s.kind == StepKind.AGENT)
        nested_error = [c for c in delegation.children if c.error]
        assert nested_error
        assert "delegation refused" in nested_error[0].error

    @pytest.mark.asyncio
    async def test_depth_increases_down_the_chain(self) -> None:
        seen: list[int] = []

        async def probe(_arguments: dict[str, Any], context: AgentContext) -> str:
            seen.append(context.depth)
            return "ok"

        inner = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("probe", text="x")]},
                    {"content": "inner done", "tool_calls": []},
                ],
            ),
            tools=[text_tool("probe", "Probe.", probe)],
            name="inner",
        )
        outer = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_inner", goal="x")]},
                    {"content": "outer done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(inner), text_tool("probe", "Probe.", probe)],
            name="outer",
        )
        await outer.run("go")
        assert seen == [1]


class TestInheritedDeadline:
    @pytest.mark.asyncio
    async def test_a_child_cannot_outlive_its_parent(self) -> None:
        async def slow(_arguments: dict[str, Any], _context: AgentContext) -> str:
            await asyncio.sleep(0.05)
            return "slow"

        child = Agent(
            Scripted(
                [{"content": "", "tool_calls": [_call("slow", text="x")]}],
                repeat=True,
            ),
            tools=[text_tool("slow", "Slow.", slow)],
            budget=AgentBudget(max_steps=100, max_seconds=60.0),
            name="slowpoke",
        )
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_slowpoke", goal="x")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child)],
            budget=AgentBudget(max_steps=100, max_seconds=0.12),
            name="boss",
        )
        started = asyncio.get_running_loop().time()
        run = await parent.run("go")
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 5.0
        delegation = next(s for s in run.steps if s.kind == StepKind.AGENT)
        assert "stopped: timeout" in delegation.output

    @pytest.mark.asyncio
    async def test_the_shorter_clock_wins_even_when_it_is_the_child(self) -> None:
        async def slow(_arguments: dict[str, Any], _context: AgentContext) -> str:
            await asyncio.sleep(0.02)
            return "slow"

        child = Agent(
            Scripted(
                [{"content": "", "tool_calls": [_call("slow", text="x")]}],
                repeat=True,
            ),
            tools=[text_tool("slow", "Slow.", slow)],
            budget=AgentBudget(max_steps=100, max_seconds=0.05),
            name="quick",
        )
        parent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("ask_quick", goal="x")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(child)],
            budget=AgentBudget(max_steps=100, max_seconds=30.0),
            name="boss",
        )
        run = await parent.run("go")
        assert run.succeeded is True
        delegation = next(s for s in run.steps if s.kind == StepKind.AGENT)
        assert "stopped: timeout" in delegation.output


class TestRunUntil:
    @pytest.mark.asyncio
    async def test_stops_at_the_first_accepted_answer(self) -> None:
        agent = Agent(
            Scripted(
                [
                    {"content": "bad", "tool_calls": []},
                    {"content": "GOOD", "tool_calls": []},
                    {"content": "never reached", "tool_calls": []},
                ],
            ),
            name="w",
        )
        result = await run_until(
            agent,
            "produce something",
            until=lambda run: run.output == "GOOD",
            max_rounds=5,
        )
        assert result.accepted is True
        assert result.output == "GOOD"
        assert result.rounds == 2

    @pytest.mark.asyncio
    async def test_running_out_of_rounds_is_not_acceptance(self) -> None:
        agent = Agent(Scripted([{"content": "bad", "tool_calls": []}], repeat=True))
        result = await run_until(
            agent,
            "produce",
            until=lambda run: run.output == "GOOD",
            max_rounds=3,
        )
        assert result.accepted is False
        assert result.rounds == 3
        assert result.output == "bad"

    @pytest.mark.asyncio
    async def test_the_rejected_attempt_is_shown_to_the_next_round(self) -> None:
        backend = Scripted([{"content": "attempt", "tool_calls": []}], repeat=True)
        agent = Agent(backend)
        await run_until(
            agent,
            "the goal",
            until=lambda run: False,
            max_rounds=2,
        )
        assert backend.prompts[0] == "the goal"
        assert "attempt" in backend.prompts[1]
        assert "rejected" in backend.prompts[1]

    @pytest.mark.asyncio
    async def test_custom_feedback_builds_the_next_prompt(self) -> None:
        backend = Scripted([{"content": "x", "tool_calls": []}], repeat=True)
        agent = Agent(backend)
        await run_until(
            agent,
            "goal",
            until=lambda run: False,
            max_rounds=2,
            feedback=lambda run, index: f"round {index}: fix {run.output}",
        )
        assert backend.prompts[1] == "round 1: fix x"

    @pytest.mark.asyncio
    async def test_an_async_predicate_works(self) -> None:
        async def check(run: AgentRun) -> bool:
            await asyncio.sleep(0)
            return run.output == "ok"

        agent = Agent(Scripted([{"content": "ok", "tool_calls": []}]))
        result = await run_until(agent, "g", until=check)
        assert result.accepted is True

    @pytest.mark.asyncio
    async def test_succeeded_helper_rejects_truncated_work(self) -> None:
        agent = Agent(
            Scripted(
                [{"content": "partial", "tool_calls": [_call("echo", text="x")]}],
                repeat=True,
            ),
            tools=[text_tool("echo", "Echo.", _echo)],
            budget=AgentBudget(max_steps=2, max_seconds=None),
        )
        result = await run_until(agent, "g", until=succeeded, max_rounds=2)
        assert result.accepted is False
        assert result.final_run.stop_reason == StopReason.MAX_STEPS

    @pytest.mark.asyncio
    async def test_the_wall_clock_bounds_every_round_together(self) -> None:
        async def slow(_arguments: dict[str, Any], _context: AgentContext) -> str:
            await asyncio.sleep(0.03)
            return "slow"

        agent = Agent(
            Scripted(
                [{"content": "", "tool_calls": [_call("slow", text="x")]}],
                repeat=True,
            ),
            tools=[text_tool("slow", "Slow.", slow)],
            budget=AgentBudget(max_steps=100, max_seconds=None),
        )
        result = await run_until(
            agent,
            "g",
            until=lambda run: False,
            max_rounds=50,
            max_seconds=0.15,
        )
        assert result.accepted is False
        assert result.seconds < 3.0
        assert result.rounds < 50

    @pytest.mark.asyncio
    async def test_rejects_a_nonsense_ceiling(self) -> None:
        agent = Agent(Scripted([]))
        with pytest.raises(ValueError, match="max_rounds"):
            await run_until(agent, "g", until=lambda run: True, max_rounds=0)

    @pytest.mark.asyncio
    async def test_artifacts_come_from_the_accepted_round(self) -> None:
        agent = Agent(
            Scripted(
                [
                    {"content": "", "tool_calls": [_call("draw")]},
                    {"content": "GOOD", "tool_calls": []},
                ],
            ),
            tools=[_drawing_tool("final.png")],
        )
        result = await run_until(agent, "g", until=lambda run: run.output == "GOOD")
        assert [a.name for a in result.artifacts] == ["final.png"]


class TestRefine:
    @pytest.mark.asyncio
    async def test_approval_ends_the_loop(self) -> None:
        worker = Agent(Scripted([{"content": "draft one", "tool_calls": []}]))
        critic = Agent(Scripted([{"content": "APPROVED", "tool_calls": []}]))
        result = await refine(worker, critic, "write it")
        assert result.accepted is True
        assert result.output == "draft one"
        assert result.rounds == 1
        assert result.iterations[0].critique is None

    @pytest.mark.asyncio
    async def test_a_critique_drives_the_next_draft(self) -> None:
        worker_backend = Scripted(
            [
                {"content": "draft one", "tool_calls": []},
                {"content": "draft two", "tool_calls": []},
            ],
        )
        critic_backend = Scripted(
            [
                {"content": "Too vague about the deadline.", "tool_calls": []},
                {"content": "APPROVED", "tool_calls": []},
            ],
        )
        result = await refine(Agent(worker_backend), Agent(critic_backend), "write it")
        assert result.accepted is True
        assert result.output == "draft two"
        assert result.iterations[0].critique == "Too vague about the deadline."
        assert "Too vague about the deadline." in worker_backend.prompts[1]
        assert "draft one" in worker_backend.prompts[1]

    @pytest.mark.asyncio
    async def test_never_approved_returns_the_last_draft_unaccepted(self) -> None:
        worker = Agent(Scripted([{"content": "draft", "tool_calls": []}], repeat=True))
        critic = Agent(
            Scripted([{"content": "still wrong", "tool_calls": []}], repeat=True)
        )
        result = await refine(worker, critic, "write it", max_rounds=3)
        assert result.accepted is False
        assert result.rounds == 3
        assert result.output == "draft"

    @pytest.mark.asyncio
    async def test_approval_is_case_insensitive(self) -> None:
        worker = Agent(Scripted([{"content": "draft", "tool_calls": []}]))
        critic = Agent(Scripted([{"content": "approved", "tool_calls": []}]))
        result = await refine(worker, critic, "g")
        assert result.accepted is True

    @pytest.mark.asyncio
    async def test_the_critic_sees_the_goal_and_the_work(self) -> None:
        critic_backend = Scripted([{"content": "APPROVED", "tool_calls": []}])
        worker = Agent(Scripted([{"content": "the draft", "tool_calls": []}]))
        await refine(worker, Agent(critic_backend), "the goal")
        prompt = critic_backend.prompts[0]
        assert "the goal" in prompt
        assert "the draft" in prompt

    @pytest.mark.asyncio
    async def test_rejects_a_nonsense_ceiling(self) -> None:
        with pytest.raises(ValueError, match="max_rounds"):
            await refine(Agent(Scripted([])), Agent(Scripted([])), "g", max_rounds=0)


class TestLoopResult:
    def test_empty_result_is_coherent(self) -> None:
        result = LoopResult(goal="g")
        assert result.rounds == 0
        assert result.final_run is None
        assert result.artifacts == []
        assert result.accepted is False
