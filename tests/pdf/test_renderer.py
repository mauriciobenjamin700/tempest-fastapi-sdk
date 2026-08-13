"""Tests for ``PdfRenderer`` and the bundled documents.

Every render here goes through WeasyPrint. That is deliberate: the
properties worth holding — a PDF comes out, the same input gives the same
bytes, a hostile field cannot reshape the page, a refused asset stops the
render — are all properties of the real engine, and a stub would assert
none of them.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.pdf import (
    AssetPolicy,
    AssetRefused,
    Branding,
    Clause,
    ContractDocument,
    LineItem,
    Party,
    PdfDocument,
    PdfRenderer,
    QuoteDocument,
    ReceiptDocument,
    ReportColumn,
    ReportDocument,
    Signatory,
    TemplateNotFound,
    VoucherDocument,
    bundled_document_names,
    document_schema,
)

PDF_MAGIC: bytes = b"%PDF-"


def _receipt(**overrides: object) -> ReceiptDocument:
    """Build a valid receipt, overriding any field.

    Args:
        **overrides (object): Fields to replace.

    Returns:
        ReceiptDocument: The document.
    """
    values: dict[str, object] = {
        "issue_date": date(2026, 8, 13),
        "issuer": Party(name="Acme LTDA", document="12345678000195"),
        "payer": Party(name="Ana Souza", document="12345678901"),
        "amount_cents": 125000,
        "reference": "consultoria de julho/2026",
        "place": "Recife",
    }
    values.update(overrides)
    return ReceiptDocument(**values)  # type: ignore[arg-type]


def _all_documents() -> list[PdfDocument]:
    """Build one valid instance of every bundled document.

    Returns:
        list[PdfDocument]: The documents, one per bundled template.
    """
    party = Party(name="Acme LTDA", document="12345678000195")
    return [
        _receipt(),
        QuoteDocument(
            issue_date=date(2026, 8, 13),
            issuer=party,
            customer=Party(name="Ana Souza"),
            items=[
                LineItem(
                    description="Consultoria", quantity=40, unit_price_cents=15000
                ),
            ],
        ),
        ReportDocument(
            heading="Vendas",
            columns=[
                ReportColumn(key="cliente", header="Cliente"),
                ReportColumn(key="total_cents", header="Total", money=True),
            ],
            rows=[{"cliente": "Ana", "total_cents": 1000}],
            totals={"total_cents": 1000},
        ),
        ContractDocument(
            heading="Contrato",
            clauses=[Clause(title="DO OBJETO", body="Primeiro.\n\nSegundo.")],
            signatories=[Signatory(name="Ana Souza")],
        ),
        VoucherDocument(heading="Comprovante", fields={"Valor": "R$ 1,00"}),
    ]


class TestBundledDocuments:
    def test_every_bundled_name_resolves_to_a_schema(self) -> None:
        """The registry and the schemas cannot drift apart."""
        for name in bundled_document_names():
            schema = document_schema(name)
            assert schema.template, f"{name} declares no template"

    def test_unknown_document_names_the_alternatives(self) -> None:
        """A typo should be one read away from fixed."""
        with pytest.raises(TemplateNotFound) as excinfo:
            document_schema("nota")
        assert "receipt" in str(excinfo.value.detail)

    @pytest.mark.parametrize("document", _all_documents(), ids=lambda d: d.template)
    async def test_renders_to_a_pdf(self, document: PdfDocument) -> None:
        """Each bundled template produces a real PDF."""
        pdf = await PdfRenderer().render_document(document)
        assert pdf.startswith(PDF_MAGIC)
        assert len(pdf) > 1000

    async def test_base_class_without_a_template_is_refused(self) -> None:
        """A subclass that forgot the class variable fails loudly."""
        with pytest.raises(ValidationException, match="declares no template"):
            await PdfRenderer().render_document(PdfDocument())


class TestDeterminism:
    async def test_same_payload_gives_the_same_bytes(self) -> None:
        """This is what makes a document hashable and cacheable.

        WeasyPrint writes no creation date and no document identifier
        unless asked; if that ever changes upstream, a rendered document
        stops comparing equal to itself and this fails.
        """
        renderer = PdfRenderer()
        first = await renderer.render_document(_receipt())
        second = await renderer.render_document(_receipt())
        assert first == second

    async def test_different_payloads_differ(self) -> None:
        """Determinism must not come from ignoring the input."""
        renderer = PdfRenderer()
        first = await renderer.render_document(_receipt(amount_cents=1000))
        second = await renderer.render_document(_receipt(amount_cents=2000))
        assert first != second


class TestEscaping:
    async def test_a_field_cannot_inject_markup(self) -> None:
        """Document data is frequently not written by the person asking.

        The rendered HTML is checked rather than the PDF, because that is
        where the injection would land; the PDF is downstream of it.
        """
        renderer = PdfRenderer()
        hostile = _receipt(
            payer=Party(name="<script>alert(1)</script> & Cia"),
        )
        html = renderer.render_html_string("receipt.html", {"doc": hostile})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&amp; Cia" in html

    async def test_a_hostile_name_still_renders(self) -> None:
        """Escaping must not turn into a failed render."""
        pdf = await PdfRenderer().render_document(
            _receipt(payer=Party(name="A & B <Ltda>")),
        )
        assert pdf.startswith(PDF_MAGIC)


class TestTemplateShadowing:
    async def test_project_templates_win(self, tmp_path: Path) -> None:
        """A project overrides a bundled document by file name."""
        (tmp_path / "receipt.html").write_text(
            "<html><body><h1>OVERRIDDEN {{ doc.amount_cents }}</h1></body></html>",
            encoding="utf-8",
        )
        renderer = PdfRenderer(template_dir=tmp_path)
        html = renderer.render_html_string("receipt.html", {"doc": _receipt()})
        assert "OVERRIDDEN 125000" in html

    async def test_bundled_templates_remain_reachable(self, tmp_path: Path) -> None:
        """Shadowing one template does not hide the rest."""
        renderer = PdfRenderer(template_dir=tmp_path)
        pdf = await renderer.render_document(_receipt())
        assert pdf.startswith(PDF_MAGIC)

    def test_missing_template_is_reported(self) -> None:
        """Naming the template beats a Jinja traceback."""
        with pytest.raises(TemplateNotFound, match=r"absent\.html"):
            PdfRenderer().render_html_string("absent.html", {})

    def test_template_dir_must_exist(self, tmp_path: Path) -> None:
        """A typo would silently fall back to the bundled set."""
        with pytest.raises(ValueError, match="not a directory"):
            PdfRenderer(template_dir=tmp_path / "absent")


class TestAssetEnforcement:
    async def test_a_refused_asset_aborts_the_render(self) -> None:
        """A silently missing logo on an invoice is the worst outcome."""
        renderer = PdfRenderer()
        html = '<html><body><img src="file:///etc/passwd"></body></html>'
        with pytest.raises(AssetRefused) as excinfo:
            await renderer.render_html(html)
        assert "refused" in excinfo.value.details

    async def test_lenient_mode_renders_without_the_asset(self) -> None:
        """Opt-in, and it still logs what it dropped."""
        renderer = PdfRenderer(strict_assets=False)
        html = '<html><body><img src="file:///etc/passwd">ok</body></html>'
        pdf = await renderer.render_html(html)
        assert pdf.startswith(PDF_MAGIC)

    async def test_an_allowed_directory_lets_the_image_through(
        self,
        tmp_path: Path,
    ) -> None:
        """The permissive path works when somebody names the directory."""
        import base64

        image = tmp_path / "logo.png"
        image.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
                "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            ),
        )
        renderer = PdfRenderer(assets=AssetPolicy(allow_dirs=(tmp_path,)))
        html = f'<html><body><img src="{image.as_uri()}"></body></html>'
        pdf = await renderer.render_html(html)
        assert pdf.startswith(PDF_MAGIC)


class TestComputedTotals:
    def test_line_totals_and_subtotal_come_from_the_items(self) -> None:
        """A printed total that disagrees with its lines is invisible."""
        quote = QuoteDocument(
            issue_date=date(2026, 8, 13),
            issuer=Party(name="Acme"),
            customer=Party(name="Ana"),
            items=[
                LineItem(description="A", quantity=2, unit_price_cents=1000),
                LineItem(description="B", quantity=2.5, unit_price_cents=999),
            ],
            discount_cents=500,
        )
        assert quote.items[0].total_cents == 2000
        assert quote.items[1].total_cents == 2498
        assert quote.subtotal_cents == 4498
        assert quote.total_cents == 3998

    def test_fractional_lines_round_half_up(self) -> None:
        """Banker's rounding would make the lines stop adding up."""
        item = LineItem(description="A", quantity=0.5, unit_price_cents=5)
        assert item.total_cents == 3

    def test_a_discount_above_the_subtotal_is_refused(self) -> None:
        """A negative total would print as if it were a real price."""
        with pytest.raises(ValueError, match="larger than the subtotal"):
            QuoteDocument(
                issue_date=date(2026, 8, 13),
                issuer=Party(name="Acme"),
                customer=Party(name="Ana"),
                items=[LineItem(description="A", unit_price_cents=100)],
                discount_cents=200,
            )


