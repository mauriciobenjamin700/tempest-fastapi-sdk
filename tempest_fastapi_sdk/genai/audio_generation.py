"""Local sound effects and music over HuggingFace diffusers.

:class:`AudioGenerator` loads a text-to-audio diffusion pipeline once and
renders on your own hardware, with the same conventions as
:class:`~tempest_fastapi_sdk.genai.ImageGenerator`: device and precision
resolution, lazy load on a worker thread, idle unload, Hub pinning keywords,
one render at a time, and a seed returned with every clip.

The pipeline is loaded with ``diffusers.DiffusionPipeline``, not
``AutoPipelineForText2Audio``: measured on diffusers 0.39.0, the auto class
refuses both ``cvssp/audioldm-s-full-v2`` and ``ucsd-reach/musicldm``
("can't find a pipeline linked to AudioLDMPipeline").

Supported: pipelines whose call takes ``audio_length_in_s`` -- the AudioLDM
family. Measured on an RTX 4070 Ti SUPER (fp16, 50 steps, 2026-10-31):
``cvssp/audioldm-s-full-v2`` (sound effects) and ``ucsd-reach/musicldm``
(music) both rendered 10 s at 16 kHz in about 1.1 s with under 1.1 GiB of
peak VRAM, and the same seed reproduced the same waveform. diffusers keeps
both classes under ``pipelines/deprecated`` ("will not receive bug fixes or
feature updates after Diffusers version 0.33.1"), so they are frozen code.
Three exclusions, each refused by :meth:`AudioGenerator.load` instead of
failing mid-call:

* ``AudioLDM2Pipeline`` loads but fails on the first call with
  ``'GPT2Model' object has no attribute '_update_model_kwargs_for_generation'``
  under transformers 4.57.6 (the upstream fix, huggingface/diffusers#13111,
  was closed unmerged). :meth:`AudioGenerator.load` refuses it up front.
* ``MusicLDMPipeline`` under transformers 5.22.1 (with diffusers 0.42.1):
  ``ClapModel.get_text_features`` returns a ``BaseModelOutputWithPooling``
  where the pipeline expects a tensor, and the call fails with
  ``'BaseModelOutputWithPooling' object has no attribute 'to'``. It works on
  transformers 4.57.6, the version the ``[genai-audio]`` extra holds the
  lock at. The load probes the text encoder once to tell the two apart.
* ``StableAudioPipeline`` takes ``audio_end_in_s`` instead.

Every model above publishes non-commercial weights (``cc-by-nc-sa-4.0``);
check the model card before shipping.

``diffusers``, ``torch`` and ``numpy`` are imported inside the methods that
need them, so this module imports without the ``[genai-sound]`` extra.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import secrets
import wave
from typing import Any

from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.schemas import (
    AudioGenerationConfig,
    GeneratedAudio,
    HardwareInfo,
    ModelDtype,
)
from tempest_fastapi_sdk.genai.text import auto_dtype_name, resolve_device
from tempest_fastapi_sdk.genai.tracing import genai_span
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

_MAX_SEED: int = 2**32 - 1

AUDIO_LENGTH_KEYWORD: str = "audio_length_in_s"
"""The call keyword that marks a supported (AudioLDM-family) pipeline."""

AUDIOLDM2_UPSTREAM_ISSUE: str = "https://github.com/huggingface/diffusers/pull/13111"
"""Where the AudioLDM2 ``GPT2Model`` breakage was proposed fixed (unmerged)."""


def _require_diffusers() -> tuple[Any, Any]:
    """Import ``torch`` + ``diffusers`` or raise a helpful error.

    Returns:
        tuple[Any, Any]: ``(torch, diffusers)``.

    Raises:
        ImportError: When the ``[genai-sound]`` extra is not installed.
    """
    try:
        import diffusers
        import torch
    except ImportError as exc:
        raise ImportError(
            "Audio generation requires the optional [genai,genai-sound] "
            "extras. Install with: "
            "pip install tempest-fastapi-sdk[genai,genai-sound]",
        ) from exc
    return torch, diffusers


def check_audio_pipeline(pipeline: Any) -> None:
    """Refuse a pipeline this generator cannot drive.

    Args:
        pipeline (Any): A loaded diffusers pipeline.

    Raises:
        ValueError: When the pipeline's call does not take
            ``audio_length_in_s`` (``StableAudioPipeline``, or an image
            pipeline), or when it is an ``AudioLDM2Pipeline`` whose language
            model lacks ``_update_model_kwargs_for_generation`` -- the
            combination that fails mid-call under transformers 4.57.6.
    """
    name = type(pipeline).__name__
    parameters = inspect.signature(pipeline.__call__).parameters
    if AUDIO_LENGTH_KEYWORD not in parameters:
        raise ValueError(
            f"{name} is not an AudioLDM-family pipeline (its call takes no "
            f"{AUDIO_LENGTH_KEYWORD!r}); AudioGenerator supports AudioLDM "
            "and MusicLDM",
        )
    language_model = getattr(pipeline, "language_model", None)
    if language_model is not None and not hasattr(
        language_model,
        "_update_model_kwargs_for_generation",
    ):
        raise ValueError(
            f"{name} cannot generate with this transformers version: its "
            f"{type(language_model).__name__} language model has no "
            "'_update_model_kwargs_for_generation', so the first call fails "
            f"with AttributeError (see {AUDIOLDM2_UPSTREAM_ISSUE}). Use "
            "cvssp/audioldm-s-full-v2 or ucsd-reach/musicldm instead",
        )


def check_text_features(pipeline: Any, torch: Any) -> None:
    """Probe a CLAP text encoder once and refuse one the pipeline cannot use.

    ``MusicLDMPipeline`` calls ``text_encoder.get_text_features(...)`` and
    then ``.to(...)`` on the result. That result is a tensor on
    transformers 4.57.6 and a ``BaseModelOutputWithPooling`` on 5.22.1, so
    the failure is decided by the installed transformers, not by the model.
    Probing the real call keeps the check correct across versions nobody
    measured, instead of guessing a version cutoff.

    Pipelines whose text encoder has no ``get_text_features`` (AudioLDM
    calls the encoder directly) are not probed.

    Args:
        pipeline (Any): A loaded diffusers pipeline, already on its device.
        torch (Any): The imported ``torch`` module.

    Raises:
        ValueError: When ``get_text_features`` returns something other than
            a tensor.
    """
    encoder = getattr(pipeline, "text_encoder", None)
    tokenizer = getattr(pipeline, "tokenizer", None)
    if encoder is None or tokenizer is None:
        return
    if not hasattr(encoder, "get_text_features"):
        return
    tokens = tokenizer(["probe"], padding=True, return_tensors="pt")
    device = getattr(encoder, "device", "cpu")
    with torch.no_grad():
        features = encoder.get_text_features(
            tokens.input_ids.to(device),
            attention_mask=tokens.attention_mask.to(device),
        )
    if not isinstance(features, torch.Tensor):
        import transformers

        raise ValueError(
            f"{type(pipeline).__name__} cannot generate with transformers "
            f"{transformers.__version__}: {type(encoder).__name__}."
            f"get_text_features returns {type(features).__name__}, not a "
            "tensor, so the first call fails. It works with transformers "
            "4.57.6 (installing [genai-audio] holds transformers below 5); "
            "cvssp/audioldm-s-full-v2 works on both",
        )


def pipeline_sample_rate(pipeline: Any) -> int:
    """Read the output sample rate a pipeline declares.

    Args:
        pipeline (Any): A loaded diffusers audio pipeline.

    Returns:
        int: Samples per second, from the vocoder config (AudioLDM family)
        or the VAE config.

    Raises:
        ValueError: When neither config declares ``sampling_rate``.
    """
    for component in ("vocoder", "vae"):
        config = getattr(getattr(pipeline, component, None), "config", None)
        rate = getattr(config, "sampling_rate", None)
        if rate:
            return int(rate)
    raise ValueError(f"{type(pipeline).__name__} declares no sampling_rate")


def encode_wav(samples: Any, sample_rate: int) -> tuple[bytes, int, float]:
    """Encode a float waveform as a 16-bit PCM WAV.

    Values are clipped to ``[-1, 1]`` before scaling, so an overshooting
    sample saturates instead of wrapping around.

    Args:
        samples (Any): A NumPy-compatible array, ``(frames,)`` for mono or
            ``(channels, frames)`` for multi-channel.
        sample_rate (int): Samples per second.

    Returns:
        tuple[bytes, int, float]: The WAV bytes, the channel count and the
        duration in seconds.

    Raises:
        ValueError: When the array has more than two dimensions.
    """
    import numpy as np

    array = np.asarray(samples, dtype=np.float32)
    if array.ndim == 1:
        array = array[np.newaxis, :]
    if array.ndim != 2:
        raise ValueError(f"expected 1-D or 2-D audio, got shape {array.shape}")
    channels, frames = array.shape
    pcm = (np.clip(array, -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm.T.tobytes())
    return buffer.getvalue(), channels, frames / sample_rate


class AudioGenerator:
    """A lazily-loaded local text-to-audio pipeline with idle unload.

    Example:

        >>> gen = AudioGenerator("cvssp/audioldm-s-full-v2")
        >>> clips = await gen.generate(
        ...     "thunderstorm with heavy rain on a tin roof",
        ...     config=AudioGenerationConfig(seconds=10.0, steps=50),
        ... )
        >>> Path("storm.wav").write_bytes(clips[0].data)

    Attributes:
        model_id (str): The HuggingFace model id.
        device (str): The resolved device (``cuda`` / ``mps`` / ``cpu``).
        dtype (ModelDtype): The resolved compute precision.
        source (ModelRef): The resolved weight identity forwarded to
            ``from_pretrained``.
        pipeline_kwargs (dict[str, Any]): Extra ``from_pretrained``
            keywords, applied last so they override what the SDK computes.
        idle_unload_seconds (float | None): Idle threshold used by
            :meth:`unload_if_idle`.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "auto",
        dtype: str | ModelDtype = "auto",
        cache_dir: str | None = None,
        hf_token: str | None = None,
        revision: str | None = None,
        local_files_only: bool | None = None,
        trust_remote_code: bool = False,
        pipeline_kwargs: dict[str, Any] | None = None,
        max_concurrent: int = 1,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
        metrics: GenAIMetrics | None = None,
    ) -> None:
        """Configure the generator (does not load weights yet).

        Args:
            model_id (str): HuggingFace model id of an AudioLDM-family
                pipeline (``cvssp/audioldm-s-full-v2``,
                ``ucsd-reach/musicldm``).
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``
                (bf16 on GPU, fp32 on CPU).
            cache_dir (str | None): Where weights are downloaded and read
                back; ``None`` uses the ``huggingface_hub`` default.
            hf_token (str | None): Hub token for gated or private
                repositories; ``None`` falls back to ``HF_TOKEN``.
            revision (str | None): Branch, tag or commit sha to load.
            local_files_only (bool | None): Load from the cache without the
                network; ``None`` takes ``GENAI_OFFLINE`` from the
                environment.
            trust_remote_code (bool): Allow the repository's own Python to
                run at load time.
            pipeline_kwargs (dict[str, Any] | None): Extra keywords for
                ``from_pretrained``; they win over what the SDK computes.
            max_concurrent (int): Simultaneous renders. Defaults to ``1``,
                since one diffusion call already saturates the GPU.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the pipeline after this many
                idle seconds.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests); probed when ``None``.
            metrics (GenAIMetrics | None): Optional Prometheus metrics;
                :meth:`generate` records request count and latency under op
                ``"audio"``.

        Raises:
            ValueError: When ``max_concurrent`` is not positive.
        """
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        self.model_id = model_id
        self.device = resolve_device(device, hardware)
        self.dtype = (
            ModelDtype(auto_dtype_name(self.device))
            if dtype == "auto"
            else ModelDtype(dtype)
        )
        self.source = ModelRef(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            token=hf_token,
            local_files_only=local_files_only,
            trust_remote_code=trust_remote_code,
        )
        self.pipeline_kwargs: dict[str, Any] = dict(pipeline_kwargs or {})
        self.idle_unload_seconds = idle_unload_seconds
        self.metrics = metrics
        self._pipeline: Any = None
        self._sample_rate: int = 0
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._pipeline is not None,
        )

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the pipeline is in memory.

        Returns:
            bool: Whether :meth:`load` has run without a later
            :meth:`unload`.
        """
        return self._pipeline is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the pipeline was last in use.

        Returns:
            float: Idle time in seconds; ``0.0`` while a render runs.
        """
        return self._lifecycle.seconds_idle()

    @property
    def pipeline(self) -> Any:
        """Return the underlying diffusers pipeline (escape hatch).

        Loads on first access, which blocks for the download and build.

        Returns:
            Any: The loaded pipeline.
        """
        self.load()
        return self._pipeline

    def load(self) -> None:
        """Download (if needed) and load the pipeline.

        Idempotent, and safe to call from several threads at once.

        Raises:
            ImportError: When the extras are missing.
            ValueError: When the model is not a supported AudioLDM-family
                pipeline (see :func:`check_audio_pipeline`).
        """
        self._lifecycle.load()

    def _build(self) -> None:
        """Build and validate the pipeline; called once, under the load lock.

        The class-level checks run before the move to the device, so a
        pipeline refused by them never occupies VRAM. The text-encoder probe
        needs the device, and a refusal there frees the memory again.

        Raises:
            ImportError: When the extras are missing.
            ValueError: When the model is not supported.
        """
        torch, diffusers = _require_diffusers()
        kwargs: dict[str, Any] = {
            "torch_dtype": getattr(torch, self.dtype.value),
            **self.source.loader_kwargs(),
            **self.pipeline_kwargs,
        }
        pipeline = diffusers.DiffusionPipeline.from_pretrained(
            self.model_id,
            **kwargs,
        )
        check_audio_pipeline(pipeline)
        sample_rate = pipeline_sample_rate(pipeline)
        pipeline = pipeline.to(self.device)
        try:
            check_text_features(pipeline, torch)
        except ValueError:
            del pipeline
            self._release()
            raise
        self._sample_rate = sample_rate
        self._pipeline = pipeline

    def unload(self) -> None:
        """Free the pipeline and its memory; waits for renders in flight."""
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop the pipeline and return cached CUDA memory."""
        self._pipeline = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def unload_if_idle(self) -> bool:
        """Free the pipeline when it has been idle past the threshold.

        Returns:
            bool: ``True`` when this call unloaded the pipeline.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

    def _run_sync(self, kwargs: dict[str, Any], seed: int) -> list[GeneratedAudio]:
        """Load if needed, render, and encode every waveform.

        Runs on the worker thread, inside the lifecycle's ``use()`` block so
        an unload cannot free the pipeline mid-render.

        Args:
            kwargs (dict[str, Any]): Pipeline keywords, already merged.
            seed (int): The seed to bind to the torch generator.

        Returns:
            list[GeneratedAudio]: One entry per generated waveform.
        """
        with self._lifecycle.use():
            torch, _ = _require_diffusers()
            generator = torch.Generator(device=self.device).manual_seed(seed)
            result = self._pipeline(generator=generator, output_type="np", **kwargs)
            clips: list[GeneratedAudio] = []
            for waveform in result.audios:
                data, channels, seconds = encode_wav(waveform, self._sample_rate)
                clips.append(
                    GeneratedAudio(
                        data=data,
                        sample_rate=self._sample_rate,
                        channels=channels,
                        seconds=seconds,
                        seed=seed,
                    ),
                )
            return clips

    async def _render(self, kwargs: dict[str, Any], seed: int) -> list[GeneratedAudio]:
        """Run the blocking pipeline off the event loop, one at a time.

        Args:
            kwargs (dict[str, Any]): Pipeline keywords, already merged.
            seed (int): The seed for this render.

        Returns:
            list[GeneratedAudio]: The encoded clips.
        """
        async with self._semaphore:
            return await asyncio.to_thread(self._run_sync, kwargs, seed)

    async def generate(
        self,
        prompt: str,
        *,
        config: AudioGenerationConfig | None = None,
    ) -> list[GeneratedAudio]:
        """Generate one or more clips from a text prompt.

        Args:
            prompt (str): The sound or music to generate.
            config (AudioGenerationConfig | None): Length, steps, guidance,
                seed and count. Unset fields fall through to the model's
                defaults.

        Returns:
            list[GeneratedAudio]: The clips, each a WAV carrying the seed
            that reproduces it. Without a seed in ``config`` one is drawn,
            so every clip is reproducible.

        Raises:
            ImportError: When the extras are missing.
            ValueError: When the model is not supported.
        """
        seed = (
            config.seed
            if config is not None and config.seed is not None
            else secrets.randbelow(_MAX_SEED)
        )
        kwargs: dict[str, Any] = {"prompt": prompt}
        if config is not None:
            kwargs.update(config.to_pipeline_kwargs())
        async with genai_span("audio", self.model_id):
            if self.metrics is None:
                return await self._render(kwargs, seed)
            async with self.metrics.track(self.model_id, "audio"):
                return await self._render(kwargs, seed)


__all__: list[str] = [
    "AUDIO_LENGTH_KEYWORD",
    "AudioGenerator",
    "check_audio_pipeline",
    "check_text_features",
    "encode_wav",
    "pipeline_sample_rate",
]
