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
    DEFAULT_GOOGLE_OPENAI_URL,
    DEFAULT_OPENAI_URL,
    GOOGLE_EFFORT_VALUES,
    AnthropicGenerator,
    OpenAICompatGenerator,
    hosted_generator,
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


class TestGoogle:
    def test_base_url_matches_the_generator_constant(self) -> None:
        assert HostedGenAISettings().GOOGLE_BASE_URL == DEFAULT_GOOGLE_OPENAI_URL

    def test_needs_a_model(self) -> None:
        with pytest.raises(ValueError, match="GOOGLE_MODEL"):
            HostedGenAISettings(GOOGLE_API_KEY="AIza").google_kwargs()

    def test_splat_builds_the_backend(self) -> None:
        settings = HostedGenAISettings(
            GOOGLE_API_KEY="AIza",
            GOOGLE_MODEL="gemini-3-flash",
        )
        gen = OpenAICompatGenerator(**settings.google_kwargs())
        assert gen.provider == "google"
        assert gen.base_url == DEFAULT_GOOGLE_OPENAI_URL
        assert gen.effort_values == dict(GOOGLE_EFFORT_VALUES)


class TestHostedKwargs:
    @pytest.mark.parametrize(
        ("model", "provider", "key"),
        [
            ("claude-haiku-5-5", "anthropic", "sk-ant"),
            ("gpt-5", "openai", "sk-openai"),
            ("gemini-3-flash", "google", "AIza"),
            ("deepseek-flash", "deepseek", "sk-deepseek"),
        ],
    )
    def test_picks_the_resolved_provider_key(
        self,
        model: str,
        provider: str,
        key: str,
    ) -> None:
        settings = HostedGenAISettings(
            ANTHROPIC_API_KEY="sk-ant",
            OPENAI_API_KEY="sk-openai",
            GOOGLE_API_KEY="AIza",
            DEEPSEEK_API_KEY="sk-deepseek",
        )
        kwargs = settings.hosted_kwargs(model)
        assert kwargs["model"] == model
        assert kwargs["provider"] == provider
        assert kwargs["api_key"] == key
        gen = hosted_generator(**kwargs)
        assert gen.model == model

    def test_explicit_provider_wins(self) -> None:
        settings = HostedGenAISettings(OPENAI_API_KEY="sk-openai")
        kwargs = settings.hosted_kwargs("my-finetune", provider="openai")
        assert kwargs["api_key"] == "sk-openai"
        assert kwargs["base_url"] == DEFAULT_OPENAI_URL

    def test_empty_key_is_returned_and_the_constructor_raises(self) -> None:
        kwargs = HostedGenAISettings().hosted_kwargs("claude-haiku-5-5")
        assert kwargs["api_key"] == ""
        with pytest.raises(ValueError, match="api_key"):
            hosted_generator(**kwargs)

    def test_unknown_provider_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown hosted provider"):
            HostedGenAISettings().hosted_kwargs("x", provider="mistral")
