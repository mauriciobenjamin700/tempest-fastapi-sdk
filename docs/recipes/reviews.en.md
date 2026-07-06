# Comments + ratings

Comments and **0-to-5-star** ratings on anything — product, post, event
— without a table per type. The `tempest_fastapi_sdk.reviews` module is
polymorphic: each comment/rating points at a target via the
`(target_type, target_id)` pair.

Three pieces, over the SDK primitives:

- **Abstract tables** — `BaseCommentModel` (threadable via `parent_id`)
  and `BaseRatingModel` (score `0..5`, one vote per user) + `make_*`
  factories.
- **`ReviewService`** — comment, rate (upsert), fetch the user's score,
  and **aggregate** (average + count + per-star distribution).
- **`make_reviews_router`** — the HTTP endpoints.

!!! info "No extra"
    Just the SDK core. The score uses the new validated `RatingField`
    (`Annotated[int, 0..5]`).

## The tables

The SDK ships the abstract row; the project ships the concrete one with
the author/user FK:

```python
from tempest_fastapi_sdk.reviews import BaseCommentModel, BaseRatingModel
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID


class CommentModel(BaseCommentModel):
    __tablename__ = "comments"

    author_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class RatingModel(BaseRatingModel):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint(
            "target_type", "target_id", "user_id", name="uq_rating_target_user"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

!!! tip "Shortcut for tests"
    ```python
    from tempest_fastapi_sdk.reviews import make_comment_model, make_rating_model

    Comment = make_comment_model()
    Rating = make_rating_model()
    ```

## The service

`ReviewService` takes two repositories (comments and ratings):

```python
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.reviews import ReviewService
from sqlalchemy.ext.asyncio import AsyncSession


def build_review_service(session: AsyncSession) -> ReviewService:
    return ReviewService(
        comments=BaseRepository(session, model=CommentModel),
        ratings=BaseRepository(session, model=RatingModel),
    )


async def demo(session: AsyncSession, product_id: UUID, user_id: UUID) -> None:
    service = build_review_service(session)

    await service.add_comment("product", product_id, user_id, "Excellent!")

    # One vote per user — re-rating updates the same row.
    await service.rate("product", product_id, user_id, 5)
    await service.rate("product", product_id, user_id, 4)  # still one rating

    aggregate = await service.aggregate("product", product_id)
    print(aggregate.average)       # 4.0
    print(aggregate.count)         # 1
    print(aggregate.distribution)  # {0: 0, 1: 0, ..., 4: 1, 5: 0}
```

`aggregate` returns a `RatingAggregateSchema` with `average` (0.0 when
there are no ratings), `count` and `distribution` (count per star, keyed
`0..5`) — exactly the numbers a "4.3 ★ (128 reviews)" widget renders.

## The router

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.reviews import make_reviews_router


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


def current_user_id() -> UUID:
    ...  # your auth dependency resolving the user's UUID


app = FastAPI()
app.include_router(
    make_reviews_router(
        service_factory=build_review_service,
        session_factory=get_session,
        current_user_id=current_user_id,
    )
)
```

Mounted endpoints:

| Method | Route | Does |
| --- | --- | --- |
| `POST` | `/api/reviews/{target_type}/{target_id}/comments` | Comment (author = logged-in user) |
| `GET` | `/api/reviews/{target_type}/{target_id}/comments` | Page the comments |
| `POST` | `/api/reviews/{target_type}/{target_id}/rating` | Set the 0-5 score (upsert) |
| `GET` | `/api/reviews/{target_type}/{target_id}` | Star aggregate |

Commenting and rating require authentication; listing and aggregating are
public. A score outside `0..5` is rejected with `422` by the
`RatingField` validation.

## Recap

- Polymorphic target `(target_type, target_id)` — one pair of tables
  serves every reviewable type.
- `rate` upserts: one vote per user per target.
- `aggregate` yields average + count + distribution, ready for the UI.
- `make_reviews_router` mounts comments + ratings with `0..5` validation.
