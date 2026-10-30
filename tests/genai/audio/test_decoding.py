"""Tests for the bounded audio decoder every audio engine shares.

The upload routes cap the bytes a client sends, and that cap was the only
bound: a WAV header declares its own sample rate and a codec packs a long
stretch of audio into few bytes, so a few kilobytes could ask for hours of
decoded samples. These tests pin the two checks that keep decoded work
proportional to the upload, through the decoder itself, through each
engine, and through both routers that accept audio. No model is loaded:
the engines carry fakes that record what reached them.
"""

from __future__ import annotations

import io
import struct
import wave
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.genai import make_genai_router
from tempest_fastapi_sdk.genai.audio import (
    DEFAULT_MAX_AUDIO_SECONDS,
    MIN_SOURCE_RATE,
    ConversationTranscriber,
    SpeakerDiarizer,
    SpeechToText,
    VoiceEmbedder,
    make_voice_router,
)
from tempest_fastapi_sdk.genai.audio import decoding as decoding_module
from tempest_fastapi_sdk.genai.audio.diarization import load_audio

RATE: int = 16_000


def _pcm_wav(
    rate: int,
    frames: int,
    *,
    width: int = 2,
    channels: int = 1,
) -> bytes:
    """Build a PCM WAV byte for byte, so the header can declare any rate.

    The standard library's writer refuses a zero rate, and the forged
    header is the thing under test.

    Args:
        rate (int): The declared sample rate, written as-is.
        frames (int): How many frames of silence follow.
        width (int): Bytes per sample.
        channels (int): Channel count.

    Returns:
        bytes: The RIFF/WAVE file.
    """
    silence = b"\x80" if width == 1 else b"\x00" * width
    data = silence * frames * channels
    fmt = struct.pack(
        "<HHIIHH",
        1,
        channels,
        rate,
        rate * width * channels,
        width * channels,
        width * 8,
    )
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _tone_wav(rate: int, seconds: float, *, channels: int = 1) -> bytes:
    """Build a real 16-bit WAV holding a tone.

    Args:
        rate (int): Sample rate.
        seconds (float): Duration.
        channels (int): Channel count.

    Returns:
        bytes: The WAV file.
    """
    count = int(rate * seconds) * channels
    samples = (np.sin(np.arange(count) / 10.0) * 8_000).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


def _silent_flac(seconds: int) -> bytes:
    """Encode silence as FLAC, which compresses it to almost nothing.

    Args:
        seconds (int): Duration of the silence.

    Returns:
        bytes: The FLAC file.
    """
    av = pytest.importorskip("av")
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="flac") as container:
        stream = container.add_stream("flac", rate=RATE, layout="mono")
        for _ in range(seconds):
            frame = av.AudioFrame.from_ndarray(
                np.zeros((1, RATE), dtype=np.int16),
                format="s16",
                layout="mono",
            )
            frame.sample_rate = RATE
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


ONE_HZ_WAV: bytes = _pcm_wav(1, 2_000)
"""4044 bytes declaring 1 Hz: 2000 frames that resample to 2000 seconds."""

ZERO_HZ_WAV: bytes = _pcm_wav(0, 10)
"""A header declaring 0 Hz, which used to divide by zero."""


