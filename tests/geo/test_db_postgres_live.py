"""``paginate_nearby`` against a real PostgreSQL, without PostGIS.

Opt-in (``make test-docker``): starts ``postgres:16-alpine`` — the plain
image, no PostGIS extension — and runs the same radius page the SQLite suite
runs, so "works on plain PostgreSQL" is a measured claim rather than a
compiled-SQL one.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Float, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.geo import Coordinate, GeoRepositoryMixin, haversine_km

IMAGE: str = "postgres:16-alpine"
CONTAINER: str = "tempest-geo-nearby-probe"
PORT: int = 55436
SAO_PAULO = Coordinate(latitude=-23.5505, longitude=-46.6333)


class _LiveVenue(BaseModel):
    __tablename__ = "geo_live_venues"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)


class _LiveVenueRepository(GeoRepositoryMixin, BaseRepository[_LiveVenue]):
    """Radius search over the live model."""


_POINTS: list[tuple[str, float | None, float | None]] = [
    ("se", -23.5505, -46.6333),
    ("paulista", -23.5614, -46.6559),
    ("pinheiros", -23.5670, -46.7020),
    ("santos", -23.9608, -46.3336),
    ("rio", -22.9068, -43.1729),
    ("no-lat", None, -46.6333),
    ("antipode", 23.5505, 133.3667),
]


@pytest.fixture
def postgres_url() -> Iterator[str]:
    """Start a plain Postgres container and yield its URL.

    Yields:
        str: An async SQLAlchemy URL for the container.
    """
    pytest.importorskip("asyncpg")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_PASSWORD=probe",
            "-e",
            "POSTGRES_DB=probe",
            "-p",
            f"{PORT}:5432",
            IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.skip("postgres never became ready")
        time.sleep(1)
        yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{PORT}/probe"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest_asyncio.fixture
async def pg_session(postgres_url: str) -> AsyncIterator[AsyncSession]:
    """Yield a session over a seeded ``geo_live_venues`` table.

    Args:
        postgres_url (str): URL from the container fixture.

    Yields:
        AsyncSession: The session, closed and the engine disposed afterwards.
    """
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(_LiveVenue.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [_LiveVenue(name=n, lat=la, lng=lo) for n, la, lo in _POINTS],
        )
        await session.commit()
        yield session
    await engine.dispose()


async def _page(session: AsyncSession, radius_km: float, **kwargs: Any) -> Any:
    repo = _LiveVenueRepository(session, model=_LiveVenue)
    return await repo.paginate_nearby(
        SAO_PAULO,
        radius_km,
        latitude_field="lat",
        longitude_field="lng",
        **kwargs,
    )


@pytest.mark.docker
class TestPaginateNearbyOnPostgres:
    async def test_pages_and_distances_match(self, pg_session: AsyncSession) -> None:
        first = await _page(pg_session, 100.0, page=1, page_size=3)
        second = await _page(pg_session, 100.0, page=2, page_size=3)
        assert [m.row.name for m in first["items"]] == ["se", "paulista", "pinheiros"]
        assert [m.row.name for m in second["items"]] == ["santos"]
        assert first["total"] == 4
        for match in first["items"] + second["items"]:
            expected = haversine_km(
                SAO_PAULO,
                Coordinate(latitude=match.row.lat, longitude=match.row.lng),
            )
            assert match.distance_km == pytest.approx(expected, rel=1e-9)

    async def test_whole_globe_includes_the_antipode(
        self, pg_session: AsyncSession
    ) -> None:
        page = await _page(pg_session, 20_100.0, page_size=10)
        names = [m.row.name for m in page["items"]]
        assert names[-1] == "antipode"
        assert "no-lat" not in names
        assert page["total"] == 6
