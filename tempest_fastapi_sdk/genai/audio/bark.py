"""Local multilingual text-to-speech with Bark over HuggingFace transformers.

:class:`BarkSpeech` runs ``suno/bark-small`` (or ``suno/bark``) with
``transformers.BarkModel``. The weights are MIT-licensed, and the model ships
voice presets for 13 languages, Portuguese included. It satisfies
:class:`~tempest_fastapi_sdk.genai.audio.SpeechSynthesizer`, so it backs
``POST /tts`` on :func:`~tempest_fastapi_sdk.genai.make_genai_router`.

Measured on an RTX 4070 Ti SUPER with ``suno/bark-small`` in fp16, on
transformers 4.57.6 and 5.22.1 alike (2026-10-31):

* One ``generate`` call stops at 13.95 s of audio. Portuguese text of 180
  characters came out whole (12.68 s); 240 characters were cut at 13.95 s.
  :meth:`BarkSpeech.synthesize` therefore splits the text at sentence
  boundaries into pieces of at most ``max_chunk_chars`` (160 by default),
  speaks each, and joins them with a short pause.
* It runs at about half real time: 12.7 s of audio took 24 to 27 s.
* The output is mono at 24 kHz, and the same seed reproduced the same
  samples. ``generate`` takes no RNG object, so the seed goes through
  ``torch.manual_seed`` (process-wide).
* ``processor.speaker_embeddings`` lists 261 presets (``v2/pt_speaker_0`` to
  ``v2/pt_speaker_9`` among them). An unknown preset made the processor
  raise ``FileNotFoundError`` with a file path; here it answers ``422``.
* The weights are a pickle (``pytorch_model.bin``); torch 2.14 loads it with
  ``weights_only=True``.

``torch`` and ``transformers`` are imported inside the methods that need
them, so this module imports without the ``[genai]`` extra.
"""

from __future__ import annotations

import asyncio
import gc
import re
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.genai.audio.language import Language
from tempest_fastapi_sdk.genai.audio_generation import encode_wav
from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.schemas import HardwareInfo, ModelDtype, precision_kwarg
from tempest_fastapi_sdk.genai.text import auto_dtype_name, resolve_device
from tempest_fastapi_sdk.genai.tracing import genai_span
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

DEFAULT_BARK_MODEL: str = "suno/bark-small"
"""The checkpoint measured for this module."""

DEFAULT_MAX_CHUNK_CHARS: int = 160
"""Largest piece sent to one ``generate`` call.

Measured: 180 Portuguese characters fit under the 13.95 s ceiling and 240
did not; 160 leaves room for slower speech.
"""

_SENTENCE_END = re.compile(r"(?<=[.!?…;:])\s+")
_CLAUSE_END = re.compile(r"(?<=[,])\s+")


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
            "Bark speech requires the optional [genai] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai]",
        ) from exc
    return torch, transformers


