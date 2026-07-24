"""Metrics wiring on TextGenerator + Embedder (exercised via cache hits)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from tempest_fastapi_sdk.genai import (
    Embedder,
    GenAIMetrics,
    GenerationConfig,
    HardwareInfo,
    InMemoryEmbeddingCache,
    InMemoryGenerationCache,
    TextGenerator,
    make_generation_key,
)


def _cpu_hw() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=4, ram_total_bytes=8 * 10**9, ram_available_bytes=6 * 10**9
    )


def _value(registry: CollectorRegistry, name: str, labels: dict[str, str]) -> float:
    return registry.get_sample_value(name, labels) or 0.0


class TestTextGeneratorMetrics:
    async def test_generate_cache_hit_records_metric(self) -> None:
        registry = CollectorRegistry()
        cache = InMemoryGenerationCache()
        gen = TextGenerator(
            "m",
            hardware=_cpu_hw(),
            generation_cache=cache,
            metrics=GenAIMetrics(registry=registry),
        )
        cfg = GenerationConfig(temperature=0)
        key = make_generation_key("m", "hi", gen._key_params(cfg, {}))
        cache.set(key, "cached-answer")

        reply = await gen.generate("hi", config=cfg)
        assert reply == "cached-answer"
        assert gen.is_loaded is False  # served from cache, model never loaded
        labels = {"model": "m", "op": "generate"}
        assert _value(registry, "genai_requests_total", labels) == 1.0


class TestEmbedderMetrics:
    async def test_embed_cache_hit_records_metric(self) -> None:
        registry = CollectorRegistry()
        cache = InMemoryEmbeddingCache()
        emb = Embedder(
            "m",
            hardware=_cpu_hw(),
            cache=cache,
            metrics=GenAIMetrics(registry=registry),
        )
        cache.set(emb._cache_key("x"), [1.0, 2.0])
        vectors = await emb.embed(["x"])
        assert vectors == [[1.0, 2.0]]
        assert emb.is_loaded is False
        assert (
            _value(registry, "genai_requests_total", {"model": "m", "op": "embed"})
            == 1.0
        )

    async def test_no_metrics_still_works(self) -> None:
        cache = InMemoryEmbeddingCache()
        emb = Embedder("m", hardware=_cpu_hw(), cache=cache)
        cache.set(emb._cache_key("x"), [1.0])
        assert await emb.embed(["x"]) == [[1.0]]
