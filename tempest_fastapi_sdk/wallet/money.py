"""Fee and split arithmetic in integer cents.

Nothing here touches ``float``. Percentages are **basis points** (1 bp =
0.01 %, so 10 000 bp = 100 %), applied with integer division, which makes
every result reproducible and every split add back up to the total.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

BASIS_POINTS_PER_UNIT: Final[int] = 10_000
"""Basis points in 100 %."""


@dataclass(frozen=True, slots=True)
class OpenPixFeeTiers:
    """The OpenPix per-transaction fee schedule, in integer cents.

    Attributes:
        low_tier_max_cents (int): Totals up to this pay ``low_tier_cents``.
        low_tier_cents (int): Flat fee of the low tier.
        mid_tier_max_cents (int): Totals up to this (and above the low
            tier) pay ``mid_tier_per_mille`` of the total.
        mid_tier_per_mille (int): Rate of the middle tier, per thousand.
        high_tier_cents (int): Flat fee above the middle tier.
        fixed_cents (int): Added to every transaction, on top of the tier.
    """

    low_tier_max_cents: int
    low_tier_cents: int
    mid_tier_max_cents: int
    mid_tier_per_mille: int
    high_tier_cents: int
    fixed_cents: int


OPENPIX_FEE_TIERS: Final[OpenPixFeeTiers] = OpenPixFeeTiers(
    low_tier_max_cents=6_250,
    low_tier_cents=50,
    mid_tier_max_cents=62_500,
    mid_tier_per_mille=8,
    high_tier_cents=500,
    fixed_cents=100,
)
"""The schedule both Tempest services charge against today.

Ported from alofans-api's ``src/core/constants/money.py``
(``OPENPIX_FEE_*`` and ``FIXED_TRANSACTION_FEE_CENTS``), which
transport-backend's ``src/core/constants/money.py`` repeats in reais: up to
R$ 62,50 the fee is R$ 0,50; up to R$ 625,00 it is 0,8 %; above that,
R$ 5,00; plus a fixed R$ 1,00 on every transaction. The values are the
contract a service negotiated with the provider, not something the SDK can
know — pass your own :class:`OpenPixFeeTiers` when yours differ.
"""


def openpix_fee_cents(
    total_cents: int,
    *,
    tiers: OpenPixFeeTiers = OPENPIX_FEE_TIERS,
) -> int:
    """Return the OpenPix fee for a transaction, in cents.

    The middle tier rounds down (``total * per_mille // 1000``), as
    alofans-api does.

    Args:
        total_cents (int): The transaction total, in cents.
        tiers (OpenPixFeeTiers): The fee schedule.

    Returns:
        int: Tier fee plus ``fixed_cents``.

    Raises:
        ValueError: If ``total_cents`` is negative.
    """
    if total_cents < 0:
        raise ValueError("total_cents must not be negative")
    if total_cents <= tiers.low_tier_max_cents:
        tier_cents = tiers.low_tier_cents
    elif total_cents <= tiers.mid_tier_max_cents:
        tier_cents = total_cents * tiers.mid_tier_per_mille // 1000
    else:
        tier_cents = tiers.high_tier_cents
    return tier_cents + tiers.fixed_cents


@dataclass(frozen=True, slots=True)
class NetSplit:
    """How one transaction total divides between platform, gateway and payees.

    ``platform_cents + gateway_fee_cents + sum(shares.values())`` equals
    the total whenever the fees fit inside it; when they do not,
    ``net_cents`` is ``0`` and every share is ``0``.

    Attributes:
        total_cents (int): The amount split.
        platform_cents (int): The platform's cut.
        gateway_fee_cents (int): The payment gateway's fee.
        net_cents (int): What is left for the payees.
        shares (Mapping[str, int]): Each payee's cents, the residual
            recipient included.
    """

    total_cents: int
    platform_cents: int
    gateway_fee_cents: int
    net_cents: int
    shares: Mapping[str, int] = field(default_factory=dict)


def split_net(
    total_cents: int,
    *,
    platform_bps: int,
    gateway_fee_cents: int,
    residual_recipient: str,
    shares_bps: Mapping[str, int] | None = None,
) -> NetSplit:
    """Split a transaction total into platform cut, gateway fee and payees.

    The platform takes ``platform_bps`` of the **gross** total, rounded
    down. The gateway fee comes off next. What remains is the net, and
    every entry of ``shares_bps`` takes its basis points of the **net**,
    rounded down. ``residual_recipient`` takes the rest of the net, so the
    rounding of every share lands on one known payee instead of
    disappearing — the rule alofans-api uses for the producer
    (``get_producer_profit``), and the only payee transport-backend has
    (the driver, with no ``shares_bps``).

    Args:
        total_cents (int): The transaction total, in cents.
        platform_bps (int): Platform cut, in basis points of the total.
        gateway_fee_cents (int): Gateway fee, in cents (for OpenPix,
            :func:`openpix_fee_cents`).
        residual_recipient (str): The payee who takes the net minus every
            other share.
        shares_bps (Mapping[str, int] | None): Other payees, each with
            basis points of the net.

    Returns:
        NetSplit: The split, with ``residual_recipient`` included in
        ``shares``.

    Raises:
        ValueError: If an amount is negative, a basis-point value is out
            of ``0..10000``, the shares add up to more than 10 000 bp, or
            ``residual_recipient`` also appears in ``shares_bps``.
    """
    others: Mapping[str, int] = shares_bps or {}
    if total_cents < 0 or gateway_fee_cents < 0:
        raise ValueError("amounts must not be negative")
    for value in (platform_bps, *others.values()):
        if not 0 <= value <= BASIS_POINTS_PER_UNIT:
            raise ValueError("basis points must be within 0..10000")
    if sum(others.values()) > BASIS_POINTS_PER_UNIT:
        raise ValueError("shares_bps add up to more than 10000")
    if residual_recipient in others:
        raise ValueError("residual_recipient must not appear in shares_bps")

    platform_cents = total_cents * platform_bps // BASIS_POINTS_PER_UNIT
    net_cents = max(total_cents - platform_cents - gateway_fee_cents, 0)
    shares: dict[str, int] = {
        name: net_cents * bps // BASIS_POINTS_PER_UNIT for name, bps in others.items()
    }
    shares[residual_recipient] = net_cents - sum(shares.values())
    return NetSplit(
        total_cents=total_cents,
        platform_cents=platform_cents,
        gateway_fee_cents=gateway_fee_cents,
        net_cents=net_cents,
        shares=shares,
    )


__all__: list[str] = [
    "BASIS_POINTS_PER_UNIT",
    "OPENPIX_FEE_TIERS",
    "NetSplit",
    "OpenPixFeeTiers",
    "openpix_fee_cents",
    "split_net",
]
