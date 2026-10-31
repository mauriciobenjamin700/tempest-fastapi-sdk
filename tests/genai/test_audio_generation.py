"""Tests for local sound-effect and music generation.

``diffusers`` is never loaded: a fake pipeline is injected over
:func:`tempest_fastapi_sdk.genai.audio_generation._require_diffusers`. The
three refusals are reproduced in the shape measured on 2026-10-31: the
``StableAudioPipeline`` keyword, the AudioLDM2 ``GPT2Model`` without
``_update_model_kwargs_for_generation``, and MusicLDM's
``get_text_features`` returning an output object under transformers 5.22.1.
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
from tempest_fastapi_sdk.genai import (
    AudioGenerationConfig,
    AudioGenerator,
    GenAIRequestLimits,
    make_genai_router,
)
from tempest_fastapi_sdk.genai import audio_generation as audio_module
from tempest_fastapi_sdk.genai.audio_generation import (
    check_audio_pipeline,
    encode_wav,
    pipeline_sample_rate,
    text_features_problem,
)


class FakeTensor:
    """Stands in for ``torch.Tensor`` in the text-feature probe."""


class FakeGenerator:
    def __init__(self, device: str) -> None:
        self.device = device
        self.seed: int | None = None

    def manual_seed(self, seed: int) -> FakeGenerator:
        self.seed = seed
        return self


class FakeTorch:
    float32 = "float32"
    float16 = "float16"
    bfloat16 = "bfloat16"
    Tensor = FakeTensor

    @staticmethod
    def Generator(device: str) -> FakeGenerator:  # noqa: N802
        return FakeGenerator(device)

    @staticmethod
    @contextlib.contextmanager
    def no_grad() -> Iterator[None]:
        yield


class FakeTokens:
    def __init__(self) -> None:
        self.input_ids = SimpleNamespace(to=lambda device: "ids")
        self.attention_mask = SimpleNamespace(to=lambda device: "mask")


class FakeEncoder:
    """A CLAP-like text encoder; ``features`` is what the probe receives."""

    def __init__(self, features: Any) -> None:
        self.features = features
        self.device = "cpu"

    def get_text_features(self, input_ids: Any, attention_mask: Any) -> Any:
        return self.features


class FakePipeline:
    """An AudioLDM-shaped pipeline: its call takes ``audio_length_in_s``."""

    def __init__(self, sample_rate: int = 16000) -> None:
        self.vocoder = SimpleNamespace(
            config=SimpleNamespace(sampling_rate=sample_rate)
        )
        self.calls: list[dict[str, Any]] = []
        self.moved_to: str | None = None

    def to(self, device: str) -> FakePipeline:
        self.moved_to = device
        return self

    def __call__(
        self,
        prompt: str,
        audio_length_in_s: float | None = None,
        num_inference_steps: int = 10,
        num_waveforms_per_prompt: int = 1,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "prompt": prompt,
                "audio_length_in_s": audio_length_in_s,
                "num_inference_steps": num_inference_steps,
                "num_waveforms_per_prompt": num_waveforms_per_prompt,
                **kwargs,
            },
        )
        frames = int((audio_length_in_s or 1.0) * 16000)
        audios = [
            np.full(frames, 0.25 * (index + 1), dtype=np.float32)
            for index in range(num_waveforms_per_prompt)
        ]
        return SimpleNamespace(audios=audios)


class StableAudioLike:
    """A pipeline whose call takes ``audio_end_in_s`` instead."""

    def __call__(self, prompt: str, audio_end_in_s: float | None = None) -> Any:
        return None


class FakeDiffusionPipeline:
    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline
        self.load_kwargs: dict[str, Any] = {}

    def from_pretrained(self, model_id: str, **kwargs: Any) -> Any:
        self.load_kwargs = {"model_id": model_id, **kwargs}
        return self._pipeline


def _install(monkeypatch: pytest.MonkeyPatch, pipeline: Any = None) -> Any:
    """Inject a fake torch + diffusers pair into the module."""
    pipeline = pipeline if pipeline is not None else FakePipeline()
    diffusers = SimpleNamespace(DiffusionPipeline=FakeDiffusionPipeline(pipeline))
    monkeypatch.setattr(
        audio_module, "_require_diffusers", lambda: (FakeTorch(), diffusers)
    )
    return diffusers


def _read(data: bytes) -> tuple[int, int, int, int]:
    """Read a WAV the way a consumer would.

    Args:
        data (bytes): The WAV bytes.

    Returns:
        tuple[int, int, int, int]: Frames, rate, channels and sample width.
    """
    with wave.open(io.BytesIO(data)) as reader:
        return (
            reader.getnframes(),
            reader.getframerate(),
            reader.getnchannels(),
            reader.getsampwidth(),
        )


class TestAudioGenerationConfig:
    def test_renames_to_the_diffusers_spellings(self) -> None:
        config = AudioGenerationConfig(seconds=4.0, steps=30, num_waveforms=2)
        assert config.to_pipeline_kwargs() == {
            "audio_length_in_s": 4.0,
            "num_inference_steps": 30,
            "num_waveforms_per_prompt": 2,
        }

    def test_seed_and_unset_fields_do_not_leak(self) -> None:
        assert AudioGenerationConfig(seed=3).to_pipeline_kwargs() == {}

    def test_zero_guidance_survives(self) -> None:
        kwargs = AudioGenerationConfig(guidance_scale=0.0).to_pipeline_kwargs()
        assert kwargs == {"guidance_scale": 0.0}

    @pytest.mark.parametrize("field", [{"seconds": 0}, {"seconds": 121}, {"steps": 0}])
    def test_absolute_bounds(self, field: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            AudioGenerationConfig(**field)


class TestEncodeWav:
    def test_mono_float_becomes_16_bit_pcm(self) -> None:
        data, channels, seconds = encode_wav(np.zeros(16000, dtype=np.float32), 16000)
        assert (channels, seconds) == (1, 1.0)
        assert _read(data) == (16000, 16000, 1, 2)

    def test_overshoot_saturates_instead_of_wrapping(self) -> None:
        data, _, _ = encode_wav(np.array([2.0, -2.0], dtype=np.float32), 8000)
        with wave.open(io.BytesIO(data)) as reader:
            samples = np.frombuffer(reader.readframes(2), dtype="<i2")
        assert samples.tolist() == [32767, -32767]

    def test_channels_first_is_interleaved(self) -> None:
        stereo = np.array([[0.5, 0.5], [-0.5, -0.5]], dtype=np.float32)
        data, channels, seconds = encode_wav(stereo, 2)
        assert (channels, seconds) == (2, 1.0)
        with wave.open(io.BytesIO(data)) as reader:
            samples = np.frombuffer(reader.readframes(2), dtype="<i2")
        assert samples.tolist() == [16383, -16383, 16383, -16383]

    def test_nan_is_silence_and_inf_saturates(self) -> None:
        samples = np.array([np.nan, np.inf, -np.inf], dtype=np.float32)
        data, _, _ = encode_wav(samples, 8000)
        with wave.open(io.BytesIO(data)) as reader:
            pcm = np.frombuffer(reader.readframes(3), dtype="<i2")
        assert pcm.tolist() == [0, 32767, -32767]

    def test_rejects_three_dimensions(self) -> None:
        with pytest.raises(ValueError, match="1-D or 2-D"):
            encode_wav(np.zeros((1, 1, 4)), 16000)


class TestPipelineChecks:
    def test_audioldm_shape_passes(self) -> None:
        check_audio_pipeline(FakePipeline())

    def test_stable_audio_keyword_is_refused(self) -> None:
        with pytest.raises(ValueError, match="audio_length_in_s"):
            check_audio_pipeline(StableAudioLike())

    def test_audioldm2_with_a_bare_gpt2_is_refused(self) -> None:
        pipeline = FakePipeline()
        pipeline.language_model = SimpleNamespace()  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="_update_model_kwargs_for_generation"):
            check_audio_pipeline(pipeline)

    def test_audioldm2_with_a_generating_model_passes(self) -> None:
        pipeline = FakePipeline()
        pipeline.language_model = SimpleNamespace(  # type: ignore[attr-defined]
            _update_model_kwargs_for_generation=lambda *args: None,
        )
        check_audio_pipeline(pipeline)

    def test_text_features_output_object_is_reported(self) -> None:
        pipeline = FakePipeline()
        pipeline.text_encoder = FakeEncoder(SimpleNamespace())  # type: ignore[attr-defined]
        pipeline.tokenizer = lambda *args, **kwargs: FakeTokens()  # type: ignore[attr-defined]
        problem = text_features_problem(pipeline, FakeTorch())
        assert problem is not None
        assert "not a tensor" in problem

    def test_text_features_tensor_passes(self) -> None:
        pipeline = FakePipeline()
        pipeline.text_encoder = FakeEncoder(FakeTensor())  # type: ignore[attr-defined]
        pipeline.tokenizer = lambda *args, **kwargs: FakeTokens()  # type: ignore[attr-defined]
        assert text_features_problem(pipeline, FakeTorch()) is None

    def test_encoder_without_text_features_is_not_probed(self) -> None:
        pipeline = FakePipeline()
        pipeline.text_encoder = SimpleNamespace()  # type: ignore[attr-defined]
        pipeline.tokenizer = lambda *args, **kwargs: FakeTokens()  # type: ignore[attr-defined]
        assert text_features_problem(pipeline, FakeTorch()) is None

    def test_sample_rate_from_vocoder_then_vae(self) -> None:
        assert pipeline_sample_rate(FakePipeline(22050)) == 22050
        vae_only = SimpleNamespace(
            vae=SimpleNamespace(config=SimpleNamespace(sampling_rate=44100))
        )
        assert pipeline_sample_rate(vae_only) == 44100
        with pytest.raises(ValueError, match="sampling_rate"):
            pipeline_sample_rate(SimpleNamespace())


class TestLoad:
    def test_forwards_dtype_and_the_model_ref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        diffusers = _install(monkeypatch)
        generator = AudioGenerator(
            "cvssp/audioldm-s-full-v2", device="cpu", local_files_only=True
        )
        generator.load()
        kwargs = diffusers.DiffusionPipeline.load_kwargs
        assert kwargs["model_id"] == "cvssp/audioldm-s-full-v2"
        assert kwargs["torch_dtype"] == "float32"
        assert kwargs["local_files_only"] is True
        assert generator.pipeline.moved_to == "cpu"

    def test_refused_pipeline_is_never_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pipeline = FakePipeline()
        pipeline.text_encoder = FakeEncoder(SimpleNamespace())  # type: ignore[attr-defined]
        pipeline.tokenizer = lambda *args, **kwargs: FakeTokens()  # type: ignore[attr-defined]
        _install(monkeypatch, pipeline)
        generator = AudioGenerator("ucsd-reach/musicldm", device="cpu")
        released: list[bool] = []
        monkeypatch.setattr(generator, "_release", lambda: released.append(True))
        with pytest.raises(ValueError, match="not a tensor") as caught:
            generator.load()
        assert generator.is_loaded is False
        assert released == [True]
        frames = []
        traceback = caught.value.__traceback__
        while traceback is not None:
            frames.append(traceback.tb_frame)
            traceback = traceback.tb_next
        module_frames = [
            frame
            for frame in frames
            if frame.f_code.co_filename == audio_module.__file__
        ]
        assert module_frames
        assert all(
            frame.f_locals.get("pipeline") is not pipeline for frame in module_frames
        )

    def test_failure_moving_to_the_device_releases(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pipeline = FakePipeline()

        def out_of_memory(device: str) -> FakePipeline:
            raise RuntimeError("CUDA out of memory")

        pipeline.to = out_of_memory  # type: ignore[method-assign]
        _install(monkeypatch, pipeline)
        generator = AudioGenerator("m", device="cpu")
        released: list[bool] = []
        monkeypatch.setattr(generator, "_release", lambda: released.append(True))
        with pytest.raises(RuntimeError, match="out of memory"):
            generator.load()
        assert released == [True]
        assert generator.is_loaded is False

    def test_rejects_non_positive_concurrency(self) -> None:
        with pytest.raises(ValueError, match="max_concurrent"):
            AudioGenerator("m", device="cpu", max_concurrent=0)

    def test_unload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch)
        generator = AudioGenerator("m", device="cpu")
        generator.load()
        generator.unload()
        assert generator.is_loaded is False


class TestGenerate:
    async def test_returns_wavs_with_the_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = AudioGenerator("m", device="cpu")
        clips = await generator.generate(
            "rain",
            config=AudioGenerationConfig(seconds=2.0, seed=7, num_waveforms=2),
        )
        assert len(clips) == 2
        assert {clip.seed for clip in clips} == {7}
        assert [clip.seconds for clip in clips] == [2.0, 2.0]
        assert _read(clips[0].data) == (32000, 16000, 1, 2)
        assert clips[0].data != clips[1].data

    async def test_a_seed_is_drawn_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = AudioGenerator("m", device="cpu")
        clip = (await generator.generate("rain"))[0]
        assert 0 <= clip.seed < 2**32

    async def test_config_reaches_the_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pipeline = FakePipeline()
        _install(monkeypatch, pipeline)
        generator = AudioGenerator("m", device="cpu")
        await generator.generate(
            "rain",
            config=AudioGenerationConfig(
                seconds=3.0, steps=25, negative_prompt="noise", seed=1
            ),
        )
        call = pipeline.calls[0]
        assert call["audio_length_in_s"] == 3.0
        assert call["num_inference_steps"] == 25
        assert call["negative_prompt"] == "noise"
        assert call["output_type"] == "np"
        assert call["generator"].seed == 1


class TestRouter:
    def _client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        _install(monkeypatch)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_genai_router(audio_generator=AudioGenerator("m", device="cpu")),
        )
        return TestClient(app)

    def test_returns_one_wav_with_the_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = self._client(monkeypatch).post(
            "/api/genai/audio",
            json={"prompt": "rain", "config": {"seconds": 1.0, "seed": 42}},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers["x-audio-seed"] == "42"
        assert _read(response.content)[0] == 16000

    @pytest.mark.parametrize(
        ("config", "field"),
        [
            ({"seconds": 30.5}, "seconds"),
            ({"steps": 201}, "steps"),
            ({"num_waveforms": 2}, "num_waveforms"),
        ],
    )
    def test_limits_answer_422(
        self,
        monkeypatch: pytest.MonkeyPatch,
        config: dict[str, Any],
        field: str,
    ) -> None:
        response = self._client(monkeypatch).post(
            "/api/genai/audio",
            json={"prompt": "rain", "config": config},
        )
        assert response.status_code == 422
        assert response.json()["details"]["field"] == field

    def test_seconds_at_the_limit_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = self._client(monkeypatch).post(
            "/api/genai/audio",
            json={"prompt": "rain", "config": {"seconds": 30.0}},
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"prompt": "x" * 4001}, "prompt"),
            (
                {"prompt": "rain", "config": {"negative_prompt": "x" * 4001}},
                "negative_prompt",
            ),
        ],
    )
    def test_prompt_limits_answer_422(
        self,
        monkeypatch: pytest.MonkeyPatch,
        body: dict[str, Any],
        field: str,
    ) -> None:
        _install(monkeypatch)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_genai_router(
                audio_generator=AudioGenerator("m", device="cpu"),
                limits=GenAIRequestLimits(max_prompt_chars=4000),
            ),
        )
        response = TestClient(app).post("/api/genai/audio", json=body)
        assert response.status_code == 422
        assert response.json()["details"]["field"] == field

    def test_raised_limits_reach_the_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_genai_router(
                audio_generator=AudioGenerator("m", device="cpu"),
                limits=GenAIRequestLimits(max_audio_seconds=60.0, max_audio_steps=500),
            ),
        )
        response = TestClient(app).post(
            "/api/genai/audio",
            json={"prompt": "rain", "config": {"seconds": 45.0, "steps": 400}},
        )
        assert response.status_code == 200


class TestMissingExtra:
    def test_import_error_names_the_extras(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "diffusers":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        with pytest.raises(ImportError, match=r"\[genai,genai-sound\]"):
            audio_module._require_diffusers()
