"""Hybrid retrieval — dense vectors + BM25, fused with RRF.

Dense retrieval (embeddings + cosine) captures meaning but misses exact terms:
proper nouns, codes, acronyms a query shares verbatim with a chunk. Sparse BM25
nails those but ignores semantics. `HybridRetriever` runs both over the same
indexed chunks and fuses their rankings with Reciprocal Rank Fusion, so a query
like "what does BACEN do?" finds the chunk that literally says "BACEN" even
when the dense score is lukewarm.

BM25 comes from ``rank-bm25`` (pure Python, the ``[genai-rag]`` extra); the
in-memory sparse index is rebuilt on each :meth:`HybridRetriever.index` call
and suits corpora up to a few tens of thousands of chunks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.rag.fusion import reciprocal_rank_fusion
from tempest_fastapi_sdk.genai.rag.retriever import Retriever, SupportsEmbed

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.rag.schemas import Chunk
    from tempest_fastapi_sdk.genai.rag.vectorstore import VectorStore

_TOKEN_RE: re.Pattern[str] = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    """Lowercase word-tokenize ``text`` for BM25.

    Args:
        text (str): The text to tokenize.

    Returns:
        list[str]: Lowercased ``\\w+`` tokens.
    """
    return _TOKEN_RE.findall(text.lower())


def _chunk_key(chunk: Chunk) -> str:
    """Return a stable fusion key for ``chunk`` (``source#index``)."""
    return f"{chunk.source}#{chunk.index}"


def _require_bm25() -> Any:
    """Import ``rank_bm25.BM25Okapi`` or raise a helpful error.

    Returns:
        Any: The ``BM25Okapi`` class.

    Raises:
        ImportError: When the ``[genai-rag]`` extra is not installed.
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError as exc:
        raise ImportError(
            "Hybrid search requires the optional [genai-rag] extra "
            "(rank-bm25). Install with: pip install tempest-fastapi-sdk[genai-rag]",
        ) from exc
    return BM25Okapi


class HybridRetriever:
    """Dense + BM25 retrieval fused with Reciprocal Rank Fusion.

    Example:

        >>> from tempest_fastapi_sdk.genai import Embedder
        >>> from tempest_fastapi_sdk.genai.rag import (
        ...     HybridRetriever,
        ...     InMemoryVectorStore,
        ... )
        >>> rag = HybridRetriever(
        ...     Embedder("...", normalize=True), InMemoryVectorStore(),
        ... )
        >>> await rag.index(chunks)               # builds dense + BM25 index
        >>> best = await rag.search("what is CNPJ?", top_k=5)

    Attributes:
        retriever (Retriever): The dense half (embedder + store).
        k_rrf (int): RRF damping constant.
    """

    def __init__(
        self,
        embedder: SupportsEmbed,
        store: VectorStore,
        *,
        k_rrf: int = 60,
    ) -> None:
        """Initialize the hybrid retriever.

        Args:
            embedder (SupportsEmbed): The embedding model for the dense half.
            store (VectorStore): The vector store for the dense half.
            k_rrf (int): RRF damping constant passed to
                :func:`reciprocal_rank_fusion`.
        """
        self.retriever = Retriever(embedder, store)
        self.k_rrf = k_rrf
        self._chunks: list[Chunk] = []
        self._by_key: dict[str, Chunk] = {}
        self._bm25: Any = None

    async def index(self, chunks: Sequence[Chunk]) -> int:
        """Index ``chunks`` into both the dense store and the BM25 index.

        Args:
            chunks (Sequence[Chunk]): Chunks to index.

        Returns:
            int: The number of chunks indexed.
        """
        if not chunks:
            return 0
        await self.retriever.index(chunks)
        self._chunks.extend(chunks)
        for chunk in chunks:
            self._by_key[_chunk_key(chunk)] = chunk
        bm25_cls = _require_bm25()
        self._bm25 = bm25_cls([_tokenize(chunk.text) for chunk in self._chunks])
        return len(chunks)

    def _bm25_ranking(self, query: str, top_k: int) -> list[str]:
        """Return the top-``top_k`` chunk keys by BM25 score (best first)."""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(
            range(len(self._chunks)),
            key=lambda i: scores[i],
            reverse=True,
        )
        return [_chunk_key(self._chunks[i]) for i in ranked[:top_k]]

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        candidates: int = 20,
    ) -> list[Chunk]:
        """Return the ``top_k`` chunks by fused dense + BM25 relevance.

        Args:
            query (str): The natural-language query.
            top_k (int): How many chunks to return.
            candidates (int): How many candidates each retriever contributes to
                the fusion before truncating to ``top_k``.

        Returns:
            list[Chunk]: The fused best chunks, best first.
        """
        dense = await self.retriever.search(query, top_k=candidates)
        dense_keys = [_chunk_key(chunk) for chunk in dense]
        sparse_keys = self._bm25_ranking(query, candidates)
        fused = reciprocal_rank_fusion([dense_keys, sparse_keys], k=self.k_rrf)
        return [self._by_key[key] for key in fused[:top_k] if key in self._by_key]


__all__: list[str] = [
    "HybridRetriever",
]
