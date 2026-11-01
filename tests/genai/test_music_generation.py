"""Tests for local MusicGen generation.

``transformers`` is never loaded: a fake processor and model are injected
over :func:`tempest_fastapi_sdk.genai.music_generation._require_transformers`.
The decoder window (2048 positions, 50 frames per second) and the
``(n - 3) * 640`` samples per ``n`` tokens are the values measured on
``facebook/musicgen-small`` on 2026-10-31.
"""

from __future__ import annotations

import contextlib
import io
import wave
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.genai import (
    AudioGenerationConfig,
    AudioGenerator,
    MusicGenerator,
    SupportsAudioGeneration,
    make_genai_router,
)
from tempest_fastapi_sdk.genai import music_generation as music_module
from tempest_fastapi_sdk.genai.music_generation import (
    MAX_MELODY_SECONDS,
    check_music_config,
    music_tokens,
)

FRAME_RATE: int = 50
MAX_POSITIONS: int = 2048


class FakeArray:
    """Stands in for the tensor ``generate`` returns."""

    def __init__(self, array: np.ndarray) -> None:
        self.array = array

    def float(self) -> FakeArray:
        return self

    def cpu(self) -> FakeArray:
        return self

    def numpy(self) -> np.ndarray:
        return self.array


class FakeFeatures:
    """Records the dtype the features were cast to."""

    def __init__(self) -> None:
        self.dtype: str = "float32"

    def to(self, dtype: str) -> FakeFeatures:
        self.dtype = dtype
        return self


class FakeInputs(dict[str, Any]):
    def to(self, device: str) -> FakeInputs:
        return self


class FakeProcessor:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(
        self,
        text: list[str],
        padding: bool,
        return_tensors: str,
        audio: list[Any] | None = None,
        sampling_rate: int | None = None,
    ) -> FakeInputs:
        self.texts = text
        self.audio = audio
        self.sampling_rate = sampling_rate
        inputs = FakeInputs(input_ids=len(text))
        if audio is not None:
            inputs["input_features"] = FakeFeatures()
        return inputs


class FakeModel:
    def __init__(self, channels: int = 1) -> None:
        self.channels = channels
        self.calls: list[dict[str, Any]] = []
        self.config = SimpleNamespace(
            audio_encoder=SimpleNamespace(sampling_rate=32000, frame_rate=FRAME_RATE),
            decoder=SimpleNamespace(max_position_embeddings=MAX_POSITIONS),
        )

    dtype: str = "bfloat16"

    def to(self, device: str) -> FakeModel:
        return self

    def generate(self, **kwargs: Any) -> FakeArray:
        self.calls.append(kwargs)
        tokens = kwargs.get("max_new_tokens", 1500)
        samples = (tokens - 3) * 640
        batch = kwargs["input_ids"]
        array = np.stack(
            [
                np.full((self.channels, samples), 0.1 * (i + 1), dtype=np.float32)
                for i in range(batch)
            ],
        )
        return FakeArray(array)


class FakeTorch:
    float32 = "float32"
    float16 = "float16"
    bfloat16 = "bfloat16"

    def __init__(self) -> None:
        self.seeds: list[int] = []

    def manual_seed(self, seed: int) -> None:
        self.seeds.append(seed)

    @staticmethod
    @contextlib.contextmanager
    def no_grad() -> Iterator[None]:
        yield


def _install(
    monkeypatch: pytest.MonkeyPatch,
    channels: int = 1,
    model_type: str = "musicgen",
) -> SimpleNamespace:
    """Inject fake torch + transformers into the module."""
    model = FakeModel(channels)
    processor = FakeProcessor()
    torch = FakeTorch()
    loaded: list[str] = []

    def _load(kind: str) -> Any:
        def _from_pretrained(*args: Any, **kwargs: Any) -> FakeModel:
            loaded.append(kind)
            return model

        return SimpleNamespace(from_pretrained=_from_pretrained)

    transformers = SimpleNamespace(
        AutoConfig=SimpleNamespace(
            from_pretrained=lambda *a, **k: SimpleNamespace(model_type=model_type)
        ),
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *a, **k: processor),
        MusicgenForConditionalGeneration=_load("musicgen"),
        MusicgenMelodyForConditionalGeneration=_load("musicgen_melody"),
    )
    monkeypatch.setattr(
        music_module, "_require_transformers", lambda: (torch, transformers)
    )
    return SimpleNamespace(model=model, processor=processor, torch=torch, loaded=loaded)


def _channels_and_frames(data: bytes) -> tuple[int, int]:
    """Read a WAV's channel and frame count.

    Args:
        data (bytes): The WAV bytes.

    Returns:
        tuple[int, int]: Channels and frames.
    """
    with wave.open(io.BytesIO(data)) as reader:
        return reader.getnchannels(), reader.getnframes()


