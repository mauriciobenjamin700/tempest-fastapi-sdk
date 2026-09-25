"""Tests for the geo repository mixin (portable radius search on SQLite)."""

from __future__ import annotations

from math import cos, pi, radians
from typing import Any, ClassVar

import pytest
from sqlalchemy import Float, String, func, literal, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    PageSizeTooLargeException,
    Q,
)
from tempest_fastapi_sdk.geo import (
    EARTH_RADIUS_KM,
    Coordinate,
    GeoPointMixin,
    GeoRepositoryMixin,
    NearbyMatch,
    bounding_box,
    destination_point,
    haversine_distance_sql,
    haversine_km,
    make_geo_point_model,
)

_Store = make_geo_point_model(tablename="geo_stores", class_name="_Store")


class _StoreRepository(GeoRepositoryMixin, BaseRepository[Any]):
    """Repository over the geo point model with the nearby mixin."""


SAO_PAULO = Coordinate(latitude=-23.5505, longitude=-46.6333)


def _repo(session: AsyncSession) -> _StoreRepository:
    return _StoreRepository(session, model=_Store)


class TestGeoPointMixin:
    def test_exposes_coordinate(self) -> None:
        row = _Store(latitude=-23.5, longitude=-46.6)
        assert isinstance(row, GeoPointMixin)
        assert row.coordinate().latitude == -23.5


class TestNearby:
    async def test_filters_and_sorts_by_distance(
        self,
        session: AsyncSession,
    ) -> None:
        repo = _repo(session)
        # ~0 km, ~1 km, and ~360 km (Rio) from São Paulo.
        await repo.add(_Store(latitude=-23.5505, longitude=-46.6333))
        await repo.add(_Store(latitude=-23.5595, longitude=-46.6333))
        await repo.add(_Store(latitude=-22.9068, longitude=-43.1729))

        found = await repo.nearby(SAO_PAULO, 50.0)

        assert len(found) == 2
        # Nearest first.
        assert found[0].latitude == -23.5505

    async def test_limit(self, session: AsyncSession) -> None:
        repo = _repo(session)
        await repo.add(_Store(latitude=-23.5505, longitude=-46.6333))
        await repo.add(_Store(latitude=-23.5595, longitude=-46.6333))
        found = await repo.nearby(SAO_PAULO, 50.0, limit=1)
        assert len(found) == 1

    async def test_empty_when_none_near(self, session: AsyncSession) -> None:
        repo = _repo(session)
        await repo.add(_Store(latitude=-22.9068, longitude=-43.1729))
        assert await repo.nearby(SAO_PAULO, 5.0) == []


class _Venue(BaseModel):
    """A model whose point is optional, to pin the ``NULL`` behavior."""

    __tablename__ = "geo_venues_nullable"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="bar")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)


class _VenueRepository(GeoRepositoryMixin, BaseRepository[_Venue]):
    """Radius search over the nullable-point model."""


class _CappedStoreRepository(GeoRepositoryMixin, BaseRepository[Any]):
    max_page_size: ClassVar[int | None] = 2


_POINTS: list[tuple[str, float | None, float | None]] = [
    ("se", -23.5505, -46.6333),
    ("paulista", -23.5614, -46.6559),
    ("pinheiros", -23.5670, -46.7020),
    ("santos", -23.9608, -46.3336),
    ("rio", -22.9068, -43.1729),
    ("no-lat", None, -46.6333),
    ("no-lng", -23.5505, None),
]


async def _venues(session: AsyncSession) -> _VenueRepository:
    repo = _VenueRepository(session, model=_Venue)
    await repo.add_all(
        [_Venue(name=name, lat=lat, lng=lng) for name, lat, lng in _POINTS],
    )
    return repo


def _expected(radius_km: float) -> list[tuple[str, float]]:
    rows = [
        (name, haversine_km(SAO_PAULO, Coordinate(latitude=lat, longitude=lng)))
        for name, lat, lng in _POINTS
        if lat is not None and lng is not None
    ]
    return sorted(
        [(name, d) for name, d in rows if d <= radius_km],
        key=lambda item: item[1],
    )


