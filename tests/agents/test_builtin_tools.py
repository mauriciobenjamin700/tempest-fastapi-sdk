"""Tests for the tools wrapping the self-hosted models.

Each model is a fake exposing the same method the real one does, so these
assert the wiring — arguments in, artifact out, chaining by name — without
loading anything.
"""

from __future__ import annotations

from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    AgentContext,
    AgentToolError,
    describe_image_tool,
    generate_image_tool,
    retrieve_tool,
    save_artifact_tool,
    speak_tool,
    transcribe_audio_tool,
    web_search_tool,
)
from tempest_fastapi_sdk.agents.schemas import AgentArtifact
from tempest_fastapi_sdk.genai import GeneratedImage


class FakeImageGenerator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def generate(self, prompt: str, *, config: Any = None) -> list[Any]:
        self.calls.append((prompt, config))
        return [
            GeneratedImage(
                data=b"\x89PNG-fake",
                image_format="png",
                seed=7,
                width=512,
                height=512,
            )
        ]


class FakeVision:
    def __init__(self) -> None:
        self.seen: list[tuple[str, Any]] = []

    async def generate(self, prompt: str, *, images: list[Any]) -> str:
        self.seen.append((prompt, images[0]))
        return "a bicycle"


class FakeTranscription:
    text = "olá mundo"
    language = "pt"


class FakeSTT:
    def __init__(self) -> None:
        self.seen: list[tuple[Any, Any]] = []

    async def transcribe(self, audio: Any, *, language: Any = None) -> Any:
        self.seen.append((audio, language))
        return FakeTranscription()


class FakeTTS:
    def __init__(self) -> None:
        self.seen: list[tuple[str, Any]] = []

    async def synthesize(self, text: str, *, language: Any = None) -> bytes:
        self.seen.append((text, language))
        return b"RIFF-fake"


class FakeRetriever:
    def __init__(self, block: str = "passage one") -> None:
        self.block = block
        self.seen: list[tuple[str, int]] = []

    async def retrieve(self, query: str, *, top_k: int = 5) -> str:
        self.seen.append((query, top_k))
        return self.block


class FakeWebSearch:
    def __init__(self, block: str = "web context") -> None:
        self.block = block
        self.seen: list[tuple[str, int]] = []

    async def retrieve(self, query: str, *, max_results: int = 3) -> str:
        self.seen.append((query, max_results))
        return self.block


class TestGenerateImageTool:
    @pytest.mark.asyncio
    async def test_produces_a_named_png_artifact(self) -> None:
        tool = generate_image_tool(FakeImageGenerator())
        result = await tool.invoke(
            {"prompt": "a bicycle", "filename": "bike"},
            AgentContext(),
        )
        assert len(result.artifacts) == 1
        artifact = result.artifacts[0]
        assert artifact.name == "bike.png"
        assert artifact.media_type == "image/png"
        assert artifact.data == b"\x89PNG-fake"
        assert "512x512" in result.text
        assert "seed 7" in result.text

    @pytest.mark.asyncio
    async def test_keeps_an_explicit_extension(self) -> None:
        tool = generate_image_tool(FakeImageGenerator())
        result = await tool.invoke({"prompt": "x", "filename": "a.png"}, AgentContext())
        assert result.artifacts[0].name == "a.png"

    @pytest.mark.asyncio
    async def test_names_are_unique_across_calls(self) -> None:
        tool = generate_image_tool(FakeImageGenerator())
        context = AgentContext()
        first = await tool.invoke({"prompt": "x"}, context)
        context.artifacts[first.artifacts[0].name] = first.artifacts[0]
        second = await tool.invoke({"prompt": "y"}, context)
        assert first.artifacts[0].name != second.artifacts[0].name

    @pytest.mark.asyncio
    async def test_default_steps_reach_the_config(self) -> None:
        generator = FakeImageGenerator()
        tool = generate_image_tool(generator, default_steps=4)
        await tool.invoke({"prompt": "x"}, AgentContext())
        assert generator.calls[0][1].steps == 4

    @pytest.mark.asyncio
    async def test_model_steps_override_the_default(self) -> None:
        generator = FakeImageGenerator()
        tool = generate_image_tool(generator, default_steps=4)
        await tool.invoke({"prompt": "x", "steps": 30}, AgentContext())
        assert generator.calls[0][1].steps == 30

    @pytest.mark.asyncio
    async def test_missing_prompt_is_a_tool_error(self) -> None:
        tool = generate_image_tool(FakeImageGenerator())
        with pytest.raises(AgentToolError, match="prompt"):
            await tool.invoke({}, AgentContext())

    def test_name_is_configurable(self) -> None:
        tool = generate_image_tool(FakeImageGenerator(), name="draw_sdxl")
        assert tool.name == "draw_sdxl"
        assert tool.to_spec()["function"]["name"] == "draw_sdxl"


