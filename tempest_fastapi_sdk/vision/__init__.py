"""Computer-vision inference integration (optional ``[vision]`` extra).

Wraps `ort-vision-sdk <https://pypi.org/project/ort-vision-sdk/>`_ — the
ONNX Runtime classification / detection / segmentation library — with the
FastAPI-facing layer it lacks: Pydantic response schemas and the mappers
that turn a result object into them.

The task classes (`Classifier` / `Detector` / `Segmenter`) are re-exported
**lazily** — accessing one imports ``ort-vision-sdk`` and raises a clear
``ImportError`` (pointing at the ``[vision]`` extra) when it is missing.
The schemas and mappers carry no such dependency, so importing this module
is always safe.

    from tempest_fastapi_sdk.vision import Detector, to_detection_schemas

    detector = Detector("yolov8n.onnx", labels="coco")

    @router.post("/detect")
    async def detect(file: UploadFile) -> list[DetectionSchema]:
        results = (await detector.async_predict(await file.read()))[0]
        return to_detection_schemas(results)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.vision.mapping import (
    to_classification_schema,
    to_detection_schemas,
    to_segmentation_schemas,
)
from tempest_fastapi_sdk.vision.schemas import (
    BoundingBoxSchema,
    ClassificationSchema,
    ClassProbabilitySchema,
    DetectionSchema,
    SegmentationSchema,
)

if TYPE_CHECKING:
    from ort_vision_sdk import Classifier, Detector, Segmenter

_LAZY_EXPORTS: frozenset[str] = frozenset({"Classifier", "Detector", "Segmenter"})


def __getattr__(name: str) -> Any:
    """Lazily resolve the ``ort-vision-sdk`` task classes.

    Args:
        name (str): The attribute requested.

    Returns:
        Any: The ``ort_vision_sdk`` class when ``name`` is one of
        ``Classifier`` / ``Detector`` / ``Segmenter``.

    Raises:
        ImportError: When the ``[vision]`` extra is not installed.
        AttributeError: For any other attribute name.
    """
    if name in _LAZY_EXPORTS:
        try:
            import ort_vision_sdk
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "Computer-vision support requires the optional [vision] extra. "
                "Install with: pip install tempest-fastapi-sdk[vision]",
            ) from exc
        return getattr(ort_vision_sdk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__: list[str] = [
    "BoundingBoxSchema",
    "ClassProbabilitySchema",
    "ClassificationSchema",
    "Classifier",
    "DetectionSchema",
    "Detector",
    "SegmentationSchema",
    "Segmenter",
    "to_classification_schema",
    "to_detection_schemas",
    "to_segmentation_schemas",
]
