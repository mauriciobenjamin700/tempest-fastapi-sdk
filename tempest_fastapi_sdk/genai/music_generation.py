"""Local music generation with MusicGen over HuggingFace transformers.

:class:`MusicGenerator` loads a MusicGen checkpoint
(``facebook/musicgen-small``, ``facebook/musicgen-stereo-small``, ...) and
renders on your own hardware, with the conventions of
:class:`~tempest_fastapi_sdk.genai.AudioGenerator`: lazy load on a worker
thread, idle unload, one render at a time, :class:`GeneratedAudio` WAVs
carrying their seed. It satisfies
:class:`~tempest_fastapi_sdk.genai.SupportsAudioGeneration`, so it backs
``POST /audio`` on :func:`~tempest_fastapi_sdk.genai.make_genai_router`.

MusicGen is autoregressive, not a diffusion model, and that shapes the
config. Measured on an RTX 4070 Ti SUPER with transformers 4.57.6 and 5.22.1
(2026-10-31):

* Length is a token budget: the model emits 50 frames per second, and
  ``n`` new tokens decode to ``(n - 3) * 640`` samples at 32 kHz, so a clip
  comes out 60 ms shorter than asked (5 s requested gave 4.94 s).
  ``seconds`` is converted to ``round(seconds * 50)`` tokens and the
  returned :attr:`GeneratedAudio.seconds` is the real length.
* The decoder has 2048 positions. 2048 tokens rendered 40.9 s; 2049 raised a
  CUDA device-side assert, and that error is sticky -- every later CUDA call
  in the process failed. :meth:`MusicGenerator.generate` refuses a length
  past the window with ``422`` before the model runs.
* ``steps`` and ``negative_prompt`` have no MusicGen equivalent and are
  refused with ``422`` rather than ignored.
* Without ``seconds`` the checkpoint's own default applies: 29.92 s for
  ``musicgen-small`` and ``musicgen-stereo-small`` (``max_length`` 1500).
* The same seed reproduced the same waveform, and the clips of one batched
  call differed from each other. transformers' ``generate`` takes no RNG
  object, so the seed is applied with ``torch.manual_seed``, which seeds the
  process-wide generator.

MusicGen weights are ``cc-by-nc-4.0`` (non-commercial). ``torch`` and
``transformers`` are imported inside the methods that need them, so this
module imports without the ``[genai]`` extra.
"""

from __future__ import annotations

import asyncio
import gc
import secrets
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.genai.audio_generation import encode_wav
from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.schemas import (
    AudioGenerationConfig,
    GeneratedAudio,
    HardwareInfo,
    ModelDtype,
    precision_kwarg,
)
from tempest_fastapi_sdk.genai.text import auto_dtype_name, resolve_device
from tempest_fastapi_sdk.genai.tracing import genai_span
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

_MAX_SEED: int = 2**32 - 1

MAX_MELODY_SECONDS: float = 60.0
"""Longest reference melody ``generate`` decodes.

A clip far longer than the output only costs decode time and memory on the
worker thread, which holds the model while it runs; past this the decode
refuses with ``422`` (``details["max_seconds"]``).
"""

MELODY_MODEL_TYPE: str = "musicgen_melody"
"""``config.model_type`` of the checkpoints that accept a reference melody."""

UNSUPPORTED_FIELDS: tuple[str, ...] = ("steps", "negative_prompt")
"""``AudioGenerationConfig`` fields MusicGen has no equivalent for."""


def _require_transformers() -> tuple[Any, Any]:
    """Import ``torch`` + ``transformers`` or raise a helpful error.

    Returns:
        tuple[Any, Any]: ``(torch, transformers)``.

    Raises:
        ImportError: When the ``[genai]`` extra is not installed.
    """
    try:
        import torch
        import transformers
    except ImportError as exc:
        raise ImportError(
            "Music generation requires the optional [genai] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai]",
        ) from exc
    return torch, transformers


def music_tokens(seconds: float, frame_rate: int) -> int:
    """Convert a clip length into MusicGen's token budget.

    Args:
        seconds (float): Requested length.
        frame_rate (int): Frames per second the audio codec emits (50 for
            every MusicGen checkpoint measured).

    Returns:
        int: ``max_new_tokens`` for ``generate``, at least 1.
    """
    return max(1, round(seconds * frame_rate))


