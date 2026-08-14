"""Finding faces and their landmarks with SCRFD, on ONNX Runtime.

SCRFD emits, per feature-map stride, a score, four distances to the box
edges and ten landmark offsets — all relative to anchor centres that the
caller has to reconstruct. That decoding is this module, and the details
are load-bearing: the wrong stride order pairs scores with the wrong
boxes, and the wrong anchor count silently halves the search.

The rest is non-maximum suppression, and one correction the measurements
forced: a face that fills the frame edge to edge is not detected at all,
so a tight crop gets a synthetic margin before inference.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.faces.schemas import BoundingBox, DetectedFace

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    import numpy.typing as npt
    from PIL import Image

_LOGGER: logging.Logger = logging.getLogger(__name__)

DETECT_SIZE: int = 640
"""Side of the square the image is letterboxed into before detection.

The models accept any size, but they were trained at 640 and a smaller
canvas loses small faces while a larger one costs time without finding
more. Fixed rather than exposed for that reason.
"""

STRIDES: tuple[int, ...] = (8, 16, 32)
"""Feature-map strides, in the order the model emits its outputs.

The nine outputs are three groups of three — scores, boxes, landmarks —
each ordered by this tuple. Reordering it pairs a score with another
stride's box, which produces detections at plausible-looking wrong
coordinates rather than an error.
"""

ANCHORS_PER_LOCATION: int = 2
"""Anchors the model places at every feature-map cell.

Verified against the output shapes: at stride 8 on a 640 canvas the
score tensor is 12800 rows, and 80 x 80 x 2 is 12800. Assuming one
anchor would read half the predictions and drop every second face.
"""

DEFAULT_SCORE_THRESHOLD: float = 0.5
"""Minimum detector score for a face to be reported."""

DEFAULT_NMS_THRESHOLD: float = 0.4
"""Overlap above which two boxes are treated as the same face."""

MIN_DETECT_PIXELS: int = 320
"""Below this, the image is upscaled before detection.

A 112x112 crop of a face returned **zero** detections until it was
enlarged. The detector letterboxes into 640 and its smallest anchor
still expects a face well inside the frame, so a small image gives it
almost nothing to work with.
"""

TIGHT_CROP_MARGIN: float = 0.2
"""Frame added around an image whose face may touch the edges.

