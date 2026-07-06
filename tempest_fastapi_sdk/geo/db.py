"""Database integration for geolocation — model mixin + radius search.

Three pieces plug geolocation into the SDK's SQLAlchemy layer:

* :class:`GeoPointMixin` — a model mixin adding indexed ``latitude`` /
  ``longitude`` columns to any :class:`~tempest_fastapi_sdk.BaseModel`.
* :class:`GeoRepositoryMixin` — a repository mixin whose :meth:`nearby`
  runs on **any** database: a cheap bounding-box pre-filter in SQL, then an
  exact Haversine refine + distance sort in Python.
* :class:`PostGISRepositoryMixin` — a repository mixin whose :meth:`nearby`
  pushes the whole radius query into PostgreSQL via PostGIS ``ST_DWithin``
  (opt-in; requires the PostGIS extension). No extra Python dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Float, Index, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from tempest_fastapi_sdk.geo.distance import haversine_km
from tempest_fastapi_sdk.geo.geometry import bounding_box
from tempest_fastapi_sdk.geo.schemas import Coordinate

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.repository import BaseRepository


class GeoPointMixin:
    """Mixin adding an indexed ``latitude`` / ``longitude`` point to a model.

    Mix it into a concrete model alongside
    :class:`~tempest_fastapi_sdk.BaseModel`::

        class StoreModel(GeoPointMixin, BaseModel):
            __tablename__ = "stores"
            name: Mapped[str] = mapped_column(String(120))

    A composite index on ``(latitude, longitude)`` backs the bounding-box
    pre-filter of :meth:`GeoRepositoryMixin.nearby`.

    Attributes:
        latitude (float): Latitude in decimal degrees (WGS84).
        longitude (float): Longitude in decimal degrees (WGS84).
    """

    latitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc="Latitude in decimal degrees (WGS84).",
    )
    longitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc="Longitude in decimal degrees (WGS84).",
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:  # noqa: N805
        """Add a composite ``(latitude, longitude)`` index for the row."""
        return (
            Index(
                f"ix_{cls.__tablename__}_lat_lon",  # type: ignore[attr-defined]
                "latitude",
                "longitude",
            ),
        )

    def coordinate(self) -> Coordinate:
        """Return this row's point as a :class:`Coordinate`.

        Returns:
            The row's latitude/longitude as a coordinate.
        """
        return Coordinate(latitude=self.latitude, longitude=self.longitude)


class GeoRepositoryMixin:
    """Repository mixin adding a database-agnostic radius search.

    Mix into a :class:`~tempest_fastapi_sdk.BaseRepository` for a model that
    carries ``latitude`` / ``longitude`` columns (e.g. via
    :class:`GeoPointMixin`)::

        class StoreRepository(GeoRepositoryMixin, BaseRepository[StoreModel]):
            ...

    :meth:`nearby` first narrows rows to the radius' bounding box in SQL
    (indexed, cheap), then computes the exact Haversine distance in Python
    and drops the box corners that fall outside the circle.
    """

    async def nearby(
        self,
        center: Coordinate,
        radius_km: float,
        *,
        extra_filters: dict[str, Any] | None = None,
        limit: int | None = None,
        latitude_field: str = "latitude",
        longitude_field: str = "longitude",
    ) -> list[Any]:
        """Return rows within ``radius_km`` of ``center``, nearest first.

        Args:
            center: The circle centre.
            radius_km: The inclusive radius in kilometres.
            extra_filters: Optional additional repository filters (ANDed).
            limit: Optional cap on the number of rows returned.
            latitude_field: Name of the latitude column.
            longitude_field: Name of the longitude column.

        Returns:
            The matching rows sorted by ascending distance (``[]`` when none
            match).
        """
        repo = cast("BaseRepository[Any]", self)
        box = bounding_box(center, radius_km)
        filters: dict[str, Any] = {
            f"{latitude_field}__gte": box.min_latitude,
            f"{latitude_field}__lte": box.max_latitude,
            f"{longitude_field}__gte": box.min_longitude,
            f"{longitude_field}__lte": box.max_longitude,
        }
        if extra_filters:
            filters.update(extra_filters)

        rows = await repo.list(filters=filters)
        scored: list[tuple[Any, float]] = []
        for row in rows:
            point = Coordinate(
                latitude=getattr(row, latitude_field),
                longitude=getattr(row, longitude_field),
            )
            distance = haversine_km(center, point)
            if distance <= radius_km:
                scored.append((row, distance))
        scored.sort(key=lambda item: item[1])
        result = [row for row, _ in scored]
        return result[:limit] if limit is not None else result


class PostGISRepositoryMixin:
    """Repository mixin whose radius search runs entirely in PostGIS.

    Mix into a :class:`~tempest_fastapi_sdk.BaseRepository` when the database
    is PostgreSQL with the PostGIS extension enabled. :meth:`nearby` pushes
    the radius filter and distance sort into SQL via ``ST_DWithin`` /
    ``ST_Distance`` over ``geography`` points built from the plain
    ``latitude`` / ``longitude`` columns — no special column type and no
    ``geoalchemy2`` dependency.
    """

    async def nearby(  # pragma: no cover - requires a live PostGIS database
        self,
        center: Coordinate,
        radius_km: float,
        *,
        limit: int | None = None,
        latitude_field: str = "latitude",
        longitude_field: str = "longitude",
    ) -> list[Any]:
        """Return rows within ``radius_km`` of ``center``, nearest first.

        Args:
            center: The circle centre.
            radius_km: The inclusive radius in kilometres.
            limit: Optional cap on the number of rows returned.
            latitude_field: Name of the latitude column.
            longitude_field: Name of the longitude column.

        Returns:
            The matching rows ordered by ascending distance.
        """
        from sqlalchemy import select

        repo = cast("BaseRepository[Any]", self)
        point = (
            f"ST_SetSRID(ST_MakePoint({longitude_field}, {latitude_field}), 4326)"
            "::geography"
        )
        origin = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography"
        query = (
            select(repo.model)
            .where(text(f"ST_DWithin({point}, {origin}, :meters)"))
            .order_by(text(f"ST_Distance({point}, {origin})"))
        )
        if limit is not None:
            query = query.limit(limit)
        params: dict[str, Any] = {
            "lat": center.latitude,
            "lon": center.longitude,
            "meters": radius_km * 1000.0,
        }
        result = await repo.session.execute(query, params)
        return list(result.unique().scalars().all())


def make_geo_point_model(
    *,
    tablename: str,
    class_name: str = "GeoPointModel",
) -> type[Any]:
    """Build a minimal concrete model with a geographic point at runtime.

    A convenience for tests and scripts — production projects should
    hand-write a model mixing :class:`GeoPointMixin` into
    :class:`~tempest_fastapi_sdk.BaseModel`. The generated class inherits
    ``id`` / ``is_active`` / timestamps from ``BaseModel`` and
    ``latitude`` / ``longitude`` from :class:`GeoPointMixin`.

    Args:
        tablename: ``__tablename__`` for the generated class.
        class_name: Python class name.

    Returns:
        A concrete mapped class with ``latitude`` / ``longitude`` columns.
    """
    from tempest_fastapi_sdk.db.model import BaseModel

    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (GeoPointMixin, BaseModel), attrs)


__all__: list[str] = [
    "GeoPointMixin",
    "GeoRepositoryMixin",
    "PostGISRepositoryMixin",
    "make_geo_point_model",
]