def refuse_unsupported_fields(config: AudioGenerationConfig | None) -> None:
    """Refuse the diffusion-only fields, without needing the model.

    Called before the model loads, so a request MusicGen cannot honour never
    triggers a multi-gigabyte download just to answer ``422``.

    Args:
        config (AudioGenerationConfig | None): The call's config.

    Raises:
        ValidationException: ``422`` with ``details={"field": ...}`` for
            ``steps`` or ``negative_prompt``.
    """
    if config is None:
        return
    for field in UNSUPPORTED_FIELDS:
        if getattr(config, field) is not None:
            raise ValidationException(
                message=f"MusicGen has no {field}; leave it unset",
                details={"field": field},
            )


def check_music_config(
    config: AudioGenerationConfig | None,
    *,
    frame_rate: int,
    max_positions: int,
) -> None:
    """Refuse a config MusicGen cannot honour, before the model runs.

    Args:
        config (AudioGenerationConfig | None): The call's config.
        frame_rate (int): Codec frames per second.
        max_positions (int): The decoder's ``max_position_embeddings``.

    Raises:
        ValidationException: ``422`` with ``details={"field": ...}`` for
            ``steps`` or ``negative_prompt``, and with
            ``details={"field": "seconds", "limit": ...}`` when the token
            budget would pass the decoder window -- which, measured, poisons
            the CUDA context of the whole process.
    """
    refuse_unsupported_fields(config)
    if config is None or config.seconds is None:
        return
    if music_tokens(config.seconds, frame_rate) > max_positions:
        limit = max_positions / frame_rate
        raise ValidationException(
            message=f"seconds exceeds the limit of {limit}",
            details={"field": "seconds", "limit": limit},
        )


