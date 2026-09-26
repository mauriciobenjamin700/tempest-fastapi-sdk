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

    from tempest_fastapi_sdk.utils import read_upload_capped
    from tempest_fastapi_sdk.vision import Detector, to_detection_schemas

    detector = Detector("yolov8n.onnx", labels="coco")

    @router.post("/detect")
    async def detect(file: UploadFile) -> list[DetectionSchema]:
        data = await read_upload_capped(file, max_bytes=20 * 1024 * 1024)
        results = (await detector.async_predict(data))[0]
        return to_detection_schemas(results)

A hand-written route like this one still decodes whatever canvas the image
header declares; :func:`make_vision_router` also refuses images over
``DEFAULT_MAX_IMAGE_PIXELS`` before decoding them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.vision.mapping import (
    to_classification_schema as to_classification_schema,
)
from tempest_fastapi_sdk.vision.mapping import (
    to_detect_classify_schemas as to_detect_classify_schemas,
)
from tempest_fastapi_sdk.vision.mapping import (
    to_detection_schemas as to_detection_schemas,
)
from tempest_fastapi_sdk.vision.mapping import (
    to_segmentation_schemas as to_segmentation_schemas,
)
from tempest_fastapi_sdk.vision.router import (
    DEFAULT_MAX_IMAGE_PIXELS as DEFAULT_MAX_IMAGE_PIXELS,
)
from tempest_fastapi_sdk.vision.router import (
    DEFAULT_MAX_IMAGE_UPLOAD_BYTES as DEFAULT_MAX_IMAGE_UPLOAD_BYTES,
)
from tempest_fastapi_sdk.vision.router import make_vision_router as make_vision_router
from tempest_fastapi_sdk.vision.schemas import (
    BoundingBoxSchema as BoundingBoxSchema,
)
from tempest_fastapi_sdk.vision.schemas import (
    ClassificationSchema as ClassificationSchema,
)
from tempest_fastapi_sdk.vision.schemas import (
    ClassProbabilitySchema as ClassProbabilitySchema,
)
from tempest_fastapi_sdk.vision.schemas import (
    DetectClassifySchema as DetectClassifySchema,
)
from tempest_fastapi_sdk.vision.schemas import (
    DetectionSchema as DetectionSchema,
)
from tempest_fastapi_sdk.vision.schemas import (
    SegmentationSchema as SegmentationSchema,
)

if TYPE_CHECKING:
    from ort_vision_sdk import (
        Classifier as Classifier,
    )
    from ort_vision_sdk import (
        DetectClassify as DetectClassify,
    )
    from ort_vision_sdk import (
        DetectClassifyResults as DetectClassifyResults,
    )
    from ort_vision_sdk import (
        Detector as Detector,
    )
    from ort_vision_sdk import (
        Segmenter as Segmenter,
    )

_LAZY_EXPORTS: frozenset[str] = frozenset(
    {
        "Classifier",
        "DetectClassify",
        "DetectClassifyResults",
        "Detector",
        "Segmenter",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily resolve the ``ort-vision-sdk`` task classes.

    Args:
        name (str): The attribute requested.

    Returns:
        Any: The ``ort_vision_sdk`` symbol when ``name`` is one of
        :data:`_LAZY_EXPORTS`.

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
    "DEFAULT_MAX_IMAGE_PIXELS",
    "DEFAULT_MAX_IMAGE_UPLOAD_BYTES",
    "BoundingBoxSchema",
    "ClassProbabilitySchema",
    "ClassificationSchema",
    "Classifier",
    "DetectClassify",
    "DetectClassifyResults",
    "DetectClassifySchema",
    "DetectionSchema",
    "Detector",
    "SegmentationSchema",
    "Segmenter",
    "make_vision_router",
    "to_classification_schema",
    "to_detect_classify_schemas",
    "to_detection_schemas",
    "to_segmentation_schemas",
]