class TestDescribeImageTool:
    @pytest.mark.asyncio
    async def test_reads_an_artifact_by_name(self) -> None:
        vision = FakeVision()
        tool = describe_image_tool(vision)
        context = AgentContext()
        context.artifacts["bike.png"] = AgentArtifact(
            name="bike.png",
            media_type="image/png",
            data=b"\x89PNG-fake",
        )
        result = await tool.invoke({"artifact": "bike.png"}, context)
        assert result.text == "a bicycle"
        assert vision.seen[0][1] == b"\x89PNG-fake"

    @pytest.mark.asyncio
    async def test_accepts_a_path_instead(self) -> None:
        vision = FakeVision()
        tool = describe_image_tool(vision)
        await tool.invoke({"path": "/tmp/x.png"}, AgentContext())
        assert vision.seen[0][1] == "/tmp/x.png"

    @pytest.mark.asyncio
    async def test_custom_question_is_forwarded(self) -> None:
        vision = FakeVision()
        tool = describe_image_tool(vision)
        await tool.invoke(
            {"path": "/tmp/x.png", "question": "What colour?"},
            AgentContext(),
        )
        assert vision.seen[0][0] == "What colour?"

    @pytest.mark.asyncio
    async def test_neither_source_is_a_tool_error(self) -> None:
        tool = describe_image_tool(FakeVision())
        with pytest.raises(AgentToolError, match="artifact"):
            await tool.invoke({}, AgentContext())

    @pytest.mark.asyncio
    async def test_unknown_artifact_lists_the_known_ones(self) -> None:
        tool = describe_image_tool(FakeVision())
        context = AgentContext()
        context.artifacts["real.png"] = AgentArtifact(
            name="real.png",
            media_type="image/png",
            data=b"x",
        )
        with pytest.raises(AgentToolError, match=r"real\.png"):
            await tool.invoke({"artifact": "ghost.png"}, context)


class TestAudioTools:
    @pytest.mark.asyncio
    async def test_transcribe_reports_the_language(self) -> None:
        stt = FakeSTT()
        tool = transcribe_audio_tool(stt)
        result = await tool.invoke({"path": "/tmp/a.wav"}, AgentContext())
        assert result.text == "[pt] olá mundo"

    @pytest.mark.asyncio
    async def test_transcribe_reads_an_artifact(self) -> None:
        stt = FakeSTT()
        tool = transcribe_audio_tool(stt)
        context = AgentContext()
        context.artifacts["clip.wav"] = AgentArtifact(
            name="clip.wav",
            media_type="audio/wav",
            data=b"RIFF",
        )
        await tool.invoke({"artifact": "clip.wav"}, context)
        assert stt.seen[0][0] == b"RIFF"

    @pytest.mark.asyncio
    async def test_language_hint_is_forwarded(self) -> None:
        stt = FakeSTT()
        tool = transcribe_audio_tool(stt)
        await tool.invoke({"path": "/tmp/a.wav", "language": "pt"}, AgentContext())
        assert stt.seen[0][1] == "pt"

    @pytest.mark.asyncio
    async def test_speak_produces_a_wav_artifact(self) -> None:
        tool = speak_tool(FakeTTS())
        result = await tool.invoke(
            {"text": "olá", "filename": "greeting"},
            AgentContext(),
        )
        artifact = result.artifacts[0]
        assert artifact.name == "greeting.wav"
        assert artifact.media_type == "audio/wav"
        assert artifact.data == b"RIFF-fake"

    @pytest.mark.asyncio
    async def test_speak_requires_text(self) -> None:
        tool = speak_tool(FakeTTS())
        with pytest.raises(AgentToolError, match="text"):
            await tool.invoke({"text": "  "}, AgentContext())


