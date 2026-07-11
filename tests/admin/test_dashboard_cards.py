"""End-to-end tests for admin dashboard business-metric cards."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    MetricCard,
    MetricPartition,
    MetricTrend,
    MetricValue,
    UserModelAuthBackend,
    make_admin_router,
)


class CardUser(BaseUserModel):
    __tablename__ = "admin_card_users"


SECRET = "x" * 48


async def _value(_session: AsyncSession) -> MetricValue:
    return MetricValue(42, unit="orders")


async def _trend(_session: AsyncSession) -> MetricTrend:
    return MetricTrend(value=120.0, previous=100.0, unit="BRL")


async def _partition(_session: AsyncSession) -> MetricPartition:
    return MetricPartition(segments=[("free", 30.0), ("pro", 10.0)])


async def _boom(_session: AsyncSession) -> MetricValue:
    raise RuntimeError("metric blew up")


@pytest.fixture
async def app_cards() -> AsyncIterator[FastAPI]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = CardUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    site = AdminSite(
        title="Cards Admin",
        dashboard_cards=[
            MetricCard("Orders", _value, help_text="last 24h"),
            MetricCard("Revenue", _trend),
            MetricCard("Plans", _partition),
            MetricCard("Broken", _boom),
        ],
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(CardUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
        )
    )
    yield app
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_dashboard_renders_cards(app_cards: FastAPI) -> None:
    async with _client(app_cards) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
        )
        response = await client.get("/admin/")

    assert response.status_code == 200
    body = response.text
    assert "tempest-admin-cards" in body
    # Value card.
    assert "Orders" in body
    assert "42" in body
    assert "last 24h" in body
    # Trend card: +20% up.
    assert "Revenue" in body
    assert "tempest-admin-card__trend--up" in body
    assert "+20.0%" in body
    # Partition card segments.
    assert "Plans" in body
    assert "free" in body
    assert "pro" in body
    # The broken card was skipped, not fatal.
    assert "Broken" not in body


def test_metric_trend_properties() -> None:
    up = MetricTrend(value=120.0, previous=100.0)
    assert up.delta == 20.0
    assert up.pct == 20.0
    assert up.direction == "up"

    down = MetricTrend(value=8.0, previous=10.0)
    assert down.direction == "down"
    assert down.pct == pytest.approx(-20.0)

    flat = MetricTrend(value=5.0, previous=5.0)
    assert flat.direction == "flat"

    no_base = MetricTrend(value=5.0, previous=0.0)
    assert no_base.pct is None


def test_metric_partition_total() -> None:
    part = MetricPartition(segments=[("a", 3.0), ("b", 7.0)])
    assert part.total == 10.0
