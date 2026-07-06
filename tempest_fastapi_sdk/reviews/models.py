"""Abstract comment + rating tables for the reviews module.

Polymorphic by design: a comment or rating points at any target via a
``(target_type, target_id)`` pair — ``"product"`` + a product id,
``"post"`` + a post id — so one pair of tables serves every reviewable
entity without a foreign key per type.

As with the SDK's other reusable tables, the abstract rows live here and
the project ships the concrete tables (so the author/user FK and
``__tablename__`` live in the application's metadata). Use the ``make_*``
factories for tests and light scripts.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseCommentModel(BaseModel):
    """Abstract comment on a polymorphic target, with optional threading.

    Attributes:
        target_type (str): The kind of thing commented on (e.g.
            ``"product"``).
        target_id (UUID): The id of the target within its type.
        author_id (UUID): FK to the comment author (set by subclass).
        body (str): The comment text.
        parent_id (UUID | None): Optional parent comment id for threaded
            replies; ``None`` for a top-level comment.
    """

    __abstract__ = True

    target_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="The kind of target commented on (e.g. 'product').",
    )
    target_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="The id of the target within its type.",
    )
    author_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the comment author (set by subclass).",
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="The comment text.",
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
        index=True,
        doc="Parent comment id for threaded replies, or NULL.",
    )


class BaseRatingModel(BaseModel):
    """Abstract 0-to-5-star rating of a polymorphic target, one per user.

    A ``(target_type, target_id, user_id)`` triple is unique — a user
    rates a target once; re-rating updates the existing row (see
    :meth:`~tempest_fastapi_sdk.reviews.ReviewService.rate`).

    Attributes:
        target_type (str): The kind of thing rated.
        target_id (UUID): The id of the target within its type.
        user_id (UUID): FK to the rating user (set by subclass).
        stars (int): The score, an integer in ``0..5``.
    """

    __abstract__ = True

    target_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="The kind of target rated (e.g. 'product').",
    )
    target_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="The id of the target within its type.",
    )
    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the rating user (set by subclass).",
    )
    stars: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        doc="Star score, an integer in 0..5.",
    )


def make_comment_model(
    *,
    user_table: str = "users",
    tablename: str = "comments",
    class_name: str = "CommentModel",
) -> type[BaseCommentModel]:
    """Build a concrete ``CommentModel`` subclass at runtime.

    Args:
        user_table (str): Table name of the concrete user model the author
            FK references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseCommentModel]: A concrete mapped class.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "author_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseCommentModel,), attrs)


def make_rating_model(
    *,
    user_table: str = "users",
    tablename: str = "ratings",
    class_name: str = "RatingModel",
) -> type[BaseRatingModel]:
    """Build a concrete ``RatingModel`` subclass at runtime.

    Args:
        user_table (str): Table name of the concrete user model the FK
            references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseRatingModel]: A concrete mapped class with a unique
        ``(target_type, target_id, user_id)`` constraint.
    """
    from sqlalchemy import UniqueConstraint

    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "__table_args__": (
            UniqueConstraint(
                "target_type",
                "target_id",
                "user_id",
                name="uq_rating_target_user",
            ),
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseRatingModel,), attrs)


__all__: list[str] = [
    "BaseCommentModel",
    "BaseRatingModel",
    "make_comment_model",
    "make_rating_model",
]
