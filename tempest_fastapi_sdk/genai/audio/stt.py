"""Speech-to-text on your own hardware, via faster-whisper.

`SpeechToText` interprets audio into text with faster-whisper (a
CTranslate2 reimplementation of OpenAI Whisper — fast on CPU and GPU).
The model loads once and is reused; each transcription runs in a worker
thread (``asyncio.to_thread``) and concurrent calls are serialized through
a semaphore to bound memory. Mirrors the leviathan STT service.

Loading is guarded by a thread lock, not an ``asyncio`` one:
:meth:`SpeechToText.load` runs **inside** the worker thread, so the
primitive that has to exclude a second caller is a thread primitive. The
semaphore does not cover this — it admits ``max_concurrent`` callers, and
two of them arriving on a cold instance both read ``is_loaded`` as False
and both build a model, doubling peak memory for the lifetime of the
process. The lock and the in-flight count live in the lifecycle helper the
other loaders share, so an idle unload also never frees the model under a
transcription that is still decoding.

``faster_whisper`` / ``torch`` import lazily, so the module and its device
helpers import without the ``[genai-audio]`` extra.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.audio.language import Language, whisper_language
from tempest_fastapi_sdk.genai.audio.schemas import Transcription, TranscriptionSegment
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_LOGGER: logging.Logger = logging.getLogger(__name__)


def resolve_audio_device(device: str) -> str:
    """Resolve ``"auto"`` to ``"cuda"`` when a GPU is present, else ``"cpu"``.

    faster-whisper targets CUDA or CPU (no MPS), so anything non-CUDA
    resolves to ``"cpu"``.

    Args:
        device (str): ``"auto"``, ``"cuda"`` or ``"cpu"``.

    Returns:
        str: The concrete device.

    Detection needs torch, and a missing torch is logged at warning level
    rather than folded into "no GPU". They are not the same answer: a
    machine with a GPU whose environment has no torch used to transcribe on
    the CPU with no sign at all, and the slowdown looked like faster-whisper
    being slow.
    """
    if device != "auto":
        return device
    try:
        import torch
    except ImportError:
        _LOGGER.warning(
            "device='auto' resolved to 'cpu' because torch is not installed, "
            "so a GPU cannot be detected. Install the [genai-audio] extra to "
            "get torch, or pass device='cuda' explicitly.",
        )
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def resolve_compute_type(compute_type: str, device: str) -> str:
    """Pick a sensible faster-whisper compute type for ``device``.

    Args:
        compute_type (str): Explicit type, or ``"auto"``.
        device (str): The resolved device.

    Returns:
        str: ``float16`` on CUDA, ``int8`` on CPU when ``"auto"``; else the
        value as given.
    """
    if compute_type != "auto":
        return compute_type
    return "float16" if device == "cuda" else "int8"


def _require_faster_whisper() -> Any:
    """Import ``faster_whisper``, keeping the reason the import failed.

    Returns:
        Any: The ``faster_whisper`` module.

    Raises:
        ImportError: When faster-whisper cannot be imported. The original
            message is quoted, for the same reason as in
            :func:`tempest_fastapi_sdk.genai.audio.tts._require_tts`.
    """
    try:
        import faster_whisper
    except ImportError as exc:
        raise ImportError(
            f"Speech-to-text could not import faster-whisper: {exc}. "
            "The [genai-audio] extra installs it; if the import still fails, "
            "the environment is missing part of its runtime: "
            "pip install 'tempest-fastapi-sdk[genai-audio]'",
        ) from exc
    return faster_whisper


class SpeechToText:
    """A lazily-loaded faster-whisper transcriber.

    Example:

        >>> stt = SpeechToText("base", device="auto")
        >>> result = await stt.transcribe("meeting.wav")
        >>> print(result.text, result.language)

    Attributes:
        model_size (str): Whisper size/name (``tiny``…``large-v3`` or a
            path).
        device (str): Resolved device (``cuda`` / ``cpu``).
        compute_type (str): Resolved faster-whisper compute type.
    """

    def __init__(
        self,
        model_size: str = "base",
        *,
        device: str = "auto",
        compute_type: str = "auto",
        max_concurrent: int = 2,
        cache_dir: str | None = None,
        revision: str | None = None,
        local_files_only: bool | None = None,
        hf_token: str | None = None,
        idle_unload_seconds: float | None = None,
        beam_size: int = 5,
        vad_filter: bool = True,
        batch_size: int | None = None,
        cpu_threads: int = 0,
        num_workers: int = 1,
        condition_on_previous_text: bool = True,
    ) -> None:
        """Configure the transcriber (does not load weights yet).

        faster-whisper names the same three Hub knobs differently
        (``download_root`` / ``use_auth_token``), so they are mapped here
        rather than splatted from a
        :class:`~tempest_fastapi_sdk.genai.ModelRef`. ``trust_remote_code``
        has no counterpart: CTranslate2 loads weights, never repository
        Python.

        Args:
            model_size (str): Whisper size/name or a local path.
            device (str): ``"auto"`` / ``"cuda"`` / ``"cpu"``.
            compute_type (str): faster-whisper compute type or ``"auto"``.
            max_concurrent (int): Max simultaneous transcriptions.
            cache_dir (str | None): Where the downloaded weights are
                written and read back from. ``None`` uses the
                ``huggingface_hub`` default — ``$HF_HOME/hub``, or
                ``~/.cache/huggingface/hub`` when ``HF_HOME`` is unset —
                which is why the second run of a script starts instantly
                instead of downloading again. Point it at a mounted
                volume when the process is a container, so the layer does
                not re-download the model on every restart.
            revision (str | None): Branch, tag or commit sha to load;
                ``None`` follows the moving Hub default.
            local_files_only (bool | None): Load from the cache without
                touching the network — what an air-gapped or deploy-frozen
                host wants. ``None`` (the default) takes ``GENAI_OFFLINE``
                from the environment; passing the argument overrides it.
            hf_token (str | None): Hub token for gated or private
                repositories. ``None`` falls back to ``HF_TOKEN`` in the
                environment; without either, anonymous downloads work but
                are rate-limited (the Hub says so on stderr).
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the model after this many idle
                seconds.
            beam_size (int): Beam width for decoding — higher is more
                accurate but slower. Overridable per call.
            vad_filter (bool): Drop non-speech with faster-whisper's voice
                activity detection before decoding. Overridable per call.
            batch_size (int | None): Decode this many VAD-detected speech
                spans in parallel through faster-whisper's
                ``BatchedInferencePipeline`` instead of one after another.
                ``None`` (the default) keeps the sequential path. Same model
                and same weights either way — only the scheduling of the
                decode changes — at the cost of peak memory proportional to
                the value. Requires ``vad_filter``: it is the VAD that cuts
                the audio into the spans a batch is made of.
            cpu_threads (int): OpenMP threads per matrix op inside
                CTranslate2 (its ``intra_threads``). ``0`` lets CTranslate2
                decide, which is faster-whisper's own default.
            num_workers (int): Parallel translations inside one model
                (CTranslate2's ``inter_threads``). Only matters when several
                ``transcribe`` calls share the instance; ``1`` is right when
                a worker handles one audio at a time.
            condition_on_previous_text (bool): Feed each window the previous
                window's text as context. ``True`` is faster-whisper's
                default and is kept here so upgrading does not silently
                change anybody's transcripts. Turn it **off** when batching:
                it serializes spans that would otherwise decode in parallel,
                and it is the documented path by which one bad span
                contaminates the ones after it (the repetition loop).

        Raises:
            ValueError: When ``max_concurrent`` is not positive, when
                ``batch_size`` is not positive, or when ``batch_size`` is
                set with ``vad_filter`` off.
        """
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        if batch_size is not None:
            if batch_size <= 0:
                raise ValueError("batch_size must be positive")
            if not vad_filter:
                raise ValueError(
                    "batch_size requires vad_filter=True: batched decoding "
                    "consumes the speech spans the VAD produces",
                )
        self.model_size = model_size
        self.device = resolve_audio_device(device)
        self.compute_type = resolve_compute_type(compute_type, self.device)
        self.cache_dir = cache_dir
        self.revision = revision
        self.local_files_only = local_files_only
        self.hf_token = hf_token
        self.idle_unload_seconds = idle_unload_seconds
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.batch_size = batch_size
        self.cpu_threads = cpu_threads
        self.num_workers = num_workers
        self.condition_on_previous_text = condition_on_previous_text
        self._model: Any = None
        self._pipeline: Any = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._model is not None,
        )

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the model is in memory."""
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the model was last in use.

        Reads ``0.0`` while a transcription is in flight.

        Returns:
            float: Idle time in seconds.
        """
        return self._lifecycle.seconds_idle()

    def unload_if_idle(self) -> bool:
        """Free the model when it has been idle past the threshold.

        Returns:
            bool: ``True`` when this call unloaded the model, ``False``
            when it was already free, still in use, or no
            ``idle_unload_seconds`` was configured.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

    def load(self) -> None:
        """Download (if needed) and load the Whisper model. Idempotent.

        Safe to call from several threads at once: the second caller blocks
        on the load lock and then sees the model the first one built, rather
        than building a second copy.

        The ``is_loaded`` test is inside the lock, not repeated outside it as
        a fast path. Every call therefore pays the lifecycle's bookkeeping —
        the load lock plus the in-flight counter — measured at ~1.6 µs per
        call on a loaded model (CPython 3.11, 1M iterations, development
        machine), against a transcription measured in seconds.
        Double-checked locking would buy nothing at that price and is the
        shape this bug hid in once already.

        Raises:
            ImportError: When the ``[genai-audio]`` extra is missing.
        """
        self._lifecycle.load()

    def _build(self) -> None:
        """Construct the Whisper model; called once, under the load lock.

        Raises:
            ImportError: When the ``[genai-audio]`` extra is missing.
        """
        faster_whisper = _require_faster_whisper()
        model = faster_whisper.WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
            num_workers=self.num_workers,
            download_root=self.cache_dir,
            revision=self.revision,
            local_files_only=self.local_files_only,
            use_auth_token=self.hf_token,
        )
        if self.batch_size is not None:
            self._pipeline = faster_whisper.BatchedInferencePipeline(model=model)
        self._model = model

    def unload(self) -> None:
        """Free the model. Safe when not loaded.

        While a transcription is in flight the release waits for it: the
        last call to finish drops the model.
        """
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop the model and its batched pipeline."""
        self._pipeline = None
        self._model = None

    async def transcribe(
        self,
        audio: str | Path | bytes,
        *,
        language: Language | str | None = None,
        with_segments: bool = True,
        beam_size: int | None = None,
        vad_filter: bool | None = None,
        on_progress: Callable[[float, float], None] | None = None,
    ) -> Transcription:
        """Transcribe ``audio`` into text.

        Runs the blocking model in a worker thread, capped by the
        concurrency semaphore.

        Args:
            audio (str | Path | bytes): Audio file path or raw bytes.
            language (Language | str | None): Force the language — a
                :class:`~tempest_fastapi_sdk.genai.audio.Language` member
                (``Language.PT_BR``), a raw Whisper code (``"pt"``), or
                ``None`` to auto-detect.
            with_segments (bool): Include per-span timestamps.
            beam_size (int | None): Override the instance beam width for
                this call; ``None`` uses the configured default.
            vad_filter (bool | None): Override the instance VAD setting for
                this call; ``None`` uses the configured default.
            on_progress (Callable[[float, float], None] | None): Called as
                the decode advances, with ``(seconds_done, total_seconds)``.
                faster-whisper hands back a generator, so a long file
                otherwise spends minutes indistinguishable from a hang; this
                is what a caller turns into a log line or a progress bar.
                **It runs on the worker thread**, so it must not touch the
                event loop — use ``loop.call_soon_threadsafe`` if it has to.

        Returns:
            Transcription: The transcript, language (+ probability),
            duration and segments.
        """
        async with self._semaphore:
            result = await asyncio.to_thread(
                self._transcribe_sync,
                audio,
                whisper_language(language),
                with_segments,
                self.beam_size if beam_size is None else beam_size,
                self.vad_filter if vad_filter is None else vad_filter,
                on_progress,
            )
        return result

    def _transcribe_sync(
        self,
        audio: str | Path | bytes,
        language: str | None,
        with_segments: bool,
        beam_size: int,
        vad_filter: bool,
        on_progress: Callable[[float, float], None] | None = None,
    ) -> Transcription:
        """Blocking transcription; assembles a :class:`Transcription`.

        Runs inside the lifecycle's ``use()`` block: the segments come out
        of a lazy generator, so the model has to stay resident until the
        last one is decoded, not only until ``transcribe`` returns.
        """
        with self._lifecycle.use():
            return self._decode(
                audio,
                language,
                with_segments,
                beam_size,
                vad_filter,
                on_progress,
            )

    def _decode(
        self,
        audio: str | Path | bytes,
        language: str | None,
        with_segments: bool,
        beam_size: int,
        vad_filter: bool,
        on_progress: Callable[[float, float], None] | None,
    ) -> Transcription:
        """Run the loaded engine and collect its segments.

        Args:
            audio (str | Path | bytes): Audio file path or raw bytes.
            language (str | None): Whisper language code, or ``None``.
            with_segments (bool): Include per-span timestamps.
            beam_size (int): Beam width.
            vad_filter (bool): Whether to apply VAD.
            on_progress (Callable[[float, float], None] | None): Progress
                callback, run on this thread.

        Returns:
            Transcription: The assembled transcript.
        """
        import io

        source: Any = io.BytesIO(audio) if isinstance(audio, bytes) else str(audio)
        engine = self._pipeline if self._pipeline is not None else self._model
        options: dict[str, Any] = {
            "language": language,
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "condition_on_previous_text": self.condition_on_previous_text,
        }
        if self._pipeline is not None:
            options["batch_size"] = self.batch_size
        segments_iter, info = engine.transcribe(source, **options)
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        segments: list[TranscriptionSegment] = []
        texts: list[str] = []
        for segment in segments_iter:
            texts.append(segment.text)
            if with_segments:
                segments.append(
                    TranscriptionSegment(
                        start=float(segment.start),
                        end=float(segment.end),
                        text=segment.text,
                    ),
                )
            if on_progress is not None:
                on_progress(float(segment.end), duration)
        return Transcription(
            text="".join(texts).strip(),
            language=getattr(info, "language", "") or "",
            language_probability=float(
                getattr(info, "language_probability", 0.0) or 0.0,
            ),
            duration=duration,
            segments=segments,
        )


__all__: list[str] = [
    "SpeechToText",
    "resolve_audio_device",
    "resolve_compute_type",
]
