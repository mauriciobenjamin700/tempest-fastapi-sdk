"""Opt-in FastAPI router for the reviews module.

:func:`make_reviews_router` wires :class:`ReviewService` onto endpoints
for commenting on and rating any polymorphic target. Same factory shape
as the other SDK routers: the caller supplies how a request-scoped
service and the current user resolve; the router owns the HTTP surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.reviews.schemas import (
    CommentCreateSchema,
    CommentResponseSchema,
    RatingAggregateSchema,
    RatingCreateSchema,
    RatingResponseSchema,
)
from tempest_fastapi_sdk.reviews.service import ReviewService


def make_reviews_router(
    *,
    service_factory: Callable[[AsyncSession], ReviewService],
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    current_user_id: Callable[..., Any],
    prefix: str = "/api/reviews",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the reviews router.

    Endpoints (writes require authentication via ``current_user_id``):

    * ``POST {prefix}/{target_type}/{target_id}/comments`` -> post a
      comment (author is the caller).
    * ``GET {prefix}/{target_type}/{target_id}/comments`` -> page the
      comments.
    * ``POST {prefix}/{target_type}/{target_id}/rating`` -> set the
      caller's 0-5 star rating (upsert).
    * ``GET {prefix}/{target_type}/{target_id}`` -> the rating aggregate
      (average, count, per-star distribution).

    Args:
        service_factory (Callable[[AsyncSession], ReviewService]): Builds
            a request-scoped :class:`ReviewService` from the session.
        session_factory (Callable[[], AsyncIterator[AsyncSession]]):
            Yields a request-scoped DB session.
        current_user_id (Callable[..., Any]): FastAPI dependency resolving
            the authenticated user's :class:`~uuid.UUID`.
        prefix (str): URL prefix. Defaults to ``"/api/reviews"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["reviews"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["reviews"]))

    async def _session() -> AsyncIterator[AsyncSession]:
        async for session in session_factory():
            yield session

    def _service(session: AsyncSession = Depends(_session)) -> ReviewService:
        return service_factory(session)

    @router.post(
        "/{target_type}/{target_id}/comments",
        response_model=CommentResponseSchema,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_comment(
        target_type: str,
        target_id: UUID,
        body: CommentCreateSchema,
        user_id: UUID = Depends(current_user_id),
        service: ReviewService = Depends(_service),
    ) -> CommentResponseSchema:
        """Post a comment on a target.

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.
            body (CommentCreateSchema): The comment text + optional parent.
            user_id (UUID): The authenticated author.
            service (ReviewService): Request-scoped service.

        Returns:
            CommentResponseSchema: The persisted comment.
        """
        return await service.add_comment(
            target_type,
            target_id,
            user_id,
            body.body,
            parent_id=body.parent_id,
        )

    @router.get("/{target_type}/{target_id}/comments")
    async def list_comments(
        target_type: str,
        target_id: UUID,
        page: int = 1,
        page_size: int = 20,
        service: ReviewService = Depends(_service),
    ) -> dict[str, Any]:
        """Page a target's comments (oldest first).

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.
            page (int): 1-indexed page number.
            page_size (int): Comments per page.
            service (ReviewService): Request-scoped service.

        Returns:
            dict[str, Any]: The paginated comment payload.
        """
        return await service.list_comments(
            target_type,
            target_id,
            page=page,
            page_size=page_size,
        )

    @router.post(
        "/{target_type}/{target_id}/rating",
        response_model=RatingResponseSchema,
    )
    async def rate(
        target_type: str,
        target_id: UUID,
        body: RatingCreateSchema,
        user_id: UUID = Depends(current_user_id),
        service: ReviewService = Depends(_service),
    ) -> RatingResponseSchema:
        """Set the caller's 0-5 star rating for a target (upsert).

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.
            body (RatingCreateSchema): The star score.
            user_id (UUID): The authenticated user.
            service (ReviewService): Request-scoped service.

        Returns:
            RatingResponseSchema: The created or updated rating.
        """
        return await service.rate(target_type, target_id, user_id, body.stars)

    @router.get(
        "/{target_type}/{target_id}",
        response_model=RatingAggregateSchema,
    )
    async def aggregate(
        target_type: str,
        target_id: UUID,
        service: ReviewService = Depends(_service),
    ) -> RatingAggregateSchema:
        """Return the rating aggregate for a target.

        Args:
            target_type (str): The kind of target.
            target_id (UUID): The target id.
            service (ReviewService): Request-scoped service.

        Returns:
            RatingAggregateSchema: Average, count and per-star
            distribution.
        """
        return await service.aggregate(target_type, target_id)

    return router


__all__: list[str] = [
    "make_reviews_router",
]
