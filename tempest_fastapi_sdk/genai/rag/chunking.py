"""Slice text into overlapping chunks sized for prompts or embeddings.

Generic and dependency-free: works on any string (a scraped web page, a
PDF page, a database field), not just PDFs. Overlap keeps a fact that
straddles a boundary whole in at least one chunk.
"""

from __future__ import annotations

from tempest_fastapi_sdk.genai.rag.schemas import Chunk


def chunk_text(
    text: str,
    *,
    source: str,
    max_chars: int = 2000,
    overlap: int = 200,
    page: int | None = None,
    start_index: int = 0,
) -> list[Chunk]:
    """Split ``text`` into overlapping :class:`Chunk`s.

    Args:
        text (str): The text to chunk.
        source (str): Origin label carried on each chunk (URL, PDF path…).
        max_chars (int): Max characters per chunk (~500 tokens at 2000).
        overlap (int): Characters shared between adjacent chunks.
        page (int | None): Originating page number, when applicable.
        start_index (int): Index the first produced chunk gets (so chunks
            from many sources can share one running counter).

    Returns:
        list[Chunk]: The chunks in order; empty when ``text`` is blank.

    Raises:
        ValueError: When ``max_chars`` is not positive or ``overlap`` is
            negative or ``>= max_chars``.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be >= 0 and < max_chars")
    chunks: list[Chunk] = []
    if not text.strip():
        return chunks
    step = max_chars - overlap
    index = start_index
    for start in range(0, len(text), step):
        piece = text[start : start + max_chars].strip()
        if piece:
            chunks.append(Chunk(text=piece, source=source, index=index, page=page))
            index += 1
        if start + max_chars >= len(text):
            break
    return chunks


__all__: list[str] = [
    "chunk_text",
]
