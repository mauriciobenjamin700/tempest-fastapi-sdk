"""Tests for PDF text-layer extraction."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.pdf import extract_pdf_pages, extract_pdf_text

pytest.importorskip("pypdf")
pymupdf = pytest.importorskip("pymupdf")


def make_pdf(*pages: str) -> bytes:
    """Build a PDF whose pages carry the given text.

    Args:
        *pages (str): Text for each page, in order. An empty string produces
            a page with no text layer — the shape a scan has.

    Returns:
        bytes: The PDF file contents.
    """
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data: bytes = document.tobytes()
    document.close()
    return data


class TestExtractPdfPages:
    def test_returns_one_entry_per_page_in_order(self) -> None:
        pages = extract_pdf_pages(make_pdf("primeira", "segunda", "terceira"))
        assert [number for number, _ in pages] == [1, 2, 3]
        assert "primeira" in pages[0][1]
        assert "terceira" in pages[2][1]

    def test_page_numbers_are_one_based(self) -> None:
        assert extract_pdf_pages(make_pdf("x"))[0][0] == 1

    def test_empty_page_is_kept_so_numbering_stays_aligned(self) -> None:
        pages = extract_pdf_pages(make_pdf("um", "", "tres"))
        assert len(pages) == 3
        assert pages[1][1].strip() == ""
        assert "tres" in pages[2][1]


class TestExtractPdfText:
    def test_marks_each_page(self) -> None:
        text = extract_pdf_text(make_pdf("alpha", "beta"))
        assert "=== PAGE 1 ===" in text
        assert "=== PAGE 2 ===" in text
        assert text.index("alpha") < text.index("beta")

    def test_marker_is_customizable(self) -> None:
        text = extract_pdf_text(make_pdf("x"), page_marker="--- PÁGINA {page} ---")
        assert "--- PÁGINA 1 ---" in text
        assert "PAGE" not in text

    def test_scanned_document_returns_empty_string(self) -> None:
        """A scan has no text layer; a blank prompt invites a hallucination."""
        assert extract_pdf_text(make_pdf("", "")) == ""

    def test_truncates_at_a_page_boundary(self) -> None:
        text = extract_pdf_text(make_pdf("alpha", "beta", "gamma"), max_chars=40)
        assert "alpha" in text
        assert "gamma" not in text

    def test_truncation_is_announced(self) -> None:
        text = extract_pdf_text(make_pdf("alpha", "beta", "gamma"), max_chars=40)
        assert "TRUNCATED" in text
        assert "OF 3" in text

    def test_truncation_notice_is_customizable(self) -> None:
        text = extract_pdf_text(
            make_pdf("alpha", "beta", "gamma"),
            max_chars=40,
            truncation_notice="cortado em {page} de {total}",
        )
        assert "cortado em 1 de 3" in text

    def test_no_ceiling_keeps_everything(self) -> None:
        text = extract_pdf_text(make_pdf("alpha", "beta", "gamma"))
        assert "gamma" in text
        assert "TRUNCATED" not in text
