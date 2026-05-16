"""Tests for tempest_fastapi_sdk.api.routers.health."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import AsyncDatabaseManager, make_health_router


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_liveness_always_ok() -> None:
    app = FastAPI()
    app.include_router(make_health_router())
    async with _client(app) as client:
        response = await client.get("/health/liveness")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_empty_checks_returns_ready() -> None:
    app = FastAPI()
    app.include_router(make_health_router())
    async with _client(app) as client:
        response = await client.get("/health/readiness")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ready"
    assert body["checks"] == {}


@pytest.mark.asyncio
async def test_readiness_with_database_ok() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    try:
        app = FastAPI()
        app.include_router(make_health_router(db=db, version="9.9.9"))
        async with _client(app) as client:
            response = await client.get("/health/readiness")
    finally:
        await db.disconnect()
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True
    assert body["version"] == "9.9.9"


@pytest.mark.asyncio
async def test_readiness_failing_check_returns_503() -> None:
    async def broken() -> bool:
        raise RuntimeError("nope")

    async def healthy() -> bool:
        return True

    app = FastAPI()
    app.include_router(make_health_router(checks={"broken": broken, "ok": healthy}))
    async with _client(app) as client:
        response = await client.get("/health/readiness")
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"] == {"broken": False, "ok": True}


@pytest.mark.asyncio
async def test_custom_prefix() -> None:
    app = FastAPI()
    app.include_router(make_health_router(prefix="/ops/health"))
    async with _client(app) as client:
        liveness = await client.get("/ops/health/liveness")
    assert liveness.status_code == 200
