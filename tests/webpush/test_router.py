"""Integration tests for make_web_push_router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    BaseUserModel,
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
    make_web_push_subscription_model,
)


class _RouterUser(BaseUserModel):
    __tablename__ = "push_router_users"


_RouterSubscription = make_web_push_subscription_model(
    user_table="push_router_users",
    tablename="webpush_subscriptions_router",
    class_name="_RouterSubscription",
)

USER_ID = uuid4()

_SUB_BODY: dict[str, Any] = {
    "endpoint": "https://push.example/router-aaa",
    "keys": {"p256dh": "p256dh-key", "auth": "auth-secret"},
}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> WebPushSubscriptionService[Any]:
        repo: BaseRepository[Any] = BaseRepository(session, model=_RouterSubscription)
        dispatcher = WebPushDispatcher("dummy", vapid_subject="mailto:x@y.z")
        return WebPushSubscriptionService(repo, dispatcher)

    def current_user_id() -> UUID:
        return USER_ID

    app = FastAPI()
    app.include_router(
        make_web_push_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=current_user_id,
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


class TestWebPushRouter:
    async def test_subscribe_persists(self, client: AsyncClient) -> None:
        resp = await client.post("/api/push/subscribe", json=_SUB_BODY)
        assert resp.status_code == 201
        assert resp.json() == {"status": "subscribed"}

    async def test_subscribe_is_idempotent(self, client: AsyncClient) -> None:
        await client.post("/api/push/subscribe", json=_SUB_BODY)
        again = await client.post("/api/push/subscribe", json=_SUB_BODY)
        assert again.status_code == 201

    async def test_unsubscribe_removes(self, client: AsyncClient) -> None:
        await client.post("/api/push/subscribe", json=_SUB_BODY)
        resp = await client.post("/api/push/unsubscribe", json=_SUB_BODY)
        assert resp.status_code == 200
        assert resp.json() == {"status": "unsubscribed"}

    async def test_subscribe_rejects_non_https(self, client: AsyncClient) -> None:
        bad = {**_SUB_BODY, "endpoint": "http://insecure.example/x"}
        resp = await client.post("/api/push/subscribe", json=bad)
        assert resp.status_code == 422


def _vapid_app(vapid_public_key: Any) -> FastAPI:
    async def session_factory() -> AsyncIterator[AsyncSession]:
        raise AssertionError("vapid route must not touch the session")
        yield  # pragma: no cover

    def service_factory(session: AsyncSession) -> WebPushSubscriptionService[Any]:
        raise AssertionError("vapid route must not build the service")

    def current_user_id() -> UUID:
        return USER_ID

    app = FastAPI()
    app.include_router(
        make_web_push_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=current_user_id,
            vapid_public_key=vapid_public_key,
        )
    )
    return app


class TestVapidPublicKeyRoute:
    async def _get(self, app: FastAPI) -> Any:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.get("/api/push/vapid-public-key")

    async def test_returns_string_key(self) -> None:
        resp = await self._get(_vapid_app("BKtest123"))
        assert resp.status_code == 200
        assert resp.json() == {"public_key": "BKtest123"}

    async def test_resolves_callable_key(self) -> None:
        resp = await self._get(_vapid_app(lambda: "BKcallable"))
        assert resp.json() == {"public_key": "BKcallable"}

    async def test_empty_key_returns_empty(self) -> None:
        resp = await self._get(_vapid_app(""))
        assert resp.json() == {"public_key": ""}

    async def test_route_absent_when_not_configured(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        async def session_factory() -> AsyncIterator[AsyncSession]:
            async with sessionmaker() as session:
                yield session

        def service_factory(
            session: AsyncSession,
        ) -> WebPushSubscriptionService[Any]:
            repo: BaseRepository[Any] = BaseRepository(
                session, model=_RouterSubscription
            )
            dispatcher = WebPushDispatcher("dummy", vapid_subject="mailto:x@y.z")
            return WebPushSubscriptionService(repo, dispatcher)

        app = FastAPI()
        app.include_router(
            make_web_push_router(
                service_factory=service_factory,
                session_factory=session_factory,
                current_user_id=lambda: USER_ID,
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/push/vapid-public-key")
        await engine.dispose()
        assert resp.status_code == 404
