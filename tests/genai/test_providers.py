"""``hosted_generator`` builds the right backend from the model id.

The per-provider constants are pinned against the pages they were read from;
the generators run over an ``httpx.MockTransport``, so what is pinned is the
request built for each provider, not a live provider's behavior.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import (
    DEFAULT_ANTHROPIC_URL,
    DEFAULT_DEEPSEEK_URL,
    DEFAULT_GOOGLE_OPENAI_URL,
    DEFAULT_OPENAI_URL,
    GOOGLE_EFFORT_VALUES,
    MODEL_PREFIXES,
    OPENAI_MAX_TOKENS_FIELD,
    AnthropicGenerator,
    OpenAICompatGenerator,
    hosted_generator,
    resolve_provider,
)


class Answer(BaseModel):
    """The schema the request tests ask for."""

    value: str


class TestConstants:
    def test_google_root_is_the_documented_one(self) -> None:
        assert (
            DEFAULT_GOOGLE_OPENAI_URL
            == "https://generativelanguage.googleapis.com/v1beta/openai"
        )

    def test_google_effort_values(self) -> None:
        assert dict(GOOGLE_EFFORT_VALUES) == {
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "high",
        }

    def test_openai_budget_field(self) -> None:
        assert OPENAI_MAX_TOKENS_FIELD == "max_completion_tokens"

    def test_prefix_table(self) -> None:
        assert dict(MODEL_PREFIXES) == {
            "claude-": "anthropic",
            "gpt-": "openai",
            "chatgpt-": "openai",
            "o1": "openai",
            "o3": "openai",
            "o4": "openai",
            "gemini-": "google",
            "deepseek-": "deepseek",
        }


class TestResolveProvider:
    @pytest.mark.parametrize(
        ("model", "provider"),
        [
            ("claude-haiku-5-5", "anthropic"),
            ("Claude-Opus-5-5", "anthropic"),
            ("gpt-5", "openai"),
            ("o3-mini", "openai"),
            ("gemini-3-flash", "google"),
            ("deepseek-flash", "deepseek"),
        ],
    )
    def test_known_prefixes(self, model: str, provider: str) -> None:
        assert resolve_provider(model) == provider

    def test_unknown_model_asks_for_provider(self) -> None:
        with pytest.raises(ValueError, match="provider="):
            resolve_provider("llama-3-70b")


class TestHostedGenerator:
    def test_anthropic(self) -> None:
        gen = hosted_generator("claude-haiku-5-5", api_key="sk-ant")
        assert isinstance(gen, AnthropicGenerator)
        assert gen.model == "claude-haiku-5-5"
        assert gen.base_url == DEFAULT_ANTHROPIC_URL

    def test_openai(self) -> None:
        gen = hosted_generator("gpt-5", api_key="sk")
        assert isinstance(gen, OpenAICompatGenerator)
        assert gen.provider == "openai"
        assert gen.base_url == DEFAULT_OPENAI_URL
        assert gen.max_tokens_field == OPENAI_MAX_TOKENS_FIELD
        assert gen.structured_output == "json_schema"
        assert gen.effort_field == "reasoning_effort"

    def test_google(self) -> None:
        gen = hosted_generator("gemini-3-flash", api_key="AIza")
        assert isinstance(gen, OpenAICompatGenerator)
        assert gen.provider == "google"
        assert gen.base_url == DEFAULT_GOOGLE_OPENAI_URL
        assert gen.effort_values == dict(GOOGLE_EFFORT_VALUES)

    def test_deepseek(self) -> None:
        gen = hosted_generator("deepseek-flash", api_key="sk")
        assert isinstance(gen, OpenAICompatGenerator)
        assert gen.provider == "deepseek"
        assert gen.base_url == DEFAULT_DEEPSEEK_URL
        assert gen.structured_output == "json_object"
        assert gen.effort_field is None

    def test_explicit_provider_and_base_url_win(self) -> None:
        gen = hosted_generator(
            "my-finetune",
            api_key="sk",
            provider="openai",
            base_url="https://proxy.test/v1",
        )
        assert isinstance(gen, OpenAICompatGenerator)
        assert gen.base_url == "https://proxy.test/v1"

    def test_unresolvable_model_raises(self) -> None:
        with pytest.raises(ValueError, match="provider="):
            hosted_generator("my-finetune", api_key="sk")

    def test_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            hosted_generator("claude-haiku-5-5", api_key="")

    async def test_google_request_goes_to_the_compat_endpoint(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            payload: dict[str, Any] = {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"value": "ok"}'},
                    },
                ],
            }
            return httpx.Response(200, json=payload)

        gen = hosted_generator(
            "gemini-3-flash",
            api_key="AIza",
            transport=httpx.MockTransport(handler),
        )
        result = await gen.complete_structured(
            [{"role": "user", "content": "hi"}],
            Answer,
            effort="xhigh",
        )
        assert str(seen[0].url) == f"{DEFAULT_GOOGLE_OPENAI_URL}/chat/completions"
        assert json.loads(seen[0].content)["reasoning_effort"] == "high"
        assert result.output == Answer(value="ok")
