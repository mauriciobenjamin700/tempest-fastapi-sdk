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


class _FakeRedisModule:
    """Stand-in for ``redis.asyncio`` handing out a fresh fake per connect."""

    def __init__(self) -> None:
        self.built: list[object] = []

    @property
    def Redis(self) -> type:  # noqa: N802
        """Return a namespace exposing ``from_url``."""
        built = self.built

        class _Factory:
            @staticmethod
            def from_url(url: str, **kwargs: object) -> object:
                client = fakeredis.aioredis.FakeRedis(decode_responses=True)
                built.append(client)
                return client

        return _Factory


@pytest.fixture
def reconnectable(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AsyncRedisManager, _FakeRedisModule]:
    module = _FakeRedisModule()
    monkeypatch.setattr(
        "tempest_fastapi_sdk.cache.redis_manager._require_redis",
        lambda: module,
    )
    return AsyncRedisManager("redis://fake:6379/0"), module


class TestClientProxy:
    def test_readable_before_connect(self) -> None:
        """The whole point: no RuntimeError while wiring middleware."""
        mgr = AsyncRedisManager("redis://nowhere:6379/0")
        proxy = mgr.client_proxy
        assert proxy is not None

    def test_same_object_every_time(self) -> None:
        mgr = AsyncRedisManager("redis://nowhere:6379/0")
        assert mgr.client_proxy is mgr.client_proxy

    def test_command_before_connect_raises(self) -> None:
        mgr = AsyncRedisManager("redis://nowhere:6379/0")
        with pytest.raises(RuntimeError):
            _ = mgr.client_proxy.get

    async def test_forwards_to_the_live_client(
        self, manager: AsyncRedisManager
    ) -> None:
        await manager.client_proxy.set("k", "v")
        assert await manager.client.get("k") == "v"

    async def test_survives_a_reconnect(
        self,
        reconnectable: tuple[AsyncRedisManager, _FakeRedisModule],
    ) -> None:
        """A handle taken once keeps writing to whichever client is live.

        The raw ``client`` captured alongside it goes stale, which is
        exactly why holding one in a store is a defect.
        """
        mgr, module = reconnectable
        proxy = mgr.client_proxy

        await mgr.connect()
        stale = mgr.client
        await mgr.disconnect()
        await mgr.connect()

        assert mgr.client is not stale
        assert len(module.built) == 2

        await proxy.set("after", "reconnect")
        assert await mgr.client.get("after") == "reconnect"
        assert await stale.get("after") is None

    async def test_repr_names_the_url(self) -> None:
        mgr = AsyncRedisManager("redis://nowhere:6379/0")
        assert "redis://nowhere:6379/0" in repr(mgr.client_proxy)