async def _page(
    repo: _VenueRepository, radius_km: float, **kwargs: Any
) -> dict[str, Any]:
    return await repo.paginate_nearby(
        SAO_PAULO,
        radius_km,
        latitude_field="lat",
        longitude_field="lng",
        **kwargs,
    )


class TestPaginateNearby:
    async def test_matches_the_python_haversine(self, session: AsyncSession) -> None:
        repo = await _venues(session)
        page = await _page(repo, 100.0, page_size=10)
        expected = _expected(100.0)
        assert [m.row.name for m in page["items"]] == [n for n, _ in expected]
        for match, (_, distance) in zip(page["items"], expected, strict=True):
            assert isinstance(match, NearbyMatch)
            assert match.distance_km == pytest.approx(distance, rel=1e-9)
        assert page["total"] == len(expected) == 4

    async def test_pages_in_the_database(self, session: AsyncSession) -> None:
        repo = await _venues(session)
        first = await _page(repo, 100.0, page=1, page_size=3)
        second = await _page(repo, 100.0, page=2, page_size=3)
        assert [m.row.name for m in first["items"]] == ["se", "paulista", "pinheiros"]
        assert [m.row.name for m in second["items"]] == ["santos"]
        assert first["total"] == second["total"] == 4
        assert first["pages"] == 2
        assert (first["page"], first["page_size"]) == (1, 3)

    async def test_items_unpack_as_row_and_distance(
        self, session: AsyncSession
    ) -> None:
        repo = await _venues(session)
        page = await _page(repo, 5.0)
        row, distance = page["items"][0]
        assert row.name == "se"
        assert distance == pytest.approx(0.0, abs=1e-9)

    async def test_radius_excludes_the_bounding_box_corners(
        self, session: AsyncSession
    ) -> None:
        repo = _VenueRepository(session, model=_Venue)
        corner = destination_point(SAO_PAULO, 45.0, 13.0)
        await repo.add(_Venue(name="corner", lat=corner.latitude, lng=corner.longitude))
        assert bounding_box(SAO_PAULO, 10.0).contains(corner)
        page = await _page(repo, 10.0)
        assert page["items"] == []
        assert page["total"] == 0
        assert page["pages"] == 0

    async def test_null_coordinates_never_match(self, session: AsyncSession) -> None:
        repo = await _venues(session)
        page = await _page(repo, 20_000.0, page_size=50)
        names = {m.row.name for m in page["items"]}
        assert "no-lat" not in names
        assert "no-lng" not in names
        assert page["total"] == 5

    async def test_extra_filters_and_where(self, session: AsyncSession) -> None:
        repo = await _venues(session)
        await repo.add(_Venue(name="club", kind="club", lat=-23.551, lng=-46.634))
        filtered = await _page(repo, 50.0, extra_filters={"kind": "club"})
        assert [m.row.name for m in filtered["items"]] == ["club"]
        assert filtered["total"] == 1
        narrowed = await _page(repo, 50.0, where=Q(name="paulista"))
        assert [m.row.name for m in narrowed["items"]] == ["paulista"]

    async def test_custom_query(self, session: AsyncSession) -> None:
        repo = await _venues(session)
        query = select(_Venue).where(_Venue.name != "se")
        page = await _page(repo, 100.0, query=query)
        assert page["items"][0].row.name == "paulista"
        assert page["total"] == 3

    async def test_empty_result_is_success(self, session: AsyncSession) -> None:
        repo = _VenueRepository(session, model=_Venue)
        page = await _page(repo, 10.0)
        assert page == {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "pages": 0,
        }

    async def test_respects_the_repository_page_size_ceiling(
        self, session: AsyncSession
    ) -> None:
        repo = _CappedStoreRepository(session, model=_Store)
        await repo.paginate_nearby(SAO_PAULO, 10.0, page_size=2)
        with pytest.raises(PageSizeTooLargeException):
            await repo.paginate_nearby(SAO_PAULO, 10.0, page_size=3)

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"page": 0}, "page"),
            ({"page_size": 0}, "page"),
            ({"latitude_field": "nope"}, "nope"),
            ({"longitude_field": "name; DROP"}, "DROP"),
        ],
    )
    async def test_rejects_bad_arguments(
        self, session: AsyncSession, kwargs: dict[str, Any], match: str
    ) -> None:
        repo = _repo(session)
        with pytest.raises(ValueError, match=match):
            await repo.paginate_nearby(SAO_PAULO, 10.0, **kwargs)

    async def test_negative_radius(self, session: AsyncSession) -> None:
        with pytest.raises(ValueError, match="radius"):
            await _repo(session).paginate_nearby(SAO_PAULO, -1.0)


