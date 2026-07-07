"""Tests for the AIChatPipeline orchestrator and its router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    AIChatResult,
    Tool,
    make_ai_chat_router,
)
from tempest_fastapi_sdk.genai.rag.chroma import MemoryHit
from tempest_fastapi_sdk.genai.rag.schemas import SearchResult


class FakeGenerator:
    """A scripted text backend recording every call it receives."""

    def __init__(
        self,
        *,
        reply: str = "final-reply",
        tool_scripts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.reply = reply
        self.tool_scripts = tool_scripts or []
        self.chat_calls: list[list[dict[str, Any]]] = []
        self.tool_calls: list[list[dict[str, Any]]] = []
        self.stream_calls: list[str] = []

    async def generate(self, prompt: str, *, config: Any = None) -> str:
        return f"gen:{prompt}"

    async def chat(
        self, messages: list[dict[str, Any]], *, config: Any = None
    ) -> str:
        self.chat_calls.append([dict(m) for m in messages])
        return self.reply

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        config: Any = None,
    ) -> dict[str, Any]:
        self.tool_calls.append([dict(m) for m in messages])
        index = len(self.tool_calls) - 1
        if index < len(self.tool_scripts):
            return self.tool_scripts[index]
        return {"role": "assistant", "content": self.reply}

    async def stream(self, prompt: str, *, config: Any = None) -> AsyncIterator[str]:
        self.stream_calls.append(prompt)
        for piece in ("Hel", "lo", "!"):
            yield piece


class FakeMemory:
    """A fake ChatMemory recording index calls and returning canned hits."""

    def __init__(self, hits: list[MemoryHit] | None = None) -> None:
        self.hits = hits or []
        self.indexed: list[dict[str, Any]] = []
        self.searched: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        user_id: Any,
        query: str,
        exclude_chat_id: Any = None,
        top_k: Any = None,
        min_similarity: Any = None,
    ) -> list[MemoryHit]:
        self.searched.append({"user_id": user_id, "query": query})
        return self.hits

    async def index(
        self,
        *,
        user_id: Any,
        chat_id: Any,
        message_id: Any,
        role: str,
        content: str,
        created_at: datetime,
    ) -> bool:
        self.indexed.append({"role": role, "content": content})
        return True


class FailingMemory(FakeMemory):
    """A memory whose index always raises, to test best-effort indexing."""

    async def index(self, **kwargs: Any) -> bool:
        raise RuntimeError("boom")


class FakeWebSearch:
    """A fake WebSearch returning canned context + sources."""

    def __init__(self) -> None:
        self.retrieved: list[str] = []
        self.searched: list[str] = []

    async def retrieve(self, query: str, **kwargs: Any) -> str:
        self.retrieved.append(query)
        return f"CTX for {query}"

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        self.searched.append(query)
        return [SearchResult(title="T", url="http://x", snippet="s")]


class FakeTTS:
    """A fake TextToSpeech returning deterministic bytes."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize(self, text: str, **kwargs: Any) -> bytes:
        self.spoken.append(text)
        return b"WAV:" + text.encode()


def _hit(content: str) -> MemoryHit:
    return MemoryHit(
        content=content,
        role="user",
        chat_id="old",
        similarity=0.9,
        score=0.9,
    )


