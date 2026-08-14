"""Typed results from the face pipeline."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class BoundingBox(BaseSchema):
    """Where a face sits in the image, in pixels.

    Attributes:
        left (float): Left edge.
        top (float): Top edge.
        right (float): Right edge.
        bottom (float): Bottom edge.
    """

    left: float = Field(title="Esquerda", examples=[120.5])
    top: float = Field(title="Topo", examples=[80.0])
    right: float = Field(title="Direita", examples=[260.5])
    bottom: float = Field(title="Base", examples=[240.0])

    @property
    def width(self) -> float:
        """Box width in pixels.

        Returns:
            float: ``right - left``.
        """
        return self.right - self.left

    @property
    def height(self) -> float:
        """Box height in pixels.

        Returns:
            float: ``bottom - top``.
        """
        return self.bottom - self.top


class DetectedFace(BaseSchema):
    """One face found in an image.

    Attributes:
        box (BoundingBox): Where it is.
        confidence (float): Detector score, ``0..1``.
        landmarks (list[list[float]]): The five ``(x, y)`` points, in
            detector order: left eye, right eye, nose, left mouth
            corner, right mouth corner.
        embedding (list[float]): The face vector, unit length. Empty
            when the caller asked to detect without recognising.
    """

    box: BoundingBox = Field(title="Caixa")
    confidence: float = Field(title="Confiança", examples=[0.93])
    landmarks: list[list[float]] = Field(
        default_factory=list,
        title="Pontos de referência",
        description="Five (x, y) points used to align the crop.",
    )
    embedding: list[float] = Field(
        default_factory=list,
        title="Vetor da face",
        description=(
            "Unit-length embedding. **Biometric data** — see the recipe "
            "before storing it."
        ),
    )


class FaceMatch(BaseSchema):
    """A detected face matched against an enrolled profile.

    Attributes:
        profile_id (UUID): The profile that matched.
        user_id (UUID): Who it belongs to.
        similarity (float): Cosine similarity.
        label (str | None): The profile's label.
    """

    profile_id: UUID = Field(title="Perfil")
    user_id: UUID = Field(title="Pessoa")
    similarity: float = Field(title="Similaridade", examples=[0.91])
    label: str | None = Field(default=None, title="Rótulo")


__all__: list[str] = [
    "BoundingBox",
    "DetectedFace",
    "FaceMatch",
]
