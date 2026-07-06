"""Tests for the GenAI refinements (cosine, normalize, chunking, RAG one-shot)."""

from __future__ import annotations

import math

import httpx
import pytest

from tempest_fastapi_sdk.genai import (
    Embedder,
    InMemoryEmbeddingCache,
    cosine_similarity,
)
from tempest_fastapi_sdk.genai.rag import (
    ContentExtractor,
    SearxngBackend,
    WebSearch,
    chunk_text,
)
from tempest_fastapi_sdk.genai.schemas import HardwareInfo


def _cpu() -> HardwareInfo:
    return HardwareInfo(cpu_cores=2, ram_total_bytes=10**9, ram_available_bytes=10**9)


class TestCosine:
    def test_identical_is_one(self) -> None:
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_is_zero(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_is_minus_one(self) -> None:
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector(self) -> None:
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_dim_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            cosine_similarity([1.0], [1.0, 2.0])


class TestNormalize:
    async def test_normalize_returns_unit_vectors(self) -> None:
        cache = InMemoryEmbeddingCache()
        emb = Embedder("m", cache=cache, normalize=True, hardware=_cpu())
        cache.set(emb._cache_key("x"), [3.0, 4.0])  # norm 5
        (vector,) = await emb.embed(["x"])
        assert math.isclose(math.sqrt(sum(c * c for c in vector)), 1.0)
        assert vector == pytest.approx([0.6, 0.8])

    async def test_no_normalize_keeps_raw(self) -> None:
        cache = InMemoryEmbeddingCache()
        emb = Embedder("m", cache=cache, hardware=_cpu())
        cache.set(emb._cache_key("x"), [3.0, 4.0])
        (vector,) = await emb.embed(["x"])
        assert vector == [3.0, 4.0]


class TestChunkText:
    def test_overlap(self) -> None:
        chunks = chunk_text("abcdefghij", source="s", max_chars=4, overlap=1)
        assert [c.text for c in chunks] == ["abcd", "defg", "ghij"]
        assert all(c.source == "s" for c in chunks)
        assert [c.index for c in chunks] == [0, 1, 2]

    def test_blank_returns_empty(self) -> None:
        assert chunk_text("   ", source="s") == []

    def test_bad_overlap(self) -> None:
        with pytest.raises(ValueError):
            chunk_text("abc", source="s", max_chars=4, overlap=4)


class TestRetrieveOneShot:
    async def test_retrieve_snippets_only(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [{"title": "T", "url": "http://a", "content": "snip"}]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        search = WebSearch(SearxngBackend("http://s", http_client=client))
        context = await search.retrieve("q", max_results=3)
        await client.aclose()
        assert "q" in context
        assert "http://a" in context
        assert "snip" in context

    async def test_retrieve_with_extractor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trafilatura

        monkeypatch.setattr(trafilatura, "extract", lambda _h: "FULL BODY")

        def handler(request: httpx.Request) -> httpx.Response:
            if "search" in str(request.url):
                return httpx.Response(
                    200, json={"results": [{"url": "http://a"}, {"url": "http://b"}]}
                )
            return httpx.Response(200, text="<html>...</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        search = WebSearch(SearxngBackend("http://s", http_client=client))
        extractor = ContentExtractor(http_client=client)
        context = await search.retrieve("q", extractor=extractor)
        await client.aclose()
        assert context.count("FULL BODY") == 2


class TestExtractMany:
    async def test_order_and_failures(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/ok":
                return httpx.Response(200, text="<html>ok</html>")
            return httpx.Response(500)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client)
        results = await extractor.extract_many(
            ["http://x/ok", "http://x/bad"], concurrency=2
        )
        await client.aclose()
        assert len(results) == 2
        assert results[1].failed is True