@pytest.fixture
def no_resample(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything reaches the resampling allocation.

    ``np.linspace`` is where the old decoder allocated the amplified
    output; a refused upload must never get that far.

    Args:
        monkeypatch (pytest.MonkeyPatch): Installs the trap.
    """

    def _trap(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resampling ran for audio that should be refused")

    monkeypatch.setattr(np, "linspace", _trap)


class TestLoadAudio:
    def test_forged_one_hz_header_is_refused_before_resampling(
        self,
        no_resample: None,
    ) -> None:
        assert len(ONE_HZ_WAV) == 4_044
        with pytest.raises(ValidationException) as caught:
            load_audio(ONE_HZ_WAV, target_rate=RATE)
        assert caught.value.status_code == 422
        assert caught.value.details["min_rate"] == MIN_SOURCE_RATE

    def test_zero_hz_header_is_refused_not_a_crash(self, no_resample: None) -> None:
        with pytest.raises(ValidationException):
            load_audio(ZERO_HZ_WAV, target_rate=RATE)

    def test_forged_rate_on_the_pyav_path_is_refused(self) -> None:
        pytest.importorskip("av")
        eight_bit = _pcm_wav(1, 1_024, width=1)
        with pytest.raises(ValidationException) as caught:
            load_audio(eight_bit, target_rate=RATE)
        assert caught.value.details["min_rate"] == MIN_SOURCE_RATE

    def test_wav_just_over_the_bound_is_refused(self) -> None:
        with pytest.raises(ValidationException) as caught:
            load_audio(_tone_wav(RATE, 2.0), target_rate=RATE, max_seconds=1.0)
        assert caught.value.details == {"max_seconds": 1.0}

    def test_wav_within_the_bound_decodes_exactly(self) -> None:
        samples = load_audio(_tone_wav(RATE, 1.0), target_rate=RATE, max_seconds=1.0)
        assert samples.dtype == np.float32
        assert samples.shape == (RATE,)

    def test_header_frame_count_is_not_trusted(self) -> None:
        """A header claiming more frames than the bound still reads only the bound.

        The refusal comes from the frames actually read, so a file whose
        header lies in either direction is judged by its data.
        """
        lying = bytearray(_tone_wav(RATE, 2.0))
        data_at = lying.index(b"data") + 4
        lying[data_at : data_at + 4] = struct.pack("<I", 0x7FFF_FFF0)
        with pytest.raises(ValidationException):
            load_audio(bytes(lying), target_rate=RATE, max_seconds=1.0)

    def test_compressed_silence_is_cut_off_while_decoding(self) -> None:
        """Ten minutes of FLAC silence is ~100 KB; the decoder stops at the bound."""
        flac = _silent_flac(600)
        assert len(flac) < 200_000
        with pytest.raises(ValidationException) as caught:
            load_audio(flac, target_rate=RATE, max_seconds=60.0)
        assert caught.value.details == {"max_seconds": 60.0}

    def test_none_lifts_the_duration_bound(self) -> None:
        flac = _silent_flac(90)
        samples = load_audio(flac, target_rate=RATE, max_seconds=None)
        assert samples.shape[0] >= 90 * RATE

    def test_stereo_44k_is_resampled_to_mono_16k(self) -> None:
        pytest.importorskip("av")
        samples = load_audio(_tone_wav(44_100, 1.0, channels=2), target_rate=RATE)
        assert samples.ndim == 1
        assert abs(samples.shape[0] - RATE) <= 64

    def test_linear_fallback_without_pyav_is_bounded_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(decoding_module, "_pyav", lambda: None)
        samples = load_audio(_tone_wav(44_100, 1.0, channels=2), target_rate=RATE)
        assert samples.shape == (RATE,)
        with pytest.raises(ValidationException):
            load_audio(ONE_HZ_WAV, target_rate=RATE)
        with pytest.raises(ValidationException):
            load_audio(_tone_wav(8_000, 2.0), target_rate=RATE, max_seconds=1.0)

    def test_default_bound_is_one_hour(self) -> None:
        assert DEFAULT_MAX_AUDIO_SECONDS == 3_600.0


class _RecordingWhisper:
    """Stands in for a loaded Whisper model and records its input."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.inputs: list[Any] = []

    def transcribe(self, source: Any, **kwargs: Any) -> tuple[Any, Any]:
        """Record the input and return no segments.

        Args:
            source (Any): What the engine was handed.
            **kwargs (Any): Ignored.

        Returns:
            tuple[Any, Any]: An empty segment iterator and an info object.
        """
        self.inputs.append(source)

        class _Info:
            language = "pt"
            language_probability = 1.0
            duration = 0.0

        return iter([]), _Info()


class _RecordingEngine:
    """Stands in for the sherpa diarization engine."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.sizes: list[int] = []

    def set_config(self, config: Any) -> None:
        """Accept a config.

        Args:
            config (Any): Ignored.
        """

    def process(self, samples: Any) -> Any:
        """Record how many samples arrived.

        Args:
            samples (Any): The decoded audio.

        Returns:
            Any: An empty, sortable result.
        """
        self.sizes.append(len(samples))

        class _Result(list[Any]):
            def sort_by_start_time(self) -> list[Any]:
                return []

        return _Result()


def _stt() -> tuple[SpeechToText, _RecordingWhisper]:
    """Build a ``SpeechToText`` whose model is already a recorder.

    Returns:
        tuple[SpeechToText, _RecordingWhisper]: The engine and its model.
    """
    stt = SpeechToText(device="cpu")
    model = _RecordingWhisper()
    stt._model = model
    return stt, model


class TestEngines:
    async def test_speech_to_text_refuses_before_the_model_runs(self) -> None:
        stt, model = _stt()
        with pytest.raises(ValidationException):
            await stt.transcribe(ONE_HZ_WAV)
        assert model.inputs == []

    async def test_speech_to_text_hands_the_model_bounded_samples(self) -> None:
        stt, model = _stt()
        await stt.transcribe(_tone_wav(RATE, 0.5))
        assert isinstance(model.inputs[0], np.ndarray)
        assert model.inputs[0].shape == (RATE // 2,)

    async def test_speech_to_text_bound_is_configurable(self) -> None:
        stt = SpeechToText(device="cpu", max_audio_seconds=0.25)
        stt._model = _RecordingWhisper()
        with pytest.raises(ValidationException):
            await stt.transcribe(_tone_wav(RATE, 0.5))

    async def test_diarizer_refuses_before_the_engine_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        diarizer = SpeakerDiarizer(num_speakers=2)
        engine = _RecordingEngine()
        diarizer._engine = engine
        monkeypatch.setattr(SpeakerDiarizer, "_make_config", lambda self, n: None)
        with pytest.raises(ValidationException):
            await diarizer.diarize(ONE_HZ_WAV)
        assert engine.sizes == []
        await diarizer.diarize(_tone_wav(RATE, 0.125))
        assert engine.sizes == [2_000]

    async def test_diarizer_bound_is_configurable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        diarizer = SpeakerDiarizer(num_speakers=2, max_audio_seconds=0.25)
        diarizer._engine = _RecordingEngine()
        monkeypatch.setattr(SpeakerDiarizer, "_make_config", lambda self, n: None)
        with pytest.raises(ValidationException):
            await diarizer.diarize(_tone_wav(RATE, 0.5))

    async def test_voice_embedder_refuses_before_extracting(self) -> None:
        embedder = VoiceEmbedder()
        embedder._extractor = object()
        with pytest.raises(ValidationException):
            await embedder.embed(ONE_HZ_WAV)
        assert VoiceEmbedder(max_audio_seconds=None).max_audio_seconds is None


class TestRouters:
    def test_genai_transcribe_refuses_forged_rate_with_422(self) -> None:
        stt, model = _stt()
        app = FastAPI()
        app.include_router(make_genai_router(speech_to_text=stt))
        register_exception_handlers(app)
        client = TestClient(app)

        refused = client.post(
            "/api/genai/transcribe",
            files={"file": ("a.wav", ONE_HZ_WAV, "audio/wav")},
        )
        accepted = client.post(
            "/api/genai/transcribe",
            files={"file": ("a.wav", _tone_wav(RATE, 0.25), "audio/wav")},
        )

        assert refused.status_code == 422
        assert refused.json()["details"]["min_rate"] == MIN_SOURCE_RATE
        assert accepted.status_code == 200
        assert len(model.inputs) == 1

    async def test_voice_transcribe_refuses_forged_rate_with_422(self) -> None:
        stt, model = _stt()
        diarizer = SpeakerDiarizer(num_speakers=2)
        engine = _RecordingEngine()
        diarizer._engine = engine
        transcriber = ConversationTranscriber(stt=stt, diarizer=diarizer)

        async def _session() -> AsyncIterator[Any]:
            yield None

        app = FastAPI()
        app.include_router(
            make_voice_router(session_factory=_session, transcriber=transcriber),
        )
        register_exception_handlers(app)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/voice/transcribe",
                files={"audio": ("a.wav", ONE_HZ_WAV, "audio/wav")},
            )

        assert response.status_code == 422
        assert engine.sizes == []
        assert model.inputs == []
