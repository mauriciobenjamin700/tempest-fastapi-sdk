"""Turn the OpenPix ``value`` field into exact integer cents.

The specification says ``Value in cents of this charge`` and then types the
field ``number``, so a generated model validates ``1990`` into the float
``1990.0``. Money that has been through a float is money that can be wrong:
add a few of them and you get ``0.30000000000000004`` — cents exist to
avoid exactly that, and the JSON layer undoes it.

Nothing here converts a *currency* amount. ``to_cents`` narrows a value the
API already states is in cents; :func:`reais_to_cents` is the separate
operation of turning a BRL amount into them.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def to_cents(value: float | int | str | Decimal) -> int:
    """Narrow an OpenPix ``value`` to exact integer cents.

    Args:
        value (float | int | str | Decimal): The value as the API typed it.
            The generated model produces an ``int`` since v0.259.0, where
            the overlay corrects the specification's ``type: number``; the
            wider signature stays because a raw payload read straight off
            the wire still carries whatever JSON parsed to.

    Returns:
        int: The same amount as an ``int``.

    Raises:
        ValueError: If the value is not a whole number of cents, or is
            negative. Both mean the caller's assumption about the field is
            wrong, and silently rounding would hide a real mismatch behind
            a plausible number.

    A ``float`` is routed through ``repr`` before ``Decimal`` so that
    ``1990.0`` reads as ``1990`` rather than the binary expansion
    ``Decimal(1990.0)`` would produce.
    """
    amount = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    if amount < 0:
        raise ValueError(f"value in cents cannot be negative: {value!r}")
    if amount != amount.to_integral_value():
        raise ValueError(
            f"value is not a whole number of cents: {value!r} — "
            f"the field is already in cents, so a fraction means the "
            f"caller is treating a reais amount as one"
        )
    return int(amount)


def reais_to_cents(amount: float | int | str | Decimal) -> int:
    """Convert an amount in BRL to integer cents.

    Args:
        amount (float | int | str | Decimal): The amount in reais.

    Returns:
        int: The amount in cents, rounded half-up at the third decimal.

    Raises:
        ValueError: If the amount is negative.

    Half-up is the rounding a person expects from money (``0.005`` -> ``1``
    cent), and is not what Python's built-in ``round`` does — it rounds
    half to even, so ``round(0.005 * 100)`` gives ``0``.
    """
    value = Decimal(repr(amount)) if isinstance(amount, float) else Decimal(amount)
    if value < 0:
        raise ValueError(f"amount cannot be negative: {amount!r}")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_reais(cents: int) -> Decimal:
    """Convert integer cents to a BRL amount for display.

    Args:
        cents (int): The amount in cents.

    Returns:
        Decimal: The amount in reais, always with two decimal places.

    Raises:
        ValueError: If ``cents`` is negative.

    Returns a ``Decimal`` rather than a ``float`` so the value stays exact
    all the way to the formatting call.
    """
    if cents < 0:
        raise ValueError(f"cents cannot be negative: {cents!r}")
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


__all__: list[str] = ["cents_to_reais", "reais_to_cents", "to_cents"]
