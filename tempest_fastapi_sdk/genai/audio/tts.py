"""Text-to-speech on your own hardware, via Coqui TTS.

`TextToSpeech` generates audio from text with Coqui TTS. The model loads
once and is reused; each synthesis runs in a worker thread
(``asyncio.to_thread``) and concurrent calls are serialized through a
semaphore. Mirrors the leviathan TTS service.

``TTS`` / ``torch`` import lazily, so the module and its device helper
import without the ``[genai-audio]`` extra.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.audio.stt import resolve_audio_device

if TYPE_CHECKING:
    from pathlib import Path


def _require_tts() -> Any:
    """Import Coqui ``TTS`` or raise a helpful error.

    Returns:
        Any: The ``TTS.api.TTS`` class.

    Raises:
        ImportError: When the ``[genai-audio]`` extra is missing.
    """
    try:
        from TTS.api import TTS
    except ImportError as exc:
        raise ImportError(
            "Text-to-speech requires the optional [genai-audio] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-audio]",
        ) from exc
    return TTS


class TextToSpeech:
    """A lazily-loaded Coqui TTS voice.

    Example:

        >>> tts = TextToSpeech("tts_models/multilingual/multi-dataset/xtts_v2")
        >>> wav = await tts.synthesize("Olá, mundo.", language="pt")
        >>> Path("hello.wav").write_bytes(wav)

    Attributes:
        model_name (str): The Coqui model id.
        device (str): Resolved device (``cuda`` / ``cpu``).
    """

    def __init__(
        self,
        model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
        *,
        device: str = "auto",
        max_concurrent: int = 2,
    ) -> None:
        """Configure the voice (does not load weights yet).

        Args:
            model_name (str): Coqui TTS model id.
            device (str): ``"auto"`` / ``"cuda"`` / ``"cpu"``.
            max_concurrent (int): Max simultaneous syntheses.

        Raises:
            ValueError: When ``max_concurrent`` is not positive.
        """
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        self.model_name = model_name
        self.device = resolve_audio_device(device)
        self._tts: Any = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the model is in memory."""
        return self._tts is not None

    def load(self) -> None:  # pragma: no cover - needs Coqui TTS + a model
        """Download (if needed) and load the TTS model. Idempotent.

        Raises:
            ImportError: When the ``[genai-audio]`` extra is missing.
        """
        if self.is_loaded:
            return
        tts_cls = _require_tts()
        self._tts = tts_cls(model_name=self.model_name).to(self.device)

    def unload(self) -> None:
        """Free the model. Safe when not loaded."""
        self._tts = None

    async def synthesize(
        self,
        text: str,
        *,
        out_path: str | Path | None = None,
        speaker: str | None = None,
        language: str | None = None,
        speaker_wav: str | Path | None = None,
    ) -> bytes:
        """Generate speech audio (WAV) from ``text``.

        Runs the blocking model in a worker thread, capped by the
        concurrency semaphore.

        Args:
            text (str): The text to speak.
            out_path (str | Path | None): When given, also write the WAV
                there; the bytes are returned either way.
            speaker (str | None): Speaker name for multi-speaker models.
            language (str | None): Language code for multilingual models.
            speaker_wav (str | Path | None): Reference clip for voice
                cloning (XTTS-style models).

        Returns:
            bytes: The synthesized WAV audio.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                self._synthesize_sync,
                text,
                out_path,
                speaker,
                language,
                speaker_wav,
            )

    def _synthesize_sync(  # pragma: no cover - needs Coqui TTS + a model
        self,
        text: str,
        out_path: str | Path | None,
        speaker: str | None,
        language: str | None,
        speaker_wav: str | Path | None,
    ) -> bytes:
        """Blocking synthesis; returns the WAV bytes (writing them once)."""
        import os
        import tempfile
        from pathlib import Path as _Path

        self.load()
        if out_path is not None:
            target = _Path(out_path)
        else:
            handle, name = tempfile.mkstemp(suffix=".wav")
            os.close(handle)
            target = _Path(name)
        self._tts.tts_to_file(
            text=text,
            file_path=str(target),
            speaker=speaker,
            language=language,
            speaker_wav=str(speaker_wav) if speaker_wav is not None else None,
        )
        data = target.read_bytes()
        if out_path is None:
            target.unlink(missing_ok=True)
        return data


__all__: list[str] = [
    "TextToSpeech",
]
