"""Read a PDF page by page, with tables and encrypted files handled.

:func:`~tempest_fastapi_sdk.pdf.extract_pdf_text` answers the common
question — "give me this document's text" — and is deliberately small: bytes
in, one string out, ``[pdf-read]`` and nothing else. This module answers the
harder ones, which a document-ingestion pipeline hits as soon as real files
arrive:

- **The document is a table.** ``pypdf`` reads a table as a stream of cells
  in visual order, which is unusable. ``pdfplumber`` recovers the grid.
- **The document has columns.** Layout-unaware extraction interleaves them
  line by line, and the result reads like two half-sentences.
- **The document is encrypted.** A password has to reach the reader, and a
  wrong one has to be distinguishable from a corrupt file.

Two backends, one call. :attr:`PdfExtractor.TEXT` is ``pypdf`` — fast, pure
Python, right for a plain prose document. :attr:`PdfExtractor.LAYOUT` is
``pdfplumber``, which is slower and needs the ``[pdf-layout]`` extra, and is
what recovers columns and tables.

**There is no OCR here either.** A scanned page carries an image and no text
layer, so it comes back with ``text=""`` rather than being dropped — entry
``n`` is always page ``n``. Check for it and route those files elsewhere;
handing a model an empty page is how a confident answer gets invented about
something nobody read.

    from tempest_fastapi_sdk.pdf import PdfExtractor, read_pdf_pages

    result = await read_pdf_pages(data, extractor=PdfExtractor.LAYOUT)
    for page in result.pages:
        print(page.page_number, page.text[:80], len(page.tables))
"""

from __future__ import annotations

import asyncio
from io import BytesIO

from pydantic import Field

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.schemas.base import BaseSchema

_MISSING_PYPDF = (
    "pypdf is required to read PDFs. Install the extra: "
    'pip install "tempest-fastapi-sdk[pdf-read]"'
)
_MISSING_PDFPLUMBER = (
    "pdfplumber is required for layout-aware extraction. Install the extra: "
    'pip install "tempest-fastapi-sdk[pdf-layout]"'
)


class PdfDecryptError(ValidationException):
    """The PDF is encrypted and the supplied password was wrong or absent."""

    message: str = "PDF is encrypted. Provide the correct password."
    code: str = "PDF_DECRYPT_FAILED"
    status_code: int = 400


class PdfExtractError(AppException):
    """Parsing the PDF failed for a reason that is not a wrong password."""

    message: str = "Failed to extract PDF content."
    code: str = "PDF_EXTRACT_FAILED"
    status_code: int = 422


class PdfExtractor(BaseStrEnum):
    """Which extraction backend reads the document.

    Attributes:
        TEXT: ``pypdf``. Fast, pure Python, no layout recovery, no tables.
            Needs ``[pdf-read]``.
        LAYOUT: ``pdfplumber``. Slower, keeps layout positioning — which is
            what makes a multi-column page readable — and fills
            :attr:`PdfPage.tables`. Needs ``[pdf-layout]``.
    """

    TEXT = "text"
    LAYOUT = "layout"


class PdfPage(BaseSchema):
    """One page's extracted contents.

    Attributes:
        page_number (int): One-based page index, matching the document.
        text (str): Plain text found on the page. Empty for a page with no
            text layer — a scan — rather than absent.
        tables (list[list[list[str]]]): Tables found on the page, only
            populated by :attr:`PdfExtractor.LAYOUT`. Outer list: tables;
            middle: rows; inner: cells. A cell the extractor read as empty
            is ``""``, never ``None``, so a row is always a list of strings.
    """

    page_number: int = Field(..., ge=1, description="One-based page index.")
    text: str = Field(default="", description="Plain text extracted from the page.")
    tables: list[list[list[str]]] = Field(
        default_factory=list,
        description=(
            "Tables found on the page (populated by the layout extractor "
            "only). Outer list: tables; middle: rows; inner: cells."
        ),
    )


class PdfPagesResult(BaseSchema):
    """A whole document, read page by page.

    Attributes:
        extractor (PdfExtractor): Which backend produced this result.
        page_count (int): Number of pages read.
        pages (list[PdfPage]): One entry per page, in document order.
    """

    extractor: PdfExtractor
    page_count: int = Field(..., ge=0)
    pages: list[PdfPage] = Field(default_factory=list)


