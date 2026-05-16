"""Response schema base with the common DB columns surfaced to clients."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils.datetime import to_utc


class BaseResponseSchema(BaseSchema):
    """Response schema with the four columns every ORM record carries.

    Used as the parent of any ``*ResponseSchema`` whose payload mirrors
    a row from a table inheriting from
    :class:`tempest_fastapi_sdk.db.model.BaseModel`. ``created_at`` and
    ``updated_at`` are normalized to UTC after validation so the API
    always emits timezone-aware timestamps regardless of how the DB
    driver returned them.

    Attributes:
        id (UUID): The unique identifier of the record.
        is_active (bool): Whether the record is active (soft-delete
            convention).
        created_at (datetime): The creation timestamp, normalized
            to UTC.
        updated_at (datetime): The last update timestamp, normalized
            to UTC.
    """

    id: UUID = Field(
        title="ID",
        description="The unique identifier of the record.",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    is_active: bool = Field(
        title="Is Active",
        description="Whether the record is active.",
        examples=[True, False],
    )
    created_at: datetime = Field(
        title="Created At",
        description="The creation timestamp of the record (UTC).",
        examples=["2024-01-01T12:00:00Z"],
    )
    updated_at: datetime = Field(
        title="Updated At",
        description="The last update timestamp of the record (UTC).",
        examples=["2024-01-02T12:00:00Z"],
    )

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalize_utc(cls, value: datetime) -> datetime:
        """Force every timestamp to be timezone-aware in UTC.

        Args:
            value (datetime): The incoming datetime value.

        Returns:
            datetime: The same instant expressed in UTC.
        """
        return to_utc(value)


__all__: list[str] = [
    "BaseResponseSchema",
]
