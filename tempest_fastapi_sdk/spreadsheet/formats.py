"""Excel number formats that render correctly under any locale.

A number format is stored in the file, not resolved from the reader's
machine — which is exactly why the obvious ``"#,##0.00"`` is a trap for a
Brazilian document. Excel renders that mask using the *reader's* locale, so
a workbook built in São Paulo shows ``1.234,56`` at home and ``1,234.56``
on a colleague's en-US laptop. The value is the same; the document is
wrong, and nobody notices until an auditor reads it.

The masks here pin the convention in the mask itself. ``[$-416]`` is the
pt-BR language code (LCID ``0x416``) as a leading section, which forces the
dot as thousands separator, the comma as decimal and the slash as date
separator regardless of where the file is opened.

Measured by rendering the workbook in LibreOffice 7.4 headless under
en-US, de-DE and pt-BR: every mask here comes out identical in the three.
The currency-tagged form ``[$R$-416] #,##0.00`` does **not** pin anything
there — it renders ``R$ 1,234.56`` under en-US — which is why the currency
mask carries the symbol as a quoted literal after the language code
instead. Excel was not measured.

**Write numbers, not strings.** A cell holding ``"R$ 1.234,56"`` cannot be
sorted, filtered or summed by the person who receives it; a cell holding
``Decimal("1234.56")`` with :data:`BR_CURRENCY_FORMAT` looks identical and
stays a number. Use
:func:`~tempest_fastapi_sdk.utils.currency.format_currency_br` for text
destined for prose (a PDF, an e-mail, an HTML page), and these formats for
cells.
"""

from __future__ import annotations

BR_CURRENCY_FORMAT: str = '[$-416]"R$ "#,##0.00'
"""Real with symbol, pinned to pt-BR: ``R$ 1.234,56``.

The leading ``[$-416]`` language code is what survives the file being
opened under another locale; the symbol is a quoted literal. The
currency-tagged ``[$R$-416] #,##0.00`` looks equivalent and is not:
LibreOffice 7.4 renders it ``R$ 1,234.56`` under en-US. Dropping the code
to ``"R$ #,##0.00"`` keeps the symbol and loses the separators the same way.
"""

BR_CURRENCY_FORMAT_NO_SYMBOL: str = "[$-416]#,##0.00"
"""Real without the symbol: ``1.234,56``.

For a column whose header already says ``(R$)`` and that would otherwise
repeat the symbol on every one of a thousand rows.
"""

BR_QUANTITY_FORMAT: str = "[$-416]#,##0.00"
"""Non-monetary quantity with two decimals: ``1.234,56``."""

BR_INTEGER_FORMAT: str = "[$-416]#,##0"
"""Whole number with thousands grouping: ``1.234``."""

BR_PERCENT_FORMAT: str = "[$-416]0.00%"
"""Percentage with two decimals: ``30,00%``.

The ``[$-416]`` prefix pins the decimal comma. Without it the mask follows
the reader's locale: LibreOffice renders a plain ``0.00%`` as ``30.00%``
under en-US and ``30,00 %`` under de-DE.

Excel multiplies by 100 itself, so the cell must hold the **ratio**
(``0.30``), never the percentage (``30``). Writing 30 into a percent-format
cell displays ``3000,00%`` — a mistake that reads as a typo but is a unit
error.
"""

BR_DATE_FORMAT: str = "[$-416]DD/MM/YYYY"
"""Date as a Brazilian document writes it: ``14/08/2026``.

The ``/`` in a date mask is the *locale's* date separator, not a literal
slash, so without ``[$-416]`` a de-DE reader sees ``14.08.2026``. The
language code pins the slash.
"""

BR_DATETIME_FORMAT: str = "[$-416]DD/MM/YYYY HH:MM"
"""Date and time, 24-hour: ``14/08/2026 19:30``.

Pinned with ``[$-416]`` for the same reason as :data:`BR_DATE_FORMAT`.
"""

TEXT_FORMAT: str = "@"
"""Force a cell to be read as text.

The escape hatch for identifiers that look numeric and must not be
normalized: a CPF with leading zeros, a process number like ``0001/2026``,
a bank branch. Without it Excel drops the zeros and there is no way back.
"""

__all__: list[str] = [
    "BR_CURRENCY_FORMAT",
    "BR_CURRENCY_FORMAT_NO_SYMBOL",
    "BR_DATETIME_FORMAT",
    "BR_DATE_FORMAT",
    "BR_INTEGER_FORMAT",
    "BR_PERCENT_FORMAT",
    "BR_QUANTITY_FORMAT",
    "TEXT_FORMAT",
]
