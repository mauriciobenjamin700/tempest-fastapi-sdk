"""Tests for the three agent memory layers."""

from __future__ import annotations

import hashlib
import logging
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
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
from tempest_fastapi_sdk.genai.rag import ChatMemory, MemoryHit


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


class _TableEmbedder:
    """Deterministic embedder: maps each text to a canned vector."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        """Keep the text-to-vector table.

        Args:
            table (dict[str, list[float]]): Vector per known text; anything
                else embeds to a near-zero vector that matches nothing.
        """
        self.table = table

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Return one canned vector per text.

        Args:
            texts (str | list[str]): One text or many.
            batch_size (int): Ignored.

        Returns:
            list[list[float]]: The vectors.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        return [list(self.table.get(text, [0.001, 0.001])) for text in items]


def _chat_memory(table: dict[str, list[float]]) -> ChatMemory:
    """Build a ``ChatMemory`` over a fresh in-process Chroma collection.

    Args:
        table (dict[str, list[float]]): The embedder's vectors.

    Returns:
        ChatMemory: The memory under test.
    """
    chromadb = pytest.importorskip("chromadb")
    client: Any = chromadb.EphemeralClient(
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    return ChatMemory(
        _TableEmbedder(table),
        client=client,
        collection_name=f"recall-{uuid.uuid4().hex}",
    )


RECALL_CONSUMER_SNIPPET: str = """
from typing import Any

from tempest_fastapi_sdk.agents import recall_prompt
from tempest_fastapi_sdk.genai.rag import ChatMemory, PgChatMemory


class OnlyRecall:
    async def recall(self, query: str, *, user_id: str, top_k: int = 4) -> list[Any]:
        return []


async def main(chroma: ChatMemory, pg: PgChatMemory) -> None:
    await recall_prompt(chroma, "goal", user_id="u1")
    await recall_prompt(pg, "goal", user_id="u1")
    await recall_prompt(OnlyRecall(), "goal", user_id="u1")
"""
"""Both shipped memories, and the ``recall``-shaped object the old docstring invited."""


class TestRecallPrompt:
    @pytest.mark.asyncio
    async def test_recalls_from_a_real_chat_memory(self) -> None:
        memory = _chat_memory(
            {
                "they prefer morning meetings": [1.0, 0.0],
                "schedule a call": [1.0, 0.0],
            }
        )
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="m1",
            role="user",
            content="they prefer morning meetings",
            created_at=datetime.now(UTC),
        )

        block = await recall_prompt(memory, "schedule a call", user_id="u1")

        assert "they prefer morning meetings" in block
        assert "Possibly relevant" in block

    @pytest.mark.asyncio
    async def test_no_hits_changes_nothing(self) -> None:
        memory = _chat_memory({"schedule a call": [1.0, 0.0]})

        assert await recall_prompt(memory, "schedule a call", user_id="u1") == ""

    @pytest.mark.asyncio
    async def test_passes_user_and_top_k_to_search(self) -> None:
        seen: list[tuple[str, str, int]] = []

        class Spy:
            async def search(
                self,
                *,
                user_id: str,
                query: str,
                top_k: int,
            ) -> list[MemoryHit]:
                seen.append((user_id, query, top_k))
                return []

        await recall_prompt(Spy(), "q", user_id="u7", top_k=2)

        assert seen == [("u7", "q", 2)]

    @pytest.mark.asyncio
    async def test_a_broken_backend_is_logged_not_silent(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class Broken:
            async def search(
                self,
                *,
                user_id: str,
                query: str,
                top_k: int,
            ) -> list[MemoryHit]:
                raise RuntimeError("vector store down")

        with caplog.at_level(
            logging.WARNING, logger="tempest_fastapi_sdk.agents.memory"
        ):
            assert await recall_prompt(Broken(), "q", user_id="u1") == ""

        assert any(
            record.levelno == logging.WARNING
            and "vector store down" in record.getMessage()
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_a_contract_error_is_not_swallowed(self) -> None:
        class OnlyRecall:
            async def recall(self, query: str, *, user_id: str) -> list[MemoryHit]:
                return []

        with pytest.raises(AttributeError):
            await recall_prompt(OnlyRecall(), "q", user_id="u1")  # type: ignore[arg-type]

    def test_mypy_accepts_both_memories_and_refuses_a_recall_object(
        self,
        tmp_path: Path,
    ) -> None:
        """``ChatMemory`` and ``PgChatMemory`` type-check; ``OnlyRecall`` does not.

        Before #834 the parameter was ``Any``, so mypy accepted all three —
        including the object whose ``recall`` the function then failed to
        find at runtime.

        Args:
            tmp_path (Path): pytest's per-test temporary directory.
        """
        mypy_api = pytest.importorskip(
            "mypy.api", reason="mypy is a dev-group dependency"
        )
        module = tmp_path / "consumer_recall.py"
        module.write_text(RECALL_CONSUMER_SNIPPET, encoding="utf-8")
        root: str = str(Path(__file__).resolve().parents[2])
        digest: str = hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]
        cache: Path = Path(tempfile.gettempdir()) / f"tempest-mypy-{digest}-recall"
        stdout, _stderr, _status = mypy_api.run(
            [
                str(module),
                "--strict",
                "--no-error-summary",
                "--hide-error-context",
                "--no-color-output",
                "--cache-dir",
                str(cache),
                "--python-executable",
                sys.executable,
            ]
        )
        errors = [line for line in stdout.splitlines() if ": error:" in line]
        assert len(errors) == 1, "\n".join(errors)
        assert "consumer_recall.py:16:" in errors[0]
        assert "[arg-type]" in errors[0]


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
