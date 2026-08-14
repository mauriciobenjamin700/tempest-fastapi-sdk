"""Turning a face into a comparable vector, and comparing them.

`FaceRecognizer` is the whole pipeline in one object: detect, align,
embed. It loads lazily and runs inference in a worker thread, matching
the rest of the SDK's model wrappers.

Measured separation on the default model pack, using a six-person group
photo: the same face across a re-encoded, rescaled, rotated and mirrored
crop scored **0.904-0.960**, while every pair of different people topped
out at **0.225**. That gap is wide enough that the threshold is not a
delicate choice — which is the opposite of the speaker-diarization case
and worth knowing when reusing intuitions between the two.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.faces.detector import FaceDetector
from tempest_fastapi_sdk.faces.geometry import align_face
from tempest_fastapi_sdk.faces.models import LIGHT_PACK, ensure_models, resolve_pack
from tempest_fastapi_sdk.faces.schemas import DetectedFace

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tempest_fastapi_sdk.faces.models import FaceModelPack

DEFAULT_MATCH_THRESHOLD: float = 0.45
"""Cosine similarity above which two faces are called the same person.

Measured on the default pack: same person 0.904-0.960, different people
at most 0.225. This sits in the middle of a 0.68-wide gap, so it is a
safe default rather than a tuned one.

**Raise it for anything that grants access.** The measurement is on
cooperative, front-facing photos; the error that matters there is
admitting the wrong person, and a stricter threshold trades that against
asking somebody to try again.
"""

MIN_FACE_PIXELS: int = 40
"""Smallest face side, in pixels, worth embedding.

