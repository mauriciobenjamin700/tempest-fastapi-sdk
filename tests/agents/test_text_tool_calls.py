"""The agent runs a tool call the model wrote as JSON in its reply text.

Some models (``qwen2.5-coder:14b`` on Ollama, measured in #866) pick the
right tool and the right arguments but return the call as JSON in
``content`` with ``tool_calls`` empty. These tests script exactly that reply
and assert the tool ran, the loop continued and the trace says where the
call came from — and that every reply that is not a call to a registered
tool still ends the run as an answer.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    AgentTool,
    StepKindEnum,
    StopReasonEnum,
)
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

LOOKUP_CALL: str = '{"name": "lookup_order", "arguments": {"order_id": "PED-1042"}}'


def _lookup_tool(seen: list[dict[str, Any]]) -> AgentTool:
    """Build a tool that records each call's arguments.

    Args:
        seen (list[dict[str, Any]]): Receives the arguments of every call.

    Returns:
        AgentTool: The ``lookup_order`` tool.
    """

    async def handler(arguments: dict[str, Any], _ctx: AgentContext) -> str:
        """Record the call and answer with a fixed status.

        Args:
            arguments (dict[str, Any]): What the model passed.
            _ctx (AgentContext): The run context (unused).

        Returns:
            str: The observation.
        """
        seen.append(arguments)
        return f"order {arguments['order_id']}: shipped"

    return AgentTool(
        name="lookup_order",
        description="Look an order up by id.",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        handler=handler,
    )


class TestTextToolCalls:
    """A call written in ``content`` is run like a native one."""

    async def test_json_call_in_content_runs_the_tool(self) -> None:
        """The tool executes, its observation reaches the model, the loop goes on."""
        seen: list[dict[str, Any]] = []
        backend = ScriptedBackend([replies(LOOKUP_CALL), replies("It shipped.")])
        agent = Agent(backend, tools=[_lookup_tool(seen)])

        run = await agent.run("Where is PED-1042?")

        assert seen == [{"order_id": "PED-1042"}]
        assert run.stop_reason == StopReasonEnum.COMPLETED
        assert run.output == "It shipped."
        assert run.tool_calls == ["lookup_order"]
        assert backend.calls == 2
        assert backend.messages_seen[1][-1] == {
            "role": "tool",
            "content": "order PED-1042: shipped",
        }

    async def test_step_records_that_the_call_came_from_text(self) -> None:
        """The tool step carries ``from_text=True``; a native call does not."""
        backend = ScriptedBackend([replies(LOOKUP_CALL), replies("It shipped.")])
        agent = Agent(backend, tools=[_lookup_tool([])])

        run = await agent.run("Where is PED-1042?")

        tool_steps = [s for s in run.steps if s.kind == StepKindEnum.TOOL]
        assert [s.from_text for s in tool_steps] == [True]
        assert all(not s.from_text for s in run.steps if s.kind == StepKindEnum.MODEL)

    async def test_list_of_calls_runs_each_one(self) -> None:
        """A JSON list of calls runs every call, in order."""
        seen: list[dict[str, Any]] = []
        content = (
            '[{"name": "lookup_order", "arguments": {"order_id": "A"}},'
            ' {"name": "lookup_order", "arguments": "{\\"order_id\\": \\"B\\"}"}]'
        )
        backend = ScriptedBackend([replies(content), replies("Both shipped.")])
        agent = Agent(backend, tools=[_lookup_tool(seen)])

        run = await agent.run("Where are A and B?")

        assert seen == [{"order_id": "A"}, {"order_id": "B"}]
        assert run.tool_calls == ["lookup_order", "lookup_order"]
        assert run.output == "Both shipped."

    async def test_objects_separated_by_whitespace_run_each(self) -> None:
        """Two call objects on separate lines run as two calls."""
        seen: list[dict[str, Any]] = []
        content = (
            '{"name": "lookup_order", "arguments": {"order_id": "A"}}\n\n'
            '{"name": "lookup_order", "arguments": {"order_id": "B"}}'
        )
        backend = ScriptedBackend([replies(content), replies("Both shipped.")])
        agent = Agent(backend, tools=[_lookup_tool(seen)])

        await agent.run("Where are A and B?")

        assert seen == [{"order_id": "A"}, {"order_id": "B"}]

    async def test_echo_carries_the_call_not_the_text(self) -> None:
        """The assistant turn sent back holds the call in ``tool_calls``.

        Content stays empty, the shape a native call has: a chat template
        that prints ``content`` when present (qwen2.5-coder's on Ollama)
        would otherwise show the model its own bare JSON instead of the
        call it made.
        """
        backend = ScriptedBackend([replies(LOOKUP_CALL), replies("It shipped.")])
        agent = Agent(backend, tools=[_lookup_tool([])])

        await agent.run("Where is PED-1042?")

        echo = backend.messages_seen[1][-2]
        assert echo["role"] == "assistant"
        assert echo["content"] == ""
        assert echo["tool_calls"] == [
            {
                "function": {
                    "name": "lookup_order",
                    "arguments": {"order_id": "PED-1042"},
                }
            },
        ]

    async def test_prose_around_the_json_is_the_final_answer(self) -> None:
        """A call embedded in prose is not a call: the reply must be only JSON."""
        seen: list[dict[str, Any]] = []
        content = f"Vou consultar: {LOOKUP_CALL}"
        backend = ScriptedBackend([replies(content), replies("unreachable")])
        agent = Agent(backend, tools=[_lookup_tool(seen)])

        run = await agent.run("Where is PED-1042?")

        assert seen == []
        assert run.output == content

    async def test_call_inside_a_json_markdown_fence_runs(self) -> None:
        """A reply that is one ```json fence around the call is a call."""
        seen: list[dict[str, Any]] = []
        content = f"```json\n{LOOKUP_CALL}\n```"
        backend = ScriptedBackend([replies(content), replies("It shipped.")])
        agent = Agent(backend, tools=[_lookup_tool(seen)])

        run = await agent.run("Where is PED-1042?")

        assert seen == [{"order_id": "PED-1042"}]
        assert run.output == "It shipped."

    async def test_unknown_tool_name_is_the_final_answer(self) -> None:
        """A name that is no registered tool ends the run, running nothing."""
        seen: list[dict[str, Any]] = []
        content = '{"name": "Ana", "arguments": {"city": "Recife"}}'
        backend = ScriptedBackend([replies(content), replies("unreachable")])
        agent = Agent(backend, tools=[_lookup_tool(seen)])

        run = await agent.run("Return the person as JSON.")

        assert seen == []
        assert run.stop_reason == StopReasonEnum.COMPLETED
        assert run.output == content
        assert backend.calls == 1

    async def test_invalid_json_is_the_final_answer(self) -> None:
        """Content that does not parse as JSON ends the run as an answer."""
        seen: list[dict[str, Any]] = []
        content = '{"name": "lookup_order", "arguments": {"order_id": "PED-1042"}'
        backend = ScriptedBackend([replies(content), replies("unreachable")])
        agent = Agent(backend, tools=[_lookup_tool(seen)])

        run = await agent.run("Where is PED-1042?")

        assert seen == []
        assert run.output == content
        assert backend.calls == 1

    async def test_disabled_keeps_the_text_as_the_answer(self) -> None:
        """``parse_text_tool_calls=False`` restores the previous behaviour."""
        seen: list[dict[str, Any]] = []
        backend = ScriptedBackend([replies(LOOKUP_CALL), replies("unreachable")])
        agent = Agent(
            backend,
            tools=[_lookup_tool(seen)],
            parse_text_tool_calls=False,
        )

        run = await agent.run("Where is PED-1042?")

        assert seen == []
        assert run.output == LOOKUP_CALL
        assert backend.calls == 1
