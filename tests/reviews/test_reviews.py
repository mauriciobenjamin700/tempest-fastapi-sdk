"""Tests for the reviews module — ReviewService and make_reviews_router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import BaseModel, BaseRepository, BaseUserModel
from tempest_fastapi_sdk.reviews import (
    ReviewService,
    make_comment_model,
    make_rating_model,
    make_reviews_router,
)


class _ReviewUser(BaseUserModel):
    __tablename__ = "review_users"


_Comment = make_comment_model(
    user_table="review_users",
    tablename="review_comments",
    class_name="_ReviewComment",
)
_Rating = make_rating_model(
    user_table="review_users",
    tablename="review_ratings",
    class_name="_ReviewRating",
)


def _service(session: AsyncSession) -> ReviewService:
    return ReviewService(
        comments=BaseRepository(session, model=_Comment),
        ratings=BaseRepository(session, model=_Rating),
    )


class TestComments:
    async def test_add_and_list(self, session: AsyncSession) -> None:
        service = _service(session)
        target = uuid4()
        await service.add_comment("product", target, uuid4(), "great")
        await service.add_comment("product", target, uuid4(), "meh")
        page = await service.list_comments("product", target)
        assert page["total"] == 2
        assert [c.body for c in page["items"]] == ["great", "meh"]

    async def test_list_scoped_to_target(self, session: AsyncSession) -> None:
        service = _service(session)
        a, b = uuid4(), uuid4()
        await service.add_comment("product", a, uuid4(), "for-a")
        await service.add_comment("product", b, uuid4(), "for-b")
        page = await service.list_comments("product", a)
        assert page["total"] == 1


class TestRatings:
    async def test_rate_creates(self, session: AsyncSession) -> None:
        service = _service(session)
        target, user = uuid4(), uuid4()
        rating = await service.rate("product", target, user, 5)
        assert rating.stars == 5

    async def test_rate_upserts_one_per_user(self, session: AsyncSession) -> None:
        service = _service(session)
        target, user = uuid4(), uuid4()
        first = await service.rate("product", target, user, 3)
        second = await service.rate("product", target, user, 5)
        assert first.id == second.id
        assert second.stars == 5
        agg = await service.aggregate("product", target)
        assert agg.count == 1

    async def test_get_user_rating(self, session: AsyncSession) -> None:
        service = _service(session)
        target, user = uuid4(), uuid4()
        assert await service.get_user_rating("product", target, user) is None
        await service.rate("product", target, user, 4)
        found = await service.get_user_rating("product", target, user)
        assert found is not None
        assert found.stars == 4

    async def test_aggregate(self, session: AsyncSession) -> None:
        service = _service(session)
        target = uuid4()
        for stars in (5, 5, 3):
            await service.rate("product", target, uuid4(), stars)
        agg = await service.aggregate("product", target)
        assert agg.count == 3
        assert agg.average == pytest.approx(13 / 3)
        assert agg.distribution[5] == 2
        assert agg.distribution[3] == 1
        assert agg.distribution[0] == 0

    async def test_aggregate_empty(self, session: AsyncSession) -> None:
        service = _service(session)
        agg = await service.aggregate("product", uuid4())
        assert agg.count == 0
        assert agg.average == 0.0


USER_ID = uuid4()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> ReviewService:
        return _service(session)

    def current_user_id() -> UUID:
        return USER_ID

    app = FastAPI()
    app.include_router(
        make_reviews_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=current_user_id,
        ),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


class TestReviewsRouter:
    async def test_comment_and_list(self, client: AsyncClient) -> None:
        target = uuid4()
        posted = await client.post(
            f"/api/reviews/product/{target}/comments",
            json={"body": "nice"},
        )
        assert posted.status_code == 201
        listed = await client.get(f"/api/reviews/product/{target}/comments")
        assert [c["body"] for c in listed.json()["items"]] == ["nice"]

    async def test_rate_and_aggregate(self, client: AsyncClient) -> None:
        target = uuid4()
        rated = await client.post(
            f"/api/reviews/product/{target}/rating",
            json={"stars": 4},
        )
        assert rated.status_code == 200
        agg = await client.get(f"/api/reviews/product/{target}")
        assert agg.json()["count"] == 1
        assert agg.json()["average"] == 4.0

    async def test_rejects_out_of_range_stars(self, client: AsyncClient) -> None:
        target = uuid4()
        resp = await client.post(
            f"/api/reviews/product/{target}/rating",
            json={"stars": 6},
        )
        assert resp.status_code == 422
