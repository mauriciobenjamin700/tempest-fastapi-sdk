"""Tests for the agent loop.

The backend is a scripted fake: each entry is one reply the model would
give, so a test states the model's plan up front and asserts what the
agent did with it. No model is loaded and nothing reaches the network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    AgentRun,
    AgentTool,
    AgentToolError,
    InMemoryAgentRunSink,
    StepKind,
    StopReason,
    ToolResult,
    text_tool,
)
from tempest_fastapi_sdk.agents.schemas import AgentArtifact


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    """Build one tool call the way a backend reports it."""
    return {"function": {"name": name, "arguments": arguments}}


class ScriptedBackend:
    """A tool-calling backend that replays a fixed list of replies."""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []
        self.specs_seen: list[list[dict[str, Any]]] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append([dict(message) for message in messages])
        self.specs_seen.append(specs)
        if not self.replies:
            return {"content": "done", "tool_calls": []}
        return self.replies.pop(0)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        self.calls.append([dict(message) for message in messages])
        if not self.replies:
            return "done"
        return str(self.replies.pop(0).get("content", ""))


class ToollessBackend:
    """A backend that only implements plain chat."""

    def __init__(self, reply: str = "plain answer") -> None:
        self.reply = reply
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        self.seen.append(messages)
        return self.reply


class ExplodingBackend:
    """A backend whose first call fails."""

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise RuntimeError("backend is down")

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        raise RuntimeError("backend is down")


async def _echo(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Return the text argument unchanged."""
    return f"echo:{arguments.get('text', '')}"


def _echo_tool() -> AgentTool:
    return text_tool("echo", "Echo the text back.", _echo)


def _drawing_tool(name: str = "draw") -> AgentTool:
    """A tool that registers a fake image artifact."""

    async def handler(
        arguments: dict[str, Any],
        _context: AgentContext,
    ) -> ToolResult:
        filename = str(arguments.get("filename", "out.png"))
        return ToolResult(
            text=f"drew {filename}",
            artifacts=[
                AgentArtifact(
                    name=filename,
                    media_type="image/png",
                    data=b"\x89PNG-fake",
                    description="a drawing",
                )
            ],
        )

    return AgentTool(
        name=name,
        description="Draw something.",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )


class TestPlainRun:
    @pytest.mark.asyncio
    async def test_answers_without_tools(self) -> None:
        backend = ScriptedBackend([{"content": "42", "tool_calls": []}])
        run = await Agent(backend, tools=[_echo_tool()]).run("What is 6*7?")
        assert run.output == "42"
        assert run.stop_reason == StopReason.COMPLETED
        assert run.succeeded is True
        assert run.tool_calls == []

    @pytest.mark.asyncio
    async def test_toolless_backend_still_answers(self) -> None:
        backend = ToollessBackend("plain answer")
        run = await Agent(backend, tools=[_echo_tool()]).run("hi")
        assert run.output == "plain answer"
        assert run.succeeded is True

    @pytest.mark.asyncio
    async def test_goal_and_system_prompt_reach_the_backend(self) -> None:
        backend = ScriptedBackend([{"content": "ok", "tool_calls": []}])
        agent = Agent(backend, tools=[_echo_tool()], system_prompt="BE BRIEF")
        await agent.run("the goal")
        first = backend.calls[0]
        assert first[0] == {"role": "system", "content": "BE BRIEF"}
        assert first[1] == {"role": "user", "content": "the goal"}

    @pytest.mark.asyncio
    async def test_tool_specs_are_sent(self) -> None:
        backend = ScriptedBackend([{"content": "ok", "tool_calls": []}])
        await Agent(backend, tools=[_echo_tool()]).run("hi")
        spec = backend.specs_seen[0][0]
        assert spec["type"] == "function"
        assert spec["function"]["name"] == "echo"


