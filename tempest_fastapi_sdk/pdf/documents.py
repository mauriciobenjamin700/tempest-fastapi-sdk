"""Typed payloads for the bundled documents.

Every bundled template has a schema, and the schema is what the template
renders from. That is the whole reason the templates are worth shipping:
an HTML file alone tells you nothing about which keys it needs, so the
first missing field shows up as a blank space in a signed document. Here
a receipt without a payer does not render — it fails validation, with
the field named.

Money is in **cents**, as integers, matching the rest of the SDK. Totals
are computed from the items rather than accepted from the caller: a
document whose printed total disagrees with its own lines is the one
defect nobody catches by looking.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from pydantic import Field, computed_field, model_validator

from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils.fields import NonEmptyStrField


class Party(BaseSchema):
    """A person or company printed on a document.

    Attributes:
        name (str): Legal or trade name.
        document (str | None): CPF or CNPJ. Rendered through
            :func:`~tempest_fastapi_sdk.pdf.formatting.format_document`,
            which formats 11 and 14 digits and leaves anything else
            untouched.
        address (str | None): Free-form address, one line or several.
        email (str | None): Contact email.
        phone (str | None): Contact phone.
        extra (dict[str, str]): Additional labelled lines, printed in
            order — ``{"Inscrição estadual": "123"}``.
    """

    name: NonEmptyStrField = Field(
        title="Nome",
        description="Legal or trade name shown on the document.",
        examples=["Acme Serviços LTDA", "Ana Souza"],
    )
    document: str | None = Field(
        default=None,
        title="CPF / CNPJ",
        description=(
            "Formatted for display; a value that is neither is printed as given."
        ),
        examples=["12345678901", "12.345.678/0001-95", None],
    )
    address: str | None = Field(
        default=None,
        title="Endereço",
        description="Free-form address. Line breaks are preserved.",
        examples=["Rua das Flores, 100 — Centro, Recife/PE", None],
    )
    email: str | None = Field(
        default=None,
        title="E-mail",
        description="Contact email.",
        examples=["contato@acme.com.br", None],
    )
    phone: str | None = Field(
        default=None,
        title="Telefone",
        description="Contact phone.",
        examples=["(81) 99999-0000", None],
    )
    extra: dict[str, str] = Field(
        default_factory=dict,
        title="Linhas adicionais",
        description="Extra labelled lines, printed in insertion order.",
        examples=[{"Inscrição estadual": "123.456.789"}],
    )


class Branding(BaseSchema):
    """Appearance shared by every bundled document.

    Attributes:
        logo_data_uri (str | None): Logo as a ``data:`` URI. A URI
            rather than a path because the default asset policy fetches
            nothing — see
            :class:`~tempest_fastapi_sdk.pdf.assets.AssetPolicy`.
        accent_color (str): Colour of rules, table headers and totals.
        footer_text (str | None): Line printed at the foot of every
            page, next to the page number.
        page_size (str): CSS ``@page size`` value.
        margin (str): CSS ``@page margin`` value.
    """

    logo_data_uri: str | None = Field(
        default=None,
        pattern=r"^data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+$",
        title="Logo (data URI)",
        description=(
            "Embedded image, e.g. ``data:image/png;base64,...``. A URL "
            "is rejected here rather than at render time: the default "
            "asset policy fetches nothing, so it would silently produce "
            "a document with no logo."
        ),
        examples=["data:image/png;base64,iVBORw0KGgo=", None],
    )
    accent_color: str = Field(
        default="#1f2933",
        pattern=r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20}|rgba?\([0-9,.%\s]+\))$",
        title="Cor de destaque",
        description=(
            "A CSS colour: hex, a named colour, or ``rgb()``/``rgba()``. "
            "The shape is constrained because this value is written into "
            "a stylesheet — anything carrying ``;`` or ``}`` could close "
            "the rule and add declarations of its own."
        ),
        examples=["#1f2933", "#0b6bcb", "rgb(11, 107, 203)"],
    )
    footer_text: str | None = Field(
        default=None,
        title="Rodapé",
        description="Printed on every page, opposite the page number.",
        examples=["Acme LTDA · CNPJ 12.345.678/0001-95", None],
    )
    page_size: str = Field(
        default="A4",
        pattern=r"^[A-Za-z0-9 .]{1,40}$",
        title="Tamanho da página",
        description=(
            "CSS ``@page size``: a named size, optionally with "
            "``landscape``, or explicit dimensions. Constrained for the "
            "same reason as ``accent_color`` — it lands in a stylesheet."
        ),
        examples=["A4", "A4 landscape", "Letter", "210mm 297mm"],
    )
    margin: str = Field(
        default="18mm 16mm",
        pattern=r"^[A-Za-z0-9 .%]{1,40}$",
        title="Margem da página",
        description="CSS ``@page margin``. Constrained like ``page_size``.",
        examples=["18mm 16mm", "10mm", "1in 0.5in"],
    )


class LineItem(BaseSchema):
    """One priced line of a quote.

    Attributes:
        description (str): What is being charged for.
        quantity (float): How many. Fractional quantities are allowed
            (hours, kilos).
        unit_price_cents (int): Price of one unit, in cents.
        unit (str | None): Unit label shown next to the quantity.
    """

    description: NonEmptyStrField = Field(
        title="Descrição",
        description="What is being charged for.",
        examples=["Consultoria técnica"],
    )
    quantity: float = Field(
        default=1.0,
        gt=0,
        title="Quantidade",
        description="How many units. Fractional values are allowed.",
        examples=[1, 2.5, 40],
    )
    unit_price_cents: int = Field(
        ge=0,
        title="Preço unitário (centavos)",
        description="Price of a single unit, in cents.",
        examples=[15000, 250000],
    )
    unit: str | None = Field(
        default=None,
        title="Unidade",
        description="Label shown next to the quantity.",
        examples=["h", "un", "kg", None],
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cents(self) -> int:
        """Line total, in cents.

        Rounded half-up to the cent, because a fractional quantity times
        a cent price does not land on an integer and the printed line
        must add up to the printed total.

        Returns:
            int: ``quantity * unit_price_cents``, rounded to the cent.
        """
        from decimal import ROUND_HALF_UP, Decimal

        exact = Decimal(str(self.quantity)) * Decimal(self.unit_price_cents)
        return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class Clause(BaseSchema):
    """One numbered clause of a contract.

    Attributes:
        title (str | None): Heading. Omit it for a plain paragraph.
        body (str): Text. Blank lines separate paragraphs.
    """

    title: str | None = Field(
        default=None,
        title="Título da cláusula",
        description="Heading, or ``null`` for an unheaded paragraph.",
        examples=["DO OBJETO", None],
    )
    body: NonEmptyStrField = Field(
        title="Texto",
        description="Clause text. Blank lines start a new paragraph.",
        examples=["O CONTRATADO prestará os serviços descritos no Anexo I."],
    )


class Signatory(BaseSchema):
    """One signature block at the foot of a contract.

    Attributes:
        name (str): Who signs.
        role (str | None): Their part — ``CONTRATANTE``, ``Testemunha``.
        document (str | None): CPF or CNPJ, printed under the line.
    """

    name: NonEmptyStrField = Field(
        title="Nome",
        description="Name printed under the signature line.",
        examples=["Ana Souza"],
    )
    role: str | None = Field(
        default=None,
        title="Qualificação",
        description="Their part in the contract.",
        examples=["CONTRATANTE", "Testemunha", None],
    )
    document: str | None = Field(
        default=None,
        title="CPF / CNPJ",
        description="Printed under the name.",
        examples=["12345678901", None],
    )


class ReportColumn(BaseSchema):
    """One column of a tabular report.

    Attributes:
        key (str): Key read from each row.
        header (str): Column heading.
        align (str): ``left``, ``right`` or ``center``.
        money (bool): Render the value as currency. The row must then
            carry an integer number of cents.
    """

    key: NonEmptyStrField = Field(
        title="Chave",
        description="Key read from each row mapping.",
        examples=["customer", "total_cents"],
    )
    header: NonEmptyStrField = Field(
        title="Cabeçalho",
        description="Text shown in the header row.",
        examples=["Cliente", "Total"],
    )
    align: str = Field(
        default="left",
        pattern="^(left|right|center)$",
        title="Alinhamento",
        description="Horizontal alignment of the column.",
        examples=["left", "right", "center"],
    )
    money: bool = Field(
        default=False,
        title="Coluna monetária",
        description="Format the value as BRL. Values must be cents.",
        examples=[False, True],
    )


class PdfDocument(BaseSchema):
    """Base for a bundled document.

    A subclass names the template it renders through, which is how
    :meth:`~tempest_fastapi_sdk.pdf.renderer.PdfRenderer.render_document`
    resolves one from the payload alone.

    Attributes:
        template (ClassVar[str]): Bundled template file name.
        branding (Branding): Shared appearance.
        title (str | None): Overrides the document's default heading.
    """

    template: ClassVar[str] = ""

    branding: Branding = Field(
        default_factory=Branding,
        title="Aparência",
        description="Logo, colour, footer and page geometry.",
    )
    title: str | None = Field(
        default=None,
        title="Título",
        description="Overrides the document's default heading.",
        examples=["RECIBO DE PAGAMENTO", None],
    )


class ReceiptDocument(PdfDocument):
    """A *recibo* — proof that an amount was paid.

    Carries the amount in words, which is the element that keeps a
    figure from being altered after signing.

    Attributes:
        number (str | None): Receipt number.
        issue_date (date): Date printed and used in the closing line.
        issuer (Party): Who received the money and signs.
        payer (Party): Who paid.
        amount_cents (int): Amount received.
        reference (str): What the payment was for.
        place (str | None): City printed before the date.
    """

    template: ClassVar[str] = "receipt.html"

    number: str | None = Field(
        default=None,
        title="Número",
        description="Receipt number, when the issuer keeps a sequence.",
        examples=["0001/2026", None],
    )
    issue_date: date = Field(
        title="Data de emissão",
        description="Printed in full in the closing line.",
        examples=["2026-08-13"],
    )
    issuer: Party = Field(
        title="Emitente",
        description="Who received the amount and signs the receipt.",
    )
    payer: Party = Field(
        title="Pagador",
        description="Who paid.",
    )
    amount_cents: int = Field(
        gt=0,
        title="Valor (centavos)",
        description="Amount received, in cents. Also spelled in words.",
        examples=[125000],
    )
    reference: NonEmptyStrField = Field(
        title="Referente a",
        description="What the payment covers.",
        examples=["Serviços de consultoria prestados em julho/2026"],
    )
    place: str | None = Field(
        default=None,
        title="Local",
        description="City printed before the date.",
        examples=["Recife", None],
    )


class QuoteDocument(PdfDocument):
    """A quote / commercial proposal with priced lines.

    Attributes:
        number (str | None): Quote number.
        issue_date (date): When it was issued.
        valid_until (date | None): Last day the prices hold.
        issuer (Party): Who is quoting.
        customer (Party): Who receives the quote.
        items (list[LineItem]): The priced lines. At least one.
        discount_cents (int): Discount applied to the subtotal.
        notes (str | None): Free text under the table.
        payment_terms (str | None): How payment is expected.
    """

    template: ClassVar[str] = "quote.html"

    number: str | None = Field(
        default=None,
        title="Número",
        description="Quote number.",
        examples=["ORC-2026-014", None],
    )
    issue_date: date = Field(
        title="Data de emissão",
        description="When the quote was issued.",
        examples=["2026-08-13"],
    )
    valid_until: date | None = Field(
        default=None,
        title="Validade",
        description="Last day these prices hold.",
        examples=["2026-09-13", None],
    )
    issuer: Party = Field(
        title="Emitente",
        description="Who is quoting.",
    )
    customer: Party = Field(
        title="Cliente",
        description="Who receives the quote.",
    )
    items: list[LineItem] = Field(
        min_length=1,
        title="Itens",
        description="Priced lines. A quote with no line is not a quote.",
    )
    discount_cents: int = Field(
        default=0,
        ge=0,
        title="Desconto (centavos)",
        description="Subtracted from the subtotal.",
        examples=[0, 5000],
    )
    notes: str | None = Field(
        default=None,
        title="Observações",
        description="Free text printed under the table.",
        examples=["Prazo de entrega: 15 dias úteis.", None],
    )
    payment_terms: str | None = Field(
        default=None,
        title="Condições de pagamento",
        description="How payment is expected.",
        examples=["50% na assinatura, 50% na entrega.", None],
    )

    @model_validator(mode="after")
    def _check_discount(self) -> QuoteDocument:
        """Refuse a discount larger than the subtotal.

        Returns:
            QuoteDocument: The validated document.

        Raises:
            ValueError: When the discount exceeds the subtotal, which
                would print a negative total as if it were a real price.
        """
        if self.discount_cents > self.subtotal_cents:
            raise ValueError("discount_cents is larger than the subtotal")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def subtotal_cents(self) -> int:
        """Sum of every line, before the discount.

        Returns:
            int: The subtotal, in cents.
        """
        return sum(item.total_cents for item in self.items)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cents(self) -> int:
        """Subtotal minus the discount.

        Returns:
            int: The total, in cents.
        """
        return self.subtotal_cents - self.discount_cents


class ReportDocument(PdfDocument):
    """A paginated tabular report.

    The header and footer repeat on every page and the page number reads
    ``página X de Y``, which is what makes a printed listing usable —
    somebody has to be able to tell a page is missing.

    Attributes:
        heading (str): Report title.
        subtitle (str | None): Line under the title.
        generated_at (date | None): Date printed in the header.
        columns (list[ReportColumn]): Column definitions. At least one.
        rows (list[dict[str, object]]): Row mappings keyed by
            ``ReportColumn.key``. A missing key prints empty rather than
            failing — a report is often assembled from partial data.
        totals (dict[str, int]): Totals per column key, in cents for
            money columns. Printed in a final row.
    """

    template: ClassVar[str] = "report.html"

    heading: NonEmptyStrField = Field(
        title="Título",
        description="Report title, repeated on every page.",
        examples=["Vendas por cliente"],
    )
    subtitle: str | None = Field(
        default=None,
        title="Subtítulo",
        description="Line under the title — the period, usually.",
        examples=["Julho de 2026", None],
    )
    generated_at: date | None = Field(
        default=None,
        title="Gerado em",
        description="Date printed in the header.",
        examples=["2026-08-13", None],
    )
    columns: list[ReportColumn] = Field(
        min_length=1,
        title="Colunas",
        description="Column definitions, in print order.",
    )
    rows: list[dict[str, object]] = Field(
        default_factory=list,
        title="Linhas",
        description=(
            "Row mappings keyed by column key. An empty list renders the "
            "report with its header and an explicit empty notice."
        ),
    )
    totals: dict[str, int] = Field(
        default_factory=dict,
        title="Totais",
        description="Per-column totals, in cents for money columns.",
        examples=[{"total_cents": 1250000}],
    )


class VoucherDocument(PdfDocument):
    """A short receipt or label — half a page or less.

    Attributes:
        heading (str): Large text at the top.
        subtitle (str | None): Line under it.
        fields (dict[str, str]): Label/value pairs, printed in order.
        qr_data_uri (str | None): QR code as a ``data:`` URI. The SDK
            does not generate it — any encoder does, and pulling one in
            for a single image is not a trade worth making.
        note (str | None): Small print at the foot.
        height (str): CSS height of the printed area.
    """

    template: ClassVar[str] = "voucher.html"

    heading: NonEmptyStrField = Field(
        title="Título",
        description="Large text at the top.",
        examples=["COMPROVANTE DE PAGAMENTO"],
    )
    subtitle: str | None = Field(
        default=None,
        title="Subtítulo",
        description="Line under the title.",
        examples=["Pix · 13/08/2026 18:42", None],
    )
    fields: dict[str, str] = Field(
        default_factory=dict,
        title="Campos",
        description="Label/value pairs, printed in insertion order.",
        examples=[{"Valor": "R$ 150,00", "Destinatário": "Acme LTDA"}],
    )
    qr_data_uri: str | None = Field(
        default=None,
        pattern=r"^data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+$",
        title="QR code (data URI)",
        description=(
            "Embedded QR image. Produce it with any encoder — ``segno`` "
            "writes a data URI in two lines."
        ),
        examples=["data:image/png;base64,iVBORw0KGgo=", None],
    )
    note: str | None = Field(
        default=None,
        title="Observação",
        description="Small print at the foot.",
        examples=["Guarde este comprovante.", None],
    )
    height: str = Field(
        default="99mm",
        title="Altura da área impressa",
        description="CSS height. Half of A4 by default, so two fit a sheet.",
        examples=["99mm", "148mm", "70mm"],
    )


class ContractDocument(PdfDocument):
    """A contract or written declaration.

    Attributes:
        heading (str): Document title.
        preamble (str | None): Paragraph before the clauses, usually
            qualifying the parties.
        parties (list[Party]): Who is bound. Printed in the header.
        clauses (list[Clause]): Numbered clauses. At least one.
        place (str | None): City in the closing line.
        signed_on (date | None): Date in the closing line.
        signatories (list[Signatory]): Signature blocks.
    """

    template: ClassVar[str] = "contract.html"

    heading: NonEmptyStrField = Field(
        title="Título",
        description="Document title.",
        examples=["CONTRATO DE PRESTAÇÃO DE SERVIÇOS"],
    )
    preamble: str | None = Field(
        default=None,
        title="Preâmbulo",
        description="Paragraph qualifying the parties, before the clauses.",
        examples=["As partes abaixo qualificadas resolvem celebrar…", None],
    )
    parties: list[Party] = Field(
        default_factory=list,
        title="Partes",
        description="Printed in the header, above the preamble.",
    )
    clauses: list[Clause] = Field(
        min_length=1,
        title="Cláusulas",
        description="Numbered in print order.",
    )
    place: str | None = Field(
        default=None,
        title="Local",
        description="City printed in the closing line.",
        examples=["Recife", None],
    )
    signed_on: date | None = Field(
        default=None,
        title="Data",
        description="Date printed in the closing line.",
        examples=["2026-08-13", None],
    )
    signatories: list[Signatory] = Field(
        default_factory=list,
        title="Assinaturas",
        description="Signature blocks at the foot.",
    )


BUNDLED_DOCUMENTS: dict[str, type[PdfDocument]] = {
    "contract": ContractDocument,
    "quote": QuoteDocument,
    "receipt": ReceiptDocument,
    "report": ReportDocument,
    "voucher": VoucherDocument,
}
"""Bundled document name to its schema.

Drives ``tempest pdf list`` and the router's document endpoint, so a new
bundled template is registered in exactly one place.
"""


__all__: list[str] = [
    "BUNDLED_DOCUMENTS",
    "Branding",
    "Clause",
    "ContractDocument",
    "LineItem",
    "Party",
    "PdfDocument",
    "QuoteDocument",
    "ReceiptDocument",
    "ReportColumn",
    "ReportDocument",
    "Signatory",
    "VoucherDocument",
]
