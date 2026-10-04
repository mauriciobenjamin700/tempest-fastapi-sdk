"""Tests for the scripted-model helpers in ``agents.testing``."""

from __future__ import annotations

from typing import Any

import pytest

from tempest_fastapi_sdk.agents import Agent, AgentContext, text_tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
)


async def _echo(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Return the text argument unchanged."""
    return str(arguments.get("text", ""))


class TestMessagesSeen:
    @pytest.mark.asyncio
    async def test_each_call_keeps_the_conversation_it_received(self) -> None:
        backend = ScriptedBackend(
            [replies_with_tool("echo", {"text": "hi"}), replies("done")],
        )
        await Agent(
            backend,
            tools=[text_tool("echo", "E.", _echo)],
            system_prompt="S",
        ).run("go")
        first, second = backend.messages_seen
        assert [message["role"] for message in first] == ["system", "user"]
        assert [message["role"] for message in second] == [
            "system",
            "user",
            "assistant",
            "tool",
        ]
        assert first[0]["content"] == "S"
        assert second[-1]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_an_earlier_snapshot_does_not_grow_with_later_turns(self) -> None:
        backend = ScriptedBackend(
            [replies_with_tool("echo", {"text": "hi"}), replies("done")],
        )
        await Agent(backend, tools=[text_tool("echo", "E.", _echo)]).run("go")
        assert len(backend.messages_seen[0]) == 2
        assert len(backend.messages_seen) == backend.calls == 2

    @pytest.mark.asyncio
    async def test_a_snapshot_is_a_deep_copy(self) -> None:
        reply = replies_with_tool("echo", {"text": "hi"})
        backend = ScriptedBackend([reply, replies("done")])
        await Agent(backend, tools=[text_tool("echo", "E.", _echo)]).run("go")
        recorded = backend.messages_seen[1][2]["tool_calls"][0]
        assert recorded == reply["tool_calls"][0]
        assert recorded is not reply["tool_calls"][0]

    @pytest.mark.asyncio
    async def test_the_plain_chat_path_is_recorded_too(self) -> None:
        backend = ScriptedBackend([replies("answer")])
        await Agent(backend).run("go")
        assert backend.messages_seen == [
            [
                {"role": "system", "content": backend.system_prompts[0]},
                {"role": "user", "content": "go"},
            ],
        ]
