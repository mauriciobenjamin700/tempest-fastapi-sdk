"""Convert ``ort-vision-sdk`` result objects into response schemas.

Each helper takes one result object — the element of the ``list``
returned by ``predict`` / ``async_predict`` / ``ort_async_predict`` — and
returns the JSON-ready schema(s). They only read public attributes of the
result objects, so they carry no import-time dependency on
``ort-vision-sdk`` (the type hints are under ``TYPE_CHECKING``).

    det = Detector("yolov8n.onnx")
    results = (await det.async_predict("img.jpg"))[0]
    return to_detection_schemas(results)   # list[DetectionSchema]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tempest_fastapi_sdk.vision.schemas import (
    BoundingBoxSchema,
    ClassificationSchema,
    ClassProbabilitySchema,
    DetectionSchema,
    SegmentationSchema,
)

if TYPE_CHECKING:
    from ort_vision_sdk import (
        ClassificationResults,
        DetectionResults,
        SegmentationResults,
    )


def _box(bbox: object) -> BoundingBoxSchema:
    """Build a :class:`BoundingBoxSchema` from a ``BoundingBox``.

    Args:
        bbox (object): An ``ort_vision_sdk.BoundingBox`` exposing
            ``as_xyxy()``.

    Returns:
        BoundingBoxSchema: The box in pixel xyxy form.
    """
    x1, y1, x2, y2 = bbox.as_xyxy()  # type: ignore[attr-defined]
    return BoundingBoxSchema(x1=x1, y1=y1, x2=x2, y2=y2)


def to_detection_schemas(results: DetectionResults) -> list[DetectionSchema]:
    """Map a detector result to a list of :class:`DetectionSchema`.

    Args:
        results (DetectionResults): One element of ``Detector.predict``'s
            return list.

    Returns:
        list[DetectionSchema]: One entry per detected object (``[]`` when
        nothing was detected).
    """
    return [
        DetectionSchema(
            class_id=d.class_id,
            class_name=d.class_name,
            confidence=d.confidence,
            box=_box(d.bbox),
        )
        for d in results.detections
    ]


def to_classification_schema(results: ClassificationResults) -> ClassificationSchema:
    """Map a classifier result to a single :class:`ClassificationSchema`.

    Args:
        results (ClassificationResults): One element of
            ``Classifier.predict``'s return list.

    Returns:
        ClassificationSchema: The top-1 label plus the ranked scores.
    """
    return ClassificationSchema(
        class_id=results.cls,
        class_name=results.name,
        confidence=results.conf,
        probabilities=[
            ClassProbabilitySchema(
                class_id=p.class_id,
                class_name=p.class_name,
                probability=p.probability,
            )
            for p in results.probabilities
        ],
    )


def to_segmentation_schemas(results: SegmentationResults) -> list[SegmentationSchema]:
    """Map a segmenter result to a list of :class:`SegmentationSchema`.

    Mask pixels are omitted (see :class:`SegmentationSchema`); only the
    box + label of each instance are returned.

    Args:
        results (SegmentationResults): One element of
            ``Segmenter.predict``'s return list.

    Returns:
        list[SegmentationSchema]: One entry per segmented instance.
    """
    return [
        SegmentationSchema(
            class_id=d.class_id,
            class_name=d.class_name,
            confidence=d.confidence,
            box=_box(d.bbox),
        )
        for d in results.detections
    ]


__all__: list[str] = [
    "to_classification_schema",
    "to_detection_schemas",
    "to_segmentation_schemas",
]
