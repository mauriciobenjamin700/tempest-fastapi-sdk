"""Indicator areas for the admin, generated from a schema and a service.

A :class:`~tempest_fastapi_sdk.admin.dashboard.MetricCard` computes one
number from a database session, which skips the service layer and puts one
query per card in the admin. A :class:`MetricsArea` is the other shape: the
service already knows how to collect the indicators, and returns them as a
Pydantic schema with one field per indicator. The admin builds the service,
calls the method with no arguments, and renders one card per field on a
page of its own, linked from the sidebar.

A :class:`Threshold` paints a numeric card green, yellow or red, so the
reader sees at a glance whether the number is healthy. The colours come
from the admin theme tokens (``--tempest-success``, ``--tempest-warning``,
``--tempest-danger``), never from a fixed hex, so light and dark stay
consistent.

```python
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.admin import AdminSite, MetricsArea, Threshold


class RideMetricsSchema(BaseModel):
    rides_created: int = Field(title="Rides created")


class RideMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def collect(self) -> RideMetricsSchema:
        return RideMetricsSchema(rides_created=42)


site = AdminSite(
    metrics_areas=[
        MetricsArea(
            "Rides",
            RideMetricsSchema,
            provider=RideMetricsService,
            method="collect",
            thresholds={"rides_created": Threshold(green=100, yellow=40)},
        )
    ],
)
```
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypeGuard

from pydantic import BaseModel

from tempest_fastapi_sdk.admin.dashboard import (
    CardData,
    MetricPartition,
    MetricTrend,
    MetricValue,
)
from tempest_fastapi_sdk.core.enums import BaseStrEnum

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class MetricLevelEnum(BaseStrEnum):
    """The band a numeric indicator falls in."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True, slots=True)
class Threshold:
    """Green / yellow / red bands for one numeric indicator.

    With ``lower_is_better=False`` (the default) a value at or above
    ``green`` is green, at or above ``yellow`` is yellow, and below
    ``yellow`` is red. With ``lower_is_better=True`` the comparison flips:
    at or below ``green`` is green, at or below ``yellow`` is yellow, above
    ``yellow`` is red — "messages until the user found the route" is good
    when it is small. Both limits are inclusive on the better side.

    Attributes:
        green (float): The limit of the green band.
        yellow (float): The limit of the yellow band.
        lower_is_better (bool): Whether smaller values are healthier.

    Raises:
        ValueError: When the limits are in the wrong order for the
            direction (``green < yellow`` when higher is better, or
            ``green > yellow`` when lower is better), which would make the
            yellow band empty or inverted.
    """

    green: float
    yellow: float
    lower_is_better: bool = False

    def __post_init__(self) -> None:
        """Validate that the limits are ordered for the direction.

        Raises:
            ValueError: When the yellow band would be inverted.
        """
        if self.lower_is_better and self.green > self.yellow:
            raise ValueError(
                f"Threshold(lower_is_better=True) needs green <= yellow, got "
                f"green={self.green} yellow={self.yellow}"
            )
        if not self.lower_is_better and self.green < self.yellow:
            raise ValueError(
                f"Threshold needs green >= yellow when higher is better, got "
                f"green={self.green} yellow={self.yellow}"
            )

    def level(self, value: float) -> MetricLevelEnum:
        """Return the band ``value`` falls in.

        Args:
            value (float): The indicator's current value.

        Returns:
            MetricLevelEnum: ``GREEN``, ``YELLOW`` or ``RED``.
        """
        if self.lower_is_better:
            if value <= self.green:
                return MetricLevelEnum.GREEN
            if value <= self.yellow:
                return MetricLevelEnum.YELLOW
            return MetricLevelEnum.RED
        if value >= self.green:
            return MetricLevelEnum.GREEN
        if value >= self.yellow:
            return MetricLevelEnum.YELLOW
        return MetricLevelEnum.RED


MetricsProvider = Callable[["AsyncSession"], Any]
"""Builds the service from the admin's database session.

The service class itself fits (``RideMetricsService`` takes the session in
its ``__init__``), and so does a composition function
(``build_ride_metrics_service``). Sync or ``async``.
"""