def _extract_with_pypdf(data: bytes, password: str) -> list[PdfPage]:
    """Extract per-page text with ``pypdf``.

    Args:
        data (bytes): The PDF file contents.
        password (str): Password for an encrypted document; ``""`` when none.

    Returns:
        list[PdfPage]: One entry per page, tables always empty.

    Raises:
        ImportError: When the ``[pdf-read]`` extra is not installed.
        PdfDecryptError: When the document is encrypted and the password does
            not open it.
        PdfExtractError: When the bytes are not a readable PDF, or a page
            fails to extract.
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise ImportError(_MISSING_PYPDF) from exc

    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise PdfExtractError(details={"reason": str(exc)}) from exc

    if reader.is_encrypted:
        try:
            if reader.decrypt(password) == 0:
                raise PdfDecryptError()
        except (NotImplementedError, PdfReadError) as exc:
            raise PdfDecryptError(details={"reason": str(exc)}) from exc

    pages: list[PdfPage] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text: str = page.extract_text() or ""
        except Exception as exc:
            raise PdfExtractError(
                message_key="PDF_PAGE_EXTRACT_FAILED",
                message_params={"page": index},
                details={"reason": str(exc)},
            ) from exc
        pages.append(PdfPage(page_number=index, text=text.strip()))
    return pages


def _reason(exc: BaseException) -> str:
    """Describe an exception for the envelope's ``details``.

    A wrapper class often carries no message of its own — ``str()`` on
    ``PdfminerException`` is empty — which would put an empty ``reason`` in
    front of whoever is debugging. The class name is used in that case.

    Args:
        exc (BaseException): The exception to describe.

    Returns:
        str: The exception's message, or its class name when it has none.
    """
    return str(exc) or type(exc).__name__


def _password_failure(
    exc: BaseException, password_error: type[BaseException]
) -> BaseException | None:
    """Find a password failure anywhere inside a raised exception.

    ``pdfplumber`` reports every parse failure as a single
    ``PdfminerException`` built from the original error, so the wrong-password
    case arrives wrapped and an ``except PDFPasswordIncorrect`` never fires.
    The wrapped error reaches here as a positional argument, a ``__cause__``
    or a ``__context__`` depending on how the wrapper was raised, so all three
    are followed.

    Args:
        exc (BaseException): The exception the backend raised.
        password_error (type[BaseException]): The backend's wrong-password
            class.

    Returns:
        BaseException | None: The password failure when the chain holds one,
        otherwise ``None``.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, password_error):
            return current
        nested: BaseException | None = next(
            (arg for arg in current.args if isinstance(arg, BaseException)), None
        )
        current = nested or current.__cause__ or current.__context__
    return None


def _extract_with_pdfplumber(data: bytes, password: str) -> list[PdfPage]:
    """Extract per-page text and tables with ``pdfplumber``.

    Args:
        data (bytes): The PDF file contents.
        password (str): Password for an encrypted document; ``""`` when none.

    Returns:
        list[PdfPage]: One entry per page, with any tables the page holds.

    Raises:
        ImportError: When the ``[pdf-layout]`` extra is not installed.
        PdfDecryptError: When the password does not open the document.
        PdfExtractError: When parsing fails for any other reason.
    """
    try:
        import pdfplumber
        from pdfminer.pdfdocument import PDFPasswordIncorrect
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise ImportError(_MISSING_PDFPLUMBER) from exc

    try:
        opener = pdfplumber.open(BytesIO(data), password=password)
    except Exception as exc:
        wrong_password: BaseException | None = _password_failure(
            exc, PDFPasswordIncorrect
        )
        if wrong_password is not None:
            raise PdfDecryptError(details={"reason": _reason(wrong_password)}) from exc
        raise PdfExtractError(details={"reason": _reason(exc)}) from exc

    pages: list[PdfPage] = []
    try:
        with opener as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                try:
                    text: str = page.extract_text() or ""
                    raw_tables = page.extract_tables() or []
                except Exception as exc:
                    raise PdfExtractError(
                        message_key="PDF_PAGE_EXTRACT_FAILED",
                        message_params={"page": index},
                        details={"reason": str(exc)},
                    ) from exc
                pages.append(
                    PdfPage(
                        page_number=index,
                        text=text.strip(),
                        tables=[
                            [
                                ["" if cell is None else cell for cell in row]
                                for row in tbl
                            ]
                            for tbl in raw_tables
                        ],
                    )
                )
    except (PdfDecryptError, PdfExtractError):
        raise
    except Exception as exc:
        raise PdfExtractError(details={"reason": _reason(exc)}) from exc
    return pages


async def read_pdf_pages(
    data: bytes,
    *,
    extractor: PdfExtractor = PdfExtractor.TEXT,
    password: str | None = None,
) -> PdfPagesResult:
    """Read a PDF page by page, optionally recovering layout and tables.

    Both backends are synchronous and CPU-bound, so the work runs in a worker
    thread. A large scanned document can occupy that thread for seconds —
    this is a call to bound with a timeout, not one to fan out.

    Args:
        data (bytes): The PDF file contents.
        extractor (PdfExtractor): Which backend to use. Defaults to
            :attr:`PdfExtractor.TEXT`.
        password (str | None): Password for an encrypted document.

    Returns:
        PdfPagesResult: The document's pages, in order.

    Raises:
        ImportError: When the extra the chosen backend needs is missing.
        PdfDecryptError: When the document is encrypted and the password does
            not open it.
        PdfExtractError: When parsing fails for any other reason.
    """
    reader = (
        _extract_with_pdfplumber
        if extractor == PdfExtractor.LAYOUT
        else _extract_with_pypdf
    )
    pages: list[PdfPage] = await asyncio.to_thread(reader, data, password or "")
    return PdfPagesResult(extractor=extractor, page_count=len(pages), pages=pages)


__all__: list[str] = [
    "PdfDecryptError",
    "PdfExtractError",
    "PdfExtractor",
    "PdfPage",
    "PdfPagesResult",
    "read_pdf_pages",
]
