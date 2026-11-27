"""The AIChatPipeline tool loop runs a call the model wrote in its reply text.

Same failure as the agent's (#866): the right call, as JSON in ``content``,
with ``tool_calls`` empty. The pipeline must dispatch it to the registered
:class:`Tool` and loop, and must leave every other reply alone.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.genai import AIChatPipeline, Tool

WEATHER_CALL: str = '{"name": "get_weather", "arguments": {"city": "SP"}}'


class _TextCallingBackend:
    """A backend that answers ``chat_with_tools`` from a fixed script."""

    def __init__(self, contents: list[str]) -> None:
        """Store the scripted reply texts.

        Args:
            contents (list[str]): One ``content`` per call; ``tool_calls`` is
                always empty.
        """
        self.contents = list(contents)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        """Plain chat, never expected here.

        Args:
            messages (list[dict[str, Any]]): The conversation.

        Returns:
            str: A marker the tests would notice.
        """
        return "plain-chat"

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return the next scripted reply with no native tool call.

        Args:
            messages (list[dict[str, Any]]): The conversation.
            tools (list[dict[str, Any]]): The tool specs (ignored).

        Returns:
            dict[str, Any]: The reply.
        """
        self.seen.append([dict(message) for message in messages])
        return {"role": "assistant", "content": self.contents.pop(0), "tool_calls": []}


def _weather(seen: list[dict[str, Any]]) -> Tool:
    """Build a weather tool that records its arguments.

    Args:
        seen (list[dict[str, Any]]): Receives every call's arguments.

    Returns:
        Tool: The ``get_weather`` tool.
    """

    async def handler(arguments: dict[str, Any]) -> str:
        """Record the call.

        Args:
            arguments (dict[str, Any]): What the model passed.

        Returns:
            str: The observation.
        """
        seen.append(arguments)
        return "sunny"

    return Tool(
        name="get_weather",
        description="Get weather",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )


class TestPipelineTextToolCalls:
    """The tool loop reads a call written in ``content``."""

    async def test_json_call_in_content_runs_the_tool(self) -> None:
        """The tool runs and the loop asks the model again with the result."""
        seen: list[dict[str, Any]] = []
        backend = _TextCallingBackend([WEATHER_CALL, "It is sunny."])
        pipeline = AIChatPipeline(backend, tools=[_weather(seen)])

        result = await pipeline.respond(user_id="u1", chat_id="c1", content="weather?")

        assert seen == [{"city": "SP"}]
        assert result.tool_calls_made == ["get_weather"]
        assert result.reply == "It is sunny."
        assert backend.seen[1][-1] == {"role": "tool", "content": "sunny"}
        assert backend.seen[1][-2]["content"] == ""
        assert backend.seen[1][-2]["tool_calls"] == [
            {"function": {"name": "get_weather", "arguments": {"city": "SP"}}},
        ]

    async def test_fenced_list_of_calls_runs_each(self) -> None:
        """A fenced JSON list runs every call."""
        seen: list[dict[str, Any]] = []
        content = (
            "```json\n"
            '[{"name": "get_weather", "arguments": {"city": "SP"}},'
            ' {"name": "get_weather", "arguments": {"city": "RJ"}}]\n'
            "```"
        )
        backend = _TextCallingBackend([content, "Sunny in both."])
        pipeline = AIChatPipeline(backend, tools=[_weather(seen)])

        result = await pipeline.respond(user_id="u1", chat_id="c1", content="weather?")

        assert seen == [{"city": "SP"}, {"city": "RJ"}]
        assert result.reply == "Sunny in both."

    async def test_unknown_name_is_the_reply(self) -> None:
        """A JSON object naming an unregistered tool is returned as the reply."""
        seen: list[dict[str, Any]] = []
        content = '{"name": "Ana", "arguments": {}}'
        backend = _TextCallingBackend([content])
        pipeline = AIChatPipeline(backend, tools=[_weather(seen)])

        result = await pipeline.respond(user_id="u1", chat_id="c1", content="who?")

        assert seen == []
        assert result.reply == content
        assert result.tool_calls_made == []

    async def test_invalid_json_is_the_reply(self) -> None:
        """Content that does not parse is returned as the reply."""
        content = '{"name": "get_weather", "arguments": {'
        backend = _TextCallingBackend([content])
        pipeline = AIChatPipeline(backend, tools=[_weather([])])

        result = await pipeline.respond(user_id="u1", chat_id="c1", content="weather?")

        assert result.reply == content
        assert result.tool_calls_made == []

    async def test_disabled_keeps_the_text_as_the_reply(self) -> None:
        """``parse_text_tool_calls=False`` restores the previous behaviour."""
        seen: list[dict[str, Any]] = []
        backend = _TextCallingBackend([WEATHER_CALL])
        pipeline = AIChatPipeline(
            backend,
            tools=[_weather(seen)],
            parse_text_tool_calls=False,
        )

        result = await pipeline.respond(user_id="u1", chat_id="c1", content="weather?")

        assert seen == []
        assert result.reply == WEATHER_CALL
