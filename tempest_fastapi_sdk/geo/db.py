"""Database integration for geolocation — model mixin + radius search.

Three pieces plug geolocation into the SDK's SQLAlchemy layer:

* :class:`GeoPointMixin` — a model mixin adding indexed ``latitude`` /
  ``longitude`` columns to any :class:`~tempest_fastapi_sdk.BaseModel`.
* :class:`GeoRepositoryMixin` — a repository mixin whose :meth:`nearby`
  runs on **any** database: a cheap bounding-box pre-filter in SQL, then an
  exact Haversine refine + distance sort in Python. Its
  :meth:`~GeoRepositoryMixin.paginate_nearby` pushes the whole radius query
  — distance, filter, sort, count and page — into SQL through
  :func:`haversine_distance_sql`, on PostgreSQL and SQLite alike.
* :class:`PostGISRepositoryMixin` — a repository mixin whose :meth:`nearby`
  pushes the whole radius query into PostgreSQL via PostGIS ``ST_DWithin``
  (opt-in; requires the PostGIS extension). No extra Python dependency.
"""

from __future__ import annotations

from math import cos, pi, radians
from typing import TYPE_CHECKING, Any, Generic, NamedTuple, TypeVar, cast

from sqlalchemy import Float, Index, case, func, literal, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from tempest_fastapi_sdk.geo.distance import EARTH_RADIUS_KM, haversine_km
from tempest_fastapi_sdk.geo.geometry import bounding_box
from tempest_fastapi_sdk.geo.schemas import Coordinate

if TYPE_CHECKING:
    from sqlalchemy import Select

    from tempest_fastapi_sdk.db.expressions import WhereClause
    from tempest_fastapi_sdk.db.repository import BaseRepository

RowT = TypeVar("RowT")

_DEGREES_TO_RADIANS: float = pi / 180.0


class NearbyMatch(NamedTuple, Generic[RowT]):
    """One row of :meth:`GeoRepositoryMixin.paginate_nearby`, with its distance.

    A named tuple, so it unpacks (``for row, distance_km in page["items"]``)
    and reads by name (``match.row``, ``match.distance_km``). The distance is
    the value the database computed and sorted by, not a second Python
    computation that could disagree at the radius boundary.

    Attributes:
        row (RowT): The model instance.
        distance_km (float): Great-circle distance from the search centre,
            in kilometres.
    """

    row: RowT
    distance_km: float


