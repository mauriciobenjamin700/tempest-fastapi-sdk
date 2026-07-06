"""Tests for RedisEmbeddingCache and Embedder's async-cache support."""

from __future__ import annotations

import json
from typing import Any

from tempest_fastapi_sdk.genai import Embedder, RedisEmbeddingCache


class _FakeRedis:
    """Minimal async Redis stub recording the last ``ex`` passed to set."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.last_ex: int | None = None

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.last_ex = ex


class TestRedisEmbeddingCache:
    async def test_set_then_get_roundtrip(self) -> None:
        cache = RedisEmbeddingCache(_FakeRedis())  # type: ignore[arg-type]
        await cache.set("k1", [0.1, 0.2, 0.3])
        assert await cache.get("k1") == [0.1, 0.2, 0.3]

    async def test_get_miss_returns_none(self) -> None:
        cache = RedisEmbeddingCache(_FakeRedis())  # type: ignore[arg-type]
        assert await cache.get("absent") is None

    async def test_prefix_and_ttl_applied(self) -> None:
        redis = _FakeRedis()
        cache = RedisEmbeddingCache(redis, prefix="emb:", ttl_seconds=60)  # type: ignore[arg-type]
        await cache.set("k", [1.0])
        assert "emb:k" in redis.store
        assert json.loads(redis.store["emb:k"]) == [1.0]
        assert redis.last_ex == 60

    async def test_decodes_bytes_payload(self) -> None:
        redis = _FakeRedis()
        cache = RedisEmbeddingCache(redis, prefix="emb:")  # type: ignore[arg-type]
        redis.store["emb:b"] = json.dumps([2.0, 3.0]).encode()  # type: ignore[assignment]
        assert await cache.get("b") == [2.0, 3.0]


class TestEmbedderWithAsyncCache:
    async def test_all_hit_skips_model_load(self) -> None:
        redis = _FakeRedis()
        cache = RedisEmbeddingCache(redis)  # type: ignore[arg-type]
        embedder = Embedder("dummy-model", cache=cache)
        key = embedder._cache_key("hello")
        await cache.set(key, [0.5, 0.5])

        vectors = await embedder.embed(["hello"])

        assert vectors == [[0.5, 0.5]]
        assert embedder.is_loaded is False

    async def test_sync_cache_still_works(self) -> None:
        from tempest_fastapi_sdk.genai import InMemoryEmbeddingCache

        cache: Any = InMemoryEmbeddingCache()
        embedder = Embedder("dummy-model", cache=cache)
        cache.set(embedder._cache_key("world"), [1.0])

        vectors = await embedder.embed(["world"])

        assert vectors == [[1.0]]
        assert embedder.is_loaded is False
