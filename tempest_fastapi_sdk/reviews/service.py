"""Business logic for comments and 0-to-5-star ratings.

:class:`ReviewService` bridges two repositories (comments, ratings) into
the operations a reviews feature needs: post/list threaded comments, and
rate a target with one vote per user (re-rating updates the existing
row). :meth:`ReviewService.aggregate` computes the average, count and
per-star distribution for a target — the numbers a "4.3 ★ (128
reviews)" widget renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from tempest_fastapi_sdk.reviews.schemas import (
    CommentResponseSchema,
    RatingAggregateSchema,
    RatingResponseSchema,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.repository import BaseRepository

_STAR_VALUES: tuple[int, ...] = (0, 1, 2, 3, 4, 5)


class ReviewService:
    """Post comments and manage per-user star ratings on any target.

    Attributes:
        comments (BaseRepository[Any]): Repository for the comment table.
        ratings (BaseRepository[Any]): Repository for the rating table.
    """

    def __init__(
        self,
        *,
        comments: BaseRepository[Any],
        ratings: BaseRepository[Any],
    ) -> None:
        """Initialize the service.

        Args:
            comments (BaseRepository[Any]): Comment repository.
            ratings (BaseRepository[Any]): Rating repository.
        """
        self.comments: BaseRepository[Any] = comments
        self.ratings: BaseRepository[Any] = ratings

    async def add_comment(
        self,
        target_type: str,
        target_id: UUID,
        author_id: UUID,
        body: str,
        *,
        parent_id: UUID | None = None,
    ) -> CommentResponseSchema:
        """Post a comment on a target.

        Args:
            target_type (str): The kind of target (e.g. ``"product"``).
            target_id (UUID): The target id.
            author_id (UUID): The commenting user.
            body (str): The comment text.
            parent_id (UUID | None): Optional parent comment for a reply.

        Returns:
            CommentResponseSchema: The persisted comment.
        """
        row = await self.comments.add(
            self.comments.model(
                target_type=target_type,
                target_id=target_id,
                author_id=author_id,
                body=body,
                parent_id=parent_id,
            ),
        )
        return CommentResponseSchema.model_validate(row)

    async def list_comments(
        self,
        target_type: str,
        target_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
    ) -> dict[str, Any]:
        """Return an offset page of a target's comments.

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.
            page (int): 1-indexed page number.
            page_size (int): Comments per page.
            ascending (bool): Oldest-first when ``True``.

        Returns:
            dict[str, Any]: ``items`` (mapped
            :class:`CommentResponseSchema`), ``total``, ``page``,
            ``size`` and ``pages``.
        """
        result = await self.comments.paginate(
            filters={"target_type": target_type, "target_id": target_id},
            order_by="created_at",
            page=page,
            page_size=page_size,
            ascending=ascending,
        )
        items = [CommentResponseSchema.model_validate(row) for row in result["items"]]
        return {**result, "items": items}

    async def rate(
        self,
        target_type: str,
        target_id: UUID,
        user_id: UUID,
        stars: int,
    ) -> RatingResponseSchema:
        """Set a user's star rating for a target (one vote per user).

        Upserts: if the user already rated the target, the existing row's
        ``stars`` is updated; otherwise a new row is created.

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.
            user_id (UUID): The rating user.
            stars (int): The score in ``0..5``.

        Returns:
            RatingResponseSchema: The created or updated rating.
        """
        existing = await self.ratings.get_or_none(
            {
                "target_type": target_type,
                "target_id": target_id,
                "user_id": user_id,
            },
        )
        if existing is not None:
            existing.stars = stars
            updated = await self.ratings.update(existing)
            return RatingResponseSchema.model_validate(updated)
        row = await self.ratings.add(
            self.ratings.model(
                target_type=target_type,
                target_id=target_id,
                user_id=user_id,
                stars=stars,
            ),
        )
        return RatingResponseSchema.model_validate(row)

    async def get_user_rating(
        self,
        target_type: str,
        target_id: UUID,
        user_id: UUID,
    ) -> RatingResponseSchema | None:
        """Return a user's rating for a target, or ``None``.

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.
            user_id (UUID): The user whose rating to fetch.

        Returns:
            RatingResponseSchema | None: The rating, or ``None`` when the
            user has not rated the target.
        """
        row = await self.ratings.get_or_none(
            {
                "target_type": target_type,
                "target_id": target_id,
                "user_id": user_id,
            },
        )
        if row is None:
            return None
        return RatingResponseSchema.model_validate(row)

    async def aggregate(
        self,
        target_type: str,
        target_id: UUID,
    ) -> RatingAggregateSchema:
        """Return average, count and per-star distribution for a target.

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.

        Returns:
            RatingAggregateSchema: The aggregate; ``average`` is ``0.0``
            and every distribution bucket ``0`` when there are no ratings.
        """
        rows = await self.ratings.list(
            filters={"target_type": target_type, "target_id": target_id},
        )
        distribution: dict[int, int] = dict.fromkeys(_STAR_VALUES, 0)
        total = 0
        for row in rows:
            stars = int(row.stars)
            distribution[stars] = distribution.get(stars, 0) + 1
            total += stars
        count = len(rows)
        average = total / count if count else 0.0
        return RatingAggregateSchema(
            target_type=target_type,
            target_id=target_id,
            average=average,
            count=count,
            distribution=distribution,
        )


__all__: list[str] = [
    "ReviewService",
]
