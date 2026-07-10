"""Tests for BaseRepository eager-loading via the ``with_`` argument."""

from uuid import UUID

import pytest
from sqlalchemy import ForeignKey, String, Uuid, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tempest_fastapi_sdk import BaseModel, BaseRepository


class Author(BaseModel):
    __tablename__ = "author_eager"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    books: Mapped[list["Book"]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
    )


class Book(BaseModel):
    __tablename__ = "book_eager"

    title: Mapped[str] = mapped_column(String(128), nullable=False)
    author_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("author_eager.id"), nullable=False
    )
    author: Mapped[Author] = relationship(back_populates="books")
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
    )


class Review(BaseModel):
    __tablename__ = "review_eager"

    body: Mapped[str] = mapped_column(String(255), nullable=False)
    book_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("book_eager.id"), nullable=False
    )
    book: Mapped[Book] = relationship(back_populates="reviews")


@pytest.fixture
def authors(session: AsyncSession) -> BaseRepository[Author]:
    return BaseRepository(session, model=Author)


def _unloaded(instance: object) -> set[str]:
    """Return the set of relationship/column names not yet loaded."""
    return set(inspect(instance).unloaded)


async def _seed(session: AsyncSession) -> UUID:
    """Persist one author with two books, one of which has a review.

    Returns the author id and detaches every instance from the session
    so a subsequent load starts from an empty identity map — the only
    way to observe whether ``with_`` eager-loaded the relationships.
    """
    author = Author(name="Ada")
    book_a = Book(title="Notes")
    book_b = Book(title="Essays")
    book_a.reviews.append(Review(body="great"))
    author.books.extend([book_a, book_b])
    session.add(author)
    await session.commit()
    await session.refresh(author)
    author_id = author.id
    session.expunge_all()
    return author_id


class TestEagerLoad:
    async def test_get_by_id_eager_loads_relationship(
        self, session: AsyncSession, authors: BaseRepository[Author]
    ) -> None:
        author_id = await _seed(session)
        loaded = await authors.get_by_id(author_id, with_=["books"])
        # `books` is already populated — reading it triggers no lazy IO.
        assert "books" not in _unloaded(loaded)
        assert {b.title for b in loaded.books} == {"Notes", "Essays"}

    async def test_get_without_with_leaves_relationship_unloaded(
        self, session: AsyncSession, authors: BaseRepository[Author]
    ) -> None:
        author_id = await _seed(session)
        loaded = await authors.get_by_id(author_id)
        assert "books" in _unloaded(loaded)

    async def test_list_eager_loads(
        self, session: AsyncSession, authors: BaseRepository[Author]
    ) -> None:
        await _seed(session)
        rows = await authors.list(with_=["books"])
        assert len(rows) == 1
        assert "books" not in _unloaded(rows[0])

    async def test_first_eager_loads(
        self, session: AsyncSession, authors: BaseRepository[Author]
    ) -> None:
        await _seed(session)
        row = await authors.first(with_=["books"])
        assert row is not None
        assert "books" not in _unloaded(row)

    async def test_nested_dotted_path(
        self, session: AsyncSession, authors: BaseRepository[Author]
    ) -> None:
        author_id = await _seed(session)
        loaded = await authors.get_by_id(author_id, with_=["books.reviews"])
        assert "books" not in _unloaded(loaded)
        by_title = {b.title: b for b in loaded.books}
        # The nested collection loaded too — no lazy IO to read it.
        assert "reviews" not in _unloaded(by_title["Notes"])
        assert by_title["Notes"].reviews[0].body == "great"

    async def test_unknown_relationship_raises_value_error(
        self, authors: BaseRepository[Author]
    ) -> None:
        with pytest.raises(ValueError, match="no relationship 'ghost'"):
            await authors.list(with_=["ghost"])

    async def test_unknown_nested_segment_raises_value_error(
        self, authors: BaseRepository[Author]
    ) -> None:
        with pytest.raises(ValueError, match="no relationship 'ghost'"):
            await authors.list(with_=["books.ghost"])