class TestToolLoop:
    @pytest.mark.asyncio
    async def test_runs_the_tool_and_feeds_the_result_back(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("echo", text="hi")]},
                {"content": "the tool said hi", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_echo_tool()]).run("use echo")
        assert run.tool_calls == ["echo"]
        assert run.output == "the tool said hi"
        second_turn = backend.calls[1]
        assert second_turn[-1] == {"role": "tool", "content": "echo:hi"}

    @pytest.mark.asyncio
    async def test_trace_records_arguments_and_timing(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("echo", text="hi")]},
                {"content": "done", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_echo_tool()]).run("go")
        tool_step = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert tool_step.arguments == {"text": "hi"}
        assert tool_step.output == "echo:hi"
        assert tool_step.error is None
        assert tool_step.seconds >= 0.0
        assert [step.index for step in run.steps] == list(range(len(run.steps)))

    @pytest.mark.asyncio
    async def test_several_calls_in_one_turn(self) -> None:
        backend = ScriptedBackend(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call("echo", text="a"),
                        _call("echo", text="b"),
                    ],
                },
                {"content": "both done", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_echo_tool()]).run("go")
        assert run.tool_calls == ["echo", "echo"]
        assert run.output == "both done"


class TestToolFailures:
    @pytest.mark.asyncio
    async def test_unknown_tool_does_not_end_the_run(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("nope")]},
                {"content": "recovered", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_echo_tool()]).run("go")
        assert run.output == "recovered"
        assert run.succeeded is True
        failed = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert failed.error is not None
        assert "unknown tool" in failed.error

    @pytest.mark.asyncio
    async def test_unknown_tool_error_lists_what_exists(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("nope")]},
                {"content": "ok", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_echo_tool()]).run("go")
        failed = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert "echo" in (failed.error or "")

    @pytest.mark.asyncio
    async def test_raising_handler_is_reported_to_the_model(self) -> None:
        async def boom(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            raise AgentToolError("disk is full")

        tool = text_tool("save", "Save something.", boom)
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("save", text="x")]},
                {"content": "gave up on saving", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[tool]).run("go")
        assert run.succeeded is True
        failed = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert failed.error == "AgentToolError: disk is full"
        assert backend.calls[1][-1]["content"] == "AgentToolError: disk is full"

    @pytest.mark.asyncio
    async def test_arbitrary_exception_is_caught_too(self) -> None:
        async def boom(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            raise ZeroDivisionError("division by zero")

        tool = text_tool("calc", "Calculate.", boom)
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("calc", text="1/0")]},
                {"content": "cannot divide", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[tool]).run("go")
        failed = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert "ZeroDivisionError" in (failed.error or "")
        assert run.succeeded is True

    @pytest.mark.asyncio
    async def test_backend_failure_ends_the_run_as_error(self) -> None:
        run = await Agent(ExplodingBackend(), tools=[_echo_tool()]).run("go")
        assert run.stop_reason == StopReason.ERROR
        assert run.succeeded is False
        assert "backend is down" in run.output

    @pytest.mark.asyncio
    async def test_non_dict_arguments_become_empty(self) -> None:
        backend = ScriptedBackend(
            [
                {
                    "content": "",
                    "tool_calls": [{"function": {"name": "echo", "arguments": "oops"}}],
                },
                {"content": "ok", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_echo_tool()]).run("go")
        tool_step = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert tool_step.arguments == {}
        assert tool_step.output == "echo:"


class TestBudget:
    @pytest.mark.asyncio
    async def test_max_steps_stops_and_says_so(self) -> None:
        backend = ScriptedBackend(
            [{"content": "", "tool_calls": [_call("echo", text="x")]}] * 20,
        )
        agent = Agent(
            backend,
            tools=[_echo_tool()],
            budget=AgentBudget(max_steps=4, max_seconds=None),
        )
        run = await agent.run("loop forever")
        assert run.stop_reason == StopReason.MAX_STEPS
        assert run.succeeded is False
        assert len(run.steps) == 4

    @pytest.mark.asyncio
    async def test_max_tool_calls_stops_the_run(self) -> None:
        backend = ScriptedBackend(
            [{"content": "", "tool_calls": [_call("echo", text="x")]}] * 20,
        )
        agent = Agent(
            backend,
            tools=[_echo_tool()],
            budget=AgentBudget(max_steps=100, max_tool_calls=2, max_seconds=None),
        )
        run = await agent.run("loop forever")
        assert run.stop_reason == StopReason.MAX_TOOL_CALLS
        assert len(run.tool_calls) == 2

    @pytest.mark.asyncio
    async def test_timeout_stops_the_run(self) -> None:
        async def slow(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            await asyncio.sleep(0.05)
            return "slow"

        tool = text_tool("slow", "Take a while.", slow)
        backend = ScriptedBackend(
            [{"content": "", "tool_calls": [_call("slow", text="x")]}] * 20,
        )
        agent = Agent(
            backend,
            tools=[tool],
            budget=AgentBudget(max_steps=100, max_seconds=0.08),
        )
        run = await agent.run("loop")
        assert run.stop_reason == StopReason.TIMEOUT
        assert run.succeeded is False

    @pytest.mark.asyncio
    async def test_truncated_run_keeps_the_last_model_text(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "working on it", "tool_calls": [_call("echo", text="x")]},
            ]
            * 20,
        )
        agent = Agent(
            backend,
            tools=[_echo_tool()],
            budget=AgentBudget(max_steps=2, max_seconds=None),
        )
        run = await agent.run("go")
        assert run.output == "working on it"
        assert run.succeeded is False

    def test_budget_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            AgentBudget(max_steps=0)