class TestBrandingConstraints:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("accent_color", "#fff; } body { display: none } .x {"),
            ("page_size", "A4; } @page { size: 1mm"),
            ("margin", "0} body{display:none"),
            ("logo_data_uri", "https://evil.test/logo.png"),
        ],
    )
    def test_values_that_reach_css_are_constrained(
        self,
        field: str,
        value: str,
    ) -> None:
        """These land inside a stylesheet, so their shape is validated.

        Without it, a caller-supplied colour could close the rule and add
        declarations of its own — and a remote logo URL would produce a
        document with no logo, since the default policy fetches nothing.
        """
        with pytest.raises(ValueError):
            Branding(**{field: value})  # type: ignore[arg-type]

    def test_legitimate_values_pass(self) -> None:
        """The constraint must not reject what people actually use."""
        Branding(
            accent_color="rgb(11, 107, 203)",
            page_size="A4 landscape",
            margin="1in 0.5in",
        )
        Branding(accent_color="rebeccapurple")


class TestReportEdgeCases:
    async def test_an_empty_report_renders_with_a_notice(self) -> None:
        """A blank page reads as a broken export."""
        renderer = PdfRenderer()
        report = ReportDocument(
            heading="Vendas",
            columns=[ReportColumn(key="a", header="A")],
            rows=[],
        )
        html = renderer.render_html_string("report.html", {"doc": report})
        assert "Nenhum registro" in html
        pdf = await renderer.render_document(report)
        assert pdf.startswith(PDF_MAGIC)

    def test_a_missing_key_prints_empty_rather_than_failing(self) -> None:
        """Reports are routinely assembled from partial data."""
        report = ReportDocument(
            heading="Vendas",
            columns=[
                ReportColumn(key="a", header="A"),
                ReportColumn(key="b", header="B"),
            ],
            rows=[{"a": "x"}],
        )
        html = PdfRenderer().render_html_string("report.html", {"doc": report})
        assert "x" in html

    async def test_the_grand_total_prints_on_the_last_page_only(self) -> None:
        """A total repeated per page would not match the rows above it.

        The row lives in ``<tbody>`` rather than ``<tfoot>`` because a
        table footer group repeats on every page — which is how this
        shipped first, printing the grand total at the foot of page 2
        above rows that summed to something else.

        Asserted against the rendered pages rather than the HTML: where
        a row lands is a layout decision, so only the PDF can answer it.
        """
        pymupdf = pytest.importorskip("pymupdf")
        report = ReportDocument(
            heading="Vendas",
            columns=[
                ReportColumn(key="cliente", header="Cliente"),
                ReportColumn(key="total_cents", header="Total", money=True),
            ],
            rows=[{"cliente": f"C{i}", "total_cents": 100} for i in range(120)],
            totals={"total_cents": 12000},
        )
        pdf = await PdfRenderer().render_document(report)
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            pages = [page.get_text() for page in document]
        assert len(pages) > 1, "the fixture must span several pages to mean anything"
        assert sum("R$ 120,00" in text for text in pages) == 1
        assert "R$ 120,00" in pages[-1]

    async def test_the_table_header_repeats_on_every_page(self) -> None:
        """A continuation page whose columns are unlabelled is unreadable."""
        pymupdf = pytest.importorskip("pymupdf")
        report = ReportDocument(
            heading="Vendas",
            columns=[ReportColumn(key="cliente", header="Cliente")],
            rows=[{"cliente": f"C{i}"} for i in range(120)],
        )
        pdf = await PdfRenderer().render_document(report)
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            pages = [page.get_text() for page in document]
        assert len(pages) > 1
        assert all("CLIENTE" in text.upper() for text in pages)

    async def test_pages_are_numbered_x_of_y(self) -> None:
        """Somebody has to be able to tell a printed page is missing."""
        pymupdf = pytest.importorskip("pymupdf")
        report = ReportDocument(
            heading="Vendas",
            columns=[ReportColumn(key="cliente", header="Cliente")],
            rows=[{"cliente": f"C{i}"} for i in range(120)],
        )
        pdf = await PdfRenderer().render_document(report)
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            pages = [page.get_text() for page in document]
        total = len(pages)
        assert f"página 1 de {total}" in pages[0]
        assert f"página {total} de {total}" in pages[-1]


class TestConcurrency:
    def test_max_concurrent_must_be_positive(self) -> None:
        """Zero would deadlock on the first render."""
        with pytest.raises(ValueError, match="max_concurrent"):
            PdfRenderer(max_concurrent=0)

    async def test_concurrent_renders_all_complete(self) -> None:
        """Layout runs in a worker thread, so the loop stays free."""
        import asyncio

        renderer = PdfRenderer(max_concurrent=2)
        results = await asyncio.gather(
            *(renderer.render_document(_receipt(amount_cents=n)) for n in range(1, 6)),
        )
        assert all(pdf.startswith(PDF_MAGIC) for pdf in results)
        assert len({bytes(pdf) for pdf in results}) == 5
