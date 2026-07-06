"""Pydantic DTOs for the reviews module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils.fields import NonEmptyStrField, RatingField


class CommentCreateSchema(BaseSchema):
    """Payload to post a comment.

    Attributes:
        body (str): The comment text (non-empty).
        parent_id (UUID | None): Optional parent comment for a reply.
    """

    body: NonEmptyStrField = Field(
        title="Body",
        description="The comment text.",
    )
    parent_id: UUID | None = Field(
        default=None,
        title="Parent id",
        description="Parent comment id for a threaded reply.",
    )


class CommentResponseSchema(BaseSchema):
    """A comment as returned to clients.

    Attributes:
        id (UUID): The comment id.
        target_type (str): The kind of target.
        target_id (UUID): The target id.
        author_id (UUID): The comment author.
        body (str): The comment text.
        parent_id (UUID | None): Parent comment id, if a reply.
        created_at (datetime): When the comment was posted.
    """

    id: UUID
    target_type: str
    target_id: UUID
    author_id: UUID
    body: str
    parent_id: UUID | None = None
    created_at: datetime


class RatingCreateSchema(BaseSchema):
    """Payload to rate a target.

    Attributes:
        stars (int): The score, an integer in ``0..5``.
    """

    stars: RatingField = Field(
        title="Stars",
        description="The score, an integer in 0..5.",
        examples=[5],
    )


class RatingResponseSchema(BaseSchema):
    """A rating as returned to clients.

    Attributes:
        id (UUID): The rating id.
        target_type (str): The kind of target.
        target_id (UUID): The target id.
        user_id (UUID): The rating user.
        stars (int): The score in ``0..5``.
        created_at (datetime): When the rating was created.
    """

    id: UUID
    target_type: str
    target_id: UUID
    user_id: UUID
    stars: int
    created_at: datetime


class RatingAggregateSchema(BaseSchema):
    """Aggregated rating statistics for one target.

    Attributes:
        target_type (str): The kind of target.
        target_id (UUID): The target id.
        average (float): Mean star score (``0.0`` when there are no
            ratings).
        count (int): Number of ratings.
        distribution (dict[int, int]): Count of ratings per star value,
            keyed ``0..5`` (missing values default to ``0``).
    """

    target_type: str
    target_id: UUID
    average: float
    count: int
    distribution: dict[int, int] = Field(default_factory=dict)


__all__: list[str] = [
    "CommentCreateSchema",
    "CommentResponseSchema",
    "RatingAggregateSchema",
    "RatingCreateSchema",
    "RatingResponseSchema",
]