def haversine_distance_sql(
    latitude: Any,
    longitude: Any,
    center: Coordinate,
) -> ColumnElement[float]:
    """Build the Haversine great-circle distance as a SQL expression.

    Uses only ``sin``, ``cos``, ``asin`` and ``sqrt`` plus arithmetic — no
    PostGIS, no ``radians()`` (degrees are converted by multiplying with a
    bound constant). Plain PostgreSQL has all four; SQLite has them when
    built with its math functions (``SQLITE_ENABLE_MATH_FUNCTIONS``, SQLite
    3.35+). Check yours with ``SELECT sin(1), asin(1), sqrt(4)``; the
    SQLite 3.47.1 inside the CPython 3.13.3 that ``uv`` installs answers it.

    The haversine term is clamped to ``[0, 1]`` with a ``CASE`` before
    ``sqrt`` / ``asin``. Floating-point error pushes it past 1: for random
    antipodal pairs it came out as ``1.0000000000000002`` in about 4% of
    2,000,000 draws on PostgreSQL 16, and on SQLite 3.47.1 too. ``sqrt``
    happens to round that value back to ``1.0``, so neither engine failed
    in those draws — but ``asin`` of anything above 1 is ``NULL`` on SQLite
    and ``ERROR: input is out of range`` on PostgreSQL (both measured), and
    the clamp keeps the result from depending on that rounding. The
    centre's cosine is computed in Python and bound, so each row pays one
    ``cos``.

    Args:
        latitude (Any): The latitude column (or expression), in degrees.
        longitude (Any): The longitude column (or expression), in degrees.
        center (Coordinate): The point distances are measured from.

    Returns:
        ColumnElement[float]: The distance in kilometres, same formula and
        Earth radius (:data:`~tempest_fastapi_sdk.geo.EARTH_RADIUS_KM`) as
        :func:`~tempest_fastapi_sdk.geo.haversine_km`.
    """
    half = _DEGREES_TO_RADIANS / 2.0
    sin_half_lat = func.sin((latitude - center.latitude) * half, type_=Float)
    sin_half_lon = func.sin((longitude - center.longitude) * half, type_=Float)
    cos_row_lat = func.cos(latitude * _DEGREES_TO_RADIANS, type_=Float)
    cos_center_lat = cos(radians(center.latitude))
    term = sin_half_lat * sin_half_lat + (
        cos_row_lat * cos_center_lat * sin_half_lon * sin_half_lon
    )
    clamped = case(
        (term > 1.0, literal(1.0, Float)),
        (term < 0.0, literal(0.0, Float)),
        else_=term,
    )
    distance: ColumnElement[float] = func.asin(
        func.sqrt(clamped, type_=Float), type_=Float
    ) * (2.0 * EARTH_RADIUS_KM)
    return distance


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

    async def paginate_nearby(
        self,
        center: Coordinate,
        radius_km: float,
        *,
        page: int = 1,
        page_size: int = 20,
        extra_filters: dict[str, Any] | None = None,
        query: Select[Any] | None = None,
        where: WhereClause | None = None,
        latitude_field: str = "latitude",
        longitude_field: str = "longitude",
    ) -> dict[str, Any]:
        """Return one page of rows within ``radius_km``, nearest first, in SQL.

        Where :meth:`nearby` loads the whole bounding box and sorts in
        Python — so paginating it means paginating in memory — this method
        keeps everything in the database: the distance is
        :func:`haversine_distance_sql`, the radius is a ``WHERE`` on it
        (behind the indexable bounding-box pre-filter), and the sort,
        ``COUNT`` and ``OFFSET`` / ``LIMIT`` all run there. Ties on
        distance break on ``id``, so pages stay stable.

        Rows whose latitude or longitude is ``NULL`` never match. The
        bounding box is clamped at the antimeridian, exactly as in
        :meth:`nearby`, so a circle crossing longitude ±180 misses the far
        side.

        Args:
            center (Coordinate): The circle centre.
            radius_km (float): The inclusive radius in kilometres.
            page (int): The 1-indexed page number.
            page_size (int): Rows per page. Subject to the repository's
                ``max_page_size`` when one is set.
            extra_filters (dict[str, Any] | None): Repository filters
                ANDed with the radius (same vocabulary as
                :meth:`~tempest_fastapi_sdk.BaseRepository.paginate`).
            query (Select[Any] | None): A pre-built ``Select`` whose first
                entity is the model; ``None`` starts from
                ``select(model)``. The distance column is appended to it.
            where (WhereClause | None): A :class:`~tempest_fastapi_sdk.Q`
                condition tree ANDed with the rest.
            latitude_field (str): Name of the latitude column.
            longitude_field (str): Name of the longitude column.

        Returns:
            dict[str, Any]: The SDK pagination envelope — ``items``,
            ``total``, ``page``, ``page_size``, ``pages`` — where each item
            is a :class:`NearbyMatch` ``(row, distance_km)``. ``items`` is
            ``[]`` (and ``total`` ``0``) when nothing is in range.

        Raises:
            ValueError: When ``radius_km`` is negative, ``page`` or
                ``page_size`` is below 1, or either field name is not a
                mapped column of the model.
            PageSizeTooLargeException: When the repository sets
                ``max_page_size`` and ``page_size`` exceeds it.
        """
        repo = cast("BaseRepository[Any]", self)
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be at least 1")
        repo._check_page_size(page_size)
        columns = sa_inspect(repo.model).columns
        for name in (latitude_field, longitude_field):
            if name not in columns:
                raise ValueError(
                    f"{repo.model.__name__!r} has no column {name!r}",
                )
        box = bounding_box(center, radius_km)
        latitude = getattr(repo.model, latitude_field)
        longitude = getattr(repo.model, longitude_field)
        distance = haversine_distance_sql(latitude, longitude, center)

        base = query if query is not None else select(repo.model)
        base = base.where(
            latitude.is_not(None),
            longitude.is_not(None),
            latitude.between(box.min_latitude, box.max_latitude),
            longitude.between(box.min_longitude, box.max_longitude),
        )
        if extra_filters:
            base = repo._apply_filters(base, extra_filters)
        base = repo._apply_where(base, where)
        base = base.where(distance <= radius_km)

        count_query = select(func.count()).select_from(base.subquery())
        total = (await repo.session.execute(count_query)).scalar() or 0

        labelled = distance.label("distance_km")
        page_query = (
            base.add_columns(labelled)
            .order_by(labelled, repo.model.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await repo.session.execute(page_query)
        items = [
            NearbyMatch(row=row[0], distance_km=float(row[-1]))
            for row in result.unique().all()
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }


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

        Raises:
            ValueError: When either field name is not a mapped column. The
                two names are interpolated into a ``text()`` fragment — the
                centre coordinates and radius are bound parameters, but a
                column name cannot be — so they are checked against the
                mapper rather than trusted. Nothing stops a route from
                forwarding a query parameter here.
        """
        repo = cast("BaseRepository[Any]", self)
        columns = sa_inspect(repo.model).columns
        for name in (latitude_field, longitude_field):
            if name not in columns:
                raise ValueError(
                    f"{repo.model.__name__!r} has no column {name!r}",
                )
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
    "NearbyMatch",
    "PostGISRepositoryMixin",
    "haversine_distance_sql",
    "make_geo_point_model",
]
