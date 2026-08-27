"""Wiring a Redis-backed store at import time, which is when FastAPI asks."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import RateLimitMiddleware, RedisRateLimitStore
from tempest_fastapi_sdk.cache import AsyncRedisManager

fakeredis = pytest.importorskip("fakeredis")


def _fake_redis_module() -> object:
    """Return a stand-in for ``redis.asyncio`` backed by ``fakeredis``."""

    class _Factory:
        @staticmethod
        def from_url(url: str, **kwargs: object) -> object:
            return fakeredis.aioredis.FakeRedis(decode_responses=True)

    class _Module:
        Redis = _Factory

    return _Module()


def _build_app(manager: AsyncRedisManager) -> FastAPI:
    """Build the app the way a service does: middleware first, connect later.

    Args:
        manager (AsyncRedisManager): The manager whose client the store
            reads, still unconnected at this point.

    Returns:
        FastAPI: An app limited to two requests per minute.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await manager.connect()
        yield
        await manager.disconnect()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=2,
        window_seconds=60.0,
        store=RedisRateLimitStore(manager.client_proxy, fail_open=False),
    )

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "1"}

    return app


class TestRedisStoreFromProxy:
    def test_client_cannot_build_the_store_at_import_time(self) -> None:
        """The defect the proxy exists to fix, pinned."""
        manager = AsyncRedisManager("redis://fake:6379/0")
        with pytest.raises(RuntimeError, match="connect"):
            RedisRateLimitStore(manager.client)

    def test_proxy_wires_and_then_limits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Built before the lifespan, counting once the lifespan ran."""
        monkeypatch.setattr(
            "tempest_fastapi_sdk.cache.redis_manager._require_redis",
            _fake_redis_module,
        )
        manager = AsyncRedisManager("redis://fake:6379/0")
        app = _build_app(manager)

        with TestClient(app) as client:
            assert client.get("/ping").status_code == 200
            assert client.get("/ping").status_code == 200
            assert client.get("/ping").status_code == 429
