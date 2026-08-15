"""Read the text layer out of a PDF — the inverse of the renderer.

:mod:`tempest_fastapi_sdk.pdf` writes documents; this module reads them
back, which is the first step of every "hand a PDF to a model" pipeline:
an invoice to classify, a contract to summarize, a tender to transcribe.

**Text layer only — there is no OCR here.** A PDF produced by a word
processor carries its text; a PDF that is a scan carries page images and
nothing else. :func:`extract_pdf_text` returns an empty string for the
latter rather than a blank document, because handing a model an empty
prompt is how a confident answer gets invented about a page nobody read.
Check for it explicitly and route those files to an OCR path.

Page boundaries survive extraction. That is what lets an extracted figure
cite where it came from, and what keeps a truncated document honest about
where it was cut.

    from tempest_fastapi_sdk.pdf import extract_pdf_text

    text = extract_pdf_text(data, max_chars=240_000)
    if not text:
        raise ValueError("PDF has no text layer — it is probably a scan")

Needs the ``[pdf-read]`` extra (``pypdf``). It is a separate extra from
``[pdf]``: rendering pulls WeasyPrint plus Pango and fontconfig from the
system, and a service that only reads PDFs should not carry any of that.
"""

from __future__ import annotations

from io import BytesIO

PageText = tuple[int, str]
"""A one-based page number paired with the text found on that page."""

DEFAULT_PAGE_MARKER: str = "=== PAGE {page} ==="
"""Separator written above each page's text.

Not decoration: it gives a model something to cite when asked where a value
came from. Override it to match the language of the surrounding prompt —
a marker in a different language than the instruction is one more thing for
the model to reconcile.
"""

DEFAULT_TRUNCATION_NOTICE: str = (
    "=== DOCUMENT TRUNCATED AFTER PAGE {page} OF {total} ==="
)
"""Line appended when the text is cut at ``max_chars``.

A silent cut is the dangerous one: the model answers about the half it was
shown, with no sign that a half is missing.
"""

_MISSING_DEPENDENCY = (
    "pypdf is required to read PDFs. Install the extra: "
    'pip install "tempest-fastapi-sdk[pdf-read]"'
)


def extract_pdf_pages(data: bytes) -> list[PageText]:
    """Read every page's text layer, keeping page boundaries.

    Args:
        data (bytes): The PDF file contents.

    Returns:
        list[PageText]: One entry per page, in document order. A page with
        no text layer comes back with an empty string rather than being
        dropped, so entry ``n`` is always page ``n``.

    Raises:
        ImportError: When the ``[pdf-read]`` extra is not installed.
        pypdf.errors.PdfReadError: When the bytes are not a readable PDF.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise ImportError(_MISSING_DEPENDENCY) from exc

    reader = PdfReader(BytesIO(data))
    return [
        (number, page.extract_text() or "")
        for number, page in enumerate(reader.pages, start=1)
    ]


def extract_pdf_text(
    data: bytes,
    *,
    max_chars: int | None = None,
    page_marker: str = DEFAULT_PAGE_MARKER,
    truncation_notice: str = DEFAULT_TRUNCATION_NOTICE,
) -> str:
    """Read a PDF into one string, annotated with page markers.

    Truncation cuts at the last **complete** page that fits, never
    mid-sentence: a page that survives is a page the model can trust, and a
    fragment is a fact with its qualifier missing.

    Args:
        data (bytes): The PDF file contents.
        max_chars (int | None): Ceiling on the returned length. ``None``
            returns everything. Size it against the model's context window,
            not against the file — a window that overflows is silently
            truncated by the daemon instead, with no notice at all.
        page_marker (str): Template for the per-page separator. Receives
            ``page`` (one-based).
        truncation_notice (str): Template appended when the text is cut.
            Receives ``page`` (last page included) and ``total``.

    Returns:
        str: The document text, or an empty string when no page carries a
        text layer — the signature of a scanned document.

    Raises:
        ImportError: When the ``[pdf-read]`` extra is not installed.
        pypdf.errors.PdfReadError: When the bytes are not a readable PDF.
    """
    pages = extract_pdf_pages(data)
    if not any(text.strip() for _, text in pages):
        return ""

    chunks: list[str] = []
    used = 0
    for number, text in pages:
        chunk = f"{page_marker.format(page=number)}\n{text.strip()}\n"
        if max_chars is not None and used + len(chunk) > max_chars:
            chunks.append(
                "\n"
                + truncation_notice.format(page=number - 1, total=len(pages))
                + "\n",
            )
            break
        chunks.append(chunk)
        used += len(chunk)
    return "\n".join(chunks)


__all__: list[str] = [
    "DEFAULT_PAGE_MARKER",
    "DEFAULT_TRUNCATION_NOTICE",
    "PageText",
    "extract_pdf_pages",
    "extract_pdf_text",
]
