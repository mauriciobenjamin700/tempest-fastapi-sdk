"""Tests for the three agent memory layers."""

from __future__ import annotations

from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    AgentToolError,
    Fact,
    FactStore,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
    recall_prompt,
    scratchpad,
    scratchpad_tools,
)


def _call(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"function": {"name": tool_name, "arguments": arguments or {}}}


class Scripted:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.replies:
            return {"content": "done", "tool_calls": []}
        return self.replies.pop(0)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        return "done"


def _tool(tools: list[Any], name: str) -> Any:
    return next(t for t in tools if t.name == name)


class TestScratchpad:
    @pytest.mark.asyncio
    async def test_write_then_read(self) -> None:
        tools = scratchpad_tools()
        context = AgentContext()
        await _tool(tools, "note_write").invoke(
            {"key": "total", "value": "1240.50"},
            context,
        )
        result = await _tool(tools, "note_read").invoke({"key": "total"}, context)
        assert result.text == "1240.50"

    @pytest.mark.asyncio
    async def test_listing_the_keys(self) -> None:
        tools = scratchpad_tools()
        context = AgentContext()
        await _tool(tools, "note_write").invoke({"key": "b", "value": "2"}, context)
        await _tool(tools, "note_write").invoke({"key": "a", "value": "1"}, context)
        result = await _tool(tools, "note_list").invoke({}, context)
        assert result.text == "a, b"

    @pytest.mark.asyncio
    async def test_empty_list_says_so(self) -> None:
        result = await _tool(scratchpad_tools(), "note_list").invoke(
            {},
            AgentContext(),
        )
        assert result.text == "No notes yet."

    @pytest.mark.asyncio
    async def test_an_unknown_note_lists_what_exists(self) -> None:
        tools = scratchpad_tools()
        context = AgentContext()
        await _tool(tools, "note_write").invoke({"key": "real", "value": "x"}, context)
        with pytest.raises(AgentToolError, match="real"):
            await _tool(tools, "note_read").invoke({"key": "ghost"}, context)

    @pytest.mark.asyncio
    async def test_a_missing_key_is_rejected(self) -> None:
        with pytest.raises(AgentToolError, match="key"):
            await _tool(scratchpad_tools(), "note_write").invoke(
                {"value": "x"},
                AgentContext(),
            )

    @pytest.mark.asyncio
    async def test_notes_do_not_survive_the_run(self) -> None:
        agent = Agent(
            Scripted(
                [
                    {
                        "content": "",
                        "tool_calls": [_call("note_write", {"key": "k", "value": "v"})],
                    },
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=scratchpad_tools(),
        )
        first = AgentContext()
        await agent.run("one", context=first)
        assert scratchpad(first) == {"k": "v"}
        assert scratchpad(AgentContext()) == {}

    def test_the_prefix_is_configurable(self) -> None:
        names = [tool.name for tool in scratchpad_tools(prefix="memo")]
        assert names == ["memo_write", "memo_read", "memo_list"]


class TestFactStore:
    @pytest.mark.asyncio
    async def test_put_get_roundtrip(self) -> None:
        store = InMemoryFactStore()
        written = await store.put("timezone", "America/Recife", subject="u1")
        assert isinstance(written, Fact)
        assert written.updated_at > 0
        found = await store.get("timezone", subject="u1")
        assert found.value == "America/Recife"

    @pytest.mark.asyncio
    async def test_subjects_are_isolated(self) -> None:
        store = InMemoryFactStore()
        await store.put("plan", "pro", subject="u1")
        await store.put("plan", "free", subject="u2")
        assert (await store.get("plan", subject="u1")).value == "pro"
        assert (await store.get("plan", subject="u2")).value == "free"

    @pytest.mark.asyncio
    async def test_writing_again_replaces(self) -> None:
        store = InMemoryFactStore()
        await store.put("plan", "free")
        await store.put("plan", "pro")
        assert (await store.get("plan")).value == "pro"
        assert len(await store.all()) == 1

    @pytest.mark.asyncio
    async def test_forget_reports_whether_it_existed(self) -> None:
        store = InMemoryFactStore()
        await store.put("plan", "pro")
        assert await store.forget("plan") is True
        assert await store.forget("plan") is False

    @pytest.mark.asyncio
    async def test_all_is_sorted_and_empty_is_a_list(self) -> None:
        store = InMemoryFactStore()
        await store.put("zebra", "z")
        await store.put("alpha", "a")
        assert [f.key for f in await store.all()] == ["alpha", "zebra"]
        assert await store.all(subject="nobody") == []

    def test_the_in_memory_store_satisfies_the_protocol(self) -> None:
        assert isinstance(InMemoryFactStore(), FactStore)


class TestFactTools:
    @pytest.mark.asyncio
    async def test_remember_then_recall(self) -> None:
        store = InMemoryFactStore()
        tools = fact_tools(store, subject="u1")
        await _tool(tools, "fact_remember").invoke(
            {"key": "timezone", "value": "America/Recife"},
            AgentContext(),
        )
        result = await _tool(tools, "fact_recall").invoke(
            {"key": "timezone"},
            AgentContext(),
        )
        assert result.text == "America/Recife"

    @pytest.mark.asyncio
    async def test_facts_survive_across_runs(self) -> None:
        store = InMemoryFactStore()
        agent = Agent(
            Scripted(
                [
                    {
                        "content": "",
                        "tool_calls": [
                            _call("fact_remember", {"key": "plan", "value": "pro"})
                        ],
                    },
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=fact_tools(store),
        )
        await agent.run("one", context=AgentContext())
        assert (await store.get("plan")).value == "pro"

    @pytest.mark.asyncio
    async def test_an_unknown_fact_lists_the_known_ones(self) -> None:
        store = InMemoryFactStore()
        await store.put("plan", "pro")
        tools = fact_tools(store)
        with pytest.raises(AgentToolError, match="plan"):
            await _tool(tools, "fact_recall").invoke({"key": "ghost"}, AgentContext())

    @pytest.mark.asyncio
    async def test_listing_renders_key_and_value(self) -> None:
        store = InMemoryFactStore()
        await store.put("plan", "pro")
        await store.put("timezone", "UTC")
        result = await _tool(fact_tools(store), "fact_list").invoke({}, AgentContext())
        assert "plan: pro" in result.text
        assert "timezone: UTC" in result.text

    @pytest.mark.asyncio
    async def test_forget_is_optional(self) -> None:
        names = [t.name for t in fact_tools(InMemoryFactStore(), allow_forget=False)]
        assert "fact_forget" not in names

    @pytest.mark.asyncio
    async def test_forgetting_something_absent_is_not_an_error(self) -> None:
        tools = fact_tools(InMemoryFactStore())
        result = await _tool(tools, "fact_forget").invoke(
            {"key": "ghost"},
            AgentContext(),
        )
        assert "Nothing was stored" in result.text

    @pytest.mark.asyncio
    async def test_both_key_and_value_are_required(self) -> None:
        tools = fact_tools(InMemoryFactStore())
        with pytest.raises(AgentToolError, match="required"):
            await _tool(tools, "fact_remember").invoke({"key": "x"}, AgentContext())


class TestFactsPrompt:
    @pytest.mark.asyncio
    async def test_renders_the_stored_facts(self) -> None:
        store = InMemoryFactStore()
        await store.put("timezone", "America/Recife", subject="u1")
        block = await facts_prompt(store, subject="u1")
        assert "- timezone: America/Recife" in block
        assert "What you already know" in block

    @pytest.mark.asyncio
    async def test_an_empty_store_changes_nothing(self) -> None:
        assert await facts_prompt(InMemoryFactStore()) == ""

    @pytest.mark.asyncio
    async def test_it_composes_into_a_system_prompt(self) -> None:
        store = InMemoryFactStore()
        await store.put("plan", "pro")
        agent = Agent(
            Scripted([{"content": "ok", "tool_calls": []}]),
            system_prompt="Base." + await facts_prompt(store),
            tools=fact_tools(store),
        )
        assert "plan: pro" in agent.system_prompt


class TestRecallPrompt:
    @pytest.mark.asyncio
    async def test_renders_the_hits(self) -> None:
        class Hit:
            def __init__(self, content: str) -> None:
                self.content = content

        class Memory:
            async def recall(
                self,
                query: str,
                *,
                user_id: str,
                top_k: int = 4,
            ) -> list[Hit]:
                return [Hit("they prefer morning meetings")]

        block = await recall_prompt(Memory(), "schedule a call", user_id="u1")
        assert "morning meetings" in block
        assert "Possibly relevant" in block

    @pytest.mark.asyncio
    async def test_no_hits_changes_nothing(self) -> None:
        class Empty:
            async def recall(self, query: str, **_kwargs: Any) -> list[Any]:
                return []

        assert await recall_prompt(Empty(), "q", user_id="u1") == ""

    @pytest.mark.asyncio
    async def test_a_broken_backend_never_stops_the_agent(self) -> None:
        class Broken:
            async def recall(self, query: str, **_kwargs: Any) -> list[Any]:
                raise RuntimeError("vector store down")

        assert await recall_prompt(Broken(), "q", user_id="u1") == ""


class TestLayersTogether:
    @pytest.mark.asyncio
    async def test_an_agent_can_carry_all_three(self) -> None:
        store = InMemoryFactStore()
        await store.put("plan", "pro")
        agent = Agent(
            Scripted(
                [
                    {
                        "content": "",
                        "tool_calls": [_call("note_write", {"key": "k", "value": "v"})],
                    },
                    {
                        "content": "",
                        "tool_calls": [_call("fact_recall", {"key": "plan"})],
                    },
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[*scratchpad_tools(), *fact_tools(store)],
            system_prompt="Base." + await facts_prompt(store),
        )
        context = AgentContext()
        run = await agent.run("go", context=context)
        assert run.tool_calls == ["note_write", "fact_recall"]
        assert scratchpad(context) == {"k": "v"}
        assert run.succeeded is True

    def test_the_two_layers_do_not_share_tool_names(self) -> None:
        notes = {tool.name for tool in scratchpad_tools()}
        facts = {tool.name for tool in fact_tools(InMemoryFactStore())}
        assert notes.isdisjoint(facts)
