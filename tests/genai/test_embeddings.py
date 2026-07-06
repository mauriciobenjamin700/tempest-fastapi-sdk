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
