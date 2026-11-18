"""Admin page: how the rows of a model spread across Brazil.

A :class:`BRLocationMap` registered with
``AdminSite(location_maps=[...])`` becomes a page under "Maps" in the
sidebar: a map of Brazil with each state shaded by its row count and a
bubble per city, a legend, the state and city tables, and how many rows have
no location. The counts come from
:func:`~tempest_fastapi_sdk.geo.br_location_distribution` over a model with
:class:`~tempest_fastapi_sdk.geo.BRLocationMixin`; the map is drawn offline
by :func:`~tempest_fastapi_sdk.geo.render_br_map_svg`, so the page makes no
request to a map or tile service.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.geo.br_map import BRMapPoint, br_map_legend, render_br_map_svg
from tempest_fastapi_sdk.geo.location import (
    BRLocationDistributionSchema,
    BRLocationMixin,
    br_location_distribution,
)
from tempest_fastapi_sdk.geo.schemas import Coordinate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement


def _location_slug(title: str) -> str:
    """Return the URL slug of a map title, accents dropped.

    Args:
        title (str): The map title.

    Returns:
        str: Lowercase ASCII words joined by ``-`` (``"usuarios-por-estado"``).

    Raises:
        ValueError: When the title has no letter or digit.
    """
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    if not slug:
        raise ValueError(f"BRLocationMap title {title!r} has no letter or digit")
    return slug


@dataclass(frozen=True)
class BRLocationMap:
    """A map page of where a model's rows are in Brazil.

    ``title`` and ``model`` are positional; the rest is keyword-only.

    The page needs ``VIEW`` on the model under the ``access_policy`` (it
    shows the model's data, aggregated), asked of the model's registered
    :class:`~tempest_fastapi_sdk.admin.AdminModel` or, when the model is not
    registered, of a throwaway ``AdminModel(model)``.

    Attributes:
        title (str): The page heading and the sidebar label.
        model (type[BRLocationMixin]): A model with
            :class:`~tempest_fastapi_sdk.geo.BRLocationMixin`.
        where (Callable[[], ColumnElement[bool]] | None): Builds an extra
            filter for every count (``lambda: UserModel.is_active.is_(True)``);
            a callable, so the expression is built per request.
        city_limit (int): How many cities the bubbles and the table show,
            largest first.
        value_label (str): Word after each number in the tooltips and the
            tables (``"usuários"``).
        slug (str): The URL segment, derived from ``title``.

    Raises:
        TypeError: When ``model`` does not have the mixin's columns.
        ValueError: When ``city_limit`` is below 1, or ``title`` yields an
            empty slug.
    """

    title: str
    model: type[BRLocationMixin]
    _: KW_ONLY
    where: Callable[[], ColumnElement[bool]] | None = None
    city_limit: int = 50
    value_label: str = "users"
    slug: str = field(init=False)

    def __post_init__(self) -> None:
        """Derive the slug and check the model and the limit.

        Raises:
            TypeError: When ``model`` lacks the mixin's columns.
            ValueError: When ``city_limit`` is below 1 or the slug is empty.
        """
        object.__setattr__(self, "slug", _location_slug(self.title))
        for name in ("city", "state", "city_ibge_code"):
            if not hasattr(self.model, name):
                raise TypeError(
                    f"BRLocationMap model {self.model.__name__} has no {name!r}; "
                    "mix BRLocationMixin in"
                )
        if self.city_limit < 1:
            raise ValueError("BRLocationMap city_limit must be at least 1")

    async def collect(self, session: AsyncSession) -> BRLocationDistributionSchema:
        """Count the model's rows per state and city.

        Args:
            session (AsyncSession): The admin's database session.

        Returns:
            BRLocationDistributionSchema: The counts, cities capped at
            :attr:`city_limit`.
        """
        return await br_location_distribution(
            session,
            self.model,
            where=None if self.where is None else self.where(),
            city_limit=self.city_limit,
        )

    def page_context(
        self, distribution: BRLocationDistributionSchema
    ) -> dict[str, Any]:
        """Build what the page template renders from the counts.

        Args:
            distribution (BRLocationDistributionSchema): The counts.

        Returns:
            dict[str, Any]: ``svg`` (the map markup, escaped inside),
            ``legend``, ``distribution`` and ``value_label``.
        """
        counts = distribution.state_counts()
        points = [
            BRMapPoint(
                label=f"{city.city} ({city.state})",
                coordinate=Coordinate(latitude=city.latitude, longitude=city.longitude),
                value=city.count,
            )
            for city in distribution.cities
        ]
        return {
            "svg": render_br_map_svg(
                counts, points, title=self.title, value_label=self.value_label
            ),
            "legend": br_map_legend(counts),
            "distribution": distribution,
            "value_label": self.value_label,
        }


__all__: list[str] = [
    "BRLocationMap",
]