class TestRespond:
    async def test_plain_respond(self) -> None:
        gen = FakeGenerator(reply="hi there")
        pipeline = AIChatPipeline(gen)
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="hello")
        assert isinstance(result, AIChatResult)
        assert result.reply == "hi there"
        assert result.sources == []
        assert result.memory_hits == []
        assert result.tool_calls_made == []
        assert result.audio_base64 is None
        # the user message is last
        assert gen.chat_calls[0][-1] == {"role": "user", "content": "hello"}

    async def test_memory_recall_and_indexing(self) -> None:
        gen = FakeGenerator(reply="answer")
        memory = FakeMemory(hits=[_hit("past fact")])
        pipeline = AIChatPipeline(gen, memory=memory, base_system_prompt="You are X.")
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="q?")

        assert result.memory_hits == [_hit("past fact")]
        # system message carries the base prompt + the recalled memory
        system = gen.chat_calls[0][0]
        assert system["role"] == "system"
        assert "You are X." in system["content"]
        assert "Relevant memory:" in system["content"]
        assert "past fact" in system["content"]
        # both sides of the turn were indexed
        assert [i["role"] for i in memory.indexed] == ["user", "assistant"]
        assert memory.indexed[0]["content"] == "q?"
        assert memory.indexed[1]["content"] == "answer"

    async def test_indexing_failure_is_swallowed(self) -> None:
        gen = FakeGenerator(reply="ok")
        pipeline = AIChatPipeline(gen, memory=FailingMemory())
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="hi")
        assert result.reply == "ok"

    async def test_web_search_augments(self) -> None:
        gen = FakeGenerator(reply="grounded")
        web = FakeWebSearch()
        pipeline = AIChatPipeline(gen, web_search=web)
        result = await pipeline.respond(
            user_id="u1", chat_id="c1", content="pix?", use_web_search=True
        )
        assert web.retrieved == ["pix?"]
        assert web.searched == ["pix?"]
        assert len(result.sources) == 1
        assert result.sources[0].url == "http://x"
        system = gen.chat_calls[0][0]
        assert "Web context:" in system["content"]
        assert "CTX for pix?" in system["content"]

    async def test_web_search_disabled_by_default(self) -> None:
        gen = FakeGenerator()
        web = FakeWebSearch()
        pipeline = AIChatPipeline(gen, web_search=web)
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="q")
        assert web.retrieved == []
        assert result.sources == []

    async def test_images_on_user_message(self) -> None:
        gen = FakeGenerator()
        pipeline = AIChatPipeline(gen)
        await pipeline.respond(
            user_id="u1", chat_id="c1", content="see this", images=["b64img"]
        )
        user_message = gen.chat_calls[0][-1]
        assert user_message["images"] == ["b64img"]

    async def test_tts_populates_audio(self) -> None:
        gen = FakeGenerator(reply="spoken")
        tts = FakeTTS()
        pipeline = AIChatPipeline(gen, tts=tts)
        result = await pipeline.respond(
            user_id="u1", chat_id="c1", content="talk", speak=True
        )
        assert tts.spoken == ["spoken"]
        import base64

        assert result.audio_base64 == base64.b64encode(b"WAV:spoken").decode()

    async def test_history_included(self) -> None:
        gen = FakeGenerator()
        pipeline = AIChatPipeline(gen)
        history = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"},
        ]
        await pipeline.respond(
            user_id="u1", chat_id="c1", content="now", history=history
        )
        sent = gen.chat_calls[0]
        assert sent[-3:] == [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "now"},
        ]


class TestToolLoop:
    async def test_single_round_tool_call(self) -> None:
        calls: list[dict[str, Any]] = []

        async def handler(args: dict[str, Any]) -> str:
            calls.append(args)
            return "sunny"

        weather = Tool(
            name="get_weather",
            description="Get weather",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
        gen = FakeGenerator(
            reply="It is sunny.",
            tool_scripts=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_weather",
                                "arguments": {"city": "SP"},
                            }
                        }
                    ],
                },
                {"role": "assistant", "content": "It is sunny."},
            ],
        )
        pipeline = AIChatPipeline(gen, tools=[weather])
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="weather?")

        assert result.reply == "It is sunny."
        assert result.tool_calls_made == ["get_weather"]
        assert calls == [{"city": "SP"}]
        # two rounds of chat_with_tools; plain chat never used
        assert len(gen.tool_calls) == 2
        assert gen.chat_calls == []
        # the tool result was appended before the second round
        second_round = gen.tool_calls[1]
        assert second_round[-1] == {"role": "tool", "content": "sunny"}

    async def test_unknown_tool_is_safe(self) -> None:
        gen = FakeGenerator(
            reply="done",
            tool_scripts=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "ghost", "arguments": {}}}
                    ],
                },
                {"role": "assistant", "content": "done"},
            ],
        )
        known = Tool(
            name="real",
            description="real",
            parameters={},
            handler=lambda args: _async_str("x"),
        )
        pipeline = AIChatPipeline(gen, tools=[known])
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="go")
        assert result.reply == "done"
        assert result.tool_calls_made == ["ghost"]
        tool_message = gen.tool_calls[1][-1]
        assert tool_message["role"] == "tool"
        assert "unknown tool" in tool_message["content"]

    async def test_max_tool_iterations_bound(self) -> None:
        loop_call = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "spin", "arguments": {}}}],
        }

        async def handler(args: dict[str, Any]) -> str:
            return "again"

        spin = Tool(
            name="spin", description="spin", parameters={}, handler=handler
        )
        gen = FakeGenerator(tool_scripts=[loop_call, loop_call, loop_call])
        pipeline = AIChatPipeline(gen, tools=[spin], max_tool_iterations=2)
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="loop")
        # bounded at 2 rounds despite the model never stopping
        assert len(gen.tool_calls) == 2
        assert result.tool_calls_made == ["spin", "spin"]

    async def test_tools_ignored_without_backend_support(self) -> None:
        class NoToolsGenerator:
            async def chat(
                self, messages: list[dict[str, Any]], *, config: Any = None
            ) -> str:
                return "plain"

            async def generate(self, prompt: str, *, config: Any = None) -> str:
                return "g"

            async def stream(
                self, prompt: str, *, config: Any = None
            ) -> AsyncIterator[str]:
                yield "x"

        tool = Tool(
            name="t",
            description="t",
            parameters={},
            handler=lambda args: _async_str("y"),
        )
        pipeline = AIChatPipeline(NoToolsGenerator(), tools=[tool])
        result = await pipeline.respond(user_id="u1", chat_id="c1", content="hi")
        assert result.reply == "plain"
        assert result.tool_calls_made == []


