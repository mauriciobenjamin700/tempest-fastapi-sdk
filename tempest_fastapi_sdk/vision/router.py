"""Opt-in FastAPI router for vision inference.

Mirrors :func:`~tempest_fastapi_sdk.genai.make_genai_router`: pass the loaded
task objects you have (`Classifier` / `Detector` / `Segmenter` from
``ort-vision-sdk``) and the router mounts **only** the matching endpoints. Each
accepts a multipart ``UploadFile`` and returns the response schemas via the
:mod:`~tempest_fastapi_sdk.vision.mapping` helpers.

The task objects are injected already-constructed, so this module carries no
import-time dependency on ``ort-vision-sdk`` — only ``fastapi`` and the
dependency-free mappers/schemas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.vision.mapping import (
    to_classification_schema,
    to_detection_schemas,
    to_segmentation_schemas,
)
from tempest_fastapi_sdk.vision.schemas import (
    ClassificationSchema,
    DetectionSchema,
    SegmentationSchema,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def make_vision_router(
    *,
    classifier: Any = None,
    detector: Any = None,
    segmenter: Any = None,
    prefix: str = "/api/vision",
    tags: Sequence[str] | None = None,
) -> APIRouter:
    """Build a router exposing only the injected vision tasks.

    Args:
        classifier (Any): A loaded ``Classifier`` (mounts ``POST /classify``),
            or ``None`` to omit it.
        detector (Any): A loaded ``Detector`` (mounts ``POST /detect``), or
            ``None``.
        segmenter (Any): A loaded ``Segmenter`` (mounts ``POST /segment``), or
            ``None``.
        prefix (str): Route prefix.
        tags (Sequence[str] | None): OpenAPI tags (defaults to ``["vision"]``).

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.

    Raises:
        ValueError: When no task object is injected.
    """
    if classifier is None and detector is None and segmenter is None:
        raise ValueError(
            "make_vision_router needs at least one of classifier / detector / "
            "segmenter",
        )
    router = APIRouter(prefix=prefix, tags=list(tags or ["vision"]))

    if classifier is not None:

        @router.post("/classify", response_model=ClassificationSchema)
        async def classify(file: UploadFile) -> ClassificationSchema:
            """Classify an uploaded image."""
            data = await file.read()
            results = (await classifier.async_predict(data))[0]
            return to_classification_schema(results)

    if detector is not None:

        @router.post("/detect", response_model=list[DetectionSchema])
        async def detect(file: UploadFile) -> list[DetectionSchema]:
            """Detect objects in an uploaded image."""
            data = await file.read()
            results = (await detector.async_predict(data))[0]
            return to_detection_schemas(results)

    if segmenter is not None:

        @router.post("/segment", response_model=list[SegmentationSchema])
        async def segment(file: UploadFile) -> list[SegmentationSchema]:
            """Segment instances in an uploaded image."""
            data = await file.read()
            results = (await segmenter.async_predict(data))[0]
            return to_segmentation_schemas(results)

    return router


__all__: list[str] = [
    "make_vision_router",
]