class TestArtifacts:
    @pytest.mark.asyncio
    async def test_artifacts_are_collected_and_named(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("draw", filename="cat.png")]},
                {"content": "drew it", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_drawing_tool()]).run("draw a cat")
        assert [artifact.name for artifact in run.artifacts] == ["cat.png"]
        assert run.artifact("cat.png") is not None
        assert run.artifact("cat.png").data == b"\x89PNG-fake"
        assert run.artifact("missing.png") is None

    @pytest.mark.asyncio
    async def test_step_lists_the_artifacts_it_made(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("draw", filename="a.png")]},
                {"content": "ok", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_drawing_tool()]).run("go")
        tool_step = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert tool_step.artifacts == ["a.png"]

    @pytest.mark.asyncio
    async def test_a_later_tool_reads_an_earlier_artifact(self) -> None:
        seen: list[bytes] = []

        async def inspect(
            arguments: dict[str, Any],
            context: AgentContext,
        ) -> str:
            artifact = context.require_artifact(str(arguments["artifact"]))
            seen.append(artifact.data)
            return f"looked at {artifact.name}"

        inspector = AgentTool(
            name="inspect",
            description="Look at an artifact.",
            parameters={"type": "object", "properties": {}},
            handler=inspect,
        )
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("draw", filename="x.png")]},
                {"content": "", "tool_calls": [_call("inspect", artifact="x.png")]},
                {"content": "all done", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_drawing_tool(), inspector]).run("go")
        assert seen == [b"\x89PNG-fake"]
        assert run.tool_calls == ["draw", "inspect"]

    @pytest.mark.asyncio
    async def test_missing_artifact_names_what_exists(self) -> None:
        async def inspect(
            arguments: dict[str, Any],
            context: AgentContext,
        ) -> str:
            return context.require_artifact(str(arguments["artifact"])).name

        inspector = AgentTool(
            name="inspect",
            description="Look at an artifact.",
            parameters={"type": "object", "properties": {}},
            handler=inspect,
        )
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("draw", filename="real.png")]},
                {"content": "", "tool_calls": [_call("inspect", artifact="wrong.png")]},
                {"content": "ok", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[_drawing_tool(), inspector]).run("go")
        failed = next(s for s in run.steps if s.error)
        assert "wrong.png" in failed.error
        assert "real.png" in failed.error

    @pytest.mark.asyncio
    async def test_seeded_context_is_visible_to_tools(self) -> None:
        seeded = AgentContext()
        seeded.artifacts["input.wav"] = AgentArtifact(
            name="input.wav",
            media_type="audio/wav",
            data=b"RIFF",
        )

        async def inspect(
            arguments: dict[str, Any],
            context: AgentContext,
        ) -> str:
            return context.require_artifact("input.wav").media_type

        tool = AgentTool(
            name="inspect",
            description="Look.",
            parameters={"type": "object", "properties": {}},
            handler=inspect,
        )
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("inspect")]},
                {"content": "ok", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[tool]).run("go", context=seeded)
        tool_step = next(s for s in run.steps if s.kind == StepKind.TOOL)
        assert tool_step.output == "audio/wav"
        assert run.artifact("input.wav") is not None


