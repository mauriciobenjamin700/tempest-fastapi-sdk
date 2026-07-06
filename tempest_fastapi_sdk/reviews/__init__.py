"""Reviews module — comments and 0-to-5-star ratings on any target.

Reusable building blocks over the SDK primitives (``BaseModel`` /
``BaseRepository`` / pagination / validated fields): polymorphic
abstract tables + ``make_*`` factories, a :class:`ReviewService`
(comment, rate with one vote per user, aggregate), and an opt-in
:func:`make_reviews_router`.
"""

from tempest_fastapi_sdk.reviews.models import (
    BaseCommentModel as BaseCommentModel,
)
from tempest_fastapi_sdk.reviews.models import (
    BaseRatingModel as BaseRatingModel,
)
from tempest_fastapi_sdk.reviews.models import (
    make_comment_model as make_comment_model,
)
from tempest_fastapi_sdk.reviews.models import (
    make_rating_model as make_rating_model,
)
from tempest_fastapi_sdk.reviews.router import (
    make_reviews_router as make_reviews_router,
)
from tempest_fastapi_sdk.reviews.schemas import (
    CommentCreateSchema as CommentCreateSchema,
)
from tempest_fastapi_sdk.reviews.schemas import (
    CommentResponseSchema as CommentResponseSchema,
)
from tempest_fastapi_sdk.reviews.schemas import (
    RatingAggregateSchema as RatingAggregateSchema,
)
from tempest_fastapi_sdk.reviews.schemas import (
    RatingCreateSchema as RatingCreateSchema,
)
from tempest_fastapi_sdk.reviews.schemas import (
    RatingResponseSchema as RatingResponseSchema,
)
from tempest_fastapi_sdk.reviews.service import ReviewService as ReviewService

__all__: list[str] = [
    "BaseCommentModel",
    "BaseRatingModel",
    "CommentCreateSchema",
    "CommentResponseSchema",
    "RatingAggregateSchema",
    "RatingCreateSchema",
    "RatingResponseSchema",
    "ReviewService",
    "make_comment_model",
    "make_rating_model",
    "make_reviews_router",
]
