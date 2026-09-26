"""Tests for Embedder — cache/state paths (no torch)."""

from __future__ import annotations

import importlib.util

import pytest

from tempest_fastapi_sdk.genai import Embedder, InMemoryEmbeddingCache
from tempest_fastapi_sdk.genai.schemas import HardwareInfo


def _cpu() -> HardwareInfo:
    return HardwareInfo(cpu_cores=2, ram_total_bytes=10**9, ram_available_bytes=10**9)


class TestEmbedderCache:
    async def test_all_cached_skips_model_load(self) -> None:
        cache = InMemoryEmbeddingCache()
        emb = Embedder("m", cache=cache, hardware=_cpu())
        cache.set(emb._cache_key("hello"), [1.0, 2.0])
        cache.set(emb._cache_key("world"), [3.0, 4.0])

        vectors = await emb.embed(["hello", "world"])
        assert vectors == [[1.0, 2.0], [3.0, 4.0]]
        assert emb.is_loaded is False  # never touched the model

    @pytest.mark.skipif(
        importlib.util.find_spec("transformers") is not None,
        reason="transformers installed; the missing-extra path can't be exercised",
    )
    async def test_miss_without_extra_raises(self) -> None:
        emb = Embedder("m", cache=InMemoryEmbeddingCache(), hardware=_cpu())
        # not cached -> tries to load transformers (absent without the extra)
        with pytest.raises(ImportError, match=r"\[genai\]"):
            await emb.embed(["uncached"])


class TestEmbedderState:
    def test_not_loaded_initially(self) -> None:
        assert Embedder("m", hardware=_cpu()).is_loaded is False

    def test_unload_noop(self) -> None:
        emb = Embedder("m", hardware=_cpu())
        emb.unload()
        assert emb.is_loaded is False

    def test_unload_if_idle_without_threshold(self) -> None:
        assert Embedder("m", hardware=_cpu()).unload_if_idle() is False


class TestInMemoryEmbeddingCacheBound:
    """The in-memory embedding cache is an LRU with a default capacity."""

    def test_default_is_bounded(self) -> None:
        """An open-ended stream of texts no longer grows the store forever."""
        cache = InMemoryEmbeddingCache()
        for i in range(1500):
            cache.set(f"k{i}", [float(i)])
        assert len(cache) == 1024
        assert cache.get("k0") is None
        assert cache.get("k1499") == [1499.0]

    def test_evicts_least_recently_used(self) -> None:
        """A read refreshes an entry, so the untouched one goes first."""
        cache = InMemoryEmbeddingCache(max_entries=2)
        cache.set("a", [1.0])
        cache.set("b", [2.0])
        assert cache.get("a") == [1.0]
        cache.set("c", [3.0])
        assert cache.get("b") is None
        assert cache.get("a") == [1.0]

    def test_none_disables_the_bound(self) -> None:
        """``max_entries=None`` keeps the unbounded store on request."""
        cache = InMemoryEmbeddingCache(max_entries=None)
        for i in range(1100):
            cache.set(f"k{i}", [0.0])
        assert len(cache) == 1100

    def test_rejects_non_positive_capacity(self) -> None:
        """A capacity of zero would evict every write."""
        with pytest.raises(ValueError, match="max_entries"):
            InMemoryEmbeddingCache(max_entries=0)
