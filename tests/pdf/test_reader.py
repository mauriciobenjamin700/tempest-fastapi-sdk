"""Tests for PDF text-layer extraction."""

from __future__ import annotations

from itertools import pairwise

import pytest

from tempest_fastapi_sdk.pdf import (
    DEFAULT_TRUNCATION_NOTICE,
    extract_pdf_pages,
    extract_pdf_text,
)

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


def _body_of(text: str) -> str:
    """Strip marker and notice lines, leaving only document text.

    Args:
        text (str): A return value of ``extract_pdf_text``.

    Returns:
        str: The characters that came from the PDF, so an assertion can tell
        payload from annotation.
    """
    return "".join(line for line in text.splitlines() if not line.startswith("==="))


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
        """The budget has to hold the notice, which is part of it now.

        ``max_chars=40`` used to produce a 46-character result: the notice
        was appended after the budget had been spent. Now the budget is a
        ceiling, so the case that asserts a page-boundary notice asks for a
        budget that can hold page 1 plus the notice — derived, not guessed,
        because the number depends on how the renderer lays the text out.
        """
        data = make_pdf("alpha " * 10, "beta " * 10, "gamma " * 10)
        pages = extract_pdf_pages(data)
        page_one = len(f"=== PAGE 1 ===\n{pages[0][1].strip()}\n")
        notice = len(DEFAULT_TRUNCATION_NOTICE.format(page=1, total=3))
        budget = page_one + notice + 4

        text = extract_pdf_text(data, max_chars=budget)

        assert "TRUNCATED" in text
        assert "OF 3" in text
        assert "gamma" not in text
        assert len(text) <= budget

    def test_truncation_notice_is_customizable(self) -> None:
        text = extract_pdf_text(
            make_pdf("alpha", "beta", "gamma"),
            max_chars=60,
            truncation_notice="cortado em {page} de {total}",
        )
        assert "cortado em 1 de 3" in text

    def test_the_notice_never_names_page_zero(self) -> None:
        """``PAGE 0`` is a page that does not exist.

        It came from ``page=number - 1`` on the first iteration, so a
        document whose page 1 overflowed the budget was reported as
        truncated after a page nobody had.
        """
        for limit in range(5, 200, 3):
            text = extract_pdf_text(make_pdf("alpha", "beta"), max_chars=limit)
            assert "PAGE 0" not in text

    def test_the_budget_is_never_exceeded(self) -> None:
        """The ceiling is a ceiling, notice included."""
        data = make_pdf("alpha " * 8, "beta " * 8, "gamma " * 8)
        for limit in range(1, 260):
            assert len(extract_pdf_text(data, max_chars=limit)) <= limit

    def test_a_budget_below_the_first_page_still_returns_document_text(
        self,
    ) -> None:
        """The defect from the issue: only the notice came back.

        A caller sizing the prompt by ``max_chars`` handed the model an
        empty document and no way to notice — the string was not empty, so
        ``if not text`` did not catch it either.
        """
        text = extract_pdf_text(make_pdf("Recibo 4021 " * 6), max_chars=30)
        assert "Recibo" in text
        assert len(text) <= 30

    def test_a_mid_page_cut_is_announced_as_mid_page(self) -> None:
        """A partial page is not the same promise as a whole one."""
        text = extract_pdf_text(make_pdf("Recibo 4021 " * 20), max_chars=100)
        assert "TRUNCATED MID-PAGE" in text
        assert "Recibo" in text
        assert len(text) <= 100

    def test_the_mid_page_notice_is_customizable(self) -> None:
        text = extract_pdf_text(
            make_pdf("Recibo 4021 " * 20),
            max_chars=100,
            partial_page_notice="cortado no meio da {page}/{total}",
        )
        assert "cortado no meio da 1/1" in text

    def test_the_document_half_is_never_crowded_out(self) -> None:
        """Annotations qualify the payload; they must not replace it.

        Appending the notice unconditionally spent the whole budget on
        annotation: a 20-repetition page at ``max_chars=45`` came back with
        3 characters of document beside a 38-character notice. The floor is
        a third of the budget, and it holds for every budget from 1 up.

        One step down is expected and allowed: the budget where the warning
        first becomes affordable buys the warning with characters that were
        text a moment earlier. What the floor guarantees is that the step
        never lands near zero.
        """
        data = make_pdf("Recibo 4021 " * 20)
        for limit in range(1, 260):
            text = extract_pdf_text(data, max_chars=limit)
            body = _body_of(text)
            assert len(text) <= limit
            assert body.strip(), f"max_chars={limit} returned no document text"
            if "MID-PAGE" in text:
                assert len(body) >= limit // 3, f"max_chars={limit} body={len(body)}"

    def test_more_budget_never_buys_less_document(self) -> None:
        """The docs promise the step down happens once. It has to be once.

        Rendering the page marker whenever it merely fit produced a second
        step at the low end: on a 239-character page, ``max_chars=16``
        returned 16 characters of document and ``max_chars=17`` returned
        one, because the 16-character marker ate the budget. Two steps, and
        the third-of-the-budget floor did not see either — it only guards
        the regime that carries a notice.

        So the property is stated over the whole range: at most one budget
        buys less document than the budget before it, and that one is where
        the mid-page notice first becomes affordable.
        """
        data = make_pdf("Recibo 4021 " * 20)
        lengths = [
            (limit, extract_pdf_text(data, max_chars=limit)) for limit in range(1, 301)
        ]
        steps = [
            (limit, text)
            for (_, previous), (limit, text) in pairwise(lengths)
            if len(_body_of(text)) < len(_body_of(previous))
        ]

        assert len(steps) == 1, [limit for limit, _ in steps]
        assert "MID-PAGE" in steps[0][1]

    def test_mid_page_is_only_claimed_when_the_page_was_really_cut(self) -> None:
        """A warning a reader cannot check is worse than no warning.

        The first shape of this fix picked the notice by branch instead of by
        outcome: on a 3-page document at ``max_chars=120`` it handed over all
        59 characters of page 1 under ``TRUNCATED MID-PAGE``, when nothing
        had been cut mid-page — pages 2 and 3 were the part that was
        missing, and ``DOCUMENT TRUNCATED AFTER PAGE 1 OF 3`` says so in 106
        characters.
        """
        data = make_pdf("alpha " * 10, "beta " * 10, "gamma " * 10)
        page_one = extract_pdf_pages(data)[0][1].strip()

        for limit in range(1, 400):
            text = extract_pdf_text(data, max_chars=limit)
            if "MID-PAGE" in text:
                assert page_one not in text, f"max_chars={limit} cut nothing"

    def test_whole_pages_are_still_preferred_when_they_fit(self) -> None:
        """Page boundaries remain the first choice, not a fallback."""
        text = extract_pdf_text(make_pdf("alpha", "beta", "gamma"), max_chars=120)
        assert "=== PAGE 1 ===" in text
        assert "MID-PAGE" not in text

    def test_no_ceiling_keeps_everything(self) -> None:
        text = extract_pdf_text(make_pdf("alpha", "beta", "gamma"))
        assert "gamma" in text
        assert "TRUNCATED" not in text
