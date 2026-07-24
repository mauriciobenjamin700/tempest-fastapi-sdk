"""Tests for ambient OpenTelemetry spans on genai calls."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.genai import OllamaGenerator, genai_span
from tempest_fastapi_sdk.utils.http_client import HTTPClient


class TestGenaiSpanNoop:
    async def test_yields_settable_carrier_without_provider(self) -> None:
        """The span is usable (and never raises) with no OTel provider set."""
        async with genai_span("chat", "some-model") as span:
            span.tokens_in = 3
            span.tokens_out = 7
        assert span.tokens_out == 7


@pytest.fixture
def span_exporter() -> Iterator[Any]:
    """Attach an in-memory exporter to the active OTel provider.

    Skips when the ``[otel]`` extra is absent. Reuses an already-configured
    SDK ``TracerProvider`` when present (so it composes with other tests that
    call ``setup_tracing``), else installs a fresh one globally.
    """
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = trace.get_tracer_provider()
    if not isinstance(provider, sdk_trace.TracerProvider):
        provider = sdk_trace.TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


class TestGenaiSpanRecords:
    async def test_records_semconv_attributes(self, span_exporter: Any) -> None:
        async with genai_span("chat", "Qwen/Qwen2.5-7B") as span:
            span.tokens_in = 11
            span.tokens_out = 22
        (recorded,) = span_exporter.get_finished_spans()
        assert recorded.name == "chat Qwen/Qwen2.5-7B"
        attrs = dict(recorded.attributes)
        assert attrs["gen_ai.system"] == "tempest"
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.request.model"] == "Qwen/Qwen2.5-7B"
        assert attrs["gen_ai.usage.input_tokens"] == 11
        assert attrs["gen_ai.usage.output_tokens"] == 22

    async def test_extra_attributes_and_none_dropped(self, span_exporter: Any) -> None:
        async with genai_span("retrieve", "rag", **{"gen_ai.request.top_k": 5}):
            pass
        (recorded,) = span_exporter.get_finished_spans()
        attrs = dict(recorded.attributes)
        assert attrs["gen_ai.request.top_k"] == 5
        assert "gen_ai.usage.input_tokens" not in attrs

    async def test_marks_error_and_reraises(self, span_exporter: Any) -> None:
        from opentelemetry.trace import StatusCode

        with pytest.raises(ValueError, match="boom"):
            async with genai_span("generate", "m"):
                raise ValueError("boom")
        (recorded,) = span_exporter.get_finished_spans()
        assert recorded.status.status_code == StatusCode.ERROR
        assert recorded.events, "exception should be recorded on the span"


class TestOllamaEmitsSpan:
    async def test_generate_span_carries_token_usage(self, span_exporter: Any) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == "llama3.2"
            return httpx.Response(
                200,
                json={
                    "response": "hi there",
                    "prompt_eval_count": 9,
                    "eval_count": 4,
                    "done": True,
                },
            )

        client = HTTPClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client)
        text = await gen.generate("say hi")
        await client.aclose()

        assert text == "hi there"
        (recorded,) = span_exporter.get_finished_spans()
        assert recorded.name == "generate llama3.2"
        attrs = dict(recorded.attributes)
        assert attrs["gen_ai.usage.input_tokens"] == 9
        assert attrs["gen_ai.usage.output_tokens"] == 4
