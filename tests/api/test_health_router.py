"""Tests for tempest_fastapi_sdk.api.routers.health."""

import asyncio
import logging
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import AsyncDatabaseManager, make_health_router

HEALTH_LOGGER: str = "tempest_fastapi_sdk.api.routers.health"


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


@pytest.mark.asyncio
async def test_readiness_hides_checks_when_expose_checks_false() -> None:
    """Production deployments can hide the per-dependency breakdown."""

    async def db_check() -> bool:
        return False

    app = FastAPI()
    app.include_router(
        make_health_router(checks={"database": db_check}, expose_checks=False),
    )
    async with _client(app) as client:
        response = await client.get("/health/readiness")
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert "checks" not in body


@pytest.mark.asyncio
async def test_readiness_hung_check_fails_alone_within_timeout() -> None:
    """A check that never answers is cancelled at ``timeout``; the others pass."""

    async def hung() -> bool:
        await asyncio.sleep(60)
        return True

    async def healthy() -> bool:
        return True

    app = FastAPI()
    app.include_router(
        make_health_router(checks={"hung": hung, "ok": healthy}, timeout=0.2),
    )
    started = time.monotonic()
    async with _client(app) as client:
        response = await client.get("/health/readiness")
    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["checks"] == {"hung": False, "ok": True}
    assert elapsed < 5.0


@pytest.mark.asyncio
async def test_readiness_runs_checks_concurrently() -> None:
    """Each check waits for the other to start, so a serial loop times out both."""
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    async def first() -> bool:
        first_started.set()
        await second_started.wait()
        return True

    async def second() -> bool:
        second_started.set()
        await first_started.wait()
        return True

    app = FastAPI()
    app.include_router(
        make_health_router(checks={"first": first, "second": second}, timeout=2.0),
    )
    async with _client(app) as client:
        response = await client.get("/health/readiness")
    assert response.status_code == 200
    assert response.json()["checks"] == {"first": True, "second": True}


@pytest.mark.asyncio
async def test_readiness_log_omits_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Driver messages carry the DSN; only the name and exception type are logged."""
    secret = "postgresql://app:s3cr3t-pa55@db.internal:5432/app"

    async def broken() -> bool:
        raise ConnectionRefusedError(f"could not connect to {secret}")

    app = FastAPI()
    app.include_router(make_health_router(checks={"database": broken}))
    with caplog.at_level(logging.WARNING, logger=HEALTH_LOGGER):
        async with _client(app) as client:
            response = await client.get("/health/readiness")
    assert response.status_code == 503
    assert "s3cr3t-pa55" not in caplog.text
    assert "db.internal" not in caplog.text
    assert "'database'" in caplog.text
    assert "ConnectionRefusedError" in caplog.text


@pytest.mark.asyncio
async def test_readiness_timeout_is_logged_as_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A timed-out check is logged as a timeout, by name."""

    async def hung() -> bool:
        await asyncio.sleep(60)
        return True

    app = FastAPI()
    app.include_router(make_health_router(checks={"redis": hung}, timeout=0.05))
    with caplog.at_level(logging.WARNING, logger=HEALTH_LOGGER):
        async with _client(app) as client:
            await client.get("/health/readiness")
    assert "'redis' timed out" in caplog.text


@pytest.mark.parametrize("timeout", [0, -1.0])
def test_non_positive_timeout_is_refused(timeout: float) -> None:
    """A zero or negative timeout would fail every check, so it is refused."""
    with pytest.raises(ValueError, match="timeout must be positive"):
        make_health_router(timeout=timeout)
