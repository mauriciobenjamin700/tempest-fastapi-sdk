"""Tests for GenerationConfig and its wiring into TextGenerator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk.genai import GenerationConfig, TextGenerator


class TestGenerationConfig:
    def test_defaults_are_unset(self) -> None:
        config = GenerationConfig()
        assert config.max_new_tokens is None
        assert config.temperature is None
        assert config.stop == []

    def test_to_generate_kwargs_only_set_fields(self) -> None:
        config = GenerationConfig(max_new_tokens=512, temperature=0.2)
        assert config.to_generate_kwargs() == {
            "max_new_tokens": 512,
            "temperature": 0.2,
        }

    def test_to_generate_kwargs_drops_seed_and_stop(self) -> None:
        config = GenerationConfig(max_new_tokens=64, seed=42, stop=["\n\n"])
        kwargs = config.to_generate_kwargs()
        assert "seed" not in kwargs
        assert "stop" not in kwargs
        assert kwargs == {"max_new_tokens": 64}

    def test_empty_config_yields_empty_kwargs(self) -> None:
        assert GenerationConfig().to_generate_kwargs() == {}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_new_tokens", 0),
            ("temperature", 2.5),
            ("top_p", 1.5),
            ("top_k", -1),
            ("repetition_penalty", 0.0),
        ],
    )
    def test_rejects_out_of_range(self, field: str, value: float) -> None:
        with pytest.raises(ValidationError):
            GenerationConfig(**{field: value})


class TestGenKwargsLayering:
    def _generator(self) -> TextGenerator:
        return TextGenerator("dummy-model", device="cpu")

    def test_defaults_when_no_config(self) -> None:
        gen = self._generator()
        assert gen._gen_kwargs({}) == {
            "max_new_tokens": 256,
            "temperature": 0.7,
            "top_p": 0.9,
            "do_sample": True,
        }

    def test_config_layers_over_defaults(self) -> None:
        gen = self._generator()
        config = GenerationConfig(temperature=0.1, max_new_tokens=1024)
        merged = gen._gen_kwargs({}, config)
        assert merged["temperature"] == 0.1
        assert merged["max_new_tokens"] == 1024
        assert merged["top_p"] == 0.9

    def test_overrides_win_over_config(self) -> None:
        gen = self._generator()
        config = GenerationConfig(temperature=0.1)
        merged = gen._gen_kwargs({"temperature": 0.9}, config)
        assert merged["temperature"] == 0.9
