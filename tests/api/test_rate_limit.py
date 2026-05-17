"""Tests for the in-process RateLimitMiddleware."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import RateLimitMiddleware


def _make_app(**kwargs: object) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, **kwargs)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health/liveness")
    async def liveness() -> dict[str, bool]:
        return {"alive": True}

    return app


@pytest.mark.asyncio
async def test_allows_requests_below_limit() -> None:
    app = _make_app(max_requests=3, window_seconds=10.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        statuses = [(await client.get("/ping")).status_code for _ in range(3)]
    assert statuses == [200, 200, 200]


@pytest.mark.asyncio
async def test_rejects_extra_requests_inside_window() -> None:
    app = _make_app(max_requests=2, window_seconds=10.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/ping")
        second = await client.get("/ping")
        third = await client.get("/ping")
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "Retry-After" in third.headers


@pytest.mark.asyncio
async def test_window_resets_after_expiry() -> None:
    app = _make_app(max_requests=1, window_seconds=0.1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/ping")
        second = await client.get("/ping")
        await asyncio.sleep(0.15)
        third = await client.get("/ping")
    assert first.status_code == 200
    assert second.status_code == 429
    assert third.status_code == 200


@pytest.mark.asyncio
async def test_exempt_paths_bypass_limiter() -> None:
    app = _make_app(
        max_requests=1,
        window_seconds=10.0,
        exempt_paths=("/health/liveness",),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            response = await client.get("/health/liveness")
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_custom_key_func_partitions_state() -> None:
    def key(request: object) -> str:
        return str(request.headers.get("x-tenant", "anon"))  # type: ignore[attr-defined]

    app = _make_app(max_requests=1, window_seconds=10.0, key_func=key)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/ping", headers={"x-tenant": "a"})
        second = await client.get("/ping", headers={"x-tenant": "b"})
        third = await client.get("/ping", headers={"x-tenant": "a"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_invalid_max_requests_raises() -> None:
    app = FastAPI()
    with pytest.raises(ValueError):
        app.add_middleware(RateLimitMiddleware, max_requests=0)
        app.build_middleware_stack()


def test_invalid_window_raises() -> None:
    app = FastAPI()
    with pytest.raises(ValueError):
        app.add_middleware(RateLimitMiddleware, window_seconds=0)
        app.build_middleware_stack()
