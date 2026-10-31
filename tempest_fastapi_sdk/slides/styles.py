"""Stylesheets for Markdown rendered to PDF.

:class:`~tempest_fastapi_sdk.slides.MarkdownPdfRenderer` takes the CSS as a
parameter, so the house style of a document is a decision of the service,
not of the SDK. Two are shipped:

* :data:`DEFAULT_MARKDOWN_CSS` — neutral: A4, sans-serif, page numbers.
* :data:`ABNT_MARKDOWN_CSS` — the page layout ABNT NBR 14724 sets for
  Brazilian academic work: A4, 3 cm top and left margins, 2 cm bottom and
  right, Times New Roman 12 pt, 1.5 line spacing, justified text, 1.25 cm
  first-line indent. Ported from ``abnt_styles.py`` of Leviathan's
  ``slide-generator``, with one correction: the original's
  ``margin: 3cm 2cm`` put 3 cm at the bottom and 2 cm on the left, which
  is not what the norm asks for.

Both style the classes the Mermaid step emits (``.mermaid-diagram`` and
``.mermaid-error``), so a custom stylesheet that starts from either keeps
diagrams sized to the page.
"""

from __future__ import annotations

_MERMAID_CSS: str = """
.mermaid-diagram {
    text-align: center;
    margin: 15px auto;
    page-break-inside: avoid;
    overflow: hidden;
}

.mermaid-diagram svg {
    max-width: 100%;
    max-height: 220mm;
    height: auto;
    width: auto;
    display: block;
    margin: 0 auto;
}

.mermaid-error {
    background: #fff3cd;
    border: 1px solid #ffc107;
    border-radius: 4px;
    padding: 10px;
    margin: 15px 0;
    font-family: "Courier New", monospace;
    font-size: 9pt;
    color: #856404;
    white-space: pre-wrap;
    page-break-inside: avoid;
}
"""

DEFAULT_MARKDOWN_CSS: str = (
    """
@page {
    size: A4;
    margin: 2.5cm 2cm;
    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #6b7280;
    }
}

body {
    font-family: "DejaVu Sans", "Liberation Sans", Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #1f2937;
}

h1, h2, h3, h4, h5, h6 {
    font-weight: bold;
    line-height: 1.25;
    margin-top: 18pt;
    margin-bottom: 8pt;
}

h1 { font-size: 20pt; }
h2 { font-size: 16pt; }
h3 { font-size: 13pt; }
h4, h5, h6 { font-size: 11pt; }

p, ul, ol { margin-bottom: 10pt; }

li { margin-bottom: 4pt; }

.codehilite, pre {
    background: #f3f4f6;
    border-radius: 4px;
    padding: 10px;
    font-family: "DejaVu Sans Mono", "Courier New", monospace;
    font-size: 9pt;
    white-space: pre-wrap;
}

code {
    font-family: "DejaVu Sans Mono", "Courier New", monospace;
    font-size: 9.5pt;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 10pt;
}

th, td {
    border: 1px solid #d1d5db;
    padding: 5px 8px;
    text-align: left;
}

th { background-color: #f3f4f6; }

blockquote {
    border-left: 3px solid #d1d5db;
    margin-left: 0;
    padding-left: 12px;
    color: #4b5563;
}

img {
    max-width: 100%;
    display: block;
    margin: 12px auto;
}

.footnote { font-size: 9pt; }
"""
    + _MERMAID_CSS
)
"""Neutral stylesheet — what :class:`MarkdownPdfRenderer` uses when given none."""

ABNT_MARKDOWN_CSS: str = (
    """
@page {
    size: A4;
    margin: 3cm 2cm 2cm 3cm;
    @bottom-center {
        content: counter(page);
        font-family: "Times New Roman";
        font-size: 10pt;
    }
}

body {
    font-family: "Times New Roman", serif;
    font-size: 12pt;
    text-align: justify;
    line-height: 1.5;
    color: #000000;
    padding-top: 20px;
}

h1 {
    font-size: 14pt;
    font-weight: bold;
    text-align: center;
    margin-top: 30pt;
    margin-bottom: 18pt;
    text-transform: uppercase;
}

h2 {
    font-size: 12pt;
    font-weight: bold;
    text-align: left;
    margin-top: 24pt;
    margin-bottom: 12pt;
}

h3, h4, h5, h6 {
    font-size: 12pt;
    font-weight: bold;
    text-align: left;
    margin-top: 18pt;
    margin-bottom: 12pt;
}

p {
    margin-bottom: 12pt;
    text-indent: 1.25cm;
}

ul, ol {
    margin-bottom: 12pt;
    margin-left: 1.25cm;
}

li {
    margin-bottom: 6pt;
}

.codehilite, pre {
    background: #f8f8f8;
    border: 1px solid #ddd;
    border-radius: 3px;
    padding: 10px;
    margin: 15px 0;
    overflow: auto;
    font-family: "Courier New", monospace;
    font-size: 10pt;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 15px 0;
    font-size: 10pt;
}

th, td {
    border: 1px solid #000;
    padding: 5px 8px;
    text-align: center;
}

th {
    background-color: #f2f2f2;
    font-weight: bold;
}

.MathJax_Display, .arithmatex {
    text-align: center;
    margin: 15px 0;
    font-size: 11pt;
}

strong, b {
    font-weight: bold;
}

em, i {
    font-style: italic;
}

img {
    max-width: 100%;
    display: block;
    margin: 15px auto;
    text-align: center;
}

.footnote {
    font-size: 10pt;
}
"""
    + _MERMAID_CSS
)
"""ABNT NBR 14724 page layout. Ported from ``ABNT_CSS`` in Leviathan's
``slide-generator`` (``abnt_styles.py``), margins corrected to the norm."""
