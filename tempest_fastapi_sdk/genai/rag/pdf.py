"""Read PDFs into LLM-ready text and chunks, using PyMuPDF.

Point an LLM at a knowledge base of PDFs: :class:`PdfReader` extracts
clean, reading-order text per page (PyMuPDF / ``pymupdf`` — richer and
more accurate than ``pypdf``), exposes document metadata, and slices the
text into overlapping :class:`Chunk`s sized to drop into a prompt or an
embedding index.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.genai.rag.chunking import chunk_text
from tempest_fastapi_sdk.genai.rag.schemas import Chunk, Document, PdfPage


def _require_pymupdf() -> Any:
    """Import PyMuPDF (``pymupdf`` or legacy ``fitz``) or raise.

    Returns:
        Any: The PyMuPDF module.

    Raises:
        ImportError: When the ``[genai-rag]`` extra is not installed.
    """
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        pass
    try:
        import fitz

        return fitz
    except ImportError as exc:
        raise ImportError(
            "PDF reading requires the optional [genai-rag] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-rag]",
        ) from exc


class PdfReader:
    """Extract text, pages and chunks from a PDF via PyMuPDF.

    Attributes:
        text_mode (str): PyMuPDF ``get_text`` mode. ``"text"`` (default)
            gives clean reading-order text; ``"blocks"`` preserves more
            layout structure.
    """

    def __init__(self, *, text_mode: str = "text") -> None:
        """Initialize the reader.

        Args:
            text_mode (str): The ``page.get_text(...)`` mode. Defaults to
                ``"text"``.
        """
        self.text_mode = text_mode

    def read(self, path: str) -> Document:
        """Read a PDF into a :class:`Document` (full text + pages + metadata).

        Args:
            path (str): Filesystem path to the PDF.

        Returns:
            Document: The extracted document.

        Raises:
            ImportError: When the ``[genai-rag]`` extra is not installed.
        """
        pymupdf = _require_pymupdf()
        pages: list[PdfPage] = []
        with pymupdf.open(path) as doc:
            raw_meta = doc.metadata or {}
            for number, page in enumerate(doc, start=1):
                pages.append(
                    PdfPage(number=number, text=page.get_text(self.text_mode)),
                )
        metadata = {str(k): str(v) for k, v in raw_meta.items() if v}
        full_text = "\n\n".join(p.text for p in pages)
        return Document(
            source=path,
            text=full_text,
            pages=pages,
            metadata=metadata,
        )

    def chunks(
        self,
        path: str,
        *,
        max_chars: int = 2000,
        overlap: int = 200,
        per_page: bool = True,
    ) -> list[Chunk]:
        """Read a PDF and slice it into overlapping chunks.

        Args:
            path (str): Filesystem path to the PDF.
            max_chars (int): Max characters per chunk (~500 tokens at
                2000). Defaults to ``2000``.
            overlap (int): Characters shared between adjacent chunks, so a
                fact split across a boundary still lands whole in one.
                Defaults to ``200``.
            per_page (bool): When ``True`` (default), chunk each page
                independently (chunks never span pages, and carry their
                page number); when ``False``, chunk the whole document as
                one stream.

        Returns:
            list[Chunk]: The chunks in document order.
        """
        document = self.read(path)
        if not per_page:
            return chunk_text(
                document.text,
                source=path,
                max_chars=max_chars,
                overlap=overlap,
            )
        chunks: list[Chunk] = []
        for pdf_page in document.pages:
            chunks.extend(
                chunk_text(
                    pdf_page.text,
                    source=path,
                    max_chars=max_chars,
                    overlap=overlap,
                    page=pdf_page.number,
                    start_index=len(chunks),
                ),
            )
        return chunks


__all__: list[str] = [
    "PdfReader",
]