class TestMusicTokens:
    def test_fifty_frames_per_second(self) -> None:
        assert music_tokens(10.0, FRAME_RATE) == 500

    def test_never_zero(self) -> None:
        assert music_tokens(0.001, FRAME_RATE) == 1


class TestSeedBounds:
    @pytest.mark.parametrize("seed", [-1, 2**64])
    def test_out_of_torch_range_is_refused_by_the_schema(self, seed: int) -> None:
        with pytest.raises(ValueError):
            AudioGenerationConfig(seed=seed)

    def test_the_edges_are_accepted(self) -> None:
        assert AudioGenerationConfig(seed=2**64 - 1).seed == 2**64 - 1
        assert AudioGenerationConfig(seed=0).seed == 0


class TestCheckMusicConfig:
    def test_none_passes(self) -> None:
        check_music_config(None, frame_rate=FRAME_RATE, max_positions=MAX_POSITIONS)

    def test_the_window_edge_passes(self) -> None:
        config = AudioGenerationConfig(seconds=40.96)
        check_music_config(config, frame_rate=FRAME_RATE, max_positions=MAX_POSITIONS)

    def test_past_the_window_is_refused(self) -> None:
        config = AudioGenerationConfig(seconds=40.98)
        with pytest.raises(ValidationException) as caught:
            check_music_config(
                config, frame_rate=FRAME_RATE, max_positions=MAX_POSITIONS
            )
        assert caught.value.details == {"field": "seconds", "limit": 40.96}

    @pytest.mark.parametrize(
        ("config", "field"),
        [
            (AudioGenerationConfig(steps=10), "steps"),
            (AudioGenerationConfig(negative_prompt="noise"), "negative_prompt"),
        ],
    )
    def test_diffusion_fields_are_refused(
        self, config: AudioGenerationConfig, field: str
    ) -> None:
        with pytest.raises(ValidationException) as caught:
            check_music_config(
                config, frame_rate=FRAME_RATE, max_positions=MAX_POSITIONS
            )
        assert caught.value.details == {"field": field}


