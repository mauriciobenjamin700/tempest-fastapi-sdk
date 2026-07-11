"""Business-metric cards for the admin dashboard.

Distinct from the system panel (CPU/RAM/disk): these are value / trend /
partition cards computed from the application's own data — "orders
today", "revenue vs last week", "users by plan". Register them on the
:class:`~tempest_fastapi_sdk.admin.site.AdminSite` and they render at the
top of the dashboard.

```python
from tempest_fastapi_sdk.admin import MetricCard, MetricTrend, MetricValue


async def orders_today(session: AsyncSession) -> MetricValue:
    return MetricValue(await OrderRepo(session).count(...), unit="orders")


site = AdminSite(
    title="Shop",
    dashboard_cards=[MetricCard("Orders today", orders_today)],
)
```
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class MetricValue:
    """A single headline number.

    Attributes:
        value (Any): The value to show (number or preformatted string).
        unit (str | None): Optional unit suffix (``"orders"``, ``"BRL"``).
    """

    value: Any
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class MetricTrend:
    """A number compared against a previous period.

    Attributes:
        value (float): The current value.
        previous (float): The value for the comparison period.
        unit (str | None): Optional unit suffix.
    """

    value: float
    previous: float
    unit: str | None = None

    @property
    def delta(self) -> float:
        """Return ``value - previous``.

        Returns:
            float: The absolute change.
        """
        return self.value - self.previous

    @property
    def pct(self) -> float | None:
        """Return the percentage change, or ``None`` when undefined.

        Returns:
            float | None: ``delta / previous * 100``; ``None`` when
            ``previous`` is zero (no baseline).
        """
        if self.previous == 0:
            return None
        return (self.value - self.previous) / self.previous * 100.0

    @property
    def direction(self) -> str:
        """Return the trend direction.

        Returns:
            str: ``"up"``, ``"down"`` or ``"flat"``.
        """
        if self.value > self.previous:
            return "up"
        if self.value < self.previous:
            return "down"
        return "flat"


@dataclass(frozen=True, slots=True)
class MetricPartition:
    """A breakdown of a total across labeled segments.

    Attributes:
        segments (list[tuple[str, float]]): ``(label, value)`` pairs.
    """

    segments: list[tuple[str, float]] = field(default_factory=list)

    @property
    def total(self) -> float:
        """Return the sum of every segment value.

        Returns:
            float: The total across segments.
        """
        return sum(value for _label, value in self.segments)


#: What a card's ``compute`` returns.
CardData = MetricValue | MetricTrend | MetricPartition

#: Async function computing one card from a DB session.
CardCompute = Callable[["AsyncSession"], Awaitable[CardData]]


@dataclass(frozen=True)
class MetricCard:
    """A dashboard business-metric card.

    Attributes:
        label (str): The card heading.
        compute (CardCompute): Async function returning the card data —
            a :class:`MetricValue`, :class:`MetricTrend` or
            :class:`MetricPartition`.
        help_text (str | None): Optional sub-label shown under the value.
    """

    label: str
    compute: CardCompute
    help_text: str | None = None


def card_kind(data: CardData) -> str:
    """Return the template discriminator for a card result.

    Args:
        data (CardData): The computed card data.

    Returns:
        str: ``"value"``, ``"trend"`` or ``"partition"``.
    """
    if isinstance(data, MetricTrend):
        return "trend"
    if isinstance(data, MetricPartition):
        return "partition"
    return "value"


__all__: list[str] = [
    "CardCompute",
    "CardData",
    "MetricCard",
    "MetricPartition",
    "MetricTrend",
    "MetricValue",
    "card_kind",
]
