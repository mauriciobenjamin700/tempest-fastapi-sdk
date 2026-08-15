"""Excel number formats that render correctly under any locale.

A number format is stored in the file, not resolved from the reader's
machine — which is exactly why the obvious ``"#,##0.00"`` is a trap for a
Brazilian document. Excel renders that mask using the *reader's* locale, so
a workbook built in São Paulo shows ``1.234,56`` at home and ``1,234.56``
on a colleague's en-US laptop. The value is the same; the document is
wrong, and nobody notices until an auditor reads it.

The masks here pin the convention in the mask itself. ``[$R$-416]`` is the
currency symbol tagged with the pt-BR language code, which forces the dot
as thousands separator and the comma as decimal regardless of where the
file is opened.

**Write numbers, not strings.** A cell holding ``"R$ 1.234,56"`` cannot be
sorted, filtered or summed by the person who receives it; a cell holding
``Decimal("1234.56")`` with :data:`BR_CURRENCY_FORMAT` looks identical and
stays a number. Use
:func:`~tempest_fastapi_sdk.utils.currency.format_currency_br` for text
destined for prose (a PDF, an e-mail, an HTML page), and these formats for
cells.
"""

from __future__ import annotations

BR_CURRENCY_FORMAT: str = "[$R$-416] #,##0.00"
"""Real with symbol, pinned to pt-BR: ``R$ 1.234,56``.

The ``-416`` language code is what survives the file being opened under
another locale. Dropping it to ``"R$ #,##0.00"`` keeps the symbol and loses
the separators.
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

BR_PERCENT_FORMAT: str = "0.00%"
"""Percentage with two decimals: ``30,00%``.

Excel multiplies by 100 itself, so the cell must hold the **ratio**
(``0.30``), never the percentage (``30``). Writing 30 into a percent-format
cell displays ``3000,00%`` — a mistake that reads as a typo but is a unit
error.
"""

BR_DATE_FORMAT: str = "DD/MM/YYYY"
"""Date as a Brazilian document writes it: ``14/08/2026``."""

BR_DATETIME_FORMAT: str = "DD/MM/YYYY HH:MM"
"""Date and time, 24-hour: ``14/08/2026 19:30``."""

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