def _split_long(piece: str, max_chars: int) -> list[str]:
    """Split one over-long sentence at commas, then at spaces.

    Args:
        piece (str): A sentence longer than ``max_chars``.
        max_chars (int): The largest piece allowed.

    Returns:
        list[str]: Pieces of at most ``max_chars`` characters (a single word
        longer than that is kept whole rather than cut mid-word).
    """
    parts: list[str] = []
    current = ""
    words = [word for clause in _CLAUSE_END.split(piece) for word in clause.split(" ")]
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            parts.append(current)
            current = word
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def split_for_bark(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> list[str]:
    """Split text into pieces Bark speaks without cutting.

    Sentences are kept together and grouped greedily up to ``max_chars``;
    a sentence longer than that is split at commas, then at spaces.

    Args:
        text (str): The text to speak.
        max_chars (int): The largest piece allowed.

    Returns:
        list[str]: The pieces, in order; empty for blank text.

    Raises:
        ValueError: When ``max_chars`` is not positive.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    sentences = [
        s.strip() for s in _SENTENCE_END.split(" ".join(text.split())) if s.strip()
    ]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        for part in (
            [sentence]
            if len(sentence) <= max_chars
            else _split_long(sentence, max_chars)
        ):
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > max_chars:
                pieces.append(current)
                current = part
            else:
                current = candidate
    if current:
        pieces.append(current)
    return pieces


def bark_language_code(language: Language | str) -> str:
    """Map a language to the code Bark's preset names use.

    Args:
        language (Language | str): A :class:`Language` member, or a code
            such as ``"pt"``, ``"pt-BR"`` or ``"en_US"``.

    Returns:
        str: The lowercase two-letter code (``"pt"``).
    """
    raw = language.value if isinstance(language, Language) else language
    return re.split(r"[-_]", raw.strip())[0].lower()


class BarkSpeech:
    """A lazily-loaded local Bark model that speaks long text in pieces.

    Example:

        >>> speech = BarkSpeech()
        >>> wav = await speech.synthesize(
        ...     "Bom dia! O ônibus das oito está atrasado.",
        ...     language=Language.PT_BR,
        ... )

    Attributes:
        model_id (str): The HuggingFace model id.
        device (str): The resolved device.
        dtype (ModelDtype): The resolved compute precision.
        source (ModelRef): The resolved weight identity.
        max_chunk_chars (int): Largest piece sent to one ``generate`` call.
        pause_seconds (float): Silence inserted between pieces.
        idle_unload_seconds (float | None): Idle threshold used by
            :meth:`unload_if_idle`.
    """

    def __init__(
        self,
        model_id: str = DEFAULT_BARK_MODEL,
        *,
        device: str = "auto",
        dtype: str | ModelDtype = "auto",
        cache_dir: str | None = None,
        hf_token: str | None = None,
        revision: str | None = None,
        local_files_only: bool | None = None,
        max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
        pause_seconds: float = 0.25,
        max_concurrent: int = 1,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
        metrics: GenAIMetrics | None = None,
    ) -> None:
        """Configure the synthesizer (does not load weights yet).

        Args:
            model_id (str): ``"suno/bark-small"`` (measured) or
                ``"suno/bark"``.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``.
            cache_dir (str | None): Where weights are downloaded and read.
            hf_token (str | None): Hub token; ``None`` falls back to
                ``HF_TOKEN``.
            revision (str | None): Branch, tag or commit sha to load.
            local_files_only (bool | None): Load from the cache without the
                network; ``None`` takes ``GENAI_OFFLINE``.
            max_chunk_chars (int): Largest piece per ``generate`` call; see
                :data:`DEFAULT_MAX_CHUNK_CHARS`.
            pause_seconds (float): Silence between pieces.
            max_concurrent (int): Simultaneous syntheses. Defaults to ``1``,
                which also keeps seeds from interleaving on the
                process-wide RNG.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the model after this many idle
                seconds.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests).
            metrics (GenAIMetrics | None): Optional Prometheus metrics,
                recorded under op ``"tts"``.

        Raises:
            ValueError: When ``max_concurrent`` or ``max_chunk_chars`` is not
                positive, or ``pause_seconds`` is negative.
        """
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        if max_chunk_chars <= 0:
            raise ValueError("max_chunk_chars must be positive")
        if pause_seconds < 0:
            raise ValueError("pause_seconds must not be negative")
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
        self.max_chunk_chars = max_chunk_chars
        self.pause_seconds = pause_seconds
        self.idle_unload_seconds = idle_unload_seconds
        self.metrics = metrics
        self._model: Any = None
        self._processor: Any = None
        self._sample_rate: int = 0
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

    def load(self) -> None:
        """Download (if needed) and load the processor and model.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
            OSError: When the checkpoint cannot be downloaded or read.
        """
        self._lifecycle.load()

    def _build(self) -> None:
        """Load processor and model; called once, under the load lock.

        The sample rate is read before the move to the device; a failure of
        the move drops and collects the model before re-raising.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        torch, transformers = _require_transformers()
        loader = self.source.loader_kwargs()
        processor = transformers.AutoProcessor.from_pretrained(self.model_id, **loader)
        model = transformers.BarkModel.from_pretrained(
            self.model_id,
            **precision_kwarg(getattr(torch, self.dtype.value)),
            **loader,
        )
        sample_rate = int(model.generation_config.sample_rate)
        try:
            model = model.to(self.device)
        except BaseException:
            del model
            gc.collect()
            self._release()
            raise
        self._sample_rate = sample_rate
        self._processor = processor
        self._model = model

    def unload(self) -> None:
        """Free the model and its memory; waits for syntheses in flight."""
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

    def voices(self) -> list[str]:
        """List the voice presets the checkpoint ships.

        Loads the model on first call, which blocks; from a coroutine, run it
        through ``asyncio.to_thread``.

        Returns:
            list[str]: Preset names such as ``"v2/pt_speaker_1"``, sorted.
        """
        self.load()
        embeddings = getattr(self._processor, "speaker_embeddings", None) or {}
        return sorted(key for key in embeddings if key != "repo_or_path")

    def _resolve_preset(
        self,
        language: Language | str | None,
        speaker: str | None,
    ) -> str | None:
        """Pick the voice preset from ``speaker`` and ``language``.

        Args:
            language (Language | str | None): The requested language; picks
                ``v2/<code>_speaker_0`` when no speaker is given.
            speaker (str | None): An explicit preset name.

        Returns:
            str | None: The preset, or ``None`` for Bark's unconditioned
            voice when neither is given.

        Raises:
            ValidationException: ``422`` when the preset is not shipped, or
                when ``speaker`` belongs to another language than
                ``language``.
        """
        code = bark_language_code(language) if language is not None else None
        preset = speaker
        if preset is None and code is not None:
            preset = f"v2/{code}_speaker_0"
        if preset is None:
            return None
        available = self.voices()
        if preset not in available:
            field = "speaker" if speaker is not None else "language"
            raise ValidationException(
                message=(
                    f"Bark has no voice preset {preset!r}; "
                    "presets look like 'v2/pt_speaker_0'"
                ),
                details={"field": field},
            )
        if speaker is not None and code is not None and f"{code}_" not in speaker:
            raise ValidationException(
                message=f"speaker {speaker!r} is not a {code!r} voice",
                details={"field": "speaker"},
            )
        return preset

    def _synthesize_sync(
        self,
        text: str,
        language: Language | str | None,
        speaker: str | None,
        seed: int | None,
    ) -> bytes:
        """Load if needed, speak every piece and join them into one WAV.

        Args:
            text (str): The text to speak.
            language (Language | str | None): See :meth:`synthesize`.
            speaker (str | None): See :meth:`synthesize`.
            seed (int | None): Seed for ``torch.manual_seed``, or ``None``.

        Returns:
            bytes: The WAV payload.

        Raises:
            ValidationException: When the text is blank or the voice is
                invalid.
        """
        pieces = split_for_bark(text, self.max_chunk_chars)
        if not pieces:
            raise ValidationException(
                message="text must not be blank",
                details={"field": "text"},
            )
        with self._lifecycle.use():
            preset = self._resolve_preset(language, speaker)
            torch, _ = _require_transformers()
            import numpy as np

            if seed is not None:
                torch.manual_seed(seed)
            pause = np.zeros(
                int(self.pause_seconds * self._sample_rate), dtype=np.float32
            )
            segments: list[Any] = []
            for index, piece in enumerate(pieces):
                inputs = self._processor(piece, voice_preset=preset).to(self.device)
                with torch.no_grad():
                    audio = self._model.generate(**inputs)
                if index:
                    segments.append(pause)
                segments.append(audio.float().cpu().numpy().reshape(-1))
            data, _, _ = encode_wav(np.concatenate(segments), self._sample_rate)
            return data

    async def synthesize(
        self,
        text: str,
        /,
        *,
        language: Language | str | None = None,
        speaker: str | None = None,
        seed: int | None = None,
        out_path: str | Path | None = None,
    ) -> bytes:
        """Speak ``text`` and return a mono 24 kHz WAV.

        Text longer than ``max_chunk_chars`` is spoken in sentence-sized
        pieces joined by ``pause_seconds`` of silence, so it is not cut at
        Bark's 13.95 s per-call ceiling.

        Args:
            text (str): The text to speak.
            language (Language | str | None): ``Language.PT_BR``, ``"pt"``,
                ``"pt-BR"``...; picks ``v2/<code>_speaker_0`` when no
                ``speaker`` is given.
            speaker (str | None): A preset from :meth:`voices`, such as
                ``"v2/pt_speaker_1"``.
            seed (int | None): Seed applied with ``torch.manual_seed`` before
                the first piece; the same seed repeats the same audio.
            out_path (str | Path | None): When given, also write the WAV.

        Returns:
            bytes: The WAV payload.

        Raises:
            ValidationException: ``422`` for blank text, an unknown preset,
                or a speaker of another language than ``language``.
            ImportError: When the ``[genai]`` extra is missing.
        """
        async with genai_span("tts", self.model_id):
            if self.metrics is None:
                data = await self._render(text, language, speaker, seed)
            else:
                async with self.metrics.track(self.model_id, "tts"):
                    data = await self._render(text, language, speaker, seed)
        if out_path is not None:
            await asyncio.to_thread(Path(out_path).write_bytes, data)
        return data

    async def _render(
        self,
        text: str,
        language: Language | str | None,
        speaker: str | None,
        seed: int | None,
    ) -> bytes:
        """Run the blocking synthesis off the event loop, one at a time.

        Args:
            text (str): The text to speak.
            language (Language | str | None): See :meth:`synthesize`.
            speaker (str | None): See :meth:`synthesize`.
            seed (int | None): See :meth:`synthesize`.

        Returns:
            bytes: The WAV payload.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                self._synthesize_sync,
                text,
                language,
                speaker,
                seed,
            )


__all__: list[str] = [
    "DEFAULT_BARK_MODEL",
    "DEFAULT_MAX_CHUNK_CHARS",
    "BarkSpeech",
    "bark_language_code",
    "split_for_bark",
]
