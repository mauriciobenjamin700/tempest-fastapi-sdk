"""Regression tests for the loop's ceilings and its tool-call plumbing.

Each class pins one defect that shipped: a ceiling that a hung call or a
many-call turn walked straight past, a wire format the loop misread, or a
failure path that leaked or crashed.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    AgentToolError,
    StepKind,
    StopReason,
    ToolResult,
    agent_tool,
    text_tool,
)
from tempest_fastapi_sdk.agents.schemas import AgentArtifact
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    replies_with_tools,
    tool_call,
)


async def _sleep_forever(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Hang far longer than any budget under test."""
    await asyncio.sleep(3)
    return "woke up"


async def _echo(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Return the text argument unchanged."""
    return str(arguments.get("text", ""))


class _HangingBackend:
    """A backend whose model call never returns in time."""

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Hang for three seconds."""
        await asyncio.sleep(3)
        return {"content": "late", "tool_calls": []}

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        """Hang for three seconds."""
        await asyncio.sleep(3)
        return "late"


class TestDeadlineInterruptsHungCalls:
    @pytest.mark.asyncio
    async def test_a_hung_tool_is_cut_at_the_deadline(self) -> None:
        backend = ScriptedBackend(
            [replies_with_tool("slow", {"text": "x"}), replies("never")],
        )
        agent = Agent(
            backend,
            tools=[text_tool("slow", "Slow.", _sleep_forever)],
            budget=AgentBudget(max_seconds=0.3),
        )
        started = time.monotonic()
        run = await agent.run("go")
        assert time.monotonic() - started < 1.5
        assert run.stop_reason == StopReason.TIMEOUT
        tool_step = run.steps[-1]
        assert tool_step.kind == StepKind.TOOL
        assert tool_step.error is not None
        assert "time budget" in tool_step.error

    @pytest.mark.asyncio
    async def test_a_hung_model_call_is_cut_at_the_deadline(self) -> None:
        agent = Agent(
            _HangingBackend(),
            tools=[text_tool("echo", "Echo.", _echo)],
            budget=AgentBudget(max_seconds=0.3),
        )
        started = time.monotonic()
        run = await agent.run("go")
        assert time.monotonic() - started < 1.5
        assert run.stop_reason == StopReason.TIMEOUT
        assert run.steps[-1].kind == StepKind.MODEL

    @pytest.mark.asyncio
    async def test_a_delegated_child_is_cut_by_the_parent_deadline(self) -> None:
        child = Agent(
            ScriptedBackend(
                [replies_with_tool("slow", {"text": "x"}), replies("never")],
            ),
            tools=[text_tool("slow", "Slow.", _sleep_forever)],
            budget=AgentBudget(max_seconds=60),
            name="child",
        )
        parent = Agent(
            ScriptedBackend(
                [replies_with_tool("ask_child", {"goal": "work"}), replies("x")],
            ),
            tools=[agent_tool(child)],
            budget=AgentBudget(max_seconds=0.3),
            name="parent",
        )
        started = time.monotonic()
        run = await parent.run("go")
        assert time.monotonic() - started < 1.5
        assert run.stop_reason == StopReason.TIMEOUT

    @pytest.mark.asyncio
    async def test_a_tool_raising_its_own_timeout_is_an_ordinary_failure(
        self,
    ) -> None:
        async def raises_timeout(
            _arguments: dict[str, Any],
            _ctx: AgentContext,
        ) -> str:
            raise TimeoutError("upstream took too long")

        backend = ScriptedBackend(
            [replies_with_tool("t", {"text": "x"}), replies("recovered")],
        )
        run = await Agent(
            backend,
            tools=[text_tool("t", "T.", raises_timeout)],
            budget=AgentBudget(max_seconds=30),
        ).run("go")
        assert run.stop_reason == StopReason.COMPLETED
        assert run.output == "recovered"


class TestBudgetInsideOneTurn:
    @pytest.mark.asyncio
    async def test_max_tool_calls_holds_inside_a_many_call_turn(self) -> None:
        executed: list[str] = []

        async def count(arguments: dict[str, Any], _ctx: AgentContext) -> str:
            executed.append(str(arguments.get("text")))
            return "ok"

        calls = [tool_call("count", {"text": str(i)}) for i in range(50)]
        backend = ScriptedBackend([replies_with_tools(*calls), replies("done")])
        run = await Agent(
            backend,
            tools=[text_tool("count", "Count.", count)],
            budget=AgentBudget(max_steps=5, max_tool_calls=2, max_seconds=None),
        ).run("go")
        assert len(executed) == 2
        assert run.stop_reason == StopReason.MAX_TOOL_CALLS
        assert len(run.tool_calls) == 2

    @pytest.mark.asyncio
    async def test_max_steps_holds_inside_a_many_call_turn(self) -> None:
        executed: list[str] = []

        async def count(arguments: dict[str, Any], _ctx: AgentContext) -> str:
            executed.append(str(arguments.get("text")))
            return "ok"

        calls = [tool_call("count", {"text": str(i)}) for i in range(50)]
        backend = ScriptedBackend([replies_with_tools(*calls), replies("done")])
        run = await Agent(
            backend,
            tools=[text_tool("count", "Count.", count)],
            budget=AgentBudget(max_steps=5, max_seconds=None),
        ).run("go")
        assert len(run.steps) == 5
        assert len(executed) == 4
        assert run.stop_reason == StopReason.MAX_STEPS


class TestArgumentWireFormats:
    @pytest.mark.asyncio
    async def test_json_string_arguments_are_parsed(self) -> None:
        backend = ScriptedBackend(
            [
                replies_with_tools(
                    {"function": {"name": "echo", "arguments": '{"text": "hi"}'}},
                ),
                replies("done"),
            ],
        )
        run = await Agent(backend, tools=[text_tool("echo", "E.", _echo)]).run("go")
        step = run.steps[1]
        assert step.error is None
        assert step.arguments == {"text": "hi"}
        assert step.output == "hi"

    @pytest.mark.asyncio
    async def test_invalid_json_arguments_are_a_tool_error(self) -> None:
        seen: list[dict[str, Any]] = []

        async def record(arguments: dict[str, Any], _ctx: AgentContext) -> str:
            seen.append(arguments)
            return "ran"

        backend = ScriptedBackend(
            [
                replies_with_tools(
                    {"function": {"name": "rec", "arguments": '{"text": '}},
                ),
                replies("done"),
            ],
        )
        run = await Agent(backend, tools=[text_tool("rec", "R.", record)]).run("go")
        step = run.steps[1]
        assert seen == []
        assert step.error is not None
        assert "JSON" in step.error

    @pytest.mark.asyncio
    async def test_a_json_array_is_not_an_argument_object(self) -> None:
        backend = ScriptedBackend(
            [
                replies_with_tools({"function": {"name": "echo", "arguments": "[1]"}}),
                replies("done"),
            ],
        )
        run = await Agent(backend, tools=[text_tool("echo", "E.", _echo)]).run("go")
        assert run.steps[1].error is not None


class _RecordingBackend(ScriptedBackend):
    """A scripted backend that also keeps every message list it was sent."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        """Configure the script."""
        super().__init__(script)
        self.transcripts: list[list[dict[str, Any]]] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record the messages, then answer from the script."""
        self.transcripts.append([dict(message) for message in messages])
        return await super().chat_with_tools(messages, tools, **kwargs)


class TestToolMessageShape:
    @pytest.mark.asyncio
    async def test_tool_message_carries_the_call_id_and_name(self) -> None:
        call = {"id": "call_1", "function": {"name": "echo", "arguments": {}}}
        backend = _RecordingBackend([replies_with_tools(call), replies("done")])
        await Agent(backend, tools=[text_tool("echo", "E.", _echo)]).run("go")
        tool_message = backend.transcripts[1][-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "call_1"
        assert tool_message["name"] == "echo"

    @pytest.mark.asyncio
    async def test_a_call_without_an_id_keeps_the_plain_shape(self) -> None:
        backend = _RecordingBackend(
            [replies_with_tool("echo", {"text": "x"}), replies("done")],
        )
        await Agent(backend, tools=[text_tool("echo", "E.", _echo)]).run("go")
        tool_message = backend.transcripts[1][-1]
        assert set(tool_message) == {"role", "content"}


class TestUnexpectedToolErrorsStayOffTheTrace:
    @pytest.mark.asyncio
    async def test_raw_exception_text_does_not_reach_the_trace(self) -> None:
        secret = "postgresql://admin:hunter2@db:5432/app"

        async def leaky(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            raise RuntimeError(f"could not connect to {secret}")

        backend = _RecordingBackend(
            [replies_with_tool("db", {"text": "x"}), replies("done")],
        )
        run = await Agent(backend, tools=[text_tool("db", "DB.", leaky)]).run("go")
        failed = run.steps[1]
        assert failed.error is not None
        assert "RuntimeError" in failed.error
        assert "hunter2" not in failed.error
        assert "hunter2" not in run.model_dump_json()
        observation = backend.transcripts[1][-1]["content"]
        assert "could not connect" in observation

    @pytest.mark.asyncio
    async def test_agent_tool_error_text_is_kept(self) -> None:
        async def deliberate(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            raise AgentToolError("disk is full")

        backend = ScriptedBackend(
            [replies_with_tool("save", {"text": "x"}), replies("done")],
        )
        run = await Agent(backend, tools=[text_tool("save", "S.", deliberate)]).run(
            "go",
        )
        assert run.steps[1].error == "AgentToolError: disk is full"

    @pytest.mark.asyncio
    async def test_expose_tool_errors_restores_the_full_text(self) -> None:
        async def leaky(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            raise RuntimeError("verbose detail")

        backend = ScriptedBackend(
            [replies_with_tool("t", {"text": "x"}), replies("done")],
        )
        run = await Agent(
            backend,
            tools=[text_tool("t", "T.", leaky)],
            expose_tool_errors=True,
        ).run("go")
        assert run.steps[1].error == "RuntimeError: verbose detail"


class _RaisingModerator:
    """A moderator whose backend is down."""

    async def check(self, text: str) -> Any:
        """Raise, as a moderation endpoint outage would."""
        raise ConnectionError("moderation endpoint unreachable")


class TestModeratorFailureFailsClosed:
    @pytest.mark.asyncio
    async def test_a_raising_moderator_blocks_instead_of_crashing(self) -> None:
        agent = Agent(
            ScriptedBackend([replies("answer")]),
            moderator=_RaisingModerator(),  # type: ignore[arg-type]
        )
        run = await agent.run("go")
        assert run.stop_reason == StopReason.BLOCKED
        assert "moderation" in run.output
        assert "unreachable" not in run.output


class TestFinalTool:
    @pytest.mark.asyncio
    async def test_a_final_tool_result_ends_the_run(self) -> None:
        async def submit(_arguments: dict[str, Any], _ctx: AgentContext) -> ToolResult:
            return ToolResult(text="submitted", final=True)

        backend = ScriptedBackend(
            [replies_with_tool("submit", {"text": "x"}), replies("extra turn")],
        )
        run = await Agent(backend, tools=[text_tool("submit", "S.", submit)]).run(
            "go",
        )
        assert backend.calls == 1
        assert run.stop_reason == StopReason.COMPLETED
        assert run.output == "submitted"


class TestArtifactCollisions:
    @pytest.mark.asyncio
    async def test_a_tool_cannot_overwrite_an_input_artifact(self) -> None:
        async def clobber(_arguments: dict[str, Any], _ctx: AgentContext) -> ToolResult:
            return ToolResult(
                text="wrote input.png",
                artifacts=[
                    AgentArtifact(
                        name="input.png", media_type="image/png", data=b"new"
                    ),
                ],
            )

        context = AgentContext()
        context.artifacts["input.png"] = AgentArtifact(
            name="input.png",
            media_type="image/png",
            data=b"original",
        )
        backend = ScriptedBackend(
            [replies_with_tool("clobber", {"text": "x"}), replies("done")],
        )
        run = await Agent(backend, tools=[text_tool("clobber", "C.", clobber)]).run(
            "go",
            context=context,
        )
        original = run.artifact("input.png")
        assert original is not None
        assert original.data == b"original"
        renamed = run.steps[1].artifacts
        assert renamed != ["input.png"]
        assert len(renamed) == 1
        stored = run.artifact(renamed[0])
        assert stored is not None
        assert stored.data == b"new"
        assert renamed[0] in run.steps[1].output


class TestDelegationsCountAsToolCalls:
    @pytest.mark.asyncio
    async def test_tool_calls_lists_delegations(self) -> None:
        child = Agent(ScriptedBackend([replies("child answer")]), name="child")
        parent = Agent(
            ScriptedBackend(
                [replies_with_tool("ask_child", {"goal": "work"}), replies("ok")],
            ),
            tools=[agent_tool(child)],
            name="parent",
        )
        run = await parent.run("go")
        assert run.steps[1].kind == StepKind.AGENT
        assert run.tool_calls == ["ask_child"]


class TestProtocolCallShape:
    @pytest.mark.asyncio
    async def test_backend_without_kwargs_and_other_names_is_driven(self) -> None:
        class Minimal:
            async def chat_with_tools(
                self,
                history: list[dict[str, Any]],
                specs: list[dict[str, Any]],
            ) -> dict[str, Any]:
                return {"content": json.dumps(len(history)), "tool_calls": []}

            async def chat(self, history: list[dict[str, Any]]) -> str:
                return "plain"

        run = await Agent(Minimal(), tools=[text_tool("e", "E.", _echo)]).run("go")
        assert run.succeeded
