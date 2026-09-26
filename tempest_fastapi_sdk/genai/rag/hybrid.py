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

Indexing is per source: a batch replaces every chunk previously indexed for
the sources it names, so re-indexing an edited document never leaves the old
version (or a stale tail of it) in either half.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.rag.fusion import reciprocal_rank_fusion
from tempest_fastapi_sdk.genai.rag.retriever import Retriever, SupportsEmbed
from tempest_fastapi_sdk.genai.rag.schemas import _chunk_identity

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

    Chunks are identified by ``(source, index, text)``, and each
    :meth:`index` call replaces whatever was indexed before for the sources
    in the batch — so index every chunk of a source in one call. The SDK's
    stores (:class:`InMemoryVectorStore`, :class:`PgVectorStore`,
    :class:`ChromaVectorStore`) apply the same replacement on the dense side;
    a third-party store that only appends still returns the replaced rows,
    and those are dropped here before fusion (they only cost candidate slots).

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
        self._chunks: dict[str, Chunk] = {}
        self._tokens: dict[str, list[str]] = {}
        self._keys: list[str] = []
        self._bm25: Any = None

    async def index(self, chunks: Sequence[Chunk]) -> int:
        """Index ``chunks`` into both the dense store and the BM25 index.

        Every chunk previously indexed for a source that appears in
        ``chunks`` is replaced, in the sparse index here and — for the SDK's
        stores — in the dense store too. Identical chunks (same source,
        index and text) are kept once.

        Args:
            chunks (Sequence[Chunk]): Chunks to index — pass every chunk of a
                source in the same call.

        Returns:
            int: The number of chunks received.
        """
        if not chunks:
            return 0
        bm25_cls = _require_bm25()
        await self.retriever.index(chunks)
        sources = {chunk.source for chunk in chunks}
        for key in [k for k, c in self._chunks.items() if c.source in sources]:
            del self._chunks[key]
            del self._tokens[key]
        for chunk in chunks:
            key = _chunk_identity(chunk)
            self._chunks[key] = chunk
            self._tokens[key] = _tokenize(chunk.text)
        self._keys = list(self._chunks)
        self._bm25 = (
            bm25_cls([self._tokens[key] for key in self._keys]) if self._keys else None
        )
        return len(chunks)

    def _bm25_ranking(self, query: str, top_k: int) -> list[str]:
        """Return the top-``top_k`` chunk keys by BM25 score (best first).

        Only chunks sharing at least one token with the query are ranked. A
        chunk with no overlap scores 0, and a list of zeros sorted "by score"
        is just insertion order — fed to RRF it would add a rank signal that
        means nothing and can invert the dense ranking. Overlap is tested on
        tokens rather than on ``score > 0`` because BM25 scores a match
        negative when the term is in most documents of a tiny corpus.

        Args:
            query (str): The query text.
            top_k (int): How many keys to return at most.

        Returns:
            list[str]: Chunk identities, best BM25 score first.
        """
        if self._bm25 is None:
            return []
        tokens = _tokenize(query)
        query_tokens = set(tokens)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        matching = [
            i
            for i, key in enumerate(self._keys)
            if not query_tokens.isdisjoint(self._tokens[key])
        ]
        matching.sort(key=lambda i: scores[i], reverse=True)
        return [self._keys[i] for i in matching[:top_k]]

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
            list[Chunk]: The fused best chunks, best first. Empty when
            ``top_k <= 0``, before either retriever runs.
        """
        if top_k <= 0:
            return []
        dense = await self.retriever.search(query, top_k=candidates)
        dense_keys = [
            key
            for key in (_chunk_identity(chunk) for chunk in dense)
            if key in self._chunks
        ]
        sparse_keys = self._bm25_ranking(query, candidates)
        fused = reciprocal_rank_fusion([dense_keys, sparse_keys], k=self.k_rrf)
        return [self._chunks[key] for key in fused[:top_k]]

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        candidates: int = 20,
        long_text: bool = True,
        max_chars: int = 2000,
    ) -> str:
        """Search (hybrid) and build a prompt-ready context block.

        The one-shot helper mirroring :meth:`Retriever.retrieve`, so a
        ``HybridRetriever`` drops into ``make_genai_router``'s ``/rag``
        endpoint (it satisfies ``SupportsRetrieve``).

        Args:
            query (str): The natural-language query.
            top_k (int): How many chunks to include.
            candidates (int): Candidates each retriever contributes to fusion.
            long_text (bool): Full chunk bodies or truncate to ``max_chars``.
            max_chars (int): Per-chunk truncation cap when ``long_text`` is
                ``False``.

        Returns:
            str: A prompt-ready context block (see :func:`build_context`).
        """
        from tempest_fastapi_sdk.genai.rag.context import build_context

        chunks = await self.search(query, top_k=top_k, candidates=candidates)
        return build_context(query, chunks, long_text=long_text, max_chars=max_chars)


__all__: list[str] = [
    "HybridRetriever",
]
