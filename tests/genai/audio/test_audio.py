"""Tests for self-hosted audio (STT/TTS) — logic + state (no engines in CI)."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.genai.audio import (
    SpeechToText,
    TextToSpeech,
    resolve_audio_device,
)
from tempest_fastapi_sdk.genai.audio.stt import resolve_compute_type


class TestResolvers:
    def test_fixed_device(self) -> None:
        assert resolve_audio_device("cpu") == "cpu"

    def test_auto_without_torch_is_cpu(self) -> None:
        # torch absent in CI -> cpu
        assert resolve_audio_device("auto") == "cpu"

    def test_compute_type_auto(self) -> None:
        assert resolve_compute_type("auto", "cuda") == "float16"
        assert resolve_compute_type("auto", "cpu") == "int8"

    def test_compute_type_explicit(self) -> None:
        assert resolve_compute_type("int8_float16", "cuda") == "int8_float16"


class TestSpeechToText:
    def test_init_resolves(self) -> None:
        stt = SpeechToText("base", device="cpu")
        assert stt.device == "cpu"
        assert stt.compute_type == "int8"
        assert stt.is_loaded is False

    def test_bad_concurrency(self) -> None:
        with pytest.raises(ValueError):
            SpeechToText(max_concurrent=0)

    def test_unload_noop(self) -> None:
        stt = SpeechToText(device="cpu")
        stt.unload()
        assert stt.is_loaded is False

    async def test_transcribe_without_extra_raises(self) -> None:
        stt = SpeechToText(device="cpu")
        with pytest.raises(ImportError, match=r"\[genai-audio\]"):
            await stt.transcribe("x.wav")


class TestTextToSpeech:
    def test_init(self) -> None:
        tts = TextToSpeech(device="cpu")
        assert tts.device == "cpu"
        assert tts.is_loaded is False

    def test_bad_concurrency(self) -> None:
        with pytest.raises(ValueError):
            TextToSpeech(max_concurrent=0)

    async def test_synthesize_without_extra_raises(self) -> None:
        tts = TextToSpeech(device="cpu")
        with pytest.raises(ImportError, match=r"\[genai-audio\]"):
            await tts.synthesize("olá")
