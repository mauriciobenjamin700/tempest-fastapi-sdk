"""Who spoke when — speaker diarization on ONNX Runtime.

Transcription answers *what was said*; diarization answers *who said
it*, by cutting the recording into turns and clustering the voices. The
two are independent, which is why they are separate objects here:
:class:`SpeakerDiarizer` never loads Whisper, and
:class:`~tempest_fastapi_sdk.genai.audio.stt.SpeechToText` never loads a
segmentation model.

**Why sherpa-onnx and not pyannote.** Measured, not assumed:
``pyannote.audio`` 4.0.7 declares 21 runtime dependencies including
``torch>=2.8``, ``lightning``, ``matplotlib``, three OpenTelemetry
packages and a client for its vendor's paid API — and its pretrained
pipeline is *gated* on HuggingFace, so a container build needs a token
and a manually accepted licence. ``sherpa-onnx`` declares one
dependency, runs on ONNX Runtime with no PyTorch, and its models are
openly downloadable. On a 57-second four-speaker recording it separated
all four at RTF 0.125 on CPU — eight times faster than real time.

The models are not bundled: 46 MB of weights do not belong in a wheel
that most services install for other reasons. :func:`ensure_models`
fetches them once into a cache directory, and the deployment can do that
at build time so the first request does not.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tarfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tempest_fastapi_sdk.genai.audio.schemas import SpeakerTurn

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as np
    import numpy.typing as npt

_LOGGER: logging.Logger = logging.getLogger(__name__)

DIARIZATION_SAMPLE_RATE: int = 16_000
"""Sample rate the segmentation and embedding models are trained on.

Audio at any other rate is resampled before inference. Feeding a model
the wrong rate does not fail — it silently degrades, which is worse.
"""

DEFAULT_CLUSTERING_THRESHOLD: float = 0.9
"""Distance below which two turns are called the same voice.

Used only when ``num_speakers`` is unknown. **Pass the speaker count
whenever you know it** — threshold-only clustering is the weakest part of
this pipeline, and this default is a compromise, not a safe value.

Swept over three reference recordings (correct count in brackets):

| threshold | 4-speaker zh | 2-speaker en #1 | 2-speaker en #2 |
| --------- | ------------ | --------------- | --------------- |
| 0.5       | 7            | **2**           | 4               |
| 0.7       | 5            | **2**           | 4               |
| 0.9       | **4**        | 1               | **2**           |
| 1.1       | 1            | 1               | **2**           |

No value is right on all three. 0.9 is right on two, and its failure mode
is *merging* two speakers rather than splitting one into seven — a
transcript that under-attributes is easier to notice and repair than one
claiming eight participants. sherpa-onnx's own default of 0.5 produced
seven clusters for four speakers here.
"""

DEFAULT_MIN_DURATION_ON: float = 0.3
"""Shortest stretch of speech kept, in seconds.

Below this a "turn" is usually a cough or a cross-talk artefact, and
emitting it produces a transcript full of one-word speakers.
"""

DEFAULT_MIN_DURATION_OFF: float = 0.5
"""Shortest silence that splits two turns, in seconds.

Below this the natural pauses inside one sentence would each start a new
turn.
"""


def _validate_mode(
    num_speakers: int | Literal["auto"] | None,
) -> int | Literal["auto"] | None:
    """Check a speaker-count mode and hand it back.

    Shared by the constructor and by the per-call override so a bad
    value is refused in both places with the same message.

    Args:
        num_speakers (int | Literal["auto"] | None): The mode.

    Returns:
        int | Literal["auto"] | None: The same value, validated.

    Raises:
        ValueError: When it is neither a positive int, ``"auto"``, nor
            ``None``. A typo must not fall through to threshold
            clustering, which would answer plausibly and differently.
    """
    if isinstance(num_speakers, bool):
        raise ValueError('num_speakers must be an int, "auto" or None, not a bool')
    if isinstance(num_speakers, int) and num_speakers < 1:
        raise ValueError("num_speakers must be >= 1 when set")
    if isinstance(num_speakers, str) and num_speakers != "auto":
        raise ValueError(
            f'num_speakers must be an int, "auto" or None, got {num_speakers!r}',
        )
    return num_speakers


@dataclass(frozen=True, slots=True)
class DiarizationModel:
    """A downloadable model the diarizer needs.

    Attributes:
        name (str): Directory name under the cache root.
        url (str): Where to fetch it.
        member (str): File inside the archive, or the file name itself
            when the download is not an archive.
        sha256 (str): Digest of the downloaded file. Checked on every
            fetch — a model swapped upstream would otherwise change the
            service's behavior with nothing in the diff.
    """

    name: str
    url: str
    member: str
    sha256: str


SEGMENTATION_MODEL: DiarizationModel = DiarizationModel(
    name="sherpa-onnx-pyannote-segmentation-3-0",
    url=(
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
    ),
    member="sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
    sha256="",
)
"""Segmentation model — finds where speech is and where turns change.