class TestGenerate:
    async def test_mono_clip_with_the_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        generator = MusicGenerator("facebook/musicgen-small", device="cpu")
        clips = await generator.generate(
            "lofi",
            config=AudioGenerationConfig(seconds=5.0, seed=3),
        )
        assert len(clips) == 1
        assert clips[0].seed == 3
        assert clips[0].sample_rate == 32000
        assert clips[0].seconds == pytest.approx(4.94)
        assert _channels_and_frames(clips[0].data) == (1, 158080)
        assert fakes.model.calls[0]["max_new_tokens"] == 250
        assert fakes.model.calls[0]["do_sample"] is True
        assert fakes.torch.seeds == [3]

    async def test_stereo_checkpoint_gives_two_channels(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install(monkeypatch, channels=2)
        generator = MusicGenerator("facebook/musicgen-stereo-small", device="cpu")
        clip = (
            await generator.generate("rock", config=AudioGenerationConfig(seconds=1.0))
        )[0]
        assert clip.channels == 2

    async def test_num_waveforms_batches_the_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        generator = MusicGenerator("m", device="cpu")
        clips = await generator.generate(
            "lofi",
            config=AudioGenerationConfig(
                seconds=1.0, num_waveforms=3, guidance_scale=4.0
            ),
        )
        assert fakes.processor.texts == ["lofi", "lofi", "lofi"]
        assert len({clip.data for clip in clips}) == 3
        assert fakes.model.calls[0]["guidance_scale"] == 4.0

    async def test_no_config_uses_the_model_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        clip = (await MusicGenerator("m", device="cpu").generate("lofi"))[0]
        assert "max_new_tokens" not in fakes.model.calls[0]
        assert 0 <= clip.seed < 2**32

    async def test_refusal_never_reaches_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        generator = MusicGenerator("m", device="cpu")
        with pytest.raises(ValidationException):
            await generator.generate("x", config=AudioGenerationConfig(seconds=60.0))
        assert fakes.model.calls == []

    async def test_diffusion_fields_refused_before_loading(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install(monkeypatch)
        generator = MusicGenerator("m", device="cpu")
        with pytest.raises(ValidationException):
            await generator.generate("x", config=AudioGenerationConfig(steps=5))
        assert generator.is_loaded is False

    async def test_unset_guidance_is_not_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        await MusicGenerator("m", device="cpu").generate(
            "x",
            config=AudioGenerationConfig(seconds=1.0),
        )
        assert "guidance_scale" not in fakes.model.calls[0]

    async def test_max_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch)
        assert MusicGenerator("m", device="cpu").max_seconds == 40.96


class TestLifecycle:
    def test_rejects_non_positive_concurrency(self) -> None:
        with pytest.raises(ValueError, match="max_concurrent"):
            MusicGenerator("m", device="cpu", max_concurrent=0)

    def test_unload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch)
        generator = MusicGenerator("m", device="cpu")
        generator.load()
        assert generator.is_loaded
        generator.unload()
        assert generator.is_loaded is False

    def test_satisfies_the_router_protocol(self) -> None:
        music: SupportsAudioGeneration = MusicGenerator("m", device="cpu")
        sound: SupportsAudioGeneration = AudioGenerator("m", device="cpu")
        assert isinstance(music, SupportsAudioGeneration)
        assert isinstance(sound, SupportsAudioGeneration)

    def test_failure_moving_to_the_device_releases(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fakes = _install(monkeypatch)

        def out_of_memory(device: str) -> FakeModel:
            raise RuntimeError("CUDA out of memory")

        fakes.model.to = out_of_memory
        generator = MusicGenerator("m", device="cpu")
        released: list[bool] = []
        monkeypatch.setattr(generator, "_release", lambda: released.append(True))
        with pytest.raises(RuntimeError, match="out of memory"):
            generator.load()
        assert released == [True]
        assert generator.is_loaded is False

    def test_config_without_codec_fields_fails_before_the_move(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fakes = _install(monkeypatch)
        fakes.model.config = SimpleNamespace(decoder=SimpleNamespace())
        moved: list[str] = []
        fakes.model.to = lambda device: moved.append(device)
        with pytest.raises(AttributeError):
            MusicGenerator("m", device="cpu").load()
        assert moved == []

    def test_idle_unload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch)
        generator = MusicGenerator("m", device="cpu", idle_unload_seconds=0.0)
        generator.load()
        assert generator.unload_if_idle() is True
        assert generator.is_loaded is False


class TestRouter:
    def _client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        _install(monkeypatch)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_genai_router(audio_generator=MusicGenerator("m", device="cpu"))
        )
        return TestClient(app)

    def test_returns_one_wav(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = self._client(monkeypatch).post(
            "/api/genai/audio",
            json={"prompt": "lofi", "config": {"seconds": 4.0, "seed": 7}},
        )
        assert response.status_code == 200
        assert response.headers["x-audio-seed"] == "7"
        assert _channels_and_frames(response.content) == (1, 126080)

    def test_steps_answer_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = self._client(monkeypatch).post(
            "/api/genai/audio",
            json={"prompt": "lofi", "config": {"steps": 5}},
        )
        assert response.status_code == 422
        assert response.json()["details"] == {"field": "steps"}


class TestMissingExtra:
    def test_import_error_names_the_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "transformers":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        with pytest.raises(ImportError, match=r"\[genai\]"):
            music_module._require_transformers()


def _wav(seconds: float = 0.5, rate: int = 32000) -> bytes:
    """Build a short 16-bit mono WAV.

    Args:
        seconds (float): Duration.
        rate (int): Sample rate.

    Returns:
        bytes: The WAV.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x10\x00" * int(seconds * rate))
    return buffer.getvalue()


class TestMelody:
    async def test_melody_checkpoint_loads_the_melody_class(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch, model_type="musicgen_melody")
        generator = MusicGenerator("facebook/musicgen-melody", device="cpu")
        assert generator.accepts_melody is True
        assert fakes.loaded == ["musicgen_melody"]

    async def test_melody_reaches_the_processor_and_features_are_cast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch, model_type="musicgen_melody")
        await MusicGenerator("m", device="cpu").generate(
            "acoustic version",
            config=AudioGenerationConfig(seconds=1.0, num_waveforms=2),
            melody=_wav(),
        )
        assert fakes.processor.sampling_rate == 32000
        assert len(fakes.processor.audio) == 2
        assert fakes.processor.audio[0].shape == (16000,)
        assert fakes.model.calls[0]["input_features"].dtype == "bfloat16"

    async def test_melody_on_a_plain_checkpoint_is_a_422(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        with pytest.raises(ValidationException) as caught:
            await MusicGenerator("m", device="cpu").generate("x", melody=_wav())
        assert caught.value.details == {"field": "melody"}
        assert fakes.model.calls == []

    async def test_missing_melody_is_refused_before_loading(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        fakes = _install(monkeypatch, model_type="musicgen_melody")
        generator = MusicGenerator("m", device="cpu")
        with pytest.raises(FileNotFoundError) as caught:
            await generator.generate("x", melody=tmp_path / "secret" / "missing.wav")
        assert generator.is_loaded is False
        assert fakes.loaded == []
        assert str(tmp_path) not in str(caught.value)

    async def test_a_melody_past_the_ceiling_is_a_422(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch, model_type="musicgen_melody")
        too_long = _wav(seconds=MAX_MELODY_SECONDS + 1, rate=8000)
        with pytest.raises(ValidationException) as caught:
            await MusicGenerator("m", device="cpu").generate("x", melody=too_long)
        assert "max_seconds" in caught.value.details
        assert fakes.model.calls == []

    async def test_plain_checkpoint_loads_the_plain_class(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        assert MusicGenerator("m", device="cpu").accepts_melody is False
        assert fakes.loaded == ["musicgen"]