class TestSearchTools:
    @pytest.mark.asyncio
    async def test_retrieve_returns_the_context_block(self) -> None:
        retriever = FakeRetriever()
        tool = retrieve_tool(retriever, top_k=3)
        result = await tool.invoke({"query": "pix"}, AgentContext())
        assert result.text == "passage one"
        assert retriever.seen[0] == ("pix", 3)

    @pytest.mark.asyncio
    async def test_retrieve_honours_an_explicit_top_k(self) -> None:
        retriever = FakeRetriever()
        tool = retrieve_tool(retriever, top_k=3)
        await tool.invoke({"query": "pix", "top_k": 7}, AgentContext())
        assert retriever.seen[0][1] == 7

    @pytest.mark.asyncio
    async def test_empty_corpus_says_so_instead_of_returning_nothing(self) -> None:
        tool = retrieve_tool(FakeRetriever(block=""))
        result = await tool.invoke({"query": "pix"}, AgentContext())
        assert result.text == "No matching passages."

    @pytest.mark.asyncio
    async def test_retrieve_requires_a_query(self) -> None:
        tool = retrieve_tool(FakeRetriever())
        with pytest.raises(AgentToolError, match="query"):
            await tool.invoke({}, AgentContext())

    @pytest.mark.asyncio
    async def test_web_search_forwards_max_results(self) -> None:
        search = FakeWebSearch()
        tool = web_search_tool(search, max_results=2)
        result = await tool.invoke({"query": "news"}, AgentContext())
        assert result.text == "web context"
        assert search.seen[0] == ("news", 2)

    @pytest.mark.asyncio
    async def test_web_search_with_no_results_says_so(self) -> None:
        tool = web_search_tool(FakeWebSearch(block=""))
        result = await tool.invoke({"query": "news"}, AgentContext())
        assert result.text == "No results."


class TestSaveArtifactTool:
    @pytest.mark.asyncio
    async def test_saves_utf8_text(self) -> None:
        tool = save_artifact_tool()
        result = await tool.invoke(
            {"content": "olá mundo", "filename": "note.txt"},
            AgentContext(),
        )
        artifact = result.artifacts[0]
        assert artifact.name == "note.txt"
        assert artifact.data.decode("utf-8") == "olá mundo"
        assert artifact.media_type.startswith("text/plain")

    @pytest.mark.asyncio
    async def test_requires_content(self) -> None:
        tool = save_artifact_tool()
        with pytest.raises(AgentToolError, match="content"):
            await tool.invoke({}, AgentContext())


class TestSpecs:
    def test_every_builtin_declares_an_object_schema(self) -> None:
        tools = [
            generate_image_tool(FakeImageGenerator()),
            describe_image_tool(FakeVision()),
            transcribe_audio_tool(FakeSTT()),
            speak_tool(FakeTTS()),
            retrieve_tool(FakeRetriever()),
            web_search_tool(FakeWebSearch()),
            save_artifact_tool(),
        ]
        for tool in tools:
            spec = tool.to_spec()
            assert spec["type"] == "function"
            assert spec["function"]["name"] == tool.name
            assert spec["function"]["parameters"]["type"] == "object"
            assert tool.description
