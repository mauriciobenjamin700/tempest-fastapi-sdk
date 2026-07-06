"""Tests for the geo repository mixin (portable radius search on SQLite)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.geo import (
    Coordinate,
    GeoPointMixin,
    GeoRepositoryMixin,
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
