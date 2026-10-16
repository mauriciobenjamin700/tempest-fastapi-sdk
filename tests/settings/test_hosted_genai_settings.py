"""`HostedGenAISettings` — keys and models of the hosted providers, splatted."""

from __future__ import annotations

import httpx
import pytest
from pydantic_settings import SettingsConfigDict

from tempest_fastapi_sdk import (
    BaseAppSettings,
    GenAISettings,
    HostedGenAISettings,
    ServerSettings,
)
from tempest_fastapi_sdk.genai import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_ANTHROPIC_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_URL,
    DEFAULT_OPENAI_URL,
    AnthropicGenerator,
    OpenAICompatGenerator,
)


class Composed(ServerSettings, GenAISettings, HostedGenAISettings, BaseAppSettings):
    """The mixin composes with the local-weights one and the server one."""

    model_config = SettingsConfigDict(env_file=None)


class TestDefaults:
    def test_defaults_match_the_backend_constants(self) -> None:
        settings = HostedGenAISettings()
        assert settings.ANTHROPIC_MODEL == DEFAULT_ANTHROPIC_MODEL
        assert settings.ANTHROPIC_BASE_URL == DEFAULT_ANTHROPIC_URL
        assert settings.OPENAI_BASE_URL == DEFAULT_OPENAI_URL
        assert settings.DEEPSEEK_BASE_URL == DEFAULT_DEEPSEEK_URL
        assert settings.DEEPSEEK_MODEL == DEFAULT_DEEPSEEK_MODEL
        assert settings.ANTHROPIC_API_KEY == ""
        assert settings.OPENAI_MODEL == ""

    def test_composes(self) -> None:
        settings = Composed(ANTHROPIC_API_KEY="k")
        assert settings.anthropic_kwargs()["api_key"] == "k"
        assert settings.GENAI_OFFLINE is False

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        settings = HostedGenAISettings()
        assert settings.deepseek_kwargs()["api_key"] == "sk-env"
        assert settings.deepseek_kwargs()["model"] == "deepseek-v4-pro"


class TestKwargs:
    def test_anthropic_splat_builds_the_backend(self) -> None:
        settings = HostedGenAISettings(ANTHROPIC_API_KEY="sk-ant")
        gen = AnthropicGenerator(**settings.anthropic_kwargs())
        assert gen.model == DEFAULT_ANTHROPIC_MODEL
        assert gen.url == f"{DEFAULT_ANTHROPIC_URL}/v1/messages"

    def test_openai_needs_a_model(self) -> None:
        with pytest.raises(ValueError, match="OPENAI_MODEL"):
            HostedGenAISettings(OPENAI_API_KEY="sk").openai_kwargs()

    async def test_openai_splat_sends_max_completion_tokens(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        settings = HostedGenAISettings(OPENAI_API_KEY="sk", OPENAI_MODEL="m")
        gen = OpenAICompatGenerator(
            **settings.openai_kwargs(),
            transport=httpx.MockTransport(handler),
        )
        assert gen.provider == "openai"
        await gen.generate("hi", max_new_tokens=8)
        assert b'"max_completion_tokens":8' in seen[0].content.replace(b" ", b"")

    def test_deepseek_splat_uses_json_object(self) -> None:
        settings = HostedGenAISettings(DEEPSEEK_API_KEY="sk")
        gen = OpenAICompatGenerator(**settings.deepseek_kwargs())
        assert gen.structured_output == "json_object"
        assert gen.base_url == DEFAULT_DEEPSEEK_URL
        assert gen.provider == "deepseek"

    def test_empty_key_fails_at_construction(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            AnthropicGenerator(**HostedGenAISettings().anthropic_kwargs())
