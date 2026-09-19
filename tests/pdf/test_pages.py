"""Page-by-page extraction, with tables and encrypted documents."""

import pytest

from tempest_fastapi_sdk.pdf import (
    PdfDecryptError,
    PdfExtractError,
    PdfExtractor,
    read_pdf_pages,
)

pytest.importorskip("pypdf")
pytest.importorskip("pdfplumber")
pymupdf = pytest.importorskip("pymupdf")


def _build_pdf(pages: list[str], password: str | None = None) -> bytes:
    """Write a small PDF carrying one text line per page.

    Args:
        pages (list[str]): Text to draw on each page, in order. An empty
            string produces a page with no text layer — the shape a scan has.
        password (str | None): Encrypt the document with this password.

    Returns:
        bytes: The finished PDF.
    """
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data: bytes = (
        document.tobytes()
        if password is None
        else document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw=password,
            user_pw=password,
        )
    )
    document.close()
    return data


@pytest.fixture(scope="module")
def two_page_pdf() -> bytes:
    """A two-page document with distinct text on each page."""
    return _build_pdf(["First page body", "Second page body"])


class TestExtractors:
    """Both backends read the same document and agree on its shape."""

    @pytest.mark.parametrize("extractor", [PdfExtractor.TEXT, PdfExtractor.LAYOUT])
    async def test_page_boundaries_survive(
        self, two_page_pdf: bytes, extractor: PdfExtractor
    ) -> None:
        """Entry ``n`` is page ``n``, which is what lets a quote be cited."""
        result = await read_pdf_pages(two_page_pdf, extractor=extractor)
        assert result.page_count == 2
        assert [page.page_number for page in result.pages] == [1, 2]
        assert "First page" in result.pages[0].text
        assert "Second page" in result.pages[1].text

    async def test_the_result_names_its_extractor(self, two_page_pdf: bytes) -> None:
        """A caller can tell whether tables were even looked for."""
        result = await read_pdf_pages(two_page_pdf, extractor=PdfExtractor.LAYOUT)
        assert result.extractor == PdfExtractor.LAYOUT

    async def test_the_text_backend_never_reports_tables(
        self, two_page_pdf: bytes
    ) -> None:
        """``pypdf`` cannot recover a grid, so it claims none."""
        result = await read_pdf_pages(two_page_pdf, extractor=PdfExtractor.TEXT)
        assert all(page.tables == [] for page in result.pages)

    async def test_a_page_without_text_is_kept_as_empty(self) -> None:
        """A blank page is entry ``n``, not a gap.

        Dropping it would shift every later page number, and a citation to
        "page 4" would point at page 5.
        """
        result = await read_pdf_pages(_build_pdf(["", "content here"]))
        assert result.page_count == 2
        assert result.pages[0].text == ""


class TestEncryptedDocuments:
    """A password has to reach the reader, and a wrong one has to be legible."""

    @pytest.mark.parametrize("extractor", [PdfExtractor.TEXT, PdfExtractor.LAYOUT])
    async def test_the_right_password_opens_it(self, extractor: PdfExtractor) -> None:
        """With the password, the document reads normally."""
        data = _build_pdf(["Secret body"], password="hunter2")
        result = await read_pdf_pages(data, extractor=extractor, password="hunter2")
        assert "Secret body" in result.pages[0].text

    @pytest.mark.parametrize("extractor", [PdfExtractor.TEXT, PdfExtractor.LAYOUT])
    async def test_a_wrong_password_is_its_own_error(
        self, extractor: PdfExtractor
    ) -> None:
        """A wrong password is fixable; a corrupt file is not.

        Collapsing both into one error would send a caller looking for a
        broken document when the document is fine.
        """
        data = _build_pdf(["Secret body"], password="hunter2")
        with pytest.raises(PdfDecryptError):
            await read_pdf_pages(data, extractor=extractor, password="wrong")

    @pytest.mark.parametrize("extractor", [PdfExtractor.TEXT, PdfExtractor.LAYOUT])
    async def test_a_missing_password_is_the_same_error(
        self, extractor: PdfExtractor
    ) -> None:
        """Supplying nothing for an encrypted file reports the same fix."""
        data = _build_pdf(["Secret body"], password="hunter2")
        with pytest.raises(PdfDecryptError):
            await read_pdf_pages(data, extractor=extractor)


class TestMalformedInput:
    """Bytes that are not a PDF are a parse failure, not a crash."""

    @pytest.mark.parametrize("extractor", [PdfExtractor.TEXT, PdfExtractor.LAYOUT])
    async def test_garbage_raises_extract_error(self, extractor: PdfExtractor) -> None:
        """Both backends report the same code for unreadable input."""
        with pytest.raises(PdfExtractError):
            await read_pdf_pages(b"this is not a pdf", extractor=extractor)