_ANTIPODE_CENTER = Coordinate(latitude=38.11504808279405, longitude=-14.123338502486462)
_ANTIPODE_ROW = (-38.11504808279405, 165.87666149751354)


class TestHaversineSQL:
    async def test_asin_argument_is_clamped(self, session: AsyncSession) -> None:
        """A pair whose haversine term comes out as ``1.0000000000000002``.

        The raw term is asserted in the same engine, so the test cannot pass
        vacuously; the clamped distance must be exactly half the
        circumference. ``asin`` just above 1 is ``NULL`` on SQLite — the
        outcome the clamp exists to rule out.
        """
        lat = literal(_ANTIPODE_ROW[0], Float)
        lng = literal(_ANTIPODE_ROW[1], Float)
        half = (pi / 180.0) / 2.0
        c = _ANTIPODE_CENTER
        s_lat = func.sin((lat - c.latitude) * half)
        s_lng = func.sin((lng - c.longitude) * half)
        raw = s_lat * s_lat + (
            func.cos(lat * (pi / 180.0)) * cos(radians(c.latitude)) * s_lng * s_lng
        )
        raw_value = (await session.execute(select(raw))).scalar_one()
        assert raw_value > 1.0
        above_one = literal(1.0000000000000004, Float)
        assert (
            await session.execute(select(func.asin(above_one)))
        ).scalar_one() is None

        distance = (
            await session.execute(select(haversine_distance_sql(lat, lng, c)))
        ).scalar_one()
        assert distance == pytest.approx(pi * EARTH_RADIUS_KM, rel=1e-12)

    async def test_identical_point_is_zero(self, session: AsyncSession) -> None:
        lat = literal(SAO_PAULO.latitude, Float)
        lng = literal(SAO_PAULO.longitude, Float)
        distance = (
            await session.execute(select(haversine_distance_sql(lat, lng, SAO_PAULO)))
        ).scalar_one()
        assert distance == 0.0

    def test_compiles_for_postgresql_without_postgis(self) -> None:
        query = select(
            haversine_distance_sql(_Venue.lat, _Venue.lng, SAO_PAULO),
        )
        sql = str(query.compile(dialect=postgresql.dialect())).lower()
        for function in ("sin(", "cos(", "asin(", "sqrt(", "case when"):
            assert function in sql
        assert "st_" not in sql
        assert "radians(" not in sql

    async def test_paginate_nearby_compiles_for_postgresql(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The page query renders on the production dialect, sorted by distance."""
        repo = await _venues(session)
        captured: list[str] = []
        original = session.execute

        async def spy(statement: Any, *args: Any, **kwargs: Any) -> Any:
            captured.append(
                str(statement.compile(dialect=postgresql.dialect())).lower(),
            )
            return await original(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", spy)
        await _page(repo, 10.0, page=2, page_size=1)
        count_sql, page_sql = captured
        assert count_sql.startswith("select count(*)")
        assert "asin(" in count_sql
        assert "order by distance_km" in page_sql
        assert "limit" in page_sql
        assert "offset" in page_sql
        assert "lat is not null" in page_sql
        assert "between" in page_sql
