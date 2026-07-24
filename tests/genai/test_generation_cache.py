"""Tests for prompt→completion generation caching."""

from __future__ import annotations

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    InMemoryGenerationCache,
    OllamaGenerator,
    cached_generate,
    is_deterministic,
    make_generation_key,
)
from tempest_fastapi_sdk.genai.generation_cache import _cache_get, _cache_set


class TestIsDeterministic:
    def test_greedy(self) -> None:
        assert is_deterministic({"do_sample": False}) is True

    def test_zero_temperature(self) -> None:
        assert is_deterministic({"temperature": 0}) is True

    def test_sampling_is_not(self) -> None:
        assert is_deterministic({"do_sample": True, "temperature": 0.7}) is False

    def test_empty_is_not(self) -> None:
        assert is_deterministic({}) is False


class TestMakeKey:
    def test_stable_and_param_sensitive(self) -> None:
        a = make_generation_key("m", "hi", {"temperature": 0})
        b = make_generation_key("m", "hi", {"temperature": 0})
        c = make_generation_key("m", "hi", {"temperature": 0, "top_p": 0.9})
        assert a == b
        assert a != c


class TestCachedGenerate:
    async def test_deterministic_hits_cache(self) -> None:
        cache = InMemoryGenerationCache()
        calls = {"n": 0}

        async def producer() -> str:
            calls["n"] += 1
            return "cached-answer"

        params = {"do_sample": False}
        first = await cached_generate(cache, "m", "hi", params, producer)
        second = await cached_generate(cache, "m", "hi", params, producer)
        assert first == second == "cached-answer"
        assert calls["n"] == 1  # second served from cache

    async def test_sampling_never_cached(self) -> None:
        cache = InMemoryGenerationCache()
        calls = {"n": 0}

        async def producer() -> str:
            calls["n"] += 1
            return f"sample-{calls['n']}"

        params = {"do_sample": True, "temperature": 0.7}
        await cached_generate(cache, "m", "hi", params, producer)
        await cached_generate(cache, "m", "hi", params, producer)
        assert calls["n"] == 2  # each call runs the producer

    async def test_no_cache_passthrough(self) -> None:
        calls = {"n": 0}

        async def producer() -> str:
            calls["n"] += 1
            return "x"

        await cached_generate(None, "m", "hi", {"do_sample": False}, producer)
        assert calls["n"] == 1

    async def test_invalidate_via_store(self) -> None:
        cache = InMemoryGenerationCache()
        key = make_generation_key("m", "hi", {"do_sample": False})
        await _cache_set(cache, key, "old")
        assert await _cache_get(cache, key) == "old"
        cache._store.pop(key)
        assert await _cache_get(cache, key) is None


class TestOllamaGeneratorCacheKeyParams:
    def test_includes_config_overrides_images(self) -> None:
        gen = OllamaGenerator("llama3.2")
        params = gen._key_params(
            GenerationConfig(temperature=0), {"top_p": 0.9}, ["<b64>"]
        )
        assert params["temperature"] == 0
        assert params["top_p"] == 0.9
        assert params["images"] == ["<b64>"]
