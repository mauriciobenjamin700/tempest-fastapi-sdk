"""BRLocationMixin, BRLocationSchema and br_location_distribution."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.geo import (
    BRLocationMixin,
    BRLocationSchema,
    UnknownMunicipalityError,
    br_location_distribution,
)


class _Person(BRLocationMixin, BaseModel):
    """A row with a location."""

    __tablename__ = "geo_location_people"

    name: Mapped[str] = mapped_column(String(32))
    paid: Mapped[bool] = mapped_column(Boolean, default=False)


class _NoLocation(BaseModel):
    """A row without the mixin."""

    __tablename__ = "geo_location_plain"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory database.

    Yields:
        AsyncSession: The session.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
        yield opened
    await engine.dispose()


class TestMixin:
    def test_stores_the_official_spelling_and_code(self) -> None:
        person = _Person(name="ana")
        municipality = person.set_br_location("sao paulo", "sp")
        assert (person.city, person.state, person.city_ibge_code) == (
            "São Paulo",
            "SP",
            3550308,
        )
        assert municipality.ibge_code == 3550308
        assert person.br_municipality() == municipality

    def test_unknown_city_raises_and_leaves_the_row(self) -> None:
        person = _Person(name="ana")
        person.set_br_location("Recife", "PE")
        with pytest.raises(UnknownMunicipalityError, match="SP has no municipality"):
            person.set_br_location("Recife", "SP")
        assert (person.city, person.state) == ("Recife", "PE")

    def test_unknown_state_raises(self) -> None:
        with pytest.raises(UnknownMunicipalityError):
            _Person(name="ana").set_br_location("Recife", "XX")

    def test_typo_cutoff_accepts_a_near_spelling(self) -> None:
        person = _Person(name="ana")
        with pytest.raises(UnknownMunicipalityError):
            person.set_br_location("Florianopoles", "SC")
        person.set_br_location("Florianopoles", "SC", typo_cutoff=0.85)
        assert person.city == "Florianópolis"

    def test_clear_forgets_everything(self) -> None:
        person = _Person(name="ana")
        person.set_br_location("Manaus", "AM")
        person.clear_br_location()
        assert (person.city, person.state, person.city_ibge_code) == (None, None, None)
        assert person.br_municipality() is None

    def test_state_and_code_are_indexed(self) -> None:
        indexed = {column.name for column in _Person.__table__.columns if column.index}
        assert {"state", "city_ibge_code"} <= indexed


class TestSchema:
    def _app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/location")
        async def location(data: BRLocationSchema) -> BRLocationSchema:
            return data

        return app

    def test_known_city_comes_back_official(self) -> None:
        with TestClient(self._app()) as client:
            response = client.post(
                "/location", json={"city": "  belo horizonte ", "state": "MG"}
            )
        assert response.status_code == 200
        assert response.json() == {
            "city": "Belo Horizonte",
            "state": "MG",
            "city_ibge_code": 3106200,
        }

    def test_unknown_city_is_422(self) -> None:
        with TestClient(self._app()) as client:
            response = client.post("/location", json={"city": "Recife", "state": "SP"})
        assert response.status_code == 422
        assert "SP has no municipality named 'Recife'" in response.text

    def test_unknown_state_is_422(self) -> None:
        with TestClient(self._app()) as client:
            response = client.post("/location", json={"city": "Recife", "state": "XX"})
        assert response.status_code == 422


async def _people(session: AsyncSession) -> None:
    """Store rows in three cities, two states, plus one without location."""
    for name, city, state, paid in [
        ("a", "São Paulo", "SP", True),
        ("b", "São Paulo", "SP", False),
        ("c", "Campinas", "SP", True),
        ("d", "Recife", "PE", True),
    ]:
        person = _Person(name=name, paid=paid)
        person.set_br_location(city, state)
        session.add(person)
    session.add(_Person(name="e"))
    handmade = _Person(name="f", state="RJ", city="Rio", city_ibge_code=None)
    session.add(handmade)
    await session.commit()


class TestDistribution:
    async def test_counts_per_state_and_city(self, session: AsyncSession) -> None:
        await _people(session)
        result = await br_location_distribution(session, _Person)
        assert (result.total, result.located, result.unlocated) == (6, 5, 1)
        assert result.state_counts() == {"SP": 3, "PE": 1, "RJ": 1}
        assert [(c.city, c.state, c.count) for c in result.cities] == [
            ("São Paulo", "SP", 2),
            ("Campinas", "SP", 1),
            ("Recife", "PE", 1),
        ]
        assert result.cities[0].latitude == pytest.approx(-23.5329)

    async def test_where_filters_every_count(self, session: AsyncSession) -> None:
        await _people(session)
        result = await br_location_distribution(
            session, _Person, where=_Person.paid.is_(True)
        )
        assert result.total == 3
        assert result.state_counts() == {"SP": 2, "PE": 1}

    async def test_city_limit_keeps_the_largest(self, session: AsyncSession) -> None:
        await _people(session)
        result = await br_location_distribution(session, _Person, city_limit=1)
        assert [c.city for c in result.cities] == ["São Paulo"]

    async def test_unknown_code_is_left_out_of_cities(
        self, session: AsyncSession
    ) -> None:
        session.add(_Person(name="x", state="SP", city="?", city_ibge_code=1))
        await session.commit()
        result = await br_location_distribution(session, _Person)
        assert result.state_counts() == {"SP": 1}
        assert result.cities == []

    async def test_empty_table(self, session: AsyncSession) -> None:
        result = await br_location_distribution(session, _Person)
        assert (result.total, result.located, result.states, result.cities) == (
            0,
            0,
            [],
            [],
        )

    async def test_model_without_the_mixin_is_refused(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(TypeError, match="mix BRLocationMixin in"):
            await br_location_distribution(session, _NoLocation)  # type: ignore[arg-type]
