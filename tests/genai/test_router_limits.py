"""Request limits enforced by make_genai_router before any model runs."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import (
    GenAIRequestLimits,
    GeneratedImage,
    ImageGenerationConfig,
    make_genai_router,
)
from tempest_fastapi_sdk.genai.audio import DEFAULT_MAX_UPLOAD_BYTES
from tempest_fastapi_sdk.schemas.base import BaseSchema


class _RecordingGenerator:
    def __init__(self) -> None:
        self.calls: int = 0

    async def generate(self, prompt: str, *, config: Any = None) -> str:
        self.calls += 1
        return "ok"

    async def chat(self, messages: list[dict[str, str]], *, config: Any = None) -> str:
        self.calls += 1
        return "ok"

    async def stream(self, prompt: str, *, config: Any = None) -> Any:
        self.calls += 1
        yield "ok"


class _RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: int = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0] for _ in texts]


class _RecordingRetriever:
    def __init__(self) -> None:
        self.calls: int = 0

    async def retrieve(self, query: str, *, top_k: int = 5) -> str:
        self.calls += 1
        return "ctx"


class _Transcription(BaseSchema):
    text: str


class _RecordingSTT:
    def __init__(self) -> None:
        self.calls: int = 0

    async def transcribe(self, audio: bytes, *, language: Any = None) -> _Transcription:
        self.calls += 1
        return _Transcription(text=str(len(audio)))


class _RecordingTTS:
    def __init__(self) -> None:
        self.calls: int = 0

    async def synthesize(
        self,
        text: str,
        *,
        language: Any = None,
        speaker: Any = None,
    ) -> bytes:
        self.calls += 1
        return b"WAV"


class _RecordingImages:
    def __init__(self) -> None:
        self.requested: list[int] = []

    async def generate(
        self,
        prompt: str,
        *,
        config: ImageGenerationConfig | None = None,
    ) -> list[GeneratedImage]:
        count = config.num_images if config is not None else 1
        self.requested.append(count)
        return [
            GeneratedImage(data=b"\x89PNG", seed=i, width=8, height=8)
            for i in range(count)
        ]


def _client(**objects: Any) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_genai_router(**objects))
    return TestClient(app)


class TestTextLimits:
    def test_oversized_prompt_is_refused_before_generation(self) -> None:
        generator = _RecordingGenerator()
        client = _client(text_generator=generator)
        response = client.post(
            "/api/genai/generate",
            json={"prompt": "x" * 2_000_000},
        )
        assert response.status_code == 422
        assert generator.calls == 0

    def test_huge_max_new_tokens_is_refused(self) -> None:
        generator = _RecordingGenerator()
        client = _client(text_generator=generator)
        response = client.post(
            "/api/genai/generate",
            json={"prompt": "hi", "config": {"max_new_tokens": 10**9}},
        )
        assert response.status_code == 422
        assert "max_new_tokens" in response.json()["detail"]
        assert generator.calls == 0

    def test_stream_applies_the_same_limits(self) -> None:
        generator = _RecordingGenerator()
        client = _client(text_generator=generator)
        response = client.post(
            "/api/genai/generate/stream",
            json={"prompt": "hi", "config": {"max_new_tokens": 10**9}},
        )
        assert response.status_code == 422
        assert generator.calls == 0

    def test_chat_message_count_and_size_are_capped(self) -> None:
        generator = _RecordingGenerator()
        client = _client(
            text_generator=generator,
            limits=GenAIRequestLimits(max_chat_messages=2, max_prompt_chars=10),
        )
        many = [{"role": "user", "content": "a"}] * 3
        assert (
            client.post("/api/genai/chat", json={"messages": many}).status_code == 422
        )
        long = [{"role": "user", "content": "a" * 6}] * 2
        assert (
            client.post("/api/genai/chat", json={"messages": long}).status_code == 422
        )
        assert generator.calls == 0

    def test_within_limits_passes(self) -> None:
        generator = _RecordingGenerator()
        client = _client(text_generator=generator)
        response = client.post(
            "/api/genai/generate",
            json={"prompt": "hi", "config": {"max_new_tokens": 256}},
        )
        assert response.status_code == 200
        assert generator.calls == 1

    def test_limits_are_configurable(self) -> None:
        generator = _RecordingGenerator()
        client = _client(
            text_generator=generator,
            limits=GenAIRequestLimits(max_new_tokens=100_000),
        )
        response = client.post(
            "/api/genai/generate",
            json={"prompt": "hi", "config": {"max_new_tokens": 50_000}},
        )
        assert response.status_code == 200


class TestEmbedRagTtsLimits:
    def test_embed_batch_is_capped(self) -> None:
        embedder = _RecordingEmbedder()
        client = _client(embedder=embedder)
        response = client.post("/api/genai/embed", json={"texts": ["a"] * 100_000})
        assert response.status_code == 422
        assert response.json()["details"] == {"field": "texts", "limit": 256}
        assert embedder.calls == 0

    @pytest.mark.parametrize("top_k", [-5, 0, 10**9])
    def test_rag_top_k_out_of_range_is_refused(self, top_k: int) -> None:
        retriever = _RecordingRetriever()
        client = _client(retriever=retriever)
        response = client.post(
            "/api/genai/rag",
            json={"query": "q", "top_k": top_k},
        )
        assert response.status_code == 422
        assert retriever.calls == 0

    def test_tts_text_is_capped(self) -> None:
        tts = _RecordingTTS()
        client = _client(text_to_speech=tts)
        response = client.post("/api/genai/tts", json={"text": "a" * 5_001})
        assert response.status_code == 422
        assert tts.calls == 0


class TestTranscribeUpload:
    def test_oversized_upload_is_refused(self) -> None:
        stt = _RecordingSTT()
        client = _client(
            speech_to_text=stt,
            limits=GenAIRequestLimits(max_upload_bytes=16),
        )
        response = client.post(
            "/api/genai/transcribe",
            files={"file": ("clip.wav", b"x" * 17, "audio/wav")},
        )
        assert response.status_code == 422
        assert "larger than" in response.json()["detail"]
        assert stt.calls == 0

    def test_default_ceiling_matches_the_voice_router(self) -> None:
        assert GenAIRequestLimits().max_upload_bytes == DEFAULT_MAX_UPLOAD_BYTES


class TestImageLimits:
    def test_unbounded_config_no_longer_validates(self) -> None:
        with pytest.raises(ValueError):
            ImageGenerationConfig(width=100_000)
        with pytest.raises(ValueError):
            ImageGenerationConfig(num_images=10**6)
        with pytest.raises(ValueError):
            ImageGenerationConfig(steps=10**6)

    def test_side_over_the_router_limit_is_refused(self) -> None:
        images = _RecordingImages()
        client = _client(image_generator=images)
        response = client.post(
            "/api/genai/image",
            json={"prompt": "cat", "config": {"width": 4096, "height": 4096}},
        )
        assert response.status_code == 422
        assert images.requested == []

    def test_steps_over_the_router_limit_is_refused(self) -> None:
        images = _RecordingImages()
        client = _client(image_generator=images)
        response = client.post(
            "/api/genai/image",
            json={"prompt": "cat", "config": {"steps": 400}},
        )
        assert response.status_code == 422
        assert images.requested == []

    def test_a_batch_is_refused_instead_of_rendered_and_dropped(self) -> None:
        images = _RecordingImages()
        client = _client(image_generator=images)
        response = client.post(
            "/api/genai/image",
            json={"prompt": "cat", "config": {"num_images": 4}},
        )
        assert response.status_code == 422
        assert response.json()["details"] == {"field": "num_images", "limit": 1}
        assert images.requested == []

    def test_single_image_passes(self) -> None:
        images = _RecordingImages()
        client = _client(image_generator=images)
        response = client.post("/api/genai/image", json={"prompt": "cat"})
        assert response.status_code == 200
        assert images.requested == [1]


class TestModelsOffTheLoop:
    def test_inventory_runs_in_a_worker_thread(self) -> None:
        seen: list[bool] = []

        class _Registry:
            def inventory(self, *, probe: bool = True) -> Any:
                from tempest_fastapi_sdk.genai import runtime_report

                try:
                    asyncio.get_running_loop()
                    seen.append(True)
                except RuntimeError:
                    seen.append(False)
                return runtime_report([], probe=False)

        client = _client(models=_Registry())
        response = client.get("/api/genai/models?probe=false")
        assert response.status_code == 200
        assert seen == [False]
