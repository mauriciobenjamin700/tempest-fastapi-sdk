"""PDF generation from HTML templates.

Renders documents — receipts, quotes, paginated reports, contracts,
vouchers — through WeasyPrint, from typed Pydantic payloads and Jinja2
templates the project can shadow file by file.

    from tempest_fastapi_sdk.pdf import PdfRenderer, ReceiptDocument

    renderer = PdfRenderer()
    pdf = await renderer.render_document(
        ReceiptDocument(
            issue_date=date.today(),
            issuer=Party(name="Acme LTDA"),
            payer=Party(name="Ana Souza"),
            amount_cents=125000,
            reference="Consultoria de julho/2026",
        ),
    )

Needs the ``[pdf]`` extra (``weasyprint`` + ``jinja2``) **and** Pango and
fontconfig from the system — on Debian/Ubuntu ``libpango-1.0-0
libpangoft2-1.0-0 libharfbuzz0b fontconfig fonts-dejavu-core``. A slim
Python image has none of them; ``tempest generate --dockerfile`` emits
the line when the project pins the extra. Importing this module needs
neither: the engine is imported at first render, so the schemas and
formatting helpers are usable anywhere.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit re-export
form combined with ``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.pdf import PdfRenderer`` without a diagnostic.
"""

from tempest_fastapi_sdk.pdf.assets import (
    DEFAULT_MAX_ASSET_BYTES as DEFAULT_MAX_ASSET_BYTES,
)
from tempest_fastapi_sdk.pdf.assets import (
    DEFAULT_REMOTE_TIMEOUT as DEFAULT_REMOTE_TIMEOUT,
)
from tempest_fastapi_sdk.pdf.assets import AssetPolicy as AssetPolicy
from tempest_fastapi_sdk.pdf.assets import AssetRefused as AssetRefused
from tempest_fastapi_sdk.pdf.assets import build_url_fetcher as build_url_fetcher
from tempest_fastapi_sdk.pdf.documents import BUNDLED_DOCUMENTS as BUNDLED_DOCUMENTS
from tempest_fastapi_sdk.pdf.documents import Branding as Branding
from tempest_fastapi_sdk.pdf.documents import Clause as Clause
from tempest_fastapi_sdk.pdf.documents import ContractDocument as ContractDocument
from tempest_fastapi_sdk.pdf.documents import LineItem as LineItem
from tempest_fastapi_sdk.pdf.documents import Party as Party
from tempest_fastapi_sdk.pdf.documents import PdfDocument as PdfDocument
from tempest_fastapi_sdk.pdf.documents import QuoteDocument as QuoteDocument
from tempest_fastapi_sdk.pdf.documents import ReceiptDocument as ReceiptDocument
from tempest_fastapi_sdk.pdf.documents import ReportColumn as ReportColumn
from tempest_fastapi_sdk.pdf.documents import ReportDocument as ReportDocument
from tempest_fastapi_sdk.pdf.documents import Signatory as Signatory
from tempest_fastapi_sdk.pdf.documents import VoucherDocument as VoucherDocument
from tempest_fastapi_sdk.pdf.formatting import MAX_EXTENSO_CENTS as MAX_EXTENSO_CENTS
from tempest_fastapi_sdk.pdf.formatting import MONTHS_PT_BR as MONTHS_PT_BR
from tempest_fastapi_sdk.pdf.formatting import format_cents as format_cents
from tempest_fastapi_sdk.pdf.formatting import format_date as format_date
from tempest_fastapi_sdk.pdf.formatting import format_date_long as format_date_long
from tempest_fastapi_sdk.pdf.formatting import format_document as format_document
from tempest_fastapi_sdk.pdf.formatting import format_quantity as format_quantity
from tempest_fastapi_sdk.pdf.formatting import valor_por_extenso as valor_por_extenso
from tempest_fastapi_sdk.pdf.reader import (
    DEFAULT_PAGE_MARKER as DEFAULT_PAGE_MARKER,
)
from tempest_fastapi_sdk.pdf.reader import (
    DEFAULT_TRUNCATION_NOTICE as DEFAULT_TRUNCATION_NOTICE,
)
from tempest_fastapi_sdk.pdf.reader import (
    PageText as PageText,
)
from tempest_fastapi_sdk.pdf.reader import (
    extract_pdf_pages as extract_pdf_pages,
)
from tempest_fastapi_sdk.pdf.reader import (
    extract_pdf_text as extract_pdf_text,
)
from tempest_fastapi_sdk.pdf.renderer import (
    BUNDLED_TEMPLATE_DIR as BUNDLED_TEMPLATE_DIR,
)
from tempest_fastapi_sdk.pdf.renderer import (
    DEFAULT_MAX_CONCURRENT_RENDERS as DEFAULT_MAX_CONCURRENT_RENDERS,
)
from tempest_fastapi_sdk.pdf.renderer import PdfRenderer as PdfRenderer
from tempest_fastapi_sdk.pdf.renderer import TemplateNotFound as TemplateNotFound
from tempest_fastapi_sdk.pdf.renderer import (
    bundled_document_names as bundled_document_names,
)
from tempest_fastapi_sdk.pdf.renderer import document_schema as document_schema
from tempest_fastapi_sdk.pdf.router import PDF_MEDIA_TYPE as PDF_MEDIA_TYPE
from tempest_fastapi_sdk.pdf.router import DocumentListSchema as DocumentListSchema
from tempest_fastapi_sdk.pdf.router import (
    DocumentRequestSchema as DocumentRequestSchema,
)
from tempest_fastapi_sdk.pdf.router import make_pdf_router as make_pdf_router
from tempest_fastapi_sdk.pdf.router import safe_filename as safe_filename

__all__: list[str] = [
    "BUNDLED_DOCUMENTS",
    "BUNDLED_TEMPLATE_DIR",
    "DEFAULT_MAX_ASSET_BYTES",
    "DEFAULT_MAX_CONCURRENT_RENDERS",
    "DEFAULT_PAGE_MARKER",
    "DEFAULT_REMOTE_TIMEOUT",
    "DEFAULT_TRUNCATION_NOTICE",
    "MAX_EXTENSO_CENTS",
    "MONTHS_PT_BR",
    "PDF_MEDIA_TYPE",
    "AssetPolicy",
    "AssetRefused",
    "Branding",
    "Clause",
    "ContractDocument",
    "DocumentListSchema",
    "DocumentRequestSchema",
    "LineItem",
    "PageText",
    "Party",
    "PdfDocument",
    "PdfRenderer",
    "QuoteDocument",
    "ReceiptDocument",
    "ReportColumn",
    "ReportDocument",
    "Signatory",
    "TemplateNotFound",
    "VoucherDocument",
    "build_url_fetcher",
    "bundled_document_names",
    "document_schema",
    "extract_pdf_pages",
    "extract_pdf_text",
    "format_cents",
    "format_date",
    "format_date_long",
    "format_document",
    "format_quantity",
    "make_pdf_router",
    "safe_filename",
    "valor_por_extenso",
]