Below this the aligned crop is mostly interpolation, and the embedding
describes the upscaling more than the person. Reported rather than
silently embedded, so a caller can tell "nobody recognisable" from "no
face at all".
"""


class FaceRecognizer:
    """Detects, aligns and embeds faces.

    Attributes:
        pack (FaceModelPack): Which models are in use.
        threshold (float): Similarity above which faces match.
    """

    def __init__(
        self,
        *,
        pack: FaceModelPack | str = LIGHT_PACK,
        cache_dir: str | Path | None = None,
        detector_model: str | Path | None = None,
        recognizer_model: str | Path | None = None,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        min_face_pixels: int = MIN_FACE_PIXELS,
        num_threads: int = 1,
        max_concurrent: int = 2,
        idle_unload_seconds: float | None = None,
    ) -> None:
        """Initialize the recognizer without loading anything.

        Args:
            pack (FaceModelPack | str): Model pack, by object or name.
            cache_dir (str | Path | None): Where packs are cached.
            detector_model (str | Path | None): Override the detection
                model path, skipping the pack download.
            recognizer_model (str | Path | None): Override the
                recognition model path.
            threshold (float): Similarity above which faces match.
            min_face_pixels (int): Smallest face worth embedding.
            num_threads (int): ONNX Runtime intra-op threads.
            max_concurrent (int): Inferences allowed at once.
            idle_unload_seconds (float | None): Idle time after which
                :meth:`unload_if_idle` releases the models.

        Raises:
            ValueError: When ``threshold`` is outside ``0..1``,
                ``max_concurrent`` is below 1, or the pack name is
                unknown.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.pack: FaceModelPack = resolve_pack(pack)
        self.threshold: float = threshold
        self.min_face_pixels: int = min_face_pixels
        self._cache_dir = cache_dir
        self._detector_model = detector_model
        self._recognizer_model = recognizer_model
        self._num_threads = num_threads
        self._idle_unload_seconds = idle_unload_seconds
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._detector: FaceDetector | None = None
        self._session: Any | None = None
        self._last_used: float = time.monotonic()

    @property
    def is_loaded(self) -> bool:
        """Whether both models are resident.

        Returns:
            bool: ``True`` once loaded.
        """
        return self._session is not None and self._detector is not None

    @property
    def seconds_idle(self) -> float:
        """Seconds since the last inference.

        Returns:
            float: Idle time.
        """
        return time.monotonic() - self._last_used

    def load(self) -> None:
        """Resolve and load both models, downloading the pack if needed.

        Idempotent.

        Raises:
            ImportError: When the ``[faces]`` extra is absent.
        """
        if self.is_loaded:
            return
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - extra-gated
            raise ImportError(
                "Face recognition needs ONNX Runtime and Pillow. Install the "
                'extra: pip install "tempest-fastapi-sdk[faces]"',
            ) from exc
        detector_path: str | Path
        recognizer_path: str | Path
        if self._detector_model is None or self._recognizer_model is None:
            fetched_detector, fetched_recognizer = ensure_models(
                self.pack,
                self._cache_dir,
            )
            detector_path = self._detector_model or fetched_detector
            recognizer_path = self._recognizer_model or fetched_recognizer
        else:
            detector_path = self._detector_model
            recognizer_path = self._recognizer_model
        self._detector = FaceDetector(
            detector_path,
            num_threads=self._num_threads,
        )
        self._detector.load()
        options = ort.SessionOptions()
        options.intra_op_num_threads = self._num_threads
        self._session = ort.InferenceSession(
            str(recognizer_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def unload(self) -> None:
        """Release both models."""
        if self._detector is not None:
            self._detector.unload()
        self._detector = None
        self._session = None

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

    async def detect(self, image: str | Path | bytes) -> list[DetectedFace]:
        """Find faces without embedding them.

        Cheaper than :meth:`recognize` when the question is *is there a
        face here* — counting people, validating an upload, cropping a
        thumbnail — and it touches no biometric data.

        Args:
            image (str | Path | bytes): Path to an image, or its bytes.

        Returns:
            list[DetectedFace]: Faces with boxes and landmarks, largest
            first, embeddings empty.
        """
        async with self._semaphore:
            faces = await asyncio.to_thread(self._detect_sync, image)
        self._last_used = time.monotonic()
        return faces

    async def recognize(self, image: str | Path | bytes) -> list[DetectedFace]:
        """Find faces and embed each one.

        Args:
            image (str | Path | bytes): Path to an image, or its bytes.

        Returns:
            list[DetectedFace]: Faces, largest first, each carrying a
            unit-length embedding. A face smaller than
            ``min_face_pixels`` comes back with an empty embedding
            rather than a vector describing its upscaling.
        """
        async with self._semaphore:
            faces = await asyncio.to_thread(self._recognize_sync, image)
        self._last_used = time.monotonic()
        return faces

    async def embed_face(self, image: str | Path | bytes) -> list[float]:
        """Embed the largest face in an image.

        The enrolment shape: one photo, one person, one vector.

        Args:
            image (str | Path | bytes): Path to an image, or its bytes.

        Returns:
            list[float]: The face vector, unit length.

        Raises:
            ValueError: When no face was found, or the largest one is
                too small to embed. Both are refusals rather than empty
                results because enrolling nothing produces a profile
                that matches nobody, forever.
        """
        faces = await self.recognize(image)
        if not faces:
            raise ValueError("no face found in the image")
        largest = faces[0]
        if not largest.embedding:
            raise ValueError(
                f"the largest face is smaller than {self.min_face_pixels}px; "
                "a profile built from it would describe the upscaling",
            )
        return largest.embedding

    def _open(self, image: str | Path | bytes) -> Any:
        """Read an image from a path or bytes.

        Args:
            image (str | Path | bytes): The source.

        Returns:
            Any: A ``PIL.Image``.
        """
        import io

        from PIL import Image

        if isinstance(image, bytes):
            return Image.open(io.BytesIO(image))
        return Image.open(str(image))

    def _detect_sync(self, image: str | Path | bytes) -> list[DetectedFace]:
        """Detect faces. Runs in a worker thread.

        Args:
            image (str | Path | bytes): The source.

        Returns:
            list[DetectedFace]: The faces.
        """
        self.load()
        assert self._detector is not None
        return self._detector.detect(self._open(image))

    def _recognize_sync(self, image: str | Path | bytes) -> list[DetectedFace]:
        """Detect and embed faces. Runs in a worker thread.

        Args:
            image (str | Path | bytes): The source.

        Returns:
            list[DetectedFace]: The faces with embeddings.
        """
        self.load()
        assert self._detector is not None
        picture = self._open(image)
        faces = self._detector.detect(picture)
        embedded: list[DetectedFace] = []
        for face in faces:
            if min(face.box.width, face.box.height) < self.min_face_pixels:
                embedded.append(face)
                continue
            crop = align_face(picture, face.landmarks)
            embedded.append(
                face.model_copy(update={"embedding": self._embed_crop(crop)}),
            )
        return embedded

    def _embed_crop(self, crop: Any) -> list[float]:
        """Run the recognizer on an aligned 112x112 crop.

        Args:
            crop (Any): The aligned ``PIL.Image``.

        Returns:
            list[float]: The unit-length embedding.
        """
        import numpy as np

        blob = (np.asarray(crop, dtype=np.float32) - 127.5) / 127.5
        blob = blob.transpose(2, 0, 1)[None]
        session = self._session
        assert session is not None
        vector = session.run(None, {session.get_inputs()[0].name: blob})[0][0]
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return [0.0] * len(vector)
        return [float(value) for value in vector / norm]


def compare_faces(first: Sequence[float], second: Sequence[float]) -> float:
    """Compare two face embeddings.

    Args:
        first (Sequence[float]): A face vector.
        second (Sequence[float]): Another face vector.

    Returns:
        float: Cosine similarity in ``[-1, 1]``; ``0.0`` when either
        vector is all zeros.

    Raises:
        ValueError: When the vectors differ in length — different models
            produce incomparable vectors, and a low score would read as
            "not this person" when the truth is "this comparison means
            nothing".
        ImportError: When NumPy is missing.
    """
    import numpy as np

    left = np.asarray(first, dtype=np.float64)
    right = np.asarray(second, dtype=np.float64)
    if left.shape != right.shape:
        raise ValueError(
            f"embeddings differ in length ({left.shape} vs {right.shape}); "
            "they came from different models and cannot be compared",
        )
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


__all__: list[str] = [
    "DEFAULT_MATCH_THRESHOLD",
    "MIN_FACE_PIXELS",
    "FaceRecognizer",
    "compare_faces",
]
