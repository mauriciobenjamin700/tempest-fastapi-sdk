"""Exact conversion between cents and the amounts Mercado Pago states.

Mercado Pago types money as ``number`` with ``format: float`` and states it
in the **major unit** — reais, not centavos. Measured on the pinned
specification: 39 monetary properties, including
``PaymentRequest.transaction_amount``, ``PreferenceItem.unit_price``,
``Payment.transaction_amount_refunded`` and ``Refund.amount``.

That is the mirror image of OpenPix, which states **cents** inside a float
and therefore has :func:`~...openpix.to_cents`. Same wrong type, different
unit — and mixing the two up is a factor-of-100 error in the direction
nobody notices until a customer is charged R$ 1.990,00 for a R$ 19,90 item.

Everything here goes through :class:`~decimal.Decimal`. A float that has
been added to another float is not money any more: ``0.1 + 0.2`` is
``0.30000000000000004``, and cents exist to avoid exactly that.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_CENTS: Decimal = Decimal("100")
"""Cents in one major unit. Named so the conversions read as conversions."""

_TWO_PLACES: Decimal = Decimal("0.01")
"""Quantum for a BRL amount — two decimal places."""


def to_cents(amount: float | int | str | Decimal) -> int:
    """Turn an amount Mercado Pago states in reais into exact cents.

    Args:
        amount (float | int | str | Decimal): The amount as the API typed
            it. A ``float`` is what a generated model produces, since the
            specification declares ``type: number``.

    Returns:
        int: The amount in cents.

    Raises:
        ValueError: If the amount is negative, or carries a fraction of a
            cent. Both mean the caller's assumption about the field is
            wrong, and silently rounding would hide a real mismatch behind
            a plausible number.

    A ``float`` is routed through ``repr`` before ``Decimal`` so that
    ``19.9`` reads as ``Decimal("19.9")`` rather than the binary expansion
    ``Decimal(19.9)`` would produce.
    """
    value = Decimal(repr(amount)) if isinstance(amount, float) else Decimal(amount)
    if value < 0:
        raise ValueError(f"amount must not be negative: {amount!r}")
    cents = value * _CENTS
    if cents != cents.to_integral_value():
        raise ValueError(
            f"amount {amount!r} is not a whole number of cents; "
            "rounding it here would hide the mismatch"
        )
    return int(cents)


def from_cents(cents: int) -> Decimal:
    """Turn exact cents into the amount Mercado Pago expects.

    Args:
        cents (int): The amount in cents.

    Returns:
        Decimal: The amount in reais, quantized to two places.

    Raises:
        ValueError: If ``cents`` is negative.

    Returns a ``Decimal`` rather than a ``float`` on purpose. Serializing a
    ``Decimal`` to JSON produces the digits you asked for; going through a
    ``float`` first can produce ``19.900000000000002``, which some providers
    reject and all of them log.
    """
    if cents < 0:
        raise ValueError(f"cents must not be negative: {cents!r}")
    return (Decimal(cents) / _CENTS).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def format_amount(cents: int) -> str:
    """Render an amount for a log line or a receipt.

    Args:
        cents (int): The amount in cents.

    Returns:
        str: The amount with two decimal places and no currency symbol —
        ``"19.90"``. The symbol is a presentation decision that belongs to
        whoever is displaying it.
    """
    return f"{from_cents(cents):.2f}"


__all__: list[str] = ["format_amount", "from_cents", "to_cents"]