class MusicGenerator:
    """A lazily-loaded local MusicGen model with idle unload.

    Example:

        >>> gen = MusicGenerator("facebook/musicgen-small")
        >>> clips = await gen.generate(
        ...     "calm lo-fi hip hop beat with soft piano",
        ...     config=AudioGenerationConfig(seconds=10.0, seed=0),
        ... )
        >>> Path("lofi.wav").write_bytes(clips[0].data)

    Attributes:
        model_id (str): The HuggingFace model id.
        device (str): The resolved device (``cuda`` / ``mps`` / ``cpu``).
        dtype (ModelDtype): The resolved compute precision.
        source (ModelRef): The resolved weight identity.
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
        max_concurrent: int = 1,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
        metrics: GenAIMetrics | None = None,
    ) -> None:
        """Configure the generator (does not load weights yet).

        Args:
            model_id (str): HuggingFace id of a MusicGen checkpoint.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``
                (bf16 on GPU, fp32 on CPU). Measured on the RTX 4070 Ti
                SUPER, 10 s took about 9.2 s in bf16, 9.3 s in fp16 and
                10 s in fp32.
            cache_dir (str | None): Where weights are downloaded and read
                back; ``None`` uses the ``huggingface_hub`` default.
            hf_token (str | None): Hub token; ``None`` falls back to
                ``HF_TOKEN``.
            revision (str | None): Branch, tag or commit sha to load.
            local_files_only (bool | None): Load from the cache without the
                network; ``None`` takes ``GENAI_OFFLINE``.
            max_concurrent (int): Simultaneous renders. Defaults to ``1``;
                it also keeps two calls from interleaving their seeds on the
                process-wide RNG.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the model after this many idle
                seconds.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests); probed when ``None``.
            metrics (GenAIMetrics | None): Optional Prometheus metrics;
                :meth:`generate` records under op ``"audio"``.

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
        )
        self.idle_unload_seconds = idle_unload_seconds
        self.metrics = metrics
        self._model: Any = None
        self._processor: Any = None
        self._sample_rate: int = 0
        self._frame_rate: int = 0
        self._max_positions: int = 0
        self._melody: bool = False
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._model is not None,
        )

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the model is in memory.

        Returns:
            bool: Whether :meth:`load` has run without a later :meth:`unload`.
        """
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the model was last in use.

        Returns:
            float: Idle time in seconds; ``0.0`` while a render runs.
        """
        return self._lifecycle.seconds_idle()

    @property
    def max_seconds(self) -> float:
        """Return the longest clip the decoder window allows.

        Loads the model on first access (it reads the model config), which
        blocks for the whole download and load: from a coroutine, read it
        through ``await asyncio.to_thread(lambda: generator.max_seconds)``
        or after an awaited :meth:`generate`.

        Returns:
            float: ``max_position_embeddings / frame_rate`` (40.96 s for the
            MusicGen checkpoints measured).
        """
        self.load()
        return self._max_positions / self._frame_rate

    def load(self) -> None:
        """Download (if needed) and load the processor and model.

        Idempotent, and safe to call from several threads at once.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
            OSError: When the checkpoint cannot be downloaded or read.
            ValueError: When the checkpoint is not a MusicGen model (raised
                by ``from_pretrained``).
        """
        self._lifecycle.load()

    def _build(self) -> None:
        """Load processor and model; called once, under the load lock.

        The config fields are read before the move to the device, so a
        checkpoint missing them fails without occupying VRAM. A failure of
        the move itself drops the model and collects it before re-raising,
        so its traceback does not pin the memory.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
            OSError: When the checkpoint cannot be downloaded or read.
            ValueError: When the checkpoint is not a MusicGen model.

        The checkpoint config is read once more (``AutoConfig``, from the
        same cache and pinning as the weights) to pick the melody class.
        """
        torch, transformers = _require_transformers()
        loader = self.source.loader_kwargs()
        model_type = transformers.AutoConfig.from_pretrained(
            self.model_id, **loader
        ).model_type
        model_class = (
            transformers.MusicgenMelodyForConditionalGeneration
            if model_type == MELODY_MODEL_TYPE
            else transformers.MusicgenForConditionalGeneration
        )
        processor = transformers.AutoProcessor.from_pretrained(self.model_id, **loader)
        model = model_class.from_pretrained(
            self.model_id,
            **precision_kwarg(getattr(torch, self.dtype.value)),
            **loader,
        )
        config = model.config
        sample_rate = int(config.audio_encoder.sampling_rate)
        frame_rate = int(config.audio_encoder.frame_rate)
        max_positions = int(config.decoder.max_position_embeddings)
        try:
            model = model.to(self.device)
        except BaseException:
            del model
            gc.collect()
            self._release()
            raise
        self._sample_rate = sample_rate
        self._frame_rate = frame_rate
        self._max_positions = max_positions
        self._melody = model_type == MELODY_MODEL_TYPE
        self._processor = processor
        self._model = model

    @property
    def accepts_melody(self) -> bool:
        """Return whether the checkpoint conditions on a reference melody.

        Loads the model on first access, which blocks.

        Returns:
            bool: ``True`` for a ``musicgen_melody`` checkpoint.
        """
        self.load()
        return self._melody

    def unload(self) -> None:
        """Free the model and its memory; waits for renders in flight."""
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop the model and return cached CUDA memory."""
        self._model = None
        self._processor = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def unload_if_idle(self) -> bool:
        """Free the model when it has been idle past the threshold.

        Returns:
            bool: ``True`` when this call unloaded the model.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

    def _run_sync(
        self,
        prompt: str,
        config: AudioGenerationConfig | None,
        seed: int,
        melody: str | Path | bytes | None = None,
    ) -> list[GeneratedAudio]:
        """Load if needed, check the config, render and encode.

        Runs on the worker thread, inside the lifecycle's ``use()`` block.
        The window check needs the loaded config, so it runs here, still
        before the model is called.

        Args:
            prompt (str): The music to generate.
            config (AudioGenerationConfig | None): The call's config.
            seed (int): The seed for ``torch.manual_seed``.
            melody (str | Path | bytes | None): Reference melody, decoded
                here, on the worker thread.

        Returns:
            list[GeneratedAudio]: One entry per generated clip.

        Raises:
            ValidationException: When the config cannot be honoured (see
                :func:`check_music_config`), or a melody is given to a
                checkpoint that does not take one.
        """
        with self._lifecycle.use():
            check_music_config(
                config,
                frame_rate=self._frame_rate,
                max_positions=self._max_positions,
            )
            if melody is not None and not self._melody:
                raise ValidationException(
                    message=(
                        f"{self.model_id} does not take a melody; use a "
                        "musicgen-melody checkpoint"
                    ),
                    details={"field": "melody"},
                )
            torch, _ = _require_transformers()
            count = config.num_waveforms if config is not None else 1
            processor_kwargs: dict[str, Any] = {}
            if melody is not None:
                from tempest_fastapi_sdk.genai.audio.decoding import load_audio

                samples = load_audio(
                    melody,
                    target_rate=self._sample_rate,
                    max_seconds=MAX_MELODY_SECONDS,
                )
                processor_kwargs = {
                    "audio": [samples] * count,
                    "sampling_rate": self._sample_rate,
                }
            inputs = self._processor(
                text=[prompt] * count,
                padding=True,
                return_tensors="pt",
                **processor_kwargs,
            ).to(self.device)
            if "input_features" in inputs:
                inputs["input_features"] = inputs["input_features"].to(
                    self._model.dtype
                )
            kwargs: dict[str, Any] = {"do_sample": True}
            if config is not None and config.seconds is not None:
                kwargs["max_new_tokens"] = music_tokens(
                    config.seconds, self._frame_rate
                )
            if config is not None and config.guidance_scale is not None:
                kwargs["guidance_scale"] = config.guidance_scale
            torch.manual_seed(seed)
            with torch.no_grad():
                audio = self._model.generate(**inputs, **kwargs)
            clips: list[GeneratedAudio] = []
            for waveform in audio.float().cpu().numpy():
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

    async def generate(
        self,
        prompt: str,
        *,
        config: AudioGenerationConfig | None = None,
        melody: str | Path | bytes | None = None,
    ) -> list[GeneratedAudio]:
        """Generate one or more music clips from a text prompt.

        Args:
            prompt (str): The music to generate.
            config (AudioGenerationConfig | None): ``seconds``,
                ``guidance_scale``, ``seed`` and ``num_waveforms``.
                ``steps`` and ``negative_prompt`` are refused.
            melody (str | Path | bytes | None): A reference clip (path or
                bytes) whose melody the music follows; only a
                ``musicgen-melody`` checkpoint takes one. It is decoded with
                :func:`~tempest_fastapi_sdk.genai.audio.load_audio` (a 16-bit
                WAV needs nothing else; other formats need PyAV, from
                ``[genai-audio]``), mixed to mono at the model's rate, and
                its input features are cast to the model's precision -- in
                bf16 they otherwise fail with ``mat1 and mat2 must have the
                same dtype``. A string is always a path, never fetched; a
                missing file is refused before the model loads, and a clip
                longer than :data:`MAX_MELODY_SECONDS` is refused when
                decoded.

        Returns:
            list[GeneratedAudio]: The clips, mono or stereo as the checkpoint
            produces them. Every clip carries the call's seed; with
            ``num_waveforms`` above 1 they differ, so reproducing clip ``i``
            takes the same seed, the same ``num_waveforms`` and index ``i``.

        Reproducibility holds while nothing else draws from torch's
        process-wide RNG during the render: keep ``max_concurrent=1`` and do
        not run other torch sampling in the same process concurrently.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
            ValidationException: When the config cannot be honoured;
                ``steps`` and ``negative_prompt`` are refused before the
                model loads, a melody for a checkpoint without melody
                support after.
            FileNotFoundError: When ``melody`` is a path that does not exist.
        """
        refuse_unsupported_fields(config)
        if (
            melody is not None
            and not isinstance(melody, bytes)
            and not await asyncio.to_thread(Path(melody).is_file)
        ):
            raise FileNotFoundError(
                f"melody must be a local file, got {Path(melody).name!r}",
            )
        seed = (
            config.seed
            if config is not None and config.seed is not None
            else secrets.randbelow(_MAX_SEED)
        )
        async with genai_span("audio", self.model_id):
            if self.metrics is None:
                return await self._render(prompt, config, seed, melody)
            async with self.metrics.track(self.model_id, "audio"):
                return await self._render(prompt, config, seed, melody)

    async def _render(
        self,
        prompt: str,
        config: AudioGenerationConfig | None,
        seed: int,
        melody: str | Path | bytes | None = None,
    ) -> list[GeneratedAudio]:
        """Run the blocking generation off the event loop, one at a time.

        Args:
            prompt (str): The music to generate.
            config (AudioGenerationConfig | None): The call's config.
            seed (int): The seed for this render.
            melody (str | Path | bytes | None): Reference melody, or ``None``.

        Returns:
            list[GeneratedAudio]: The encoded clips.
        """
        async with self._semaphore:
            return await asyncio.to_thread(self._run_sync, prompt, config, seed, melody)


__all__: list[str] = [
    "MAX_MELODY_SECONDS",
    "MELODY_MODEL_TYPE",
    "UNSUPPORTED_FIELDS",
    "MusicGenerator",
    "check_music_config",
    "music_tokens",
    "refuse_unsupported_fields",
]
