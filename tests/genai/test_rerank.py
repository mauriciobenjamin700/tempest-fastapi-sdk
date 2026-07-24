"""Tests for the RAG reranker and its Retriever integration."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from tempest_fastapi_sdk.genai.rag import Chunk, Retriever
from tempest_fastapi_sdk.genai.rag.rerank import _rank_by_scores


def _chunk(text: str, index: int) -> Chunk:
    return Chunk(text=text, source="s", index=index)


class TestRankByScores:
    def test_sorts_desc_and_sets_score(self) -> None:
        chunks = [_chunk("a", 0), _chunk("b", 1), _chunk("c", 2)]
        ranked = _rank_by_scores(chunks, [0.1, 0.9, 0.5], top_k=None)
        assert [c.text for c in ranked] == ["b", "c", "a"]
        assert ranked[0].score == pytest.approx(0.9)

    def test_truncates_to_top_k(self) -> None:
        chunks = [_chunk("a", 0), _chunk("b", 1), _chunk("c", 2)]
        ranked = _rank_by_scores(chunks, [0.1, 0.9, 0.5], top_k=2)
        assert [c.text for c in ranked] == ["b", "c"]


class _FakeEmbedder:
    async def embed(
        self, texts: str | list[str], *, batch_size: int = 32
    ) -> list[list[float]]:
        items = [texts] if isinstance(texts, str) else list(texts)
        return [[0.0] for _ in items]


class _FakeStore:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.asked_top_k: int | None = None

    async def add(self, chunks: Sequence[Chunk], vectors: list[list[float]]) -> None:
        return None

    async def search(self, vector: list[float], *, top_k: int) -> list[Chunk]:
        self.asked_top_k = top_k
        return self.chunks[:top_k]


class _FakeReranker:
    def __init__(self) -> None:
        self.called = False

    async def rerank(
        self, query: str, chunks: Sequence[Chunk], *, top_k: int | None = None
    ) -> list[Chunk]:
        self.called = True
        reordered = list(reversed(list(chunks)))
        return reordered[:top_k] if top_k is not None else reordered


class TestRetrieverReranking:
    async def test_overfetches_then_reranks(self) -> None:
        store = _FakeStore([_chunk(f"c{i}", i) for i in range(25)])
        reranker = _FakeReranker()
        rag = Retriever(_FakeEmbedder(), store, reranker=reranker)  # type: ignore[arg-type]
        result = await rag.search("q", top_k=5, rerank_candidates=20)
        assert store.asked_top_k == 20
        assert reranker.called is True
        assert len(result) == 5

    async def test_dense_only_without_reranker(self) -> None:
        store = _FakeStore([_chunk(f"c{i}", i) for i in range(25)])
        rag = Retriever(_FakeEmbedder(), store)  # type: ignore[arg-type]
        result = await rag.search("q", top_k=5)
        assert store.asked_top_k == 5
        assert len(result) == 5


@pytest.mark.model
class TestRerankerWithModel:
    async def test_ranks_relevant_chunk_first(self) -> None:
        from tempest_fastapi_sdk.genai.rag import Reranker

        reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", device="cpu")
        chunks = [
            _chunk("Bananas are a yellow fruit.", 0),
            _chunk("PIX is the Brazilian instant payment system.", 1),
            _chunk("The Eiffel Tower stands in Paris.", 2),
        ]
        ranked = await reranker.rerank("What is PIX?", chunks, top_k=3)
        assert ranked[0].text.startswith("PIX")