class TestStreaming:
    @pytest.mark.asyncio
    async def test_yields_each_step_in_order(self) -> None:
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("echo", text="hi")]},
                {"content": "done", "tool_calls": []},
            ],
        )
        agent = Agent(backend, tools=[_echo_tool()])
        kinds = [step.kind async for step in agent.stream("go")]
        assert kinds == [StepKind.MODEL, StepKind.TOOL, StepKind.MODEL]

    @pytest.mark.asyncio
    async def test_streaming_records_the_run_at_the_end(self) -> None:
        sink = InMemoryAgentRunSink()
        backend = ScriptedBackend([{"content": "done", "tool_calls": []}])
        agent = Agent(backend, tools=[_echo_tool()], run_sink=sink)
        async for _step in agent.stream("go"):
            pass
        assert len(sink) == 1
        assert sink.recent()[0].output == "done"

    @pytest.mark.asyncio
    async def test_abandoning_the_stream_records_nothing(self) -> None:
        sink = InMemoryAgentRunSink()
        backend = ScriptedBackend(
            [
                {"content": "", "tool_calls": [_call("echo", text="hi")]},
                {"content": "done", "tool_calls": []},
            ],
        )
        agent = Agent(backend, tools=[_echo_tool()], run_sink=sink)
        stream = agent.stream("go")
        await stream.__anext__()
        await stream.aclose()
        assert len(sink) == 0


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_two_runs_on_one_agent_do_not_mix(self) -> None:
        class SlowBackend:
            async def chat_with_tools(
                self,
                messages: list[dict[str, Any]],
                specs: list[dict[str, Any]],
            ) -> dict[str, Any]:
                goal = messages[1]["content"]
                await asyncio.sleep(0.02 if goal == "first" else 0.0)
                return {"content": f"answer to {goal}", "tool_calls": []}

        agent = Agent(SlowBackend(), tools=[_echo_tool()])
        first, second = await asyncio.gather(
            agent.run("first"),
            agent.run("second"),
        )
        assert first.goal == "first"
        assert first.output == "answer to first"
        assert second.goal == "second"
        assert second.output == "answer to second"

    @pytest.mark.asyncio
    async def test_concurrent_artifacts_stay_separate(self) -> None:
        backend = ScriptedBackend([])

        class PerGoalBackend:
            async def chat_with_tools(
                self,
                messages: list[dict[str, Any]],
                specs: list[dict[str, Any]],
            ) -> dict[str, Any]:
                goal = messages[1]["content"]
                if not any(m.get("role") == "tool" for m in messages):
                    return {
                        "content": "",
                        "tool_calls": [_call("draw", filename=f"{goal}.png")],
                    }
                return {"content": f"done {goal}", "tool_calls": []}

        del backend
        agent = Agent(PerGoalBackend(), tools=[_drawing_tool()])
        left, right = await asyncio.gather(agent.run("a"), agent.run("b"))
        assert [item.name for item in left.artifacts] == ["a.png"]
        assert [item.name for item in right.artifacts] == ["b.png"]


