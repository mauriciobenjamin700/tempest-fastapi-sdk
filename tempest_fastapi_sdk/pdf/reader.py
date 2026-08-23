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
"""Line appended when the text is cut at a page boundary.

A silent cut is the dangerous one: the model answers about the half it was
shown, with no sign that a half is missing.
"""

DEFAULT_PARTIAL_PAGE_NOTICE: str = "=== PAGE {page} OF {total} TRUNCATED MID-PAGE ==="
"""Line appended when even the first page did not fit ``max_chars``.

Distinct from :data:`DEFAULT_TRUNCATION_NOTICE` on purpose: a reader (human
or model) that sees this knows the last sentence may stop in the middle, so a
value read near the end is not to be trusted the way a whole page is. Asking
for a budget smaller than one page used to return **only** the notice — no
document text at all, and 46 characters for a ``max_chars=40``.
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


def _rendered_pages(pages: list[PageText], page_marker: str) -> list[str]:
    """Render each page as marker + text.

    Args:
        pages (list[PageText]): Page number and text, in document order.
        page_marker (str): Template for the per-page separator.

    Returns:
        list[str]: One chunk per page, in the same order.
    """
    return [
        f"{page_marker.format(page=number)}\n{text.strip()}\n" for number, text in pages
    ]


def _joined(chunks: list[str], notice: str | None = None) -> str:
    """Assemble chunks the way the return value is assembled.

    Args:
        chunks (list[str]): Rendered page chunks to include.
        notice (str | None): Notice line to append, without its blank-line
            padding.

    Returns:
        str: The candidate result, so its length can be measured **before**
        deciding to return it. Measuring the parts separately is what let a
        ``max_chars=40`` call return 46 characters: the notice was appended
        after the budget had already been spent.
    """
    parts = list(chunks)
    if notice is not None:
        parts.append(f"\n{notice}\n")
    return "\n".join(parts)


def _annotated_first_page(
    page: PageText,
    *,
    total: int,
    max_chars: int,
    truncation_notice: str,
    partial_page_notice: str,
) -> str | None:
    """Fit page 1 plus a notice that says what actually happened.

    Args:
        page (PageText): The first page's number and text.
        total (int): Page count, for the notice.
        max_chars (int): The ceiling the result must respect.
        truncation_notice (str): Template for the page-boundary notice, used
            when the whole page fits.
        partial_page_notice (str): Template for the mid-page notice, used
            when the page has to be cut.

    Returns:
        str | None: Page-1 text with the matching notice appended, or
        ``None`` when no notice leaves at least a third of the budget for
        text — then the caller spends everything on text instead.

    Which notice is chosen follows the **cut**, not the branch: announcing
    ``TRUNCATED MID-PAGE`` while handing over a whole page is a lie a reader
    cannot check. Measured on a 3-page document at ``max_chars=120``, the
    first shape of this fix returned all 59 characters of page 1 under a
    mid-page warning; the honest line there is
    ``DOCUMENT TRUNCATED AFTER PAGE 1 OF 3``, and it fits in 106 characters.

    Between the two there is a budget that holds the page but not the
    boundary notice — 100 characters, for that same document, where the
    boundary form needs 106. The slice is capped at ``len(body) - 1`` so the
    mid-page warning stays true: 58 of the 59 characters of page 1 under a
    warning beats all 59 with pages 2 and 3 gone in silence, which is the
    failure this module exists to prevent.

    The page marker is deliberately **not** rendered. It costs characters
    that a tight budget does not have, and either notice already names the
    page, so nothing citable is lost. Keeping both meant the richer form
    returned *less* document text than the plainer one at the same budget,
    which reads as a bug from the outside.

    The third is the line between annotating a payload and replacing it.
    Measured on a 239-character page: with no floor, a budget of 45 returned
    4 characters of document beside a 38-character notice, while a budget of
    40 returned 24 characters. Paying more for less text is not a trade-off
    a caller asked for.
    """
    number, text = page
    body = text.strip()
    floor = max(1, max_chars // 3)

    whole = _joined([body], truncation_notice.format(page=number, total=total))
    if len(whole) <= max_chars:
        return whole

    partial = partial_page_notice.format(page=number, total=total)
    room = min(max_chars - len(_joined([""], partial)), len(body) - 1)
    if room < floor:
        return None
    return _joined([body[:room]], partial)


def extract_pdf_text(
    data: bytes,
    *,
    max_chars: int | None = None,
    page_marker: str = DEFAULT_PAGE_MARKER,
    truncation_notice: str = DEFAULT_TRUNCATION_NOTICE,
    partial_page_notice: str = DEFAULT_PARTIAL_PAGE_NOTICE,
) -> str:
    """Read a PDF into one string, annotated with page markers.

    Truncation prefers page boundaries: whole pages are kept while they fit,
    because a page that survives is a page the model can cite. When not even
    the **first** page fits, the cut happens mid-page and
    ``partial_page_notice`` says so — returning nothing but a notice would
    hand the model an empty prompt, which is the failure this module exists
    to prevent.

    **The result never exceeds ``max_chars``.** The notice is part of the
    budget, not an addition to it.

    Args:
        data (bytes): The PDF file contents.
        max_chars (int | None): Ceiling on the returned length. ``None``
            returns everything. Size it against the model's context window,
            not against the file — a window that overflows is silently
            truncated by the daemon instead, with no notice at all.
        page_marker (str): Template for the per-page separator. Receives
            ``page`` (one-based).
        truncation_notice (str): Template appended when the cut lands on a
            page boundary. Receives ``page`` (last page included, always
            ``>= 1``) and ``total``.
        partial_page_notice (str): Template appended when the cut lands
            inside page 1. Receives ``page`` and ``total``.

    Returns:
        str: The document text, or an empty string when no page carries a
        text layer — the signature of a scanned document.

    Raises:
        ImportError: When the ``[pdf-read]`` extra is not installed.
        pypdf.errors.PdfReadError: When the bytes are not a readable PDF.

    Notes:
        A budget too small to carry the notice beside a real slice of text
        returns raw document text cut to ``max_chars`` — no notice, and no
        page marker either. The marker is dropped for the same reason the
        notice is: it costs 16 characters that this regime does not have.
        Rendering it while it merely fit made the result *shrink* as the
        budget grew — measured on a 239-character page, ``max_chars=16``
        returned 16 characters of document and ``max_chars=17`` returned
        one, the marker having eaten the rest. Paying more for less is not
        a trade-off a caller asked for.
    """
    pages = extract_pdf_pages(data)
    if not any(text.strip() for _, text in pages):
        return ""

    chunks = _rendered_pages(pages, page_marker)
    total = len(pages)
    if max_chars is None:
        return _joined(chunks)

    complete = _joined(chunks)
    if len(complete) <= max_chars:
        return complete

    for count in range(total - 1, 0, -1):
        notice = truncation_notice.format(page=count, total=total)
        candidate = _joined(chunks[:count], notice)
        if len(candidate) <= max_chars:
            return candidate

    annotated = _annotated_first_page(
        pages[0],
        total=total,
        max_chars=max_chars,
        truncation_notice=truncation_notice,
        partial_page_notice=partial_page_notice,
    )
    if annotated is not None:
        return annotated

    for count in range(total - 1, 0, -1):
        candidate = _joined(chunks[:count])
        if len(candidate) <= max_chars:
            return candidate
    return pages[0][1].strip()[:max_chars]


__all__: list[str] = [
    "DEFAULT_PAGE_MARKER",
    "DEFAULT_PARTIAL_PAGE_NOTICE",
    "DEFAULT_TRUNCATION_NOTICE",
    "PageText",
    "extract_pdf_pages",
    "extract_pdf_text",
]