def _slugify(title: str) -> str:
    """Return the URL slug of an area title.

    Args:
        title (str): The area title.

    Returns:
        str: Lowercase ASCII words joined by ``-``.

    Raises:
        ValueError: When the title has no letter or digit to build a slug.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        raise ValueError(f"MetricsArea title {title!r} has no letter or digit")
    return slug


def _is_number(value: object) -> TypeGuard[int | float | Decimal]:
    """Return whether ``value`` is a number a threshold can compare.

    Args:
        value (object): A field value.

    Returns:
        TypeGuard[int | float | Decimal]: True for ``int``, ``float``
        and ``Decimal``; False for ``bool``, which is an ``int`` but not
        a quantity.
    """
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


@dataclass(frozen=True)
class MetricsArea:
    """A page of indicators, one card per field of ``schema``.

    Attributes:
        title (str): The page heading and the sidebar label.
        schema (type[BaseModel]): The schema the method returns. Each field
            becomes a card: ``title`` is the label (the field name when
            unset), ``description`` the line under the value. A field typed
            :class:`MetricValue`, :class:`MetricTrend` or
            :class:`MetricPartition` renders as that card; a number renders
            as a value card.
        provider (MetricsProvider): Builds the service from the admin's
            database session.
        method (str): The service method that returns ``schema``. Called
            with no arguments; sync or ``async``.
        thresholds (Mapping[str, Threshold]): Field name → bands. Applies to
            a number or to the current value of a :class:`MetricTrend`.
        units (Mapping[str, str]): Field name → unit shown after a number
            (``"BRL"``, ``"rides"``).
        slug (str): The URL segment, derived from ``title``.

    Raises:
        ValueError: When ``method`` is empty, or ``thresholds`` / ``units``
            name a field ``schema`` does not have — a typo there would
            otherwise leave the card uncoloured in silence.
    """

    title: str
    schema: type[BaseModel]
    provider: MetricsProvider
    method: str
    thresholds: Mapping[str, Threshold] = field(default_factory=dict)
    units: Mapping[str, str] = field(default_factory=dict)
    slug: str = field(init=False)

    def __post_init__(self) -> None:
        """Derive the slug and validate the field references.

        Raises:
            ValueError: On an empty method or an unknown field name.
        """
        object.__setattr__(self, "slug", _slugify(self.title))
        if not self.method:
            raise ValueError(f"MetricsArea {self.title!r} needs a method name")
        known = set(self.schema.model_fields)
        for option, names in (("thresholds", self.thresholds), ("units", self.units)):
            unknown = sorted(set(names) - known)
            if unknown:
                raise ValueError(
                    f"MetricsArea {self.title!r}: {option} name {unknown} not in "
                    f"{self.schema.__name__} fields {sorted(known)}"
                )

    async def collect(self, session: AsyncSession) -> BaseModel:
        """Build the service and call the method.

        Args:
            session (AsyncSession): The admin's database session.

        Returns:
            BaseModel: What the method returned.

        Raises:
            TypeError: When the method returns something other than an
                instance of :attr:`schema`.
        """
        service = self.provider(session)
        if inspect.isawaitable(service):
            service = await service
        result = getattr(service, self.method)()
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, self.schema):
            raise TypeError(
                f"{type(service).__name__}.{self.method}() returned "
                f"{type(result).__name__}, not {self.schema.__name__}"
            )
        return result

    def cards(
        self, result: BaseModel
    ) -> list[tuple[str, str | None, CardData, MetricLevelEnum | None]]:
        """Turn the method's result into card data, in field order.

        Args:
            result (BaseModel): An instance of :attr:`schema`.

        Returns:
            list[tuple[str, str | None, CardData, MetricLevelEnum | None]]:
            ``(label, help, data, level)`` per field; ``level`` is ``None``
            for a field with no threshold or no numeric value.
        """
        cards: list[tuple[str, str | None, CardData, MetricLevelEnum | None]] = []
        for name, info in self.schema.model_fields.items():
            value = getattr(result, name)
            data: CardData
            if isinstance(value, (MetricValue, MetricTrend, MetricPartition)):
                data = value
            else:
                data = MetricValue(value, unit=self.units.get(name))
            threshold = self.thresholds.get(name)
            level: MetricLevelEnum | None = None
            if threshold is not None:
                current = (
                    data.value if isinstance(data, (MetricValue, MetricTrend)) else None
                )
                if _is_number(current):
                    level = threshold.level(float(current))
            cards.append((info.title or name, info.description, data, level))
        return cards


__all__: list[str] = [
    "MetricLevelEnum",
    "MetricsArea",
    "MetricsProvider",
    "Threshold",
]