class TestSinkAndMetrics:
    @pytest.mark.asyncio
    async def test_run_reaches_the_sink(self) -> None:
        sink = InMemoryAgentRunSink(max_runs=2)
        backend = ScriptedBackend([{"content": "a", "tool_calls": []}])
        agent = Agent(backend, run_sink=sink, name="worker")
        await agent.run("one")
        assert len(sink) == 1
        assert sink.recent()[0].agent == "worker"

    @pytest.mark.asyncio
    async def test_sink_failure_never_fails_the_run(self) -> None:
        async def broken(_run: AgentRun) -> None:
            raise RuntimeError("db down")

        backend = ScriptedBackend([{"content": "fine", "tool_calls": []}])
        run = await Agent(backend, run_sink=broken).run("go")
        assert run.output == "fine"
        assert run.succeeded is True

    @pytest.mark.asyncio
    async def test_metrics_failure_never_fails_the_run(self) -> None:
        class BrokenMetrics:
            def record(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("prometheus down")

        backend = ScriptedBackend([{"content": "fine", "tool_calls": []}])
        run = await Agent(backend, metrics=BrokenMetrics()).run("go")
        assert run.succeeded is True

    @pytest.mark.asyncio
    async def test_metrics_receive_the_duration(self) -> None:
        recorded: list[tuple[Any, ...]] = []

        class Metrics:
            def record(self, *args: Any) -> None:
                recorded.append(args)

        backend = ScriptedBackend([{"content": "fine", "tool_calls": []}])
        await Agent(backend, metrics=Metrics(), name="w").run("go")
        assert recorded[0][0] == "w"
        assert recorded[0][1] == "agent"


class TestModeration:
    @pytest.mark.asyncio
    async def test_a_blocked_goal_never_reaches_the_model(self) -> None:
        class Verdict:
            def __init__(self) -> None:
                self.flagged = True
                self.labels = ["toxicity"]

        class Moderator:
            async def check(self, _text: str) -> Verdict:
                return Verdict()

        backend = ScriptedBackend([{"content": "should not run", "tool_calls": []}])
        run = await Agent(backend, moderator=Moderator()).run("something bad")
        assert run.stop_reason == StopReason.BLOCKED
        assert run.succeeded is False
        assert "toxicity" in run.output
        assert backend.calls == []

    @pytest.mark.asyncio
    async def test_a_blocked_answer_is_replaced(self) -> None:
        class Verdict:
            def __init__(self, flagged: bool) -> None:
                self.flagged = flagged
                self.labels = ["policy"] if flagged else []

        class Moderator:
            def __init__(self) -> None:
                self.seen = 0

            async def check(self, _text: str) -> Verdict:
                self.seen += 1
                return Verdict(self.seen > 1)

        backend = ScriptedBackend([{"content": "bad answer", "tool_calls": []}])
        run = await Agent(backend, moderator=Moderator()).run("fine goal")
        assert run.stop_reason == StopReason.BLOCKED
        assert "bad answer" not in run.output


class TestInMemorySink:
    @pytest.mark.asyncio
    async def test_drops_the_oldest_past_the_cap(self) -> None:
        sink = InMemoryAgentRunSink(max_runs=2)
        for index in range(3):
            await sink(AgentRun(goal=f"g{index}", output=f"o{index}"))
        assert len(sink) == 2
        assert [run.goal for run in sink.recent()] == ["g2", "g1"]

    @pytest.mark.asyncio
    async def test_recent_honours_the_limit(self) -> None:
        sink = InMemoryAgentRunSink()
        for index in range(5):
            await sink(AgentRun(goal=f"g{index}"))
        assert len(sink.recent(2)) == 2

    @pytest.mark.asyncio
    async def test_clear_empties_it(self) -> None:
        sink = InMemoryAgentRunSink()
        await sink(AgentRun(goal="g"))
        sink.clear()
        assert len(sink) == 0

    def test_rejects_a_nonsense_cap(self) -> None:
        with pytest.raises(ValueError, match="max_runs"):
            InMemoryAgentRunSink(max_runs=0)