class TestStream:
    async def test_stream_yields_pieces(self) -> None:
        gen = FakeGenerator()
        pipeline = AIChatPipeline(gen)
        pieces = [
            piece
            async for piece in pipeline.stream(
                user_id="u1", chat_id="c1", content="hi"
            )
        ]
        assert pieces == ["Hel", "lo", "!"]
        # prompt-mode: the built messages were flattened into one prompt
        assert "user: hi" in gen.stream_calls[0]

    async def test_stream_indexes_final_answer(self) -> None:
        gen = FakeGenerator()
        memory = FakeMemory()
        pipeline = AIChatPipeline(gen, memory=memory)
        async for _ in pipeline.stream(user_id="u1", chat_id="c1", content="hi"):
            pass
        assert [i["role"] for i in memory.indexed] == ["user", "assistant"]
        assert memory.indexed[1]["content"] == "Hello!"


async def _async_str(value: str) -> str:
    return value


def _client(pipeline: AIChatPipeline) -> TestClient:
    app = FastAPI()
    app.include_router(make_ai_chat_router(pipeline))
    return TestClient(app)


class TestRouter:
    def test_chat_endpoint(self) -> None:
        gen = FakeGenerator(reply="router reply")
        pipeline = AIChatPipeline(gen)
        client = _client(pipeline)
        resp = client.post(
            "/api/ai-chat/chat",
            json={"user_id": "u1", "chat_id": "c1", "content": "hello"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "router reply"
        assert body["sources"] == []
        assert body["tool_calls_made"] == []
        assert body["audio_base64"] is None

    def test_chat_endpoint_with_history(self) -> None:
        gen = FakeGenerator(reply="ok")
        pipeline = AIChatPipeline(gen)
        client = _client(pipeline)
        resp = client.post(
            "/api/ai-chat/chat",
            json={
                "user_id": "u1",
                "chat_id": "c1",
                "content": "now",
                "history": [{"role": "user", "content": "earlier"}],
            },
        )
        assert resp.status_code == 200
        sent = gen.chat_calls[0]
        assert {"role": "user", "content": "earlier"} in sent

    def test_stream_endpoint(self) -> None:
        gen = FakeGenerator()
        pipeline = AIChatPipeline(gen)
        client = _client(pipeline)
        resp = client.post(
            "/api/ai-chat/chat/stream",
            json={"user_id": "u1", "chat_id": "c1", "content": "hi"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        text = resp.text
        assert "data: Hel" in text
        assert "event: done" in text


@pytest.mark.parametrize("speak", [True, False])
async def test_audio_only_when_speak_and_tts(speak: bool) -> None:
    gen = FakeGenerator(reply="r")
    tts = FakeTTS()
    pipeline = AIChatPipeline(gen, tts=tts)
    result = await pipeline.respond(
        user_id="u1", chat_id="c1", content="x", speak=speak
    )
    assert (result.audio_base64 is not None) is speak
