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

from tempest_fastapi_sdk.genai.audio.language import (
    Language,
    preset_for,
    tts_language,
)
from tempest_fastapi_sdk.genai.audio.stt import resolve_audio_device

if TYPE_CHECKING:
    from pathlib import Path


def _require_tts() -> Any:
    """Import Coqui ``TTS``, keeping the reason the import failed.

    Returns:
        Any: The ``TTS.api.TTS`` class.

    Raises:
        ImportError: When Coqui TTS cannot be imported. The original
            message is quoted, because it is the one that names the fix.

    ``except ImportError`` catches far more than a missing extra: a missing
    torchaudio, a missing torchcodec and an incompatible transformers all
    arrive here carrying the sentence that says what to install. Replacing
    it with "install [genai-audio]" answered every one of them with the one
    instruction that cannot help — the extra is already installed. That cost
    a consumer a diagnosis, so the original message is quoted instead.
    """
    try:
        from TTS.api import TTS
    except ImportError as exc:
        raise ImportError(
            f"Text-to-speech could not import Coqui TTS: {exc}. "
            "The [genai-audio] extra installs coqui-tts together with torch, "
            "torchaudio, torchcodec and transformers<5; if any of those is "
            "missing or mismatched in this environment, reinstall the extra: "
            "pip install 'tempest-fastapi-sdk[genai-audio]'",
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

    On a server, set ``COQUI_TOS_AGREED=1`` before the first synthesis. The
    default model (XTTS v2) is licence-gated, and read in coqui-tts 0.27.5,
    ``ModelManager.tos_agreed`` accepts only a ``tos_agreed.txt`` beside the
    weights or that variable set to the **string** ``"1"``; otherwise
    ``ask_tos`` calls :func:`input`. Synthesis runs in
    ``asyncio.to_thread``, where there is no tty to answer it, so a fresh
    install has nothing to type into. ``COQUI_TOS_AGREED=true`` does not
    count — the comparison is against ``"1"``.
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

    @classmethod
    def for_language(
        cls,
        language: Language,
        *,
        device: str = "auto",
        max_concurrent: int = 2,
    ) -> TextToSpeech:
        """Build a voice with the default TTS model for ``language``.

        Picks a good Coqui model per language (see
        :func:`~tempest_fastapi_sdk.genai.audio.preset_for`) so you don't
        hand-pick a model id.

        Args:
            language (Language): ``Language.PT_BR`` or ``Language.EN_US``.
            device (str): ``"auto"`` / ``"cuda"`` / ``"cpu"``.
            max_concurrent (int): Max simultaneous syntheses.

        Returns:
            TextToSpeech: A voice configured for the language.
        """
        return cls(
            preset_for(language).tts_model,
            device=device,
            max_concurrent=max_concurrent,
        )

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
        language: Language | str | None = None,
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
            language (Language | str | None): Language for multilingual
                models — a :class:`~tempest_fastapi_sdk.genai.audio.Language`
                member, a raw code (``"pt"``), or ``None``.
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
                tts_language(language),
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
