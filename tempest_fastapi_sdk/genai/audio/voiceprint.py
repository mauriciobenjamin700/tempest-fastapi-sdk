"""Voice embeddings — turning a recorded voice into a comparable vector.

A voiceprint is a fixed-length vector summarising *how someone sounds*,
not *what they said*. Two recordings of the same person land close
together; two people land far apart. Measured on the reference
recording with the bundled model: the same speaker across two utterances
scored 0.687 cosine similarity, while different speakers scored 0.146
and 0.074.

**This is biometric data.** Under the LGPD a voiceprint is *dado pessoal
sensível* (Art. 5, II), which is why
:class:`~tempest_fastapi_sdk.db.voice_profile_model.BaseVoiceProfileModel`
records consent alongside the vector and why nothing here writes audio to
disk. The embedding is not reversible into speech — you cannot play it
back — but it identifies a person as reliably as a fingerprint template,
so treat it with the same care.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.audio.diarization import (
    DIARIZATION_SAMPLE_RATE,
    EMBEDDING_MODEL,
    ensure_models,
    load_audio,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

EMBEDDING_DIMENSIONS: int = 512
"""Length of the vector the bundled model produces.

Pinned so a stored profile can be validated on read: a row whose vector
is a different length was written by a different model and comparing it
would produce a number that means nothing.
"""

DEFAULT_MATCH_THRESHOLD: float = 0.5
"""Cosine similarity above which a voice is called a match.

Measured on the reference recording: same speaker 0.687, different
speakers 0.146 and 0.074. The gap is wide, so the exact value between
roughly 0.35 and 0.6 changes little — but this is the knob that trades
*letting the wrong person through* against *failing to recognise the
right one*, and which error costs more depends entirely on what the
match is used for. Attributing a line in a meeting transcript can be
generous; anything that grants access should not be.
"""

MIN_ENROLLMENT_SECONDS: float = 3.0
"""Shortest audio accepted for enrolment.

