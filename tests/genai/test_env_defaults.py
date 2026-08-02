"""Tests for the ``GENAI_*`` defaults behind every weight loader."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk import GenAISettings
from tempest_fastapi_sdk.genai.hub import ModelRef


class TestEnvironmentDefaults:
    def test_nothing_set_sends_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no variables, the call is byte-identical to before."""
        for name in ("GENAI_CACHE_DIR", "GENAI_OFFLINE", "GENAI_HF_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        assert ModelRef(model_id="org/name").loader_kwargs() == {}

    def test_the_environment_supplies_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A service configures the cache and offline switch once."""
        monkeypatch.setenv("GENAI_CACHE_DIR", "/models")
        monkeypatch.setenv("GENAI_OFFLINE", "true")
        monkeypatch.setenv("GENAI_HF_TOKEN", "hf_env")
        assert ModelRef(model_id="org/name").loader_kwargs() == {
            "cache_dir": "/models",
            "token": "hf_env",
            "local_files_only": True,
        }

    def test_an_argument_beats_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing the parameter is the override, in both directions."""
        monkeypatch.setenv("GENAI_CACHE_DIR", "/models")
        monkeypatch.setenv("GENAI_OFFLINE", "true")
        ref = ModelRef(
            model_id="org/name",
            cache_dir="/elsewhere",
            local_files_only=False,
        )
        assert ref.loader_kwargs() == {"cache_dir": "/elsewhere"}

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_spellings_enable_offline(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """The usual ways of writing "yes" in an env file all work."""
        monkeypatch.setenv("GENAI_OFFLINE", raw)
        assert ModelRef(model_id="org/name").local_files_only is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
    def test_anything_else_stays_online(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Offline is opt-in: an unrecognized value does not enable it."""
        monkeypatch.setenv("GENAI_OFFLINE", raw)
        assert ModelRef(model_id="org/name").local_files_only is False

    def test_the_settings_mixin_names_the_same_variables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The typed mixin and the loaders must not drift apart."""
        monkeypatch.setenv("GENAI_CACHE_DIR", "/models")
        monkeypatch.setenv("GENAI_OFFLINE", "true")
        monkeypatch.setenv("GENAI_HF_TOKEN", "hf_env")
        settings = GenAISettings()
        ref = ModelRef(model_id="org/name")
        assert ref.cache_dir == settings.GENAI_CACHE_DIR
        assert ref.local_files_only == settings.GENAI_OFFLINE
        assert ref.token == settings.GENAI_HF_TOKEN
