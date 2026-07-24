"""Tests for hybrid retrieval (BM25 + dense) and RRF fusion."""

from __future__ import annotations

from collections.abc import Sequence

from tempest_fastapi_sdk.genai.rag import Chunk, HybridRetriever, reciprocal_rank_fusion
from tests.genai._eval import recall_at_k
from tests.genai._eval.corpus import CORPUS, PROPER_NOUN_QUERIES


class TestReciprocalRankFusion:
    def test_rewards_agreement(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["a", "c", "b"]])
        assert fused[0] == "a"

    def test_merges_disjoint_lists(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b"], ["c", "d"]])
        assert set(fused) == {"a", "b", "c", "d"}
        assert fused[0] in {"a", "c"}

    def test_empty(self) -> None:
        assert reciprocal_rank_fusion([]) == []

    def test_top_rank_beats_low_rank_across_lists(self) -> None:
        fused = reciprocal_rank_fusion([["x", "y", "z"], ["y", "x", "z"]])
        assert fused[:2] == ["x", "y"] or fused[:2] == ["y", "x"]
        assert fused[-1] == "z"


class _DenseBlindEmbedder:
    """Returns a constant vector for everything — dense search is useless.

    Isolates BM25's contribution: any correct hit must come from the sparse
    side, proving hybrid recovers exact-term matches a blind dense stage misses.
    """

    async def embed(
        self, texts: str | list[str], *, batch_size: int = 32
    ) -> list[list[float]]:
        items = [texts] if isinstance(texts, str) else list(texts)
        return [[1.0, 0.0] for _ in items]


class _ListStore:
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    async def add(self, chunks: Sequence[Chunk], vectors: list[list[float]]) -> None:
        self._chunks.extend(chunks)

    async def search(self, vector: list[float], *, top_k: int = 5) -> list[Chunk]:
        return self._chunks[:top_k]


def _corpus_chunks() -> list[Chunk]:
    return [
        Chunk(text=doc.text, source="corpus", index=i) for i, doc in enumerate(CORPUS)
    ]


class TestHybridRetriever:
    async def test_indexes_both_halves(self) -> None:
        rag = HybridRetriever(_DenseBlindEmbedder(), _ListStore())  # type: ignore[arg-type]
        assert await rag.index(_corpus_chunks()) == len(CORPUS)

    async def test_bm25_recovers_proper_nouns_dense_misses(self) -> None:
        rag = HybridRetriever(_DenseBlindEmbedder(), _ListStore())  # type: ignore[arg-type]
        await rag.index(_corpus_chunks())

        index_of = {doc.id: i for i, doc in enumerate(CORPUS)}
        hits = 0
        for query in PROPER_NOUN_QUERIES:
            results = await rag.search(query.text, top_k=5, candidates=8)
            ranked_ids = [f"corpus#{c.index}" for c in results]
            target = f"corpus#{index_of[query.relevant_id]}"
            hits += int(recall_at_k(ranked_ids, [target], k=5) > 0)
        # A blind dense stage alone would score ~0; BM25 must carry these.
        assert hits >= 4