A profile built from one word matches almost anyone. Three seconds is
where the reference measurements were taken and is a common floor for
this class of model; below it the vector describes the phrase more than
the person.
"""


def cosine_similarity(first: Sequence[float], second: Sequence[float]) -> float:
    """Compare two voiceprints.

    Args:
        first (Sequence[float]): A voice embedding.
        second (Sequence[float]): Another voice embedding.

    Returns:
        float: Cosine similarity in ``[-1, 1]``; ``0.0`` when either
        vector is all zeros, which is what an empty recording produces.

    Raises:
        ValueError: When the vectors have different lengths — that means
            two different models, and the comparison would be a number
            with no meaning rather than a low score.
    """
    import numpy as np

    left = np.asarray(first, dtype=np.float32)
    right = np.asarray(second, dtype=np.float32)
    if left.shape != right.shape:
        raise ValueError(
            f"embeddings have different lengths ({left.shape} vs {right.shape}); "
            "they were produced by different models and cannot be compared",
        )
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


class VoiceEmbedder:
    """Extracts voiceprints from audio.

    Loads lazily and runs inference in a worker thread, matching the rest
    of the audio module. Shares the embedding model with
    :class:`~tempest_fastapi_sdk.genai.audio.diarization.SpeakerDiarizer`
    by default, so a service doing both keeps one copy in memory.
    """

    def __init__(
        self,
        *,
        model: str | Path | None = None,
        cache_dir: str | Path | None = None,
        num_threads: int = 1,
        provider: str = "cpu",
        max_concurrent: int = 2,
    ) -> None:
        """Initialize the embedder without loading anything.

        Args:
            model (str | Path | None): Path to the ONNX embedding model.
                ``None`` resolves it from the cache, downloading on first
                use.
            cache_dir (str | Path | None): Where models are cached.
            num_threads (int): ONNX Runtime intra-op threads.
            provider (str): Execution provider.
            max_concurrent (int): Extractions allowed at once.

        Raises:
            ValueError: If ``max_concurrent`` is below 1.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._model = model
        self._cache_dir = cache_dir
        self._num_threads = num_threads
        self._provider = provider
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._extractor: Any | None = None
        self._last_used: float = time.monotonic()

    @property
    def is_loaded(self) -> bool:
        """Whether the model is resident.

        Returns:
            bool: ``True`` once loaded.
        """
        return self._extractor is not None

    @property
    def seconds_idle(self) -> float:
        """Seconds since the last extraction.

        Returns:
            float: Idle time.
        """
        return time.monotonic() - self._last_used

    @property
    def dimensions(self) -> int:
        """Length of the vectors this model produces.

        Returns:
            int: The dimension, loading the model if needed.
        """
        self.load()
        assert self._extractor is not None
        dim: int = self._extractor.dim
        return dim

    def load(self) -> None:
        """Resolve and load the embedding model. Idempotent.

        Raises:
            ImportError: When the ``[genai-diarization]`` extra is absent.
        """
        if self._extractor is not None:
            return
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - extra-gated
            raise ImportError(
                "Voice embeddings need the [genai-diarization] extra: "
                'pip install "tempest-fastapi-sdk[genai-diarization]"',
            ) from exc
        if self._model is None:
            resolved = ensure_models(self._cache_dir, models=(EMBEDDING_MODEL,))
            model_path = resolved[EMBEDDING_MODEL.name]
        else:
            model_path = self._model  # type: ignore[assignment]
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(model_path),
                num_threads=self._num_threads,
                provider=self._provider,
            ),
        )

    def unload(self) -> None:
        """Release the model."""
        self._extractor = None

    async def embed(
        self,
        audio: str | Path | bytes,
        *,
        start: float | None = None,
        end: float | None = None,
    ) -> list[float]:
        """Extract a voiceprint from a recording, or a span of one.

        Args:
            audio (str | Path | bytes): The recording.
            start (float | None): Span start in seconds. ``None`` starts
                at the beginning.
            end (float | None): Span end in seconds. ``None`` runs to the
                end.

        Returns:
            list[float]: The voiceprint.

        Raises:
            ImportError: When the ``[genai-diarization]`` extra is absent.
            ValueError: When the span is empty.
        """
        async with self._semaphore:
            vector = await asyncio.to_thread(self._embed_sync, audio, start, end)
        self._last_used = time.monotonic()
        return vector

    def _embed_sync(
        self,
        audio: str | Path | bytes,
        start: float | None,
        end: float | None,
    ) -> list[float]:
        """Run the extractor. Executes in a worker thread.

        Args:
            audio (str | Path | bytes): The recording.
            start (float | None): Span start in seconds.
            end (float | None): Span end in seconds.

        Returns:
            list[float]: The voiceprint.

        Raises:
            ValueError: When the requested span holds no samples.
        """
        self.load()
        samples = load_audio(audio, target_rate=DIARIZATION_SAMPLE_RATE)
        if start is not None or end is not None:
            first = int((start or 0.0) * DIARIZATION_SAMPLE_RATE)
            last = (
                int(end * DIARIZATION_SAMPLE_RATE) if end is not None else len(samples)
            )
            samples = samples[first:last]
        if len(samples) == 0:
            raise ValueError("the requested audio span is empty")
        extractor = self._extractor
        assert extractor is not None
        stream = extractor.create_stream()
        stream.accept_waveform(
            sample_rate=DIARIZATION_SAMPLE_RATE,
            waveform=samples,
        )
        stream.input_finished()
        return [float(value) for value in extractor.compute(stream)]

    async def embed_for_enrollment(
        self,
        audio: str | Path | bytes,
        *,
        min_seconds: float = MIN_ENROLLMENT_SECONDS,
    ) -> list[float]:
        """Extract a voiceprint, refusing audio too short to be one.

        Enrolment is the one place where a bad vector is not a bad
        answer but a permanently bad profile — it will keep matching the
        wrong people until somebody deletes it. So the length check lives
        here rather than in :meth:`embed`, which legitimately runs on
        two-second turns.

        Args:
            audio (str | Path | bytes): The enrolment recording.
            min_seconds (float): Shortest audio accepted.

        Returns:
            list[float]: The voiceprint.

        Raises:
            ValueError: When the recording is shorter than
                ``min_seconds``.
        """
        samples = await asyncio.to_thread(
            load_audio,
            audio,
            target_rate=DIARIZATION_SAMPLE_RATE,
        )
        duration = len(samples) / DIARIZATION_SAMPLE_RATE
        if duration < min_seconds:
            raise ValueError(
                f"enrollment needs at least {min_seconds:g}s of audio, got "
                f"{duration:.1f}s — a profile built from less matches almost "
                "anyone",
            )
        return await self.embed(audio)


__all__: list[str] = [
    "DEFAULT_MATCH_THRESHOLD",
    "EMBEDDING_DIMENSIONS",
    "MIN_ENROLLMENT_SECONDS",
    "VoiceEmbedder",
    "cosine_similarity",
]
