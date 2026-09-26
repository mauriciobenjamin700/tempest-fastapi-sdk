"""Tests for prompt→completion generation caching."""

from __future__ import annotations

import json

import pytest

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    InMemoryGenerationCache,
    OllamaGenerator,
    TextGenerator,
    cached_generate,
    is_deterministic,
    make_generation_key,
)
from tempest_fastapi_sdk.genai.generation_cache import _cache_get, _cache_set
from tempest_fastapi_sdk.genai.schemas import HardwareInfo


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


class TestInMemoryBound:
    """The in-memory cache is an LRU, not a dict that grows forever."""

    def test_default_is_bounded(self) -> None:
        """1001 prompts used to leave 1001 entries; the default caps at 1024."""
        cache = InMemoryGenerationCache()
        for i in range(2000):
            cache.set(f"k{i}", "v")
        assert len(cache) == 1024
        assert cache.get("k0") is None
        assert cache.get("k1999") == "v"

    def test_evicts_least_recently_used(self) -> None:
        """A read refreshes an entry, so the untouched one goes first."""
        cache = InMemoryGenerationCache(max_entries=2)
        cache.set("a", "1")
        cache.set("b", "2")
        assert cache.get("a") == "1"
        cache.set("c", "3")
        assert cache.get("b") is None
        assert cache.get("a") == "1"
        assert cache.get("c") == "3"

    def test_none_disables_the_bound(self) -> None:
        """``max_entries=None`` keeps the old unbounded behaviour on request."""
        cache = InMemoryGenerationCache(max_entries=None)
        for i in range(1500):
            cache.set(f"k{i}", "v")
        assert len(cache) == 1500

    def test_rejects_non_positive_capacity(self) -> None:
        """A capacity of zero would evict every write."""
        with pytest.raises(ValueError, match="max_entries"):
            InMemoryGenerationCache(max_entries=0)


class TestKeyScope:
    """The key separates call shape and weight identity."""

    def test_chat_and_generate_do_not_collide(self) -> None:
        """``generate(json.dumps(msgs))`` and ``chat(msgs)`` shared a key."""
        prompt = '[{"content": "hi", "role": "user"}]'
        params = {"do_sample": False}
        assert make_generation_key("m", prompt, params) != make_generation_key(
            "m", prompt, params, operation="chat"
        )

    def test_identity_separates_revision_and_quantization(self) -> None:
        """Same model id at another revision or quantization is another key."""
        base = make_generation_key("m", "hi", {"do_sample": False})
        rev = make_generation_key(
            "m", "hi", {"do_sample": False}, identity={"revision": "abc"}
        )
        quant = make_generation_key(
            "m", "hi", {"do_sample": False}, identity={"quantization": "int4"}
        )
        assert len({base, rev, quant}) == 3

    def test_default_key_is_unchanged(self) -> None:
        """Plain ``generate`` keys keep their digest, so Redis stays warm."""
        empty = make_generation_key(
            "m", "hi", {"do_sample": False}, identity={"revision": None}
        )
        assert empty == make_generation_key("m", "hi", {"do_sample": False})
        assert empty == (
            "196dc35ceabafa43b1eb2af18603c89d798db7d49ea82c7ffba75d458e39868f"
        )

    async def test_text_generator_keys_on_revision(self) -> None:
        """A pinned revision never reads the entry of an unpinned generator."""
        hardware = HardwareInfo(
            cpu_cores=2, ram_total_bytes=10**9, ram_available_bytes=10**9
        )
        cache = InMemoryGenerationCache()
        pinned = TextGenerator(
            "m", revision="bbb", hardware=hardware, generation_cache=cache
        )
        cfg = GenerationConfig(do_sample=False)
        stale = make_generation_key("m", "hi", pinned._key_params(cfg, {}))
        cache.set(stale, "other-weights")

        def fresh(*_args: object) -> str:
            """Stand in for the model so no weights are loaded."""
            return "pinned-weights"

        pinned._generate_sync = fresh  # type: ignore[method-assign]
        assert await pinned.generate("hi", config=cfg) == "pinned-weights"
        assert await pinned.generate("hi", config=cfg) == "pinned-weights"
        assert len(cache) == 2

    async def test_text_generator_chat_keys_under_chat(self) -> None:
        """``TextGenerator.chat`` reads the ``chat``-scoped entry."""
        hardware = HardwareInfo(
            cpu_cores=2, ram_total_bytes=10**9, ram_available_bytes=10**9
        )
        cache = InMemoryGenerationCache()
        gen = TextGenerator("m", hardware=hardware, generation_cache=cache)
        cfg = GenerationConfig(do_sample=False)
        messages = [{"role": "user", "content": "hi"}]
        key = make_generation_key(
            "m",
            json.dumps(messages, sort_keys=True, default=str),
            gen._key_params(cfg, {}),
            operation="chat",
        )
        cache.set(key, "cached-chat")
        assert await gen.chat(messages, config=cfg) == "cached-chat"
        assert gen.is_loaded is False
