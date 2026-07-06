"""Retriever — RAG over your own corpus in one object.

Ties an embedder to a :class:`VectorStore`: :meth:`index` embeds chunks
and stores them once; :meth:`search` / :meth:`retrieve` embed the query
and pull the nearest chunks — cheap, repeatable, no re-embedding of the
whole corpus per request. Pairs with :class:`~tempest_fastapi_sdk.genai.Embedder`
and any store (in-memory, pgvector, …).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.rag.context import build_context

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.rag.schemas import Chunk
    from tempest_fastapi_sdk.genai.rag.vectorstore import VectorStore


@runtime_checkable
class SupportsEmbed(Protocol):
    """Anything that turns texts into vectors (e.g. ``Embedder``)."""

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = ...,
    ) -> list[list[float]]:
        """Return one vector per input text."""
        ...


class Retriever:
    """Index chunks and retrieve the most relevant ones for a query.

    Example:

        >>> from tempest_fastapi_sdk.genai import Embedder
        >>> from tempest_fastapi_sdk.genai.rag import (
        ...     InMemoryVectorStore, PdfReader, Retriever,
        ... )
        >>> rag = Retriever(Embedder("...", normalize=True), InMemoryVectorStore())
        >>> await rag.index(PdfReader().chunks("/kb/manual.pdf"))   # once
        >>> context = await rag.retrieve("how to refund?", top_k=5) # cheap, repeatable
        >>> answer = await generator.generate(context)

    Attributes:
        embedder (SupportsEmbed): Turns text into vectors.
        store (VectorStore): Persists and searches the vectors.
    """

    def __init__(self, embedder: SupportsEmbed, store: VectorStore) -> None:
        """Initialize the retriever.

        Args:
            embedder (SupportsEmbed): The embedding model (e.g. ``Embedder``).
            store (VectorStore): The vector store to index into and search.
        """
        self.embedder = embedder
        self.store = store

    async def index(self, chunks: Sequence[Chunk]) -> int:
        """Embed ``chunks`` and add them to the store.

        Args:
            chunks (Sequence[Chunk]): Chunks (e.g. from ``PdfReader.chunks``
                or ``chunk_text``).

        Returns:
            int: The number of chunks indexed.
        """
        if not chunks:
            return 0
        vectors = await self.embedder.embed([chunk.text for chunk in chunks])
        await self.store.add(list(chunks), vectors)
        return len(chunks)

    async def search(self, query: str, *, top_k: int = 5) -> list[Chunk]:
        """Return the ``top_k`` chunks most relevant to ``query``.

        Args:
            query (str): The natural-language query.
            top_k (int): How many chunks to return.

        Returns:
            list[Chunk]: The nearest chunks, each with its ``score`` set.
        """
        (vector,) = await self.embedder.embed([query])
        return await self.store.search(vector, top_k=top_k)

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        long_text: bool = True,
        max_chars: int = 2000,
    ) -> str:
        """Search the corpus and build a prompt-ready context block.

        The one-shot corpus-RAG helper: query in, context string out.

        Args:
            query (str): The natural-language query.
            top_k (int): How many chunks to include.
            long_text (bool): Full chunk bodies (``True``) or truncate to
                ``max_chars``.
            max_chars (int): Per-chunk truncation cap when ``long_text``
                is ``False``.

        Returns:
            str: A prompt-ready context block (see :func:`build_context`).
        """
        chunks = await self.search(query, top_k=top_k)
        return build_context(query, chunks, long_text=long_text, max_chars=max_chars)


__all__: list[str] = [
    "Retriever",
    "SupportsEmbed",
]
