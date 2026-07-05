"""Assemble retrieved sources into a single prompt-ready context block.

Turns web :class:`SearchResult`s and PDF :class:`Chunk`s into one plain-text
block you drop straight into a system/user prompt. Each source is
delimited and labeled with its origin so the model can cite it. Mirrors
the leviathan ContextBuilder: plain text, clear structure, optional
truncation per source.
"""

from __future__ import annotations

from collections.abc import Sequence

from tempest_fastapi_sdk.genai.rag.schemas import Chunk, SearchResult


def _body(source: SearchResult | Chunk, *, long_text: bool, max_chars: int) -> str:
    """Return the text body of a source, truncated when not ``long_text``."""
    if isinstance(source, SearchResult):
        text = source.content or source.snippet
    else:
        text = source.text
    text = text.strip()
    if not long_text and len(text) > max_chars:
        return text[:max_chars].rstrip() + " …"
    return text


def _label(source: SearchResult | Chunk) -> str:
    """Return a citation label for a source."""
    if isinstance(source, SearchResult):
        title = source.title or source.url
        return f"{title} ({source.url})"
    if source.page is not None:
        return f"{source.source} (page {source.page})"
    return source.source


def build_context(
    question: str,
    sources: Sequence[SearchResult | Chunk],
    *,
    long_text: bool = True,
    max_chars: int = 2000,
) -> str:
    """Render retrieved sources into a single context string for an LLM.

    Args:
        question (str): The user's question, echoed at the top so the model
            has the task in view.
        sources (Sequence[SearchResult | Chunk]): Web results and/or PDF
            chunks, in the order they should appear.
        long_text (bool): When ``True`` (default), include each source's
            full body; when ``False``, truncate each to ``max_chars``.
        max_chars (int): Per-source truncation cap when ``long_text`` is
            ``False``.

    Returns:
        str: A prompt-ready block. Sources are delimited by ``---`` and
        labeled with their origin; returns a short notice when ``sources``
        is empty.
    """
    if not sources:
        return f"Question: {question}\n\nNo sources were retrieved for this question."

    parts: list[str] = [
        f"Question: {question}",
        "",
        f"Below are {len(sources)} sources retrieved to answer this question. "
        "Each is delimited by `---`. Use them as ground truth; cite the "
        "source when relevant.",
        "",
    ]
    for index, source in enumerate(sources, start=1):
        parts.append("---")
        parts.append(f"[{index}] {_label(source)}")
        parts.append(_body(source, long_text=long_text, max_chars=max_chars))
        parts.append("")
    parts.append("---")
    return "\n".join(parts)


__all__: list[str] = [
    "build_context",
]
