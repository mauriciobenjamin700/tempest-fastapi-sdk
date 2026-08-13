# PDF generation

Every service eventually has to issue a document: a receipt, a quote, a report,
a contract, a payment slip. The usual path is to assemble HTML by hand and hand
it to some library — and what breaks is never the rendering, it is everything
else: the printed total disagrees with the lines, the amount in words is wrong,
the table header vanishes on page 2, the logo failed to load and nobody
noticed.

`tempest_fastapi_sdk.pdf` handles the whole document: typed payload → Jinja2
template → PDF, with real pagination and Brazilian formatting.

!!! info "Required extra"
    ```bash
    uv add "tempest-fastapi-sdk[pdf]"
    ```
    Brings in `weasyprint` + `jinja2`. **And it needs system libraries** — read
    [Deployment](#deployment-the-system-libraries) before shipping a container.

## Your first document

```python
# scripts/receipt.py

import asyncio
from datetime import date

from tempest_fastapi_sdk.pdf import Party, PdfRenderer, ReceiptDocument


async def main() -> None:
    """Render a receipt to disk."""
    renderer = PdfRenderer()
    receipt = ReceiptDocument(
        number="0001/2026",
        issue_date=date(2026, 8, 13),
        issuer=Party(name="Acme Serviços LTDA", document="12345678000195"),
        payer=Party(name="Ana Souza", document="12345678901"),
        amount_cents=125000,
        reference="serviços de consultoria prestados em julho/2026",
        place="Recife",
    )
    pdf: bytes = await renderer.render_document(receipt)
    with open("receipt.pdf", "wb") as handle:
        handle.write(pdf)


if __name__ == "__main__":
    asyncio.run(main())
```

Out comes an A4 page with the amount highlighted, the discharge wording, the
issuer and payer blocks, the date spelled out, and a signature line — plus the
amount **in words**, which is the element that stops the figure from being
altered after signing:

> Recebi de **Ana Souza**, inscrito(a) no CPF/CNPJ sob o nº 123.456.789-01, a
> importância de **R$ 1.250,00** (mil, duzentos e cinquenta reais), referente a
> serviços de consultoria prestados em julho/2026.

!!! tip "Cents, always"
    `amount_cents=125000` is R$ 1,250.00. It is the same choice the SDK makes
    for payments: a `float` cannot represent `0.1 + 0.2`, and a document off by
    a cent is worse than one that fails.

## The five bundled documents

| Document | Class | What for |
| --- | --- | --- |
| `receipt` | `ReceiptDocument` | Receipt — proof of payment with the amount in words and a signature |
| `quote` | `QuoteDocument` | Quote / proposal with line items, subtotal, discount and total |
| `report` | `ReportDocument` | Paginated tabular report with a repeating header and a grand total |
| `contract` | `ContractDocument` | Contract or declaration with numbered clauses and signatures |
| `voucher` | `VoucherDocument` | Short receipt or label, half a page, with room for a QR code |

Each has its own Pydantic schema. That is what makes bundling the templates
worthwhile: an HTML file alone tells you nothing about which keys it needs, so
the first missing field shows up as a blank space in a signed document. Here a
receipt with no payer **does not render** — it fails validation, with the field
named.

### Quote: totals are computed, not accepted

```python
from datetime import date

from tempest_fastapi_sdk.pdf import LineItem, Party, QuoteDocument

quote = QuoteDocument(
    number="ORC-2026-014",
    issue_date=date(2026, 8, 13),
    valid_until=date(2026, 9, 13),
    issuer=Party(name="Acme Serviços LTDA"),
    customer=Party(name="Ana Souza"),
    items=[
        LineItem(description="Consultoria técnica", quantity=40, unit="h", unit_price_cents=15000),
        LineItem(description="Licença anual", unit_price_cents=250000),
    ],
    discount_cents=50000,
    payment_terms="50% na assinatura, 50% na entrega.",
)

print(quote.subtotal_cents)  # 850000
print(quote.total_cents)     # 800000
```

`subtotal_cents` and `total_cents` are computed from the items — there is no way
to pass a total that disagrees with its own lines, which is the defect nobody
catches by looking. A discount larger than the subtotal is rejected at
validation: a negative total would print as if it were a real price.

Fractional quantities round half-up to the cent so the line adds up to the
total, and print with a comma (`2,5 mês`) — a dot reads as a thousands
separator to the person holding the paper.

### Report: what makes a printed listing usable

```python
from datetime import date

from tempest_fastapi_sdk.pdf import ReportColumn, ReportDocument

report = ReportDocument(
    heading="Vendas por cliente",
    subtitle="Julho de 2026",
    generated_at=date(2026, 8, 13),
    columns=[
        ReportColumn(key="cliente", header="Cliente"),
        ReportColumn(key="pedidos", header="Pedidos", align="right"),
        ReportColumn(key="total_cents", header="Total", align="right", money=True),
    ],
    rows=[{"cliente": "Ana", "pedidos": 3, "total_cents": 125000}],
    totals={"total_cents": 125000},
)
```

Three things the template guarantees, all of which only surface once the report
runs past one page:

- **The table header repeats** on every page. A continuation page whose columns
  are unlabelled is unreadable.
- **Pages are numbered `página X de Y`.** Somebody has to be able to tell a
  sheet is missing.
- **The grand total prints once, on the last page.** The first version used
  `<tfoot>`, which is `table-footer-group` and therefore **repeats** — the
  total appeared at the foot of page 2, above rows that summed to something
  else. It is pinned by a test that reads the text of the rendered pages.

!!! note "A missing key prints empty"
    A row without a column's key renders the cell blank instead of failing.
    Reports are routinely assembled from partial data, and dropping the whole
    document over one absent cell helps nobody.

## Serving over HTTP

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.pdf import PdfRenderer, make_pdf_router

from src.api.dependencies.auth import require_user


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(
        make_pdf_router(
            PdfRenderer(),
            dependencies=[Depends(require_user)],
        ),
    )
    return app
```

That mounts two routes:

- `GET /pdf/documents` — lists the documents with the **full JSON Schema** of
  each payload, enough for a client to build the form without reading the SDK
  source.
- `POST /pdf/documents/{document}` — validates and returns the bytes with a
  `Content-Disposition` header.

!!! warning "The router adds no auth by itself"
    Rendering is CPU-bound and the caller holds the connection open.
    `dependencies=` is where the service's authentication and rate limit go —
    the router does not guess which, because guessing is how a document
    endpoint ends up public.

A client-supplied filename goes through `safe_filename`: a quote ends the
header value and a newline splits the response into two.

## The CLI: the template-editing loop

```bash
tempest pdf list                                    # what exists
tempest pdf schema receipt                          # the payload it accepts
tempest pdf render receipt data.json -o receipt.pdf
tempest pdf render receipt data.json --html -o preview.html
```

`--html` stops before layout and writes the HTML — it opens in a browser and
reloads instantly. It is the fast way to iterate on a layout before checking
how it actually paginates.

`--template-dir` points at the project's own templates, so you can preview your
overrides without starting the service.

## Overriding a template

The renderer looks in the project's `template_dir` first and falls back to the
bundled set — the same shadowing rule `EmailUtils` uses. To replace just the
receipt, drop a `receipt.html` into your directory:

```html
{% extends "_base.html" %}

{% block heading %}RECIBO — {{ doc.issuer.name }}{% endblock %}

{% block content %}
<p>Recebi {{ doc.amount_cents | brl }} ({{ doc.amount_cents | extenso }})
   de {{ doc.payer.name }}.</p>
{% endblock %}
```

```python
from tempest_fastapi_sdk.pdf import PdfRenderer

renderer = PdfRenderer(template_dir="src/templates/pdf")
```

The filters available in any template:

| Filter | Input | Output |
| --- | --- | --- |
| `brl` | `125000` | `R$ 1.250,00` |
| `extenso` | `125000` | `mil, duzentos e cinquenta reais` |
| `data` | `date(2026, 8, 13)` | `13/08/2026` |
| `data_extenso` | `date(2026, 8, 13)` | `13 de agosto de 2026` |
| `doc` | `"12345678901"` | `123.456.789-01` |
| `qtd` | `2.5` | `2,5` |

`_base.html` exposes the blocks `lang`, `doc_title`, `extra_head`, `header`,
`heading`, `subheading`, `header_meta` and `content`.

## Security: what a template may load

This is the part worth reading carefully.

An HTML renderer resolves URLs on the page's behalf: `<img src>`, `@import`,
`url()` in CSS. Point that at a document whose contents came from a user and it
becomes two bugs at once — `file:///etc/passwd` reads the host, and
`http://169.254.169.254/` reaches the cloud metadata endpoint from inside your
network.

**The default denies everything.** `data:` always passes, because it carries
its own bytes and fetches nothing. Anything else has to be named:

```python
from pathlib import Path

from tempest_fastapi_sdk.pdf import AssetPolicy, PdfRenderer

renderer = PdfRenderer(
    assets=AssetPolicy(allow_dirs=(Path("src/assets"),)),
)
```

The check is on the **resolved** path: neither `../` nor a symlink pointing
outside gets through. A directory that does not exist fails at construction,
because a typo would read as "nothing is allowed" and only surface later as a
document missing its images.

And refusal is **loud**: WeasyPrint's default is to log the failure and carry
on, which would turn a blocked logo into a silent hole in an invoice. The SDK
aborts the render at the first refusal. `strict_assets=False` restores the
lenient behavior — and still logs what it dropped.

!!! danger "`allow_remote=True` is SSRF surface"
    Turning it on means anything that reaches the template controls a request
    made from inside your network. Prefer embedding the image as a `data:` URI.

That is why `Branding.logo_data_uri` only accepts `data:` — a URL would be
refused at render time and silently produce a document with no logo. And
`accent_color` / `page_size` / `margin` have a constrained shape: those values
are written **into the stylesheet**, and one carrying `;` or `}` could close
the rule and add declarations of its own.

## Deployment: the system libraries

WeasyPrint draws text through **Pango** and resolves fonts through
**fontconfig**. A `python:slim` image has neither, and the error does not
appear at build time — it appears at the first render, as an `OSError` from
cffi.

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        fontconfig \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
```

`tempest generate --dockerfile` emits that block automatically when the project
pins the `[pdf]` extra in its `pyproject.toml`.

!!! warning "With no font, every glyph is a box"
    `fonts-dejavu-core` is not optional. A container with no font at all lays
    the document out **correctly** and draws every glyph as a rectangle — the
    layout is right and the text unreadable, which is the hardest failure on
    this list to diagnose.

## Same input, same bytes

Two renders of the same payload produce **byte-identical** PDFs, across
processes: WeasyPrint writes no creation date and no document identifier unless
asked. That is what makes a rendered document hashable, cacheable and
comparable in a test.

```python
import asyncio
import hashlib

from tempest_fastapi_sdk.pdf import PdfRenderer, ReceiptDocument


async def digest(receipt: ReceiptDocument) -> str:
    """Return a stable digest of the rendered receipt."""
    pdf: bytes = await PdfRenderer().render_document(receipt)
    return hashlib.sha256(pdf).hexdigest()
```

Passing `metadata=` (for instance `{"pdf_identifier": True}`) gives that up, on
purpose.

## Concurrency

Layout is CPU-bound. Every render goes through a worker thread behind a
semaphore, so the event loop never stalls:

```python
from tempest_fastapi_sdk.pdf import PdfRenderer

renderer = PdfRenderer(max_concurrent=8)
```

The default is 4. More workers than cores turns latency into queueing, not
throughput.

## Recap

- `PdfRenderer` renders HTML, a template, or a typed document; always `async`.
- Five bundled documents, each with a Pydantic schema — totals computed, a
  missing field fails validation.
- The report paginates properly: repeating header, `página X de Y`, grand total
  on the last page only.
- `make_pdf_router` serves it over HTTP; `dependencies=` is where auth and rate
  limiting go.
- `tempest pdf render --html` is the fast loop for adjusting a template.
- The asset default **denies everything**; opening it is an explicit decision.
- The container needs Pango + fontconfig + a font.

Next: [Transactional email](email.md) to send the document as an attachment, or
[Versioned artifacts](artifact-registry.md) if you need to keep every issued
document with a hash.
