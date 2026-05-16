"""Tests for tempest_fastapi_sdk.cache.AsyncRedisManager."""

import pytest

from tempest_fastapi_sdk.cache import AsyncRedisManager

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
async def manager(monkeypatch: pytest.MonkeyPatch) -> AsyncRedisManager:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mgr = AsyncRedisManager("redis://fake:6379/0")
    mgr._client = fake
    return mgr


class TestAsyncRedisManager:
    def test_client_raises_when_not_connected(self) -> None:
        mgr = AsyncRedisManager("redis://nowhere:6379/0")
        with pytest.raises(RuntimeError):
            _ = mgr.client

    async def test_set_get_round_trip(self, manager: AsyncRedisManager) -> None:
        await manager.client.set("k", "v")
        assert await manager.client.get("k") == "v"

    async def test_health_check_returns_true_on_ping(
        self, manager: AsyncRedisManager
    ) -> None:
        assert await manager.health_check() is True

    async def test_get_client_context_yields_client(
        self, manager: AsyncRedisManager
    ) -> None:
        async with manager.get_client_context() as client:
            await client.set("ctx", "ok")
            assert await client.get("ctx") == "ok"

    async def test_client_dependency_yields_client(
        self, manager: AsyncRedisManager
    ) -> None:
        gen = manager.client_dependency()
        client = await gen.__anext__()
        await client.set("dep", "ok")
        assert await client.get("dep") == "ok"
