"""FastAPI-serializable schemas for computer-vision predictions.

``ort-vision-sdk`` returns Ultralytics-style result objects
(``DetectionResults`` / ``ClassificationResults`` / ``SegmentationResults``)
that are great in Python but not JSON responses. These Pydantic schemas
are the wire shape an endpoint returns; the :mod:`mapping
<tempest_fastapi_sdk.vision.mapping>` helpers convert a result object
into them. They carry no ``ort-vision-sdk`` dependency, so importing this
module never requires the ``[vision]`` extra.
"""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class BoundingBoxSchema(BaseSchema):
    """An axis-aligned box in pixel coordinates (top-left origin).

    Attributes:
        x1 (float): Left edge.
        y1 (float): Top edge.
        x2 (float): Right edge.
        y2 (float): Bottom edge.
    """

    x1: float = Field(description="Left edge (px).")
    y1: float = Field(description="Top edge (px).")
    x2: float = Field(description="Right edge (px).")
    y2: float = Field(description="Bottom edge (px).")


class DetectionSchema(BaseSchema):
    """A single detected object.

    Attributes:
        class_id (int): Integer class index.
        class_name (str): Human-readable label.
        confidence (float): Detection score in ``[0, 1]``.
        box (BoundingBoxSchema): Object bounding box.
    """

    class_id: int = Field(description="Integer class index.")
    class_name: str = Field(description="Human-readable label.")
    confidence: float = Field(description="Detection score in [0, 1].")
    box: BoundingBoxSchema = Field(description="Object bounding box.")


class ClassProbabilitySchema(BaseSchema):
    """One class score from a classifier's ranked output.

    Attributes:
        class_id (int): Integer class index.
        class_name (str): Human-readable label.
        probability (float): Score in ``[0, 1]``.
    """

    class_id: int = Field(description="Integer class index.")
    class_name: str = Field(description="Human-readable label.")
    probability: float = Field(description="Score in [0, 1].")


class ClassificationSchema(BaseSchema):
    """A classification result: the top label plus the ranked scores.

    Attributes:
        class_id (int): Top-1 class index.
        class_name (str): Top-1 label.
        confidence (float): Top-1 score in ``[0, 1]``.
        probabilities (list[ClassProbabilitySchema]): The ranked scores
            (top-k), highest first.
    """

    class_id: int = Field(description="Top-1 class index.")
    class_name: str = Field(description="Top-1 label.")
    confidence: float = Field(description="Top-1 score in [0, 1].")
    probabilities: list[ClassProbabilitySchema] = Field(
        default_factory=list,
        description="Ranked class scores (top-k), highest first.",
    )


class SegmentationSchema(BaseSchema):
    """A single segmented instance (box + label; mask data omitted).

    The raw mask is intentionally not serialized — returning a per-pixel
    array in JSON is rarely what an API wants. Read it from the original
    ``SegmentationResult.mask`` when you need the pixels.

    Attributes:
        class_id (int): Integer class index.
        class_name (str): Human-readable label.
        confidence (float): Instance score in ``[0, 1]``.
        box (BoundingBoxSchema): Instance bounding box.
    """

    class_id: int = Field(description="Integer class index.")
    class_name: str = Field(description="Human-readable label.")
    confidence: float = Field(description="Instance score in [0, 1].")
    box: BoundingBoxSchema = Field(description="Instance bounding box.")


__all__: list[str] = [
    "BoundingBoxSchema",
    "ClassProbabilitySchema",
    "ClassificationSchema",
    "DetectionSchema",
    "SegmentationSchema",
]
