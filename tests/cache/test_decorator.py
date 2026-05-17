"""Tests for the @cached decorator."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
async def manager() -> AsyncRedisManager:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mgr = AsyncRedisManager("redis://fake:6379/0")
    mgr._client = fake
    return mgr


class TestCachedDecorator:
    """Validate the @cached decorator behavior."""

    async def test_caches_result_and_avoids_second_call(
        self, manager: AsyncRedisManager
    ) -> None:
        calls: int = 0

        @cached(manager, ttl=60)
        async def expensive(x: int) -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"x": x, "doubled": x * 2}

        assert await expensive(3) == {"x": 3, "doubled": 6}
        assert await expensive(3) == {"x": 3, "doubled": 6}
        assert calls == 1

    async def test_different_args_get_different_keys(
        self, manager: AsyncRedisManager
    ) -> None:
        calls: list[int] = []

        @cached(manager, ttl=60)
        async def add(a: int, b: int) -> int:
            calls.append((a, b))
            return a + b

        assert await add(1, 2) == 3
        assert await add(4, 5) == 9
        assert await add(1, 2) == 3
        assert len(calls) == 2

    async def test_kwargs_order_does_not_affect_key(
        self, manager: AsyncRedisManager
    ) -> None:
        calls: int = 0

        @cached(manager, ttl=60)
        async def lookup(*, foo: int, bar: int) -> int:
            nonlocal calls
            calls += 1
            return foo + bar

        await lookup(foo=1, bar=2)
        await lookup(bar=2, foo=1)
        assert calls == 1

    async def test_zero_ttl_stores_without_expiry(
        self, manager: AsyncRedisManager
    ) -> None:
        @cached(manager, ttl=0, key_prefix="noexp:")
        async def value() -> int:
            return 42

        assert await value() == 42
        keys = await manager.client.keys("noexp:*")
        assert len(keys) == 1
        assert await manager.client.ttl(keys[0]) == -1

    async def test_skip_cache_bypasses_read_and_write(
        self, manager: AsyncRedisManager
    ) -> None:
        calls: int = 0

        @cached(
            manager,
            ttl=60,
            skip_cache=lambda args, kwargs: kwargs.get("nocache", False),
        )
        async def fetch(key: str, *, nocache: bool = False) -> int:
            nonlocal calls
            calls += 1
            return calls

        first = await fetch("k")
        assert first == 1
        # Cache hit — no new call.
        assert await fetch("k") == 1
        # Skip cache — new call.
        assert await fetch("k", nocache=True) == 2

    async def test_custom_key_prefix_namespaces_entries(
        self, manager: AsyncRedisManager
    ) -> None:
        @cached(manager, ttl=60, key_prefix="users:")
        async def get_user(user_id: int) -> dict[str, int]:
            return {"id": user_id}

        await get_user(7)
        keys = await manager.client.keys("users:*")
        assert any("get_user" in k for k in keys)

    async def test_corrupt_cache_value_falls_back_to_function(
        self, manager: AsyncRedisManager
    ) -> None:
        calls: int = 0

        @cached(manager, ttl=60, key_prefix="weird:")
        async def value() -> int:
            nonlocal calls
            calls += 1
            return 7

        # Seed an undecodable entry under the expected key.
        await value()
        keys = await manager.client.keys("weird:*")
        await manager.client.set(keys[0], "not-json-{{{")

        # Decode fails -> function runs again.
        assert await value() == 7
        assert calls == 2
