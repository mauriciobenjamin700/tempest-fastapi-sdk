"""Stripe amounts — cents, and the currencies that do not have them.

Stripe takes every amount as an integer in the currency's **smallest
unit**. For most currencies that is 1/100 of the major unit, so
``R$ 10,50`` is ``1050``. For a handful it is the major unit itself: JPY
has no subunit at all, so ``¥ 1050`` is ``1050``, not ``105000``.

Dividing everything by 100 is therefore a silent billing bug — it
overcharges a Japanese customer a hundredfold, and no test that only uses
BRL or USD will ever see it. This module is the one place that knows the
difference.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "bif",
        "clp",
        "djf",
        "gnf",
        "jpy",
        "kmf",
        "krw",
        "mga",
        "pyg",
        "rwf",
        "ugx",
        "vnd",
        "vuv",
        "xaf",
        "xof",
        "xpf",
    }
)
"""Currencies Stripe charges in whole units, with no subunit.

Ported from Stripe's "Zero-decimal currencies" table
(<https://docs.stripe.com/currencies#zero-decimal>). ``tests`` pins the
set, so an upstream change shows up as a failing test rather than as a
hundredfold billing error. Lower-case, because Stripe returns and accepts
currency codes in lower case.
"""

THREE_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {"bhd", "jod", "kwd", "omr", "tnd"}
)
"""Currencies with three decimal places rather than two.

Ported from Stripe's "Three-decimal currencies" table. Stripe requires
the amount to be a multiple of 10 for these — the API rounds to the
nearest ten itself, but building the value correctly here keeps the
rounding visible to the caller instead of surprising them on the invoice.
"""


def currency_exponent(currency: str) -> int:
    """Return how many decimal places a currency has on Stripe.

    Args:
        currency (str): ISO-4217 code, in any case (``"BRL"``, ``"jpy"``).

    Returns:
        int: ``0`` for a zero-decimal currency, ``3`` for a three-decimal
        one, ``2`` for everything else.
    """
    code = currency.strip().lower()
    if code in ZERO_DECIMAL_CURRENCIES:
        return 0
    if code in THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


def to_minor_units(amount: Decimal | int | float | str, currency: str) -> int:
    """Convert a human amount into the integer Stripe charges.

    Args:
        amount (Decimal | int | float | str): The amount in major units —
            ``Decimal("10.50")``, ``"10.50"``, ``10.5``. A string or a
            :class:`~decimal.Decimal` is preferred: binary floats cannot
            represent every cent exactly.
        currency (str): ISO-4217 code.

    Returns:
        int: The amount in the currency's smallest unit, half-up rounded.

    Examples:
        >>> to_minor_units(Decimal("10.50"), "BRL")
        1050
        >>> to_minor_units(Decimal("1050"), "JPY")
        1050
        >>> to_minor_units(Decimal("10.505"), "BHD")
        10505
    """
    exponent = currency_exponent(currency)
    quantum = Decimal(1).scaleb(-exponent)
    value = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    scaled = (value / quantum).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(scaled)


def from_minor_units(amount: int, currency: str) -> Decimal:
    """Convert the integer Stripe reports back into a human amount.

    Args:
        amount (int): The amount in the currency's smallest unit, as it
            appears on a Stripe object.
        currency (str): ISO-4217 code.

    Returns:
        Decimal: The amount in major units, exact — never a float, so it
        can go straight into an invoice or a ledger.

    Examples:
        >>> from_minor_units(1050, "BRL")
        Decimal('10.50')
        >>> from_minor_units(1050, "JPY")
        Decimal('1050')
    """
    exponent = currency_exponent(currency)
    return (Decimal(amount).scaleb(-exponent)).quantize(Decimal(1).scaleb(-exponent))


def format_amount(amount: int, currency: str) -> str:
    """Render a Stripe amount for a human, without a locale library.

    Args:
        amount (int): The amount in the currency's smallest unit.
        currency (str): ISO-4217 code.

    Returns:
        str: ``"<value> <CODE>"`` with the right number of decimals —
        ``"10.50 BRL"``, ``"1050 JPY"``. Deliberately not localized: the
        SDK does not know the reader's locale, and a wrong thousands
        separator on an invoice is worse than a plain one.
    """
    return f"{from_minor_units(amount, currency)} {currency.strip().upper()}"


__all__: list[str] = [
    "THREE_DECIMAL_CURRENCIES",
    "ZERO_DECIMAL_CURRENCIES",
    "currency_exponent",
    "format_amount",
    "from_minor_units",
    "to_minor_units",
]
