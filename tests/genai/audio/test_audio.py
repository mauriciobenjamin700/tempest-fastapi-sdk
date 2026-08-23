"""Tests for self-hosted audio (STT/TTS) — logic + state (no engines in CI)."""

from __future__ import annotations

import builtins
import importlib.util
from typing import Any

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

    def test_auto_without_torch_is_cpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No torch means no way to see a GPU, so ``auto`` lands on cpu.

        The import is blocked rather than left to the host: asserting the bare
        outcome would pass only on a CPU-only machine and fail on every
        developer box with a working CUDA install.
        """
        real_import = builtins.__import__

        def no_torch(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "torch" or name.startswith("torch."):
                raise ImportError("no module named torch")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_torch)
        assert resolve_audio_device("auto") == "cpu"

    def test_auto_without_torch_says_so(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A missing torch and a missing GPU are not the same answer.

        Folding them together transcribed on the CPU of a GPU machine with no
        sign at all, and the slowdown read as faster-whisper being slow
        (issue #191).
        """
        real_import = builtins.__import__

        def no_torch(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "torch" or name.startswith("torch."):
                raise ImportError("no module named torch")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_torch)
        with caplog.at_level("WARNING"):
            assert resolve_audio_device("auto") == "cpu"

        assert "torch is not installed" in caplog.text

    def test_auto_with_a_cuda_gpu_is_cuda(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A visible CUDA device wins ``auto``."""
        torch = pytest.importorskip("torch")
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert resolve_audio_device("auto") == "cuda"

    def test_auto_with_torch_but_no_gpu_is_cpu(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Torch installed but no device visible still resolves to cpu."""
        torch = pytest.importorskip("torch")
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
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
            "condition_on_previous_text": True,
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

    async def test_condition_on_previous_text_is_forwarded_when_off(self) -> None:
        """Turning it off reaches faster-whisper, it is not just stored."""
        stt = SpeechToText(device="cpu", condition_on_previous_text=False)
        stt._model = _FakeWhisperModel()
        await stt.transcribe("clip.wav")
        assert stt._model.calls[0]["condition_on_previous_text"] is False

    async def test_batch_size_reaches_the_batched_pipeline(self) -> None:
        """With ``batch_size`` set, the pipeline decodes — not the model.

        The batched path is a different object with a different call
        signature; asserting on the model would pass while the batch size
        went nowhere.
        """
        stt = SpeechToText(device="cpu", batch_size=4)
        pipeline = _FakeWhisperModel()
        stt._model = _FakeWhisperModel()
        stt._pipeline = pipeline

        await stt.transcribe("clip.wav")

        assert pipeline.calls[0]["batch_size"] == 4
        assert stt._model.calls == []

    def test_batch_size_without_vad_is_refused(self) -> None:
        """Batching consumes VAD spans, so the pair is checked up front.

        Raising at construction rather than on the first transcription is
        the point: the second failure arrives inside a worker, minutes
        later, with the audio already uploaded.
        """
        with pytest.raises(ValueError, match="vad_filter"):
            SpeechToText(device="cpu", batch_size=8, vad_filter=False)

    def test_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            SpeechToText(device="cpu", batch_size=0)

    async def test_on_progress_is_called_per_segment(self) -> None:
        """The callback sees each segment end against the total duration."""
        stt = SpeechToText(device="cpu")
        stt._model = _FakeWhisperModel()
        seen: list[tuple[float, float]] = []

        await stt.transcribe("clip.wav", on_progress=lambda a, b: seen.append((a, b)))

        assert seen == [(1.0, 3.5)]

    async def test_transcribe_without_on_progress_still_works(self) -> None:
        """The callback is optional; omitting it changes nothing."""
        stt = SpeechToText(device="cpu")
        stt._model = _FakeWhisperModel()
        result = await stt.transcribe("clip.wav")
        assert result.text == "olá"

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
