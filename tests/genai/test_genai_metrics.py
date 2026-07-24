"""Tests for genai Prometheus metrics."""

from __future__ import annotations

import httpx
from prometheus_client import CollectorRegistry

from tempest_fastapi_sdk.genai import GenAIMetrics, OllamaGenerator
from tempest_fastapi_sdk.utils.http_client import HTTPClient


def _value(registry: CollectorRegistry, name: str, labels: dict[str, str]) -> float:
    return registry.get_sample_value(name, labels) or 0.0


class TestGenAIMetrics:
    async def test_track_records_request_and_latency(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        async with metrics.track("m", "generate"):
            pass
        labels = {"model": "m", "op": "generate"}
        assert _value(registry, "genai_requests_total", labels) == 1.0
        assert _value(registry, "genai_request_seconds_count", labels) == 1.0

    async def test_track_records_tokens_set_on_span(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        async with metrics.track("m", "generate") as span:
            span.tokens_in = 10
            span.tokens_out = 20
        assert _value(registry, "genai_tokens_in_total", {"model": "m"}) == 10.0
        assert _value(registry, "genai_tokens_out_total", {"model": "m"}) == 20.0

    async def test_multiple_calls_accumulate(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        for _ in range(3):
            async with metrics.track("m", "generate"):
                pass
        labels = {"model": "m", "op": "generate"}
        assert _value(registry, "genai_requests_total", labels) == 3.0

    def test_record_tokens_ignores_none(self) -> None:
        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        metrics.record_tokens("m", tokens_in=None, tokens_out=None)
        assert _value(registry, "genai_tokens_in_total", {"model": "m"}) == 0.0


class TestOllamaGeneratorMetrics:
    async def test_generate_records_request_and_ollama_tokens(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "response": "hi",
                    "prompt_eval_count": 3,
                    "eval_count": 5,
                    "done": True,
                },
            )

        registry = CollectorRegistry()
        metrics = GenAIMetrics(registry=registry)
        client = HTTPClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client, metrics=metrics)
        text = await gen.generate("x")
        await client.aclose()

        assert text == "hi"
        labels = {"model": "llama3.2", "op": "generate"}
        assert _value(registry, "genai_requests_total", labels) == 1.0
        assert _value(registry, "genai_tokens_in_total", {"model": "llama3.2"}) == 3.0
        assert _value(registry, "genai_tokens_out_total", {"model": "llama3.2"}) == 5.0

    async def test_generate_without_metrics_still_works(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"response": "ok", "done": True})

        client = HTTPClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client)
        assert await gen.generate("x") == "ok"
        await client.aclose()
