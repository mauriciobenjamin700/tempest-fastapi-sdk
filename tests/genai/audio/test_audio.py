"""Tests for self-hosted audio (STT/TTS) — logic + state (no engines in CI)."""

from __future__ import annotations

import importlib.util

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


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    language = "pt"
    language_probability = 0.97
    duration = 3.5


class _FakeWhisperModel:
    """Records transcribe kwargs and returns canned segments + info."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transcribe(self, source: object, **kwargs: object) -> tuple[object, object]:
        self.calls.append(kwargs)
        return iter([_FakeSegment(0.0, 1.0, "olá ")]), _FakeInfo()


class TestSpeechToText:
    def test_init_resolves(self) -> None:
        stt = SpeechToText("base", device="cpu")
        assert stt.device == "cpu"
        assert stt.compute_type == "int8"
        assert stt.is_loaded is False

    def test_init_stores_beam_and_vad(self) -> None:
        stt = SpeechToText(device="cpu", beam_size=8, vad_filter=False)
        assert stt.beam_size == 8
        assert stt.vad_filter is False

    async def test_transcribe_forwards_knobs_and_language_probability(self) -> None:
        stt = SpeechToText(device="cpu", beam_size=8, vad_filter=False)
        stt._model = _FakeWhisperModel()  # pre-loaded -> load() short-circuits
        result = await stt.transcribe("clip.wav", language="pt")
        assert stt._model.calls[0] == {
            "language": "pt",
            "beam_size": 8,
            "vad_filter": False,
        }
        assert result.text == "olá"
        assert result.language == "pt"
        assert result.language_probability == 0.97
        assert result.duration == 3.5
        assert len(result.segments) == 1

    async def test_transcribe_per_call_overrides_win(self) -> None:
        stt = SpeechToText(device="cpu", beam_size=5, vad_filter=True)
        stt._model = _FakeWhisperModel()
        await stt.transcribe("clip.wav", beam_size=1, vad_filter=False)
        assert stt._model.calls[0]["beam_size"] == 1
        assert stt._model.calls[0]["vad_filter"] is False

    def test_bad_concurrency(self) -> None:
        with pytest.raises(ValueError):
            SpeechToText(max_concurrent=0)

    def test_unload_noop(self) -> None:
        stt = SpeechToText(device="cpu")
        stt.unload()
        assert stt.is_loaded is False

    @pytest.mark.skipif(
        importlib.util.find_spec("faster_whisper") is not None,
        reason="faster-whisper installed; the missing-extra path can't be exercised",
    )
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

    @pytest.mark.skipif(
        importlib.util.find_spec("TTS") is not None,
        reason="coqui-tts installed; the missing-extra path can't be exercised",
    )
    async def test_synthesize_without_extra_raises(self) -> None:
        tts = TextToSpeech(device="cpu")
        with pytest.raises(ImportError, match=r"\[genai-audio\]"):
            await tts.synthesize("olá")
