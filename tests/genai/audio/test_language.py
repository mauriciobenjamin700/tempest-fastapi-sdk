"""Tests for audio language presets (PT-BR / EN-US)."""

from __future__ import annotations

from tempest_fastapi_sdk.genai.audio import (
    Language,
    SpeechToText,
    TextToSpeech,
    preset_for,
)
from tempest_fastapi_sdk.genai.audio.language import tts_language, whisper_language


class TestPresets:
    def test_ptbr_preset(self) -> None:
        p = preset_for(Language.PT_BR)
        assert p.whisper_language == "pt"
        assert "pt/" in p.tts_model

    def test_enus_preset(self) -> None:
        p = preset_for(Language.EN_US)
        assert p.whisper_language == "en"
        assert "en/" in p.tts_model


class TestCoercion:
    def test_whisper_language(self) -> None:
        assert whisper_language(Language.PT_BR) == "pt"
        assert whisper_language(Language.EN_US) == "en"
        assert whisper_language("pt") == "pt"  # raw passthrough
        assert whisper_language(None) is None

    def test_tts_language(self) -> None:
        assert tts_language(Language.PT_BR) is None  # mono model, no lang arg
        assert tts_language("pt") == "pt"
        assert tts_language(None) is None


class TestForLanguage:
    def test_tts_for_language_picks_model(self) -> None:
        tts = TextToSpeech.for_language(Language.PT_BR, device="cpu")
        assert tts.model_name == preset_for(Language.PT_BR).tts_model
        assert tts.device == "cpu"

    def test_stt_accepts_language_enum(self) -> None:
        # constructing + resolution wiring; no engine needed
        stt = SpeechToText(device="cpu")
        assert stt.device == "cpu"
        # whisper_language resolves the enum used by transcribe()
        assert whisper_language(Language.EN_US) == "en"
