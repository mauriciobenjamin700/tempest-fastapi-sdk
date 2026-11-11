"""`HostedGenAISettings` — keys and models of the hosted providers, splatted."""

from __future__ import annotations

from typing import get_args

import httpx
import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from tempest_fastapi_sdk import (
    BaseAppSettings,
    EnvironmentSettings,
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
    HOSTED_PROVIDERS,
    AnthropicGenerator,
    OpenAICompatGenerator,
    OpenAICompatSpeech,
    hosted_generator,
)


class Composed(ServerSettings, GenAISettings, HostedGenAISettings, BaseAppSettings):
    """The mixin composes with the local-weights one and the server one."""

    model_config = SettingsConfigDict(env_file=None)


class ProductionHosted(EnvironmentSettings, HostedGenAISettings):
    """The mixin under the production guard."""

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
        assert gen.effort_field is None

    def test_empty_key_fails_at_construction(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            AnthropicGenerator(**HostedGenAISettings().anthropic_kwargs())


class TestGoogle:
    def test_base_url_matches_the_generator_constant(self) -> None:
        assert HostedGenAISettings().GOOGLE_BASE_URL == DEFAULT_GOOGLE_OPENAI_URL

    def test_needs_a_model(self) -> None:
        with pytest.raises(ValueError, match="GOOGLE_MODEL"):
            HostedGenAISettings(GOOGLE_API_KEY="AIza").gemini_kwargs()

    def test_splat_builds_the_backend(self) -> None:
        settings = HostedGenAISettings(
            GOOGLE_API_KEY="AIza",
            GOOGLE_MODEL="gemini-3-flash",
        )
        gen = OpenAICompatGenerator(**settings.gemini_kwargs())
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


class TestGenericProvider:
    """``GENAI_PROVIDER`` / ``GENAI_API_KEY`` / ``GENAI_BASE_URL``."""

    def test_provider_literal_matches_the_generator_constant(self) -> None:
        field = HostedGenAISettings.model_fields["GENAI_PROVIDER"]
        literal = get_args(field.annotation)[0]
        assert get_args(literal) == HOSTED_PROVIDERS

    @pytest.mark.parametrize(
        ("provider", "model", "default_url"),
        [
            ("anthropic", "claude-haiku-5-5", DEFAULT_ANTHROPIC_URL),
            ("openai", "gpt-5", DEFAULT_OPENAI_URL),
            ("google", "gemini-3-flash", DEFAULT_GOOGLE_OPENAI_URL),
            ("deepseek", "deepseek-flash", DEFAULT_DEEPSEEK_URL),
        ],
    )
    def test_generic_key_goes_to_the_chosen_provider(
        self,
        provider: str,
        model: str,
        default_url: str,
    ) -> None:
        settings = HostedGenAISettings(
            GENAI_PROVIDER=provider,
            GENAI_API_KEY="sk-generic",
            ANTHROPIC_API_KEY="sk-ant",
            OPENAI_API_KEY="sk-openai",
        )
        kwargs = settings.hosted_kwargs(model)
        assert kwargs == {
            "model": model,
            "api_key": "sk-generic",
            "base_url": default_url,
            "provider": provider,
        }
        gen = hosted_generator(**kwargs)
        assert gen.model == model

    def test_falls_back_to_the_provider_key_and_url(self) -> None:
        settings = HostedGenAISettings(
            GENAI_PROVIDER="openai",
            OPENAI_API_KEY="sk-openai",
            OPENAI_BASE_URL="https://proxy.example/v1",
        )
        kwargs = settings.hosted_kwargs("gpt-5")
        assert kwargs["api_key"] == "sk-openai"
        assert kwargs["base_url"] == "https://proxy.example/v1"

    def test_generic_base_url_wins(self) -> None:
        settings = HostedGenAISettings(
            GENAI_PROVIDER="openai",
            GENAI_API_KEY="sk",
            GENAI_BASE_URL="https://gateway.example/v1",
        )
        assert (
            settings.hosted_kwargs("gpt-5")["base_url"] == "https://gateway.example/v1"
        )

    def test_model_of_another_provider_is_refused(self) -> None:
        settings = HostedGenAISettings(GENAI_PROVIDER="openai", GENAI_API_KEY="sk")
        with pytest.raises(ValueError, match="belongs to 'anthropic'"):
            settings.hosted_kwargs("claude-haiku-5-5")

    def test_disagreeing_provider_argument_is_refused(self) -> None:
        settings = HostedGenAISettings(GENAI_PROVIDER="openai", GENAI_API_KEY="sk")
        with pytest.raises(ValueError, match="disagrees with GENAI_PROVIDER"):
            settings.hosted_kwargs("gpt-5", provider="google")

    def test_unprefixed_model_goes_to_the_chosen_provider(self) -> None:
        settings = HostedGenAISettings(GENAI_PROVIDER="openai", GENAI_API_KEY="sk")
        kwargs = settings.hosted_kwargs("my-finetune")
        assert kwargs["provider"] == "openai"
        assert kwargs["api_key"] == "sk"

    def test_unset_keeps_the_prefix_resolution(self) -> None:
        settings = HostedGenAISettings(
            GENAI_API_KEY="sk-ignored",
            GENAI_BASE_URL="https://ignored.example",
            ANTHROPIC_API_KEY="sk-ant",
        )
        assert settings.GENAI_PROVIDER is None
        kwargs = settings.hosted_kwargs("claude-haiku-5-5")
        assert kwargs == {
            "model": "claude-haiku-5-5",
            "api_key": "sk-ant",
            "base_url": DEFAULT_ANTHROPIC_URL,
            "provider": "anthropic",
        }

    def test_blank_provider_in_env_is_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GENAI_PROVIDER", "  ")
        assert HostedGenAISettings().GENAI_PROVIDER is None

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENAI_PROVIDER", "deepseek")
        monkeypatch.setenv("GENAI_API_KEY", "sk-env")
        settings = HostedGenAISettings()
        assert settings.hosted_kwargs("deepseek-flash")["api_key"] == "sk-env"

    def test_unknown_provider_value_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            HostedGenAISettings(GENAI_PROVIDER="mistral")


class TestGenericProviderProductionGuard:
    def test_chosen_provider_without_any_key_is_a_violation(self) -> None:
        violations = HostedGenAISettings(
            GENAI_PROVIDER="openai"
        ).production_violations()
        assert any(
            v.startswith("GENAI_API_KEY") and "OPENAI_API_KEY" in v for v in violations
        )

    @pytest.mark.parametrize(
        "keys",
        [{"GENAI_API_KEY": "sk"}, {"OPENAI_API_KEY": "sk"}],
    )
    def test_any_effective_key_satisfies_the_guard(self, keys: dict[str, str]) -> None:
        settings = HostedGenAISettings(GENAI_PROVIDER="openai", **keys)
        assert not any(
            v.startswith("GENAI_API_KEY") for v in settings.production_violations()
        )

    def test_service_without_provider_is_not_affected(self) -> None:
        violations = HostedGenAISettings().production_violations()
        assert not any(v.startswith("GENAI_API_KEY") for v in violations)

    def test_environment_settings_refuse_the_boot(self) -> None:
        with pytest.raises(ValidationError, match="GENAI_API_KEY"):
            ProductionHosted(ENV="production", GENAI_PROVIDER="anthropic")

    def test_environment_settings_boot_with_a_key(self) -> None:
        settings = ProductionHosted(
            ENV="production",
            GENAI_PROVIDER="anthropic",
            GENAI_API_KEY="sk-ant",
        )
        assert settings.genai_effective_api_key() == "sk-ant"


class TestOpenAISpeechKwargs:
    def test_needs_a_stt_model(self) -> None:
        with pytest.raises(ValueError, match="OPENAI_STT_MODEL"):
            HostedGenAISettings(
                OPENAI_API_KEY="sk", OPENAI_MODEL="m"
            ).openai_speech_kwargs()

    def test_shares_key_and_root_with_openai_kwargs(self) -> None:
        settings = HostedGenAISettings(
            OPENAI_API_KEY="tk",
            OPENAI_BASE_URL="https://ai.buscar.app.br/api/openai/v1",
            OPENAI_MODEL="chat",
            OPENAI_STT_MODEL="base",
        )
        kwargs = settings.openai_speech_kwargs()
        assert kwargs == {
            "model": "base",
            "api_key": "tk",
            "base_url": "https://ai.buscar.app.br/api/openai/v1",
            "provider": "openai",
        }
        assert kwargs["api_key"] == settings.openai_kwargs()["api_key"]
        assert kwargs["base_url"] == settings.openai_kwargs()["base_url"]

    def test_unset_stt_model_is_not_a_production_violation(self) -> None:
        settings = HostedGenAISettings(OPENAI_API_KEY="sk")
        assert not any(
            "OPENAI_STT_MODEL" in v for v in settings.production_violations()
        )

    async def test_splat_builds_a_working_backend(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"text": "oi", "duration": 1.0})

        settings = HostedGenAISettings(
            OPENAI_API_KEY="tk",
            OPENAI_BASE_URL="https://ai.buscar.app.br/api/openai/v1",
            OPENAI_STT_MODEL="base",
        )
        speech = OpenAICompatSpeech(
            **settings.openai_speech_kwargs(),
            transport=httpx.MockTransport(handler),
        )
        assert speech.provider == "openai"
        assert (await speech.transcribe(b"RIFF")).text == "oi"
        assert str(seen[0].url) == (
            "https://ai.buscar.app.br/api/openai/v1/audio/transcriptions"
        )
        assert seen[0].headers["Authorization"] == "Bearer tk"
        await speech.aclose()
