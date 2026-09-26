"""Chunk identity, reindex semantics and fusion edge cases across RAG stores.

Regression suite for the audit that found ``source#index`` / ``source::index``
used as the chunk key: :func:`chunk_text` restarts ``index`` at 0 on every
call, so two batches from one source collided — the hybrid retriever answered
``"alpha"`` with ``beta`` texts, Chroma kept 2 of 4 rows, and re-indexing a
source duplicated it. The contract now is: a chunk is identified by
``(source, index, text)``, and indexing a batch **replaces** every chunk
previously stored for the sources the batch names.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.rag import (
    ChromaVectorStore,
    Chunk,
    HybridRetriever,
    InMemoryVectorStore,
    PgVectorStore,
    chunk_text,
    reciprocal_rank_fusion,
)
from tests.genai.test_chat_memory import _HAS_CHROMA, _FakeChromaClient

_HAS_BM25: bool = importlib.util.find_spec("rank_bm25") is not None


@pytest.fixture(params=["fake", "real"])
def chroma_client(request: pytest.FixtureRequest) -> Any:
    """Yield the fake Chroma client, plus a real ephemeral one when installed.

    Args:
        request (pytest.FixtureRequest): The parametrization.

    Returns:
        Any: A Chroma-like client.
    """
    if request.param == "real":
        if not _HAS_CHROMA:
            pytest.skip("chromadb not installed")
        import chromadb

        return chromadb.EphemeralClient(
            settings=chromadb.config.Settings(anonymized_telemetry=False)
        )
    return _FakeChromaClient()


class _TableEmbedder:
    """Maps known texts to canned vectors; unknown texts get a neutral one."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        """Store the lookup table.

        Args:
            table (dict[str, list[float]]): Text to vector.
        """
        self.table = table

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Return the canned vector for each text.

        Args:
            texts (str | list[str]): Texts to embed.
            batch_size (int): Ignored.

        Returns:
            list[list[float]]: One vector per text.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        return [list(self.table.get(t, [0.5, 0.5, 0.5])) for t in items]


class _AppendOnlyStore:
    """A third-party store that never replaces — appends every batch."""

    def __init__(self) -> None:
        """Start empty."""
        self.chunks: list[Chunk] = []

    async def add(self, chunks: Sequence[Chunk], vectors: list[list[float]]) -> None:
        """Append the batch.

        Args:
            chunks (Sequence[Chunk]): Chunks.
            vectors (list[list[float]]): Ignored.
        """
        self.chunks.extend(chunks)

    async def search(self, vector: list[float], *, top_k: int = 5) -> list[Chunk]:
        """Return the first ``top_k`` stored chunks.

        Args:
            vector (list[float]): Ignored.
            top_k (int): How many.

        Returns:
            list[Chunk]: Stored chunks in insertion order.
        """
        return self.chunks[:top_k]


class TestReciprocalRankFusionDuplicates:
    def test_a_repeated_id_counts_once_per_list(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "a", "a"], ["b", "a"]])
        assert fused == ["b", "a"] or fused == ["a", "b"]
        single = reciprocal_rank_fusion([["x", "y", "x", "x", "x"]])
        assert single == ["x", "y"]

    def test_duplicate_does_not_outrank_a_genuine_agreement(self) -> None:
        fused = reciprocal_rank_fusion([["dup", "dup", "dup", "ok"], ["ok"]])
        assert fused[0] == "ok"


@pytest.mark.skipif(not _HAS_BM25, reason="rank-bm25 not installed")
class TestHybridRetrieverIdentity:
    async def test_a_second_batch_from_one_source_replaces_the_first(
        self,
    ) -> None:
        rag = HybridRetriever(_TableEmbedder({}), InMemoryVectorStore())
        alpha = chunk_text("alpha one. " * 6, source="kb", max_chars=20, overlap=0)
        beta = chunk_text("beta two.", source="kb", max_chars=20, overlap=0)
        assert len(alpha) > len(beta) == 1
        await rag.index(alpha)
        await rag.index(beta)
        hits = await rag.search("alpha", top_k=10)
        assert [c.text for c in hits] == [beta[0].text]

    async def test_reindexing_a_source_does_not_duplicate(self) -> None:
        rag = HybridRetriever(_TableEmbedder({}), InMemoryVectorStore())
        chunks = chunk_text("gamma delta " * 6, source="kb", max_chars=25, overlap=0)
        await rag.index(chunks)
        await rag.index(chunks)
        assert len(rag._chunks) == len(chunks)
        hits = await rag.search("gamma", top_k=50, candidates=50)
        assert len(hits) == len(chunks)

    async def test_distinct_sources_coexist(self) -> None:
        rag = HybridRetriever(_TableEmbedder({}), InMemoryVectorStore())
        await rag.index([Chunk(text="alpha text", source="a", index=0)])
        await rag.index([Chunk(text="beta text", source="b", index=0)])
        texts = {c.text for c in await rag.search("text", top_k=5)}
        assert texts == {"alpha text", "beta text"}

    async def test_same_key_different_text_in_one_batch_keeps_both(self) -> None:
        rag = HybridRetriever(_TableEmbedder({}), InMemoryVectorStore())
        await rag.index(
            [
                Chunk(text="page one alpha", source="doc", index=0, page=1),
                Chunk(text="page two alpha", source="doc", index=0, page=2),
            ]
        )
        texts = {c.text for c in await rag.search("alpha", top_k=5)}
        assert texts == {"page one alpha", "page two alpha"}

    async def test_stale_rows_of_an_append_only_store_are_dropped(self) -> None:
        store = _AppendOnlyStore()
        rag = HybridRetriever(_TableEmbedder({}), store)  # type: ignore[arg-type]
        await rag.index([Chunk(text="old alpha", source="kb", index=0)])
        await rag.index([Chunk(text="new beta", source="kb", index=0)])
        hits = await rag.search("alpha", top_k=5)
        assert [c.text for c in hits] == ["new beta"]


@pytest.mark.skipif(not _HAS_BM25, reason="rank-bm25 not installed")
class TestHybridSparseZeroOverlap:
    async def test_zero_overlap_bm25_does_not_invert_the_dense_ranking(self) -> None:
        chunks = [
            Chunk(text="apple orchard", source="s", index=0),
            Chunk(text="cherry blossom", source="s", index=1),
            Chunk(text="date palm", source="s", index=2),
            Chunk(text="banana plantation", source="s", index=3),
        ]
        embedder = _TableEmbedder(
            {
                "banana plantation": [1.0, 0.0, 0.0],
                "apple orchard": [0.8, 0.6, 0.0],
                "cherry blossom": [0.5, 0.866, 0.0],
                "date palm": [0.0, 1.0, 0.0],
                "zzz": [1.0, 0.0, 0.0],
            }
        )
        rag = HybridRetriever(embedder, InMemoryVectorStore())
        await rag.index(chunks)
        dense = await rag.retriever.search("zzz", top_k=4)
        hybrid = await rag.search("zzz", top_k=4)
        assert [c.text for c in dense] == [
            "banana plantation",
            "apple orchard",
            "cherry blossom",
            "date palm",
        ]
        assert [c.text for c in hybrid] == [c.text for c in dense]


class TestInMemoryVectorStoreReplace:
    async def test_add_replaces_the_sources_it_names(self) -> None:
        store = InMemoryVectorStore()
        await store.add(
            [
                Chunk(text="a0", source="kb", index=0),
                Chunk(text="a1", source="kb", index=1),
                Chunk(text="x0", source="other", index=0),
            ],
            [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        )
        await store.add([Chunk(text="b0", source="kb", index=0)], [[1.0, 0.0]])
        texts = sorted(c.text for c in await store.search([1.0, 0.0], top_k=10))
        assert texts == ["b0", "x0"]
        assert len(store) == 2


class TestChromaVectorStoreIdentity:
    async def test_a_shorter_reindex_leaves_no_stale_tail(
        self, chroma_client: Any
    ) -> None:
        store = ChromaVectorStore(client=chroma_client, collection_name="ident-1")
        await store.add(
            [
                Chunk(text="alpha 0", source="kb", index=0),
                Chunk(text="alpha 1", source="kb", index=1),
                Chunk(text="alpha 2", source="kb", index=2),
            ],
            [[1.0, 0.0], [1.0, 0.1], [1.0, 0.2]],
        )
        await store.add([Chunk(text="beta 0", source="kb", index=0)], [[0.0, 1.0]])
        hits = await store.search([1.0, 0.0], top_k=10)
        assert [c.text for c in hits] == ["beta 0"]

    async def test_same_key_different_text_in_one_batch_keeps_both(
        self, chroma_client: Any
    ) -> None:
        store = ChromaVectorStore(client=chroma_client, collection_name="ident-2")
        await store.add(
            [
                Chunk(text="page one", source="doc", index=0, page=1),
                Chunk(text="page two", source="doc", index=0, page=2),
            ],
            [[1.0, 0.0], [0.0, 1.0]],
        )
        hits = await store.search([1.0, 0.0], top_k=10)
        assert sorted(c.text for c in hits) == ["page one", "page two"]

    async def test_identical_chunks_in_one_batch_are_deduplicated(
        self, chroma_client: Any
    ) -> None:
        store = ChromaVectorStore(client=chroma_client, collection_name="ident-3")
        chunk = Chunk(text="same", source="doc", index=0)
        await store.add([chunk, chunk], [[1.0, 0.0], [1.0, 0.0]])
        hits = await store.search([1.0, 0.0], top_k=10)
        assert [c.text for c in hits] == ["same"]

    async def test_other_sources_survive_a_reindex(self, chroma_client: Any) -> None:
        store = ChromaVectorStore(client=chroma_client, collection_name="ident-4")
        await store.add([Chunk(text="keep", source="a", index=0)], [[1.0, 0.0]])
        await store.add([Chunk(text="old", source="b", index=0)], [[1.0, 0.0]])
        await store.add([Chunk(text="new", source="b", index=0)], [[1.0, 0.0]])
        hits = await store.search([1.0, 0.0], top_k=10)
        assert sorted(c.text for c in hits) == ["keep", "new"]


class TestPgVectorStoreIdentifier:
    @pytest.mark.parametrize(
        "table",
        [
            "rag; DROP TABLE users",
            "rag chunks",
            '"quoted"',
            "1starts_with_digit",
            "a.b.c",
            "",
            "x" * 64,
        ],
    )
    def test_rejects_an_unsafe_table_name(self, table: str) -> None:
        with pytest.raises(ValueError, match="table"):
            PgVectorStore(object(), dim=3, table=table)  # type: ignore[arg-type]

    @pytest.mark.parametrize("table", ["rag_chunks", "kb.rag_chunks", "_T1"])
    def test_accepts_a_plain_or_schema_qualified_name(self, table: str) -> None:
        store = PgVectorStore(object(), dim=3, table=table)  # type: ignore[arg-type]
        assert store.table == table

    @pytest.mark.parametrize("dim", [0, -1])
    def test_rejects_a_non_positive_dimension(self, dim: int) -> None:
        with pytest.raises(ValueError, match="dim"):
            PgVectorStore(object(), dim=dim)  # type: ignore[arg-type]