Measured: the same 112x112 portrait went from zero detections to one
with a 20% border. The detector needs context around a face, and a crop
that was already tight has none to give.
"""


class FaceDetector:
    """Detects faces and their five landmarks.

    Attributes:
        score_threshold (float): Minimum score to report a face.
        nms_threshold (float): Overlap at which boxes merge.
    """

    def __init__(
        self,
        model: str | Path,
        *,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
        nms_threshold: float = DEFAULT_NMS_THRESHOLD,
        num_threads: int = 1,
        providers: list[str] | None = None,
    ) -> None:
        """Initialize the detector without loading the model.

        Args:
            model (str | Path): Path to the SCRFD ONNX model.
            score_threshold (float): Minimum score to report.
            nms_threshold (float): Overlap at which boxes merge.
            num_threads (int): ONNX Runtime intra-op threads.
            providers (list[str] | None): Execution providers. ``None``
                uses CPU, which is what a web worker has.

        Raises:
            ValueError: When either threshold is outside ``0..1``.
        """
        for name, value in (
            ("score_threshold", score_threshold),
            ("nms_threshold", nms_threshold),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        self._model = model
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self._num_threads = num_threads
        self._providers = providers or ["CPUExecutionProvider"]
        self._session: Any | None = None
        self._output_names: list[str] = []

    @property
    def is_loaded(self) -> bool:
        """Whether the model is resident.

        Returns:
            bool: ``True`` once loaded.
        """
        return self._session is not None

    def load(self) -> None:
        """Build the inference session. Idempotent.

        Raises:
            ImportError: When ONNX Runtime is missing — install the
                ``[faces]`` extra.
        """
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - extra-gated
            raise ImportError(
                "Face detection needs ONNX Runtime. Install the extra: "
                'pip install "tempest-fastapi-sdk[faces]"',
            ) from exc
        options = ort.SessionOptions()
        options.intra_op_num_threads = self._num_threads
        self._session = ort.InferenceSession(
            str(self._model),
            sess_options=options,
            providers=self._providers,
        )
        self._output_names = [output.name for output in self._session.get_outputs()]

    def unload(self) -> None:
        """Release the model."""
        self._session = None
        self._output_names = []

    def detect(self, image: Image.Image) -> list[DetectedFace]:
        """Find every face in ``image``.

        Args:
            image (Image.Image): The image to search.

        Returns:
            list[DetectedFace]: Faces with boxes, scores and landmarks,
            ordered largest first — the subject of a photo is usually
            the biggest face in it, so a caller taking ``[0]`` gets what
            they meant.

        Raises:
            ImportError: When the ``[faces]`` extra is absent.
        """
        import numpy as np

        self.load()
        prepared, offset, scale = _prepare(image)
        blob = (np.asarray(prepared, dtype=np.float32) - 127.5) / 128.0
        blob = blob.transpose(2, 0, 1)[None]
        session = self._session
        assert session is not None
        outputs = session.run(
            self._output_names,
            {session.get_inputs()[0].name: blob},
        )

        boxes: list[npt.NDArray[np.float32]] = []
        landmarks: list[npt.NDArray[np.float32]] = []
        scores: list[npt.NDArray[np.float32]] = []
        for index, stride in enumerate(STRIDES):
            score = outputs[index].reshape(-1)
            keep = score >= self.score_threshold
            if not keep.any():
                continue
            centers = _anchor_centers(stride)
            boxes.append(
                _distance_to_box(centers[keep], outputs[index + 3][keep] * stride),
            )
            landmarks.append(
                _distance_to_landmarks(
                    centers[keep],
                    outputs[index + 6][keep] * stride,
                ),
            )
            scores.append(score[keep])
        if not boxes:
            return []

        all_boxes = np.concatenate(boxes)
        all_landmarks = np.concatenate(landmarks)
        all_scores = np.concatenate(scores)
        kept = _non_max_suppression(all_boxes, all_scores, self.nms_threshold)

        faces: list[DetectedFace] = []
        for index in kept:
            box = (all_boxes[index] - np.array([*offset, *offset])) / scale
            points = (all_landmarks[index] - np.asarray(offset)) / scale
            faces.append(
                DetectedFace(
                    box=BoundingBox(
                        left=float(box[0]),
                        top=float(box[1]),
                        right=float(box[2]),
                        bottom=float(box[3]),
                    ),
                    confidence=float(all_scores[index]),
                    landmarks=[[float(x), float(y)] for x, y in points],
                ),
            )
        faces.sort(key=lambda face: face.box.width * face.box.height, reverse=True)
        return faces


def _prepare(
    image: Image.Image,
) -> tuple[Image.Image, tuple[float, float], float]:
    """Letterbox ``image`` into the detector's canvas.

    Small images are upscaled and every image gets a margin, because a
    face touching the frame edge is not found at all. Both operations
    have to be undone on the way out, which is why the offset and scale
    come back with the canvas.

    Args:
        image (Image.Image): The image to fit onto the canvas.

    Returns:
        tuple[Image.Image, tuple[float, float], float]: The canvas, the pixel
        offset the content was pasted at, and the scale applied to the
        original — so ``(point - offset) / scale`` returns to original
        coordinates.

    Raises:
        ImportError: When Pillow is missing.
    """
    from PIL import Image

    rgb = image.convert("RGB")
    width, height = rgb.size
    upscale = 1.0
    if max(width, height) < MIN_DETECT_PIXELS:
        upscale = MIN_DETECT_PIXELS / max(width, height)
        rgb = rgb.resize(
            (max(1, round(width * upscale)), max(1, round(height * upscale))),
            Image.Resampling.BICUBIC,
        )
        width, height = rgb.size

    margin = round(max(width, height) * TIGHT_CROP_MARGIN)
    fit = DETECT_SIZE / (max(width, height) + 2 * margin)
    canvas = Image.new("RGB", (DETECT_SIZE, DETECT_SIZE), (0, 0, 0))
    resized = rgb.resize(
        (max(1, round(width * fit)), max(1, round(height * fit))),
        Image.Resampling.BILINEAR,
    )
    paste = round(margin * fit)
    canvas.paste(resized, (paste, paste))
    return canvas, (float(paste), float(paste)), upscale * fit


def _anchor_centers(stride: int) -> npt.NDArray[np.float32]:
    """Build the anchor centre for every prediction at one stride.

    Args:
        stride (int): The feature-map stride.

    Returns:
        npt.NDArray[np.float32]: An ``(N, 2)`` array of centres in canvas
        pixels, repeated
        per anchor so it lines up row-for-row with the model's output.
    """
    import numpy as np

    size = DETECT_SIZE // stride
    xs, ys = np.meshgrid(np.arange(size), np.arange(size))
    centers = np.stack([xs, ys], axis=-1).astype(np.float32) * stride
    return np.repeat(centers.reshape(-1, 2), ANCHORS_PER_LOCATION, axis=0)


def _distance_to_box(
    centers: npt.NDArray[np.float32],
    distances: npt.NDArray[np.float32],
) -> npt.NDArray[np.float32]:
    """Turn edge distances into absolute boxes.

    Args:
        centers (npt.NDArray[np.float32]): ``(N, 2)`` anchor centres.
        distances (npt.NDArray[np.float32]): ``(N, 4)`` distances to left,
            top, right and bottom.

    Returns:
        npt.NDArray[np.float32]: ``(N, 4)`` boxes as left, top, right,
        bottom.
    """
    import numpy as np

    return np.stack(
        [
            centers[:, 0] - distances[:, 0],
            centers[:, 1] - distances[:, 1],
            centers[:, 0] + distances[:, 2],
            centers[:, 1] + distances[:, 3],
        ],
        axis=-1,
    )


def _distance_to_landmarks(
    centers: npt.NDArray[np.float32],
    distances: npt.NDArray[np.float32],
) -> npt.NDArray[np.float32]:
    """Turn landmark offsets into absolute points.

    Args:
        centers (npt.NDArray[np.float32]): ``(N, 2)`` anchor centres.
        distances (npt.NDArray[np.float32]): ``(N, 10)`` alternating x and y
            offsets.

    Returns:
        npt.NDArray[np.float32]: ``(N, 5, 2)`` landmark points.
    """
    import numpy as np

    coordinates = []
    for index in range(0, distances.shape[1], 2):
        coordinates.append(centers[:, 0] + distances[:, index])
        coordinates.append(centers[:, 1] + distances[:, index + 1])
    return np.stack(coordinates, axis=-1).reshape(-1, 5, 2)


def _non_max_suppression(
    boxes: npt.NDArray[np.float32],
    scores: npt.NDArray[np.float32],
    threshold: float,
) -> list[int]:
    """Keep the best box among each cluster of overlapping ones.

    Args:
        boxes (npt.NDArray[np.float32]): ``(N, 4)`` boxes.
        scores (npt.NDArray[np.float32]): ``(N,)`` scores.
        threshold (float): Overlap above which a box is suppressed.

    Returns:
        list[int]: Indices to keep, highest score first.
    """
    import numpy as np

    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep: list[int] = []
    while order.size:
        current = int(order[0])
        keep.append(current)
        if order.size == 1:
            break
        rest = order[1:]
        left = np.maximum(boxes[current, 0], boxes[rest, 0])
        top = np.maximum(boxes[current, 1], boxes[rest, 1])
        right = np.minimum(boxes[current, 2], boxes[rest, 2])
        bottom = np.minimum(boxes[current, 3], boxes[rest, 3])
        overlap = np.maximum(0.0, right - left) * np.maximum(0.0, bottom - top)
        union = areas[current] + areas[rest] - overlap
        order = rest[overlap / np.maximum(union, 1e-9) <= threshold]
    return keep


__all__: list[str] = [
    "ANCHORS_PER_LOCATION",
    "DEFAULT_NMS_THRESHOLD",
    "DEFAULT_SCORE_THRESHOLD",
    "DETECT_SIZE",
    "MIN_DETECT_PIXELS",
    "STRIDES",
    "TIGHT_CROP_MARGIN",
    "FaceDetector",
]
