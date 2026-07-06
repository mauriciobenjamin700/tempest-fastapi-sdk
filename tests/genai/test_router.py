"""Tests for make_genai_router (endpoint wiring + conditional mounting)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.genai import make_genai_router
from tempest_fastapi_sdk.schemas.base import BaseSchema


class _FakeGenerator:
    async def generate(self, prompt: str, *, config: Any = None) -> str:
        return f"gen:{prompt}"

    async def chat(self, messages: list[dict[str, str]], *, config: Any = None) -> str:
        return f"reply:{messages[-1]['content']}"

    async def stream(self, prompt: str, *, config: Any = None) -> AsyncIterator[str]:
        for piece in ("a", "b", "c"):
            yield piece


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] for t in texts]


class _FakeRetriever:
    async def retrieve(self, query: str, *, top_k: int = 5) -> str:
        return f"context({query}, {top_k})"


class _FakeTranscription(BaseSchema):
    text: str


class _FakeSTT:
    async def transcribe(
        self, audio: bytes, *, language: Any = None
    ) -> _FakeTranscription:
        return _FakeTranscription(text=f"heard {len(audio)} bytes")


class _FakeTTS:
    async def synthesize(
        self,
        text: str,
        *,
        language: Any = None,
        speaker: Any = None,
    ) -> bytes:
        return b"WAVDATA:" + text.encode()


def _client(**objects: Any) -> TestClient:
    app = FastAPI()
    app.include_router(make_genai_router(**objects))
    return TestClient(app)


class TestConditionalMounting:
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            make_genai_router()

    def test_only_embed_mounted(self) -> None:
        client = _client(embedder=_FakeEmbedder())
        assert (
            client.post("/api/genai/embed", json={"texts": ["hi"]}).status_code == 200
        )
        assert (
            client.post("/api/genai/generate", json={"prompt": "x"}).status_code == 404
        )


class TestTextEndpoints:
    def test_generate(self) -> None:
        client = _client(text_generator=_FakeGenerator())
        resp = client.post("/api/genai/generate", json={"prompt": "hello"})
        assert resp.status_code == 200
        assert resp.json() == {"text": "gen:hello"}

    def test_generate_with_config(self) -> None:
        client = _client(text_generator=_FakeGenerator())
        resp = client.post(
            "/api/genai/generate",
            json={"prompt": "hi", "config": {"max_new_tokens": 10}},
        )
        assert resp.status_code == 200

    def test_chat(self) -> None:
        client = _client(text_generator=_FakeGenerator())
        resp = client.post(
            "/api/genai/chat",
            json={"messages": [{"role": "user", "content": "ping"}]},
        )
        assert resp.json() == {"reply": "reply:ping"}

    def test_stream(self) -> None:
        client = _client(text_generator=_FakeGenerator())
        resp = client.post("/api/genai/generate/stream", json={"prompt": "x"})
        assert resp.status_code == 200
        body = resp.text
        assert "data: a" in body
        assert "event: done" in body


class TestOtherEndpoints:
    def test_embed(self) -> None:
        client = _client(embedder=_FakeEmbedder())
        resp = client.post("/api/genai/embed", json={"texts": ["ab", "cde"]})
        assert resp.json() == {"vectors": [[2.0], [3.0]], "dimensions": 1}

    def test_rag(self) -> None:
        client = _client(retriever=_FakeRetriever())
        resp = client.post("/api/genai/rag", json={"query": "q", "top_k": 3})
        assert resp.json() == {"context": "context(q, 3)"}

    def test_transcribe(self) -> None:
        client = _client(speech_to_text=_FakeSTT())
        resp = client.post(
            "/api/genai/transcribe",
            files={"file": ("clip.wav", b"1234", "audio/wav")},
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "heard 4 bytes"

    def test_tts(self) -> None:
        client = _client(text_to_speech=_FakeTTS())
        resp = client.post("/api/genai/tts", json={"text": "oi"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert resp.content == b"WAVDATA:oi"
