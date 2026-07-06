"""Schemas for the RAG context layer (web search + PDF)."""

from __future__ import annotations

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


__all__: list[str] = [
    "Chunk",
    "Document",
    "PdfPage",
    "SearchResult",
]
