"""Brazilian money: parsing printed amounts and formatting exact decimals.

Two directions, both missing from the SDK until now.

**Reading.** :func:`parse_currency_br` turns an amount as a document
prints it (``"R$ 2.930,00"``) back into an exact
:class:`~decimal.Decimal`. Anything that ingests money written for humans
needs this: a model transcribing a PDF, an imported CSV, a scraped page.
Sending such a value through ``float`` first is what silently moves a
cent, and a cent is what a public-procurement bid is disqualified over.

**Writing.** :func:`format_currency_br` and friends render a ``Decimal``
in the continental convention — ``.`` groups thousands, ``,`` separates
decimals — without going through ``locale``, which is process-global,
depends on locales being generated in the container, and is not
thread-safe.

Money is quantized with ``ROUND_HALF_UP``, not Python's default banker's
rounding: Brazilian accounting practice rounds half away from zero, and
matching it is what lets a generated document reproduce a manually built
one cent for cent.

    from decimal import Decimal
    from tempest_fastapi_sdk.utils import format_currency_br, parse_currency_br

    amount = parse_currency_br("R$ 2.930,00")   # Decimal("2930.00")
    format_currency_br(amount * Decimal("0.7")) # "R$ 2.051,00"

For amounts already stored as integer cents (the :data:`CentsField`
convention), use
:func:`~tempest_fastapi_sdk.pdf.formatting.format_cents`, which delegates
here.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_NON_NUMERIC = re.compile(r"[^0-9.,\-]")
"""Everything a printed amount may carry around its digits.

Currency symbol, spaces, the non-breaking space a word processor emits,
letters from ``"2.930,00 (dois mil...)"``. Stripping by exclusion rather
than matching a shape is what keeps the parser working on the messy end of
real documents.
"""

CENT: Decimal = Decimal("0.01")
"""The smallest representable monetary unit; the quantization target."""

HUNDRED: Decimal = Decimal("100")
"""Literal one hundred, used to convert ratios to display percentages."""


def quantize_money(amount: Decimal) -> Decimal:
    """Round a monetary amount to two decimal places, half away from zero.

    Uses ``ROUND_HALF_UP`` rather than :class:`~decimal.Decimal`'s default
    ``ROUND_HALF_EVEN``. Banker's rounding is the better choice for a long
    series of independent roundings, but it is not what a Brazilian
    accountant, a printed invoice, or a spreadsheet built by hand does —
    and a generated document that disagrees with the manual one by a cent
    is a document that gets rejected.

    Args:
        amount (Decimal): The unrounded amount.

    Returns:
        Decimal: The amount quantized to two decimal places.
    """
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_currency_br(text: str) -> Decimal | None:
    """Read an amount printed in Brazilian notation into an exact ``Decimal``.

    Accepts both separator conventions, because a source that transcribes a
    Brazilian document sometimes normalizes to the US one:

    * ``"R$ 2.930,00"`` / ``"2.930,00"`` — comma decimal, dot thousands
    * ``"2930.00"`` / ``"2,930.00"`` — dot decimal, the US convention
    * ``"2930"`` — no separator at all

    The **last** separator present decides which one is decimal. The one
    genuinely ambiguous case is a lone dot followed by exactly three digits:
    ``"2.930"`` is read as two thousand nine hundred and thirty, never as
    two point nine three, because that is what the notation means in the
    documents this parses. Pass the US form with its own thousands
    separator (``"2,930.00"``) when the other reading is intended.

    Args:
        text (str): The printed amount, with or without symbol and spaces.

    Returns:
        Decimal | None: The parsed amount, or ``None`` when the text carries
        no digit at all. ``None`` rather than zero keeps "the document did
        not print a price" distinguishable from "the document printed
        R$ 0,00" — a distinction that decides whether a line is missing data
        or is genuinely free of charge.
    """
    cleaned = _NON_NUMERIC.sub("", text or "")
    if not any(character.isdigit() for character in cleaned):
        return None

    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-")
    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")

    if last_comma > last_dot:
        normalized = cleaned.replace(".", "").replace(",", ".")
    elif last_dot > last_comma:
        integer, _, fraction = cleaned.rpartition(".")
        normalized = (
            cleaned.replace(".", "").replace(",", "")
            if len(fraction) == 3 and "," not in cleaned
            else f"{integer.replace('.', '').replace(',', '')}.{fraction}"
        )
    else:
        normalized = cleaned

    try:
        parsed = Decimal(normalized)
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


def _group_thousands(digits: str) -> str:
    """Insert ``.`` every three digits of an unsigned integer string.

    Args:
        digits (str): Digits of the integer part, without sign.

    Returns:
        str: The digits grouped in thousands, e.g. ``"484365"`` ->
        ``"484.365"``.
    """
    blocks: list[str] = []
    while len(digits) > 3:
        blocks.insert(0, digits[-3:])
        digits = digits[:-3]
    blocks.insert(0, digits)
    return ".".join(blocks)


def _format_fixed(amount: Decimal, places: int) -> str:
    """Render a signed decimal with grouped thousands and a comma decimal.

    Args:
        amount (Decimal): The value to render.
        places (int): How many decimal places to keep; ``0`` drops the
            decimal separator entirely.

    Returns:
        str: The formatted number, sign included, without any symbol.
    """
    scale = Decimal(1).scaleb(-places)
    quantized = amount.quantize(scale, rounding=ROUND_HALF_UP)
    integer, _, fraction = format(abs(quantized), "f").partition(".")
    text = _group_thousands(integer)
    if places > 0:
        text = f"{text},{fraction.ljust(places, '0')}"
    return f"-{text}" if quantized < 0 else text


def format_currency_br(amount: Decimal, *, symbol: bool = True) -> str:
    """Render an amount in Brazilian currency notation.

    Args:
        amount (Decimal): The amount to render; quantized to two places
            before formatting.
        symbol (bool): Whether to prefix the ``R$`` symbol.

    Returns:
        str: The formatted amount, e.g. ``"R$ 484.365,84"``. A negative
        amount carries the sign ahead of the symbol (``"-R$ 0,01"``), which
        is how a credit reads on a Brazilian statement and how a closing
        difference must appear on a worksheet.
    """
    body = _format_fixed(quantize_money(amount), 2)
    if not symbol:
        return body
    return f"-R$ {body[1:]}" if body.startswith("-") else f"R$ {body}"


def format_percent_br(ratio: Decimal, *, places: int = 2) -> str:
    """Render a ratio as a Brazilian-formatted percentage.

    Args:
        ratio (Decimal): The ratio to render, where ``Decimal("0.30")``
            means thirty percent.
        places (int): How many decimal places to keep.

    Returns:
        str: The formatted percentage, e.g. ``"30,00%"``.
    """
    return f"{_format_fixed(ratio * HUNDRED, places)}%"


def format_quantity_br(amount: Decimal, *, places: int = 2) -> str:
    """Render a non-monetary quantity in Brazilian notation.

    Args:
        amount (Decimal): The quantity to render.
        places (int): How many decimal places to keep.

    Returns:
        str: The formatted quantity, e.g. ``"1.250,00"``.
    """
    return _format_fixed(amount, places)


__all__: list[str] = [
    "CENT",
    "HUNDRED",
    "format_currency_br",
    "format_percent_br",
    "format_quantity_br",
    "parse_currency_br",
    "quantize_money",
]
