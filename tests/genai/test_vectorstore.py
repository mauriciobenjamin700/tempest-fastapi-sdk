"""Tests for VectorStore + Retriever (InMemory; no torch/pgvector)."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.genai.rag import (
    Chunk,
    InMemoryVectorStore,
    Retriever,
)


def _chunk(text: str, i: int) -> Chunk:
    return Chunk(text=text, source="kb", index=i)


class TestInMemoryVectorStore:
    async def test_add_and_search_orders_by_similarity(self) -> None:
        store = InMemoryVectorStore()
        await store.add(
            [_chunk("a", 0), _chunk("b", 1), _chunk("c", 2)],
            [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]],
        )
        # query close to [1,0] -> "a" then "c" then "b"
        results = await store.search([1.0, 0.0], top_k=2)
        assert [c.text for c in results] == ["a", "c"]
        assert results[0].score == pytest.approx(1.0)
        assert results[0].score >= results[1].score

    async def test_len_and_mismatch(self) -> None:
        store = InMemoryVectorStore()
        with pytest.raises(ValueError):
            await store.add([_chunk("a", 0)], [[1.0], [2.0]])
        await store.add([_chunk("a", 0)], [[1.0, 0.0]])
        assert len(store) == 1


class _FakeEmbedder:
    """Deterministic 2-D embeddings keyed by first char, for tests."""

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        table = {"cat": [1.0, 0.0], "dog": [0.9, 0.1], "car": [0.0, 1.0]}
        items = [texts] if isinstance(texts, str) else texts
        return [table.get(t, [0.5, 0.5]) for t in items]


class TestRetriever:
    async def test_index_then_retrieve(self) -> None:
        rag = Retriever(_FakeEmbedder(), InMemoryVectorStore())
        indexed = await rag.index(
            [_chunk("cat", 0), _chunk("dog", 1), _chunk("car", 2)]
        )
        assert indexed == 3

        results = await rag.search("cat", top_k=2)
        assert [c.text for c in results] == ["cat", "dog"]  # nearest to [1,0]

    async def test_retrieve_builds_context(self) -> None:
        rag = Retriever(_FakeEmbedder(), InMemoryVectorStore())
        await rag.index([_chunk("cat", 0), _chunk("car", 1)])
        context = await rag.retrieve("cat", top_k=1)
        assert "cat" in context
        assert "kb" in context  # source label

    async def test_index_empty_is_noop(self) -> None:
        rag = Retriever(_FakeEmbedder(), InMemoryVectorStore())
        assert await rag.index([]) == 0