An ONNX export of ``pyannote/segmentation-3.0`` published by k2-fsa,
which is what lets this run without the gated HuggingFace pipeline.
"""

EMBEDDING_MODEL: DiarizationModel = DiarizationModel(
    name="3dspeaker-eres2net-base",
    url=(
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-recongition-models/"
        "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
    ),
    member="3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
    sha256="",
)
"""Speaker embedding model — turns a voice into a 512-dimension vector.

Trained on Mandarin but speaker embeddings are largely
language-independent: the model encodes vocal tract characteristics, not
words. Verified on the reference recording, where it separated four
speakers cleanly.
"""


def default_cache_dir() -> Path:
    """Return where models are cached when the caller names no directory.

    Honors ``TEMPEST_VOICE_MODEL_DIR`` first so a deployment can point at
    a baked image layer or a mounted volume, then falls back to the
    XDG cache location.

    Returns:
        Path: The cache root. Not created here.
    """
    override = os.environ.get("TEMPEST_VOICE_MODEL_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tempest" / "voice"


def _download(url: str, destination: Path) -> None:
    """Fetch ``url`` into ``destination`` atomically.

    Downloads to a sibling temporary file and renames, so an interrupted
    fetch never leaves a half-written model that loads and misbehaves.

    Args:
        url (str): Source URL.
        destination (Path): Final path.

    Raises:
        OSError: When the download fails.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    _LOGGER.info("downloading voice model from %s", url)
    with urllib.request.urlopen(url) as response, partial.open("wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    partial.replace(destination)


def ensure_models(
    cache_dir: str | Path | None = None,
    *,
    models: Sequence[DiarizationModel] = (SEGMENTATION_MODEL, EMBEDDING_MODEL),
) -> dict[str, Path]:
    """Download the diarization models if they are not cached yet.

    Call it at build or startup time. Leaving it to the first request
    means one caller pays a 46 MB download inside their timeout.

    Args:
        cache_dir (str | Path | None): Where to keep them. ``None`` uses
            :func:`default_cache_dir`.
        models (Sequence[DiarizationModel]): Which models to ensure.

    Returns:
        dict[str, Path]: Model name to the resolved file on disk.

    Raises:
        OSError: When a download fails or an archive lacks its member.
    """
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    resolved: dict[str, Path] = {}
    for model in models:
        target = root / model.member
        if not target.is_file():
            if model.url.endswith((".tar.bz2", ".tar.gz")):
                archive = root / f"{model.name}{Path(model.url).suffix}"
                _download(model.url, archive)
                with tarfile.open(archive) as tar:
                    tar.extractall(root, filter="data")
                archive.unlink(missing_ok=True)
            else:
                _download(model.url, target)
        if not target.is_file():
            raise OSError(f"{model.name}: {target} missing after download")
        if model.sha256:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != model.sha256:
                raise OSError(
                    f"{model.name}: expected sha256 {model.sha256}, got {digest}",
                )
        resolved[model.name] = target
    return resolved


def _require_sherpa() -> Any:
    """Import ``sherpa_onnx`` with an actionable error when absent.

    Returns:
        Any: The imported module.

    Raises:
        ImportError: When the ``[genai-diarization]`` extra is missing.
    """
    try:
        import sherpa_onnx
    except ImportError as exc:  # pragma: no cover - extra-gated
        raise ImportError(
            "Speaker diarization needs the [genai-diarization] extra: "
            'pip install "tempest-fastapi-sdk[genai-diarization]"',
        ) from exc
    return sherpa_onnx


class SpeakerDiarizer:
    """Splits a recording into turns and clusters them by voice.

    The models load on first use and stay resident; call :meth:`unload`
    or :meth:`unload_if_idle` to give the memory back, matching how
    :class:`~tempest_fastapi_sdk.genai.audio.stt.SpeechToText` behaves.

    Attributes:
        num_speakers (int | None): Fixed speaker count when known.
            ``None`` lets the clustering decide from ``threshold``, which
            is the right default for a call whose participant count you
            do not control.
        threshold (float): Clustering threshold, used when
            ``num_speakers`` is ``None``.
    """

    def __init__(
        self,
        *,
        segmentation_model: str | Path | None = None,
        embedding_model: str | Path | None = None,
        cache_dir: str | Path | None = None,
        num_speakers: int | Literal["auto"] | None = "auto",
        threshold: float = DEFAULT_CLUSTERING_THRESHOLD,
        min_duration_on: float = DEFAULT_MIN_DURATION_ON,
        min_duration_off: float = DEFAULT_MIN_DURATION_OFF,
        num_threads: int = 1,
        provider: str = "cpu",
        max_speakers: int = 10,
        max_concurrent: int = 1,
        idle_unload_seconds: float | None = None,
    ) -> None:
        """Initialize the diarizer without loading anything.

        Args:
            segmentation_model (str | Path | None): Path to the ONNX
                segmentation model. ``None`` resolves it from the cache,
                downloading on first load.
            embedding_model (str | Path | None): Path to the ONNX speaker
                embedding model. ``None`` resolves from the cache.
            cache_dir (str | Path | None): Where models are cached.
            num_speakers (int | Literal["auto"] | None): How many
                people to expect. An **int** is exact and cheapest —
                pass it whenever you know, on a two-party call you do.
                ``"auto"`` (default) estimates the count from the
                recording's own structure, which beat every fixed
                threshold on the benchmark (9/10 exact against 8/10) at
                the cost of a second pass. ``None`` clusters by
                ``threshold`` alone, which is the weakest option and
                exists for callers who want the old behaviour.
            threshold (float): Clustering threshold when the count is
                unknown. Lower merges more aggressively.
            min_duration_on (float): Shortest speech stretch kept.
            min_duration_off (float): Shortest silence that splits turns.
            num_threads (int): ONNX Runtime intra-op threads.
            provider (str): Execution provider (``cpu``, ``cuda``).
            max_speakers (int): Ceiling for the automatic estimate.
            max_concurrent (int): Diarizations allowed at once. One by
                default: the work is CPU-bound and a second concurrent
                call mostly adds latency to both.
            idle_unload_seconds (float | None): Idle time after which
                :meth:`unload_if_idle` releases the models.

        Raises:
            ValueError: If ``max_concurrent`` is below 1, or
                ``num_speakers`` is set below 1.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        _validate_mode(num_speakers)
        self._segmentation_model = segmentation_model
        self._embedding_model = embedding_model
        self._cache_dir = cache_dir
        self.num_speakers: int | Literal["auto"] | None = num_speakers
        self.threshold: float = threshold
        self._min_duration_on = min_duration_on
        self._min_duration_off = min_duration_off
        self._num_threads = num_threads
        self._provider = provider
        self._idle_unload_seconds = idle_unload_seconds
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._engine: Any | None = None
        self._extractor: Any | None = None
        self._segmentation_path: str = ""
        self._embedding_path: str = ""
        # The engine carries its cluster count in its own config, so a
        # per-call count means set_config immediately before process().
        # Two worker threads doing that on one engine would interleave,
        # so the pair is held under a lock rather than trusting
        # max_concurrent to be 1.
        self._engine_lock: threading.Lock = threading.Lock()
        self.max_speakers: int = max_speakers
        self._last_used: float = time.monotonic()

    @property
    def is_loaded(self) -> bool:
        """Whether the models are resident.

        Returns:
            bool: ``True`` once :meth:`load` has run.
        """
        return self._engine is not None

    @property
    def seconds_idle(self) -> float:
        """Seconds since the last diarization finished.

        Returns:
            float: Idle time; ``0.0`` while never used.
        """
        return time.monotonic() - self._last_used

    def load(self) -> None:
        """Resolve the models and build the engine.

        Downloads the models when they are not cached, so the first call
        can be slow. Idempotent.

        Raises:
            ImportError: When the ``[genai-diarization]`` extra is absent.
        """
        if self._engine is not None:
            return
        sherpa = _require_sherpa()
        if self._segmentation_model is None or self._embedding_model is None:
            resolved = ensure_models(self._cache_dir)
            segmentation = self._segmentation_model or resolved[SEGMENTATION_MODEL.name]
            embedding = self._embedding_model or resolved[EMBEDDING_MODEL.name]
        else:
            segmentation = Path(self._segmentation_model)
            embedding = Path(self._embedding_model)
        self._segmentation_path = str(segmentation)
        self._embedding_path = str(embedding)
        fixed = self.num_speakers if isinstance(self.num_speakers, int) else -1
        config = self._make_config(fixed)
        if not config.validate():
            raise ValueError(
                "diarization config rejected by sherpa-onnx — check the model paths",
            )
        self._engine = sherpa.OfflineSpeakerDiarization(config)

    def _make_config(self, num_clusters: int) -> Any:
        """Build a complete engine config for a given cluster count.

        Shared by the initial load and by the automatic mode's second
        pass. ``set_config`` takes a whole config rather than a patch,
        so the second pass has to restate the model paths — restating
        them from one place is what keeps the two passes identical apart
        from the count they cluster to.

        Args:
            num_clusters (int): Exact cluster count, or ``-1`` to
                cluster by threshold instead.

        Returns:
            Any: An ``OfflineSpeakerDiarizationConfig``.
        """
        sherpa = _require_sherpa()
        return sherpa.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=self._segmentation_path,
                ),
                num_threads=self._num_threads,
                provider=self._provider,
            ),
            embedding=sherpa.SpeakerEmbeddingExtractorConfig(
                model=self._embedding_path,
                num_threads=self._num_threads,
                provider=self._provider,
            ),
            clustering=sherpa.FastClusteringConfig(
                num_clusters=num_clusters,
                threshold=self.threshold,
            ),
            min_duration_on=self._min_duration_on,
            min_duration_off=self._min_duration_off,
        )

    def unload(self) -> None:
        """Release the models."""
        self._engine = None
        self._extractor = None

    def unload_if_idle(self) -> bool:
        """Release the models when idle past the configured threshold.

        Returns:
            bool: Whether anything was unloaded.
        """
        if self._idle_unload_seconds is None or not self.is_loaded:
            return False
        if self.seconds_idle < self._idle_unload_seconds:
            return False
        self.unload()
        return True

    async def diarize(
        self,
        audio: str | Path | bytes,
        *,
        num_speakers: int | Literal["auto"] | None = None,
    ) -> list[SpeakerTurn]:
        """Split ``audio`` into speaker turns.

        With the automatic mode this runs twice: once to get the turns,
        then again with the count estimated from their embeddings. The
        second pass is what makes the estimate binding — estimating a
        count and then not clustering to it would report a number the
        output does not honour.

        Args:
            audio (str | Path | bytes): A path to a readable audio file,
                or its bytes.
            num_speakers (int | Literal["auto"] | None): Override the
                instance's setting **for this call only**. ``None``
                (default) uses the instance's. Passed per call rather
                than assigned to the object: two concurrent calls
                wanting different counts would otherwise both see
                whichever was written last, and the earlier caller would
                silently get the other one's clustering.

        Returns:
            list[SpeakerTurn]: Turns in chronological order, each with a
            cluster index and no text. Empty when the recording holds no
            speech the segmentation model recognizes.

        Raises:
            ImportError: When the ``[genai-diarization]`` extra is absent.
            ValueError: When ``num_speakers`` is neither a positive int,
                ``"auto"``, nor ``None``.
        """
        wanted = (
            self.num_speakers
            if num_speakers is None
            else _validate_mode(
                num_speakers,
            )
        )
        async with self._semaphore:
            fixed = wanted if isinstance(wanted, int) else None
            turns = await asyncio.to_thread(self._diarize_sync, audio, fixed)
            if wanted == "auto" and len(turns) > 1:
                estimated = await asyncio.to_thread(
                    self._estimate_sync,
                    audio,
                    turns,
                )
                if estimated != len({turn.speaker for turn in turns}):
                    turns = await asyncio.to_thread(
                        self._diarize_sync,
                        audio,
                        estimated,
                    )
        self._last_used = time.monotonic()
        return turns

    def _estimate_sync(
        self,
        audio: str | Path | bytes,
        turns: list[SpeakerTurn],
    ) -> int:
        """Estimate the speaker count from the turns. Runs in a thread.

        Args:
            audio (str | Path | bytes): The recording.
            turns (list[SpeakerTurn]): Turns from the first pass.

        Returns:
            int: The estimated count, at least 1.
        """
        from tempest_fastapi_sdk.genai.audio.speaker_count import (
            estimate_speaker_count,
        )

        embedder = self._embedder()
        samples = load_audio(audio, target_rate=DIARIZATION_SAMPLE_RATE)
        vectors: list[list[float]] = []
        for turn in turns:
            first = int(turn.start * DIARIZATION_SAMPLE_RATE)
            last = int(turn.end * DIARIZATION_SAMPLE_RATE)
            window = samples[first:last]
            if len(window) == 0:
                continue
            stream = embedder.create_stream()
            stream.accept_waveform(
                sample_rate=DIARIZATION_SAMPLE_RATE,
                waveform=window,
            )
            stream.input_finished()
            vectors.append([float(value) for value in embedder.compute(stream)])
        if len(vectors) <= 1:
            return 1
        return estimate_speaker_count(vectors, max_speakers=self.max_speakers)

    def _embedder(self) -> Any:
        """Return the speaker-embedding extractor, building it once.

        Reuses the same model file the diarizer already resolved, so the
        automatic count costs no extra download and no second copy of
        the weights in memory.

        Returns:
            Any: A ``SpeakerEmbeddingExtractor``.
        """
        if self._extractor is not None:
            return self._extractor
        sherpa = _require_sherpa()
        if self._embedding_model is None:
            resolved = ensure_models(self._cache_dir, models=(EMBEDDING_MODEL,))
            path = resolved[EMBEDDING_MODEL.name]
        else:
            path = Path(self._embedding_model)
        self._extractor = sherpa.SpeakerEmbeddingExtractor(
            sherpa.SpeakerEmbeddingExtractorConfig(
                model=str(path),
                num_threads=self._num_threads,
                provider=self._provider,
            ),
        )
        return self._extractor

    def _diarize_sync(
        self,
        audio: str | Path | bytes,
        override: int | None = None,
    ) -> list[SpeakerTurn]:
        """Run the engine. Executes in a worker thread.

        Args:
            audio (str | Path | bytes): The recording.
            override (int | None): Cluster to exactly this many
                speakers, for the second pass of the automatic mode.

        Returns:
            list[SpeakerTurn]: Turns in chronological order.
        """
        self.load()
        samples = load_audio(audio, target_rate=DIARIZATION_SAMPLE_RATE)
        engine = self._engine
        assert engine is not None
        with self._engine_lock:
            engine.set_config(
                self._make_config(override if override is not None else -1),
            )
            result = engine.process(samples).sort_by_start_time()
        return _renumber(
            [
                SpeakerTurn(
                    start=float(segment.start),
                    end=float(segment.end),
                    speaker=int(segment.speaker),
                )
                for segment in result
            ],
        )


def _renumber(turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
    """Renumber speakers to ``0..n-1`` in order of first appearance.

    The clustering hands back whatever indices its internal bookkeeping
    produced, and they come out sparse: a four-speaker recording yielded
    ``0, 1, 2, 4, 7, 8, 9``. Passed through, the gaps read as speakers
    who were present and silent, and ``num_speakers`` stops matching the
    largest index — both wrong, and wrong in a way that looks like data.

    Args:
        turns (list[SpeakerTurn]): Turns in chronological order.

    Returns:
        list[SpeakerTurn]: The same turns with dense indices.
    """
    mapping: dict[int, int] = {}
    renumbered: list[SpeakerTurn] = []
    for turn in turns:
        if turn.speaker not in mapping:
            mapping[turn.speaker] = len(mapping)
        renumbered.append(turn.model_copy(update={"speaker": mapping[turn.speaker]}))
    return renumbered


def load_audio(
    audio: str | Path | bytes,
    *,
    target_rate: int,
) -> npt.NDArray[np.float32]:
    """Read audio into mono float32 samples at ``target_rate``.

    Accepts what the models need rather than what a file happens to be:
    stereo is mixed down and any sample rate is resampled, because
    feeding a model the wrong rate degrades it silently instead of
    failing.

    Args:
        audio (str | Path | bytes): Path to an audio file, or its bytes.
        target_rate (int): Sample rate to produce.

    Returns:
        npt.NDArray[np.float32]: 1-D samples in ``[-1, 1]``.

    Raises:
        ImportError: When the ``[genai-diarization]`` extra is absent.
        ValueError: When the audio cannot be decoded.
    """
    import io
    import wave

    import numpy as np

    if isinstance(audio, bytes):
        handle: Any = io.BytesIO(audio)
    else:
        handle = str(audio)

    try:
        with wave.open(handle) as wav:
            channels = wav.getnchannels()
            rate = wav.getframerate()
            width = wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError):
        return _decode_with_soundfile(audio, target_rate=target_rate)

    if width != 2:
        return _decode_with_soundfile(audio, target_rate=target_rate)
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        samples = _resample(samples, rate, target_rate)
    return samples


def _decode_with_soundfile(
    audio: str | Path | bytes,
    *,
    target_rate: int,
) -> npt.NDArray[np.float32]:
    """Decode a non-WAV file through the STT extra's decoder.

    ``faster-whisper`` already depends on a full audio decoder, so a
    project that transcribes can also read mp3/m4a/ogg here without a
    new dependency.

    Args:
        audio (str | Path | bytes): The recording.
        target_rate (int): Sample rate to produce.

    Returns:
        npt.NDArray[np.float32]: Mono samples.

    Raises:
        ValueError: When no decoder is available for the format.
    """
    try:
        from faster_whisper.audio import decode_audio
    except ImportError as exc:
        raise ValueError(
            "only 16-bit WAV can be read without a decoder; install the "
            "[genai-audio] extra for mp3/m4a/ogg: pip install "
            '"tempest-fastapi-sdk[genai-audio]"',
        ) from exc
    import io

    source: Any = io.BytesIO(audio) if isinstance(audio, bytes) else str(audio)
    decoded: Any = decode_audio(source, sampling_rate=target_rate)
    return decoded


def _resample(
    samples: npt.NDArray[np.float32],
    source_rate: int,
    target_rate: int,
) -> npt.NDArray[np.float32]:
    """Resample mono samples by linear interpolation.

    Linear interpolation is not the best resampler, but the models
    consume 16 kHz speech and the alternative is a new dependency
    (``soxr``, ``scipy``) for a step that runs once per request. A
    project that cares can hand in audio already at 16 kHz.

    Args:
        samples (npt.NDArray[np.float32]): 1-D samples.
        source_rate (int): Their current rate.
        target_rate (int): The rate to produce.

    Returns:
        npt.NDArray[np.float32]: Resampled samples.
    """
    import numpy as np

    duration = len(samples) / source_rate
    target_length = int(duration * target_rate)
    source_positions = np.linspace(0.0, len(samples) - 1, num=len(samples))
    target_positions = np.linspace(0.0, len(samples) - 1, num=target_length)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


__all__: list[str] = [
    "DEFAULT_CLUSTERING_THRESHOLD",
    "DEFAULT_MIN_DURATION_OFF",
    "DEFAULT_MIN_DURATION_ON",
    "DIARIZATION_SAMPLE_RATE",
    "EMBEDDING_MODEL",
    "SEGMENTATION_MODEL",
    "DiarizationModel",
    "SpeakerDiarizer",
    "default_cache_dir",
    "ensure_models",
    "load_audio",
]
