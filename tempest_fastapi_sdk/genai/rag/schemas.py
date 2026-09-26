"""Schemas for the RAG context layer (web search + PDF)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class SearchResult(BaseSchema):
    """One source returned by a web search backend.

    Attributes:
        title (str): Page title reported by the search engine.
        url (str): Canonical URL of the source.
        snippet (str): Short summary from the search engine.
        content (str): Full page body once extracted (empty until an
            extractor fills it, or when extraction failed).
        score (float | None): Relevance score when the backend provides
            one; ``None`` otherwise.
    """

    title: str = ""
    url: str
    snippet: str = ""
    content: str = ""
    score: float | None = None


class PdfPage(BaseSchema):
    """The text of one page extracted from a PDF.

    Attributes:
        number (int): 1-based page number.
        text (str): Extracted text for the page.
    """

    number: int
    text: str


class Chunk(BaseSchema):
    """A slice of source text sized to drop into a prompt.

    Attributes:
        text (str): The chunk body.
        source (str): Where it came from (a URL or a PDF path).
        index (int): 0-based position of the chunk within its source.
        page (int | None): Originating PDF page (1-based), when applicable.
        score (float | None): Relevance score when returned by a vector
            search (higher = closer); ``None`` otherwise.
    """

    text: str
    source: str
    index: int
    page: int | None = None
    score: float | None = None


class Document(BaseSchema):
    """A read document (e.g. a PDF) with its full text and page breakdown.

    Attributes:
        source (str): Path or identifier the document was read from.
        text (str): The full concatenated text.
        pages (list[PdfPage]): Per-page text (empty when not paginated).
        metadata (dict[str, str]): Extra metadata (title, author, …) when
            the reader exposes it.
    """

    source: str
    text: str
    pages: list[PdfPage] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


def _chunk_identity(chunk: Chunk) -> str:
    """Return the identity every SDK store and retriever keys a chunk by.

    ``source#index`` was not unique: :func:`chunk_text` restarts ``index`` at
    0 on every call, so two batches from one source (or two pages chunked
    separately) produced the same key and silently overwrote each other. The
    identity therefore folds the text in as well — two chunks share it only
    when they are the same slice of the same source, which is exactly when
    deduplicating them is correct.

    Args:
        chunk (Chunk): The chunk to identify.

    Returns:
        str: A 64-character SHA-256 hex digest of ``(source, index, text)``.
    """
    payload = f"{chunk.source}\x1f{chunk.index}\x1f{chunk.text}".encode()
    return hashlib.sha256(payload).hexdigest()


def _unique_batch(
    chunks: Sequence[Chunk],
    vectors: Sequence[list[float]],
) -> list[tuple[Chunk, list[float]]]:
    """Pair chunks with vectors, keeping the first of identical chunks.

    Args:
        chunks (Sequence[Chunk]): The batch.
        vectors (Sequence[list[float]]): One vector per chunk, aligned.

    Returns:
        list[tuple[Chunk, list[float]]]: The pairs, deduplicated by
        ``(source, index, text)`` in first-seen order.

    Raises:
        ValueError: When the counts differ.
    """
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors must have the same length")
    pairs: dict[str, tuple[Chunk, list[float]]] = {}
    for chunk, vector in zip(chunks, vectors, strict=True):
        pairs.setdefault(_chunk_identity(chunk), (chunk, list(vector)))
    return list(pairs.values())


__all__: list[str] = [
    "Chunk",
    "Document",
    "PdfPage",
    "SearchResult",
]
