"""Speech-to-text on your own hardware, via faster-whisper.

`SpeechToText` interprets audio into text with faster-whisper (a
CTranslate2 reimplementation of OpenAI Whisper — fast on CPU and GPU).
The model loads once and is reused; each transcription runs in a worker
thread (``asyncio.to_thread``) and concurrent calls are serialized through
a semaphore to bound memory. Mirrors the leviathan STT service.

``faster_whisper`` / ``torch`` import lazily, so the module and its device
helpers import without the ``[genai-audio]`` extra.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.audio.language import Language, whisper_language
from tempest_fastapi_sdk.genai.audio.schemas import Transcription, TranscriptionSegment

if TYPE_CHECKING:
    from pathlib import Path


def resolve_audio_device(device: str) -> str:
    """Resolve ``"auto"`` to ``"cuda"`` when a GPU is present, else ``"cpu"``.

    faster-whisper targets CUDA or CPU (no MPS), so anything non-CUDA
    resolves to ``"cpu"``.

    Args:
        device (str): ``"auto"``, ``"cuda"`` or ``"cpu"``.

    Returns:
        str: The concrete device.
    """
    if device != "auto":
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


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
    """Import ``faster_whisper`` or raise a helpful error.

    Returns:
        Any: The ``faster_whisper`` module.

    Raises:
        ImportError: When the ``[genai-audio]`` extra is missing.
    """
    try:
        import faster_whisper
    except ImportError as exc:
        raise ImportError(
            "Speech-to-text requires the optional [genai-audio] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-audio]",
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
    ) -> None:
        """Configure the transcriber (does not load weights yet).

        Args:
            model_size (str): Whisper size/name or a local path.
            device (str): ``"auto"`` / ``"cuda"`` / ``"cpu"``.
            compute_type (str): faster-whisper compute type or ``"auto"``.
            max_concurrent (int): Max simultaneous transcriptions.
            cache_dir (str | None): Where to cache downloaded weights.

        Raises:
            ValueError: When ``max_concurrent`` is not positive.
        """
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        self.model_size = model_size
        self.device = resolve_audio_device(device)
        self.compute_type = resolve_compute_type(compute_type, self.device)
        self.cache_dir = cache_dir
        self._model: Any = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the model is in memory."""
        return self._model is not None

    def load(self) -> None:  # pragma: no cover - needs faster-whisper + a model
        """Download (if needed) and load the Whisper model. Idempotent.

        Raises:
            ImportError: When the ``[genai-audio]`` extra is missing.
        """
        if self.is_loaded:
            return
        faster_whisper = _require_faster_whisper()
        self._model = faster_whisper.WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.cache_dir,
        )

    def unload(self) -> None:
        """Free the model. Safe when not loaded."""
        self._model = None

    async def transcribe(
        self,
        audio: str | Path | bytes,
        *,
        language: Language | str | None = None,
        with_segments: bool = True,
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

        Returns:
            Transcription: The transcript, language, duration and segments.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                self._transcribe_sync,
                audio,
                whisper_language(language),
                with_segments,
            )

    def _transcribe_sync(  # pragma: no cover - needs faster-whisper + a model
        self,
        audio: str | Path | bytes,
        language: str | None,
        with_segments: bool,
    ) -> Transcription:
        """Blocking transcription; assembles a :class:`Transcription`."""
        import io

        self.load()
        source: Any = io.BytesIO(audio) if isinstance(audio, bytes) else str(audio)
        segments_iter, info = self._model.transcribe(source, language=language)
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
        return Transcription(
            text="".join(texts).strip(),
            language=getattr(info, "language", "") or "",
            duration=float(getattr(info, "duration", 0.0) or 0.0),
            segments=segments,
        )


__all__: list[str] = [
    "SpeechToText",
    "resolve_audio_device",
    "resolve_compute_type",
]
