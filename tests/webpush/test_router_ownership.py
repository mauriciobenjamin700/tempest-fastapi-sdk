"""Ownership scoping of ``POST /unsubscribe`` in ``make_web_push_router`` (#523)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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


class _OwnershipUser(BaseUserModel):
    __tablename__ = "webpush_ownership_users"


_OwnershipSubscription = make_web_push_subscription_model(
    user_table="webpush_ownership_users",
    tablename="webpush_subscriptions_ownership",
    class_name="_OwnershipSubscription",
)

ALICE: UUID = uuid4()
MALLORY: UUID = uuid4()
_ALICE_BODY: dict[str, Any] = {
    "endpoint": "https://push.example/alice",
    "keys": {"p256dh": "p256dh-key", "auth": "auth-secret"},
}


class _LegacySubscriptionService(WebPushSubscriptionService[Any]):
    """Override written against the pre-#523 ``unsubscribe(endpoint)`` signature."""

    async def unsubscribe(self, endpoint: str) -> bool:  # type: ignore[override]
        """Delete by endpoint only, as overrides written before #523 do.

        Args:
            endpoint (str): The push endpoint.

        Returns:
            bool: Whatever the base class answers.
        """
        return await super().unsubscribe(endpoint)


async def _current_user_id(x_user: str = Header()) -> UUID:
    """Resolve the caller from the ``x-user`` header.

    Args:
        x_user (str): The user id the test claims to be.

    Returns:
        UUID: The authenticated user.
    """
    return UUID(x_user)


@pytest.fixture(
    params=[WebPushSubscriptionService, _LegacySubscriptionService],
    ids=["current", "legacy"],
)
async def harness(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    """Yield a client and the sessionmaker behind the Web Push router.

    Parametrised over the stock service and an override that still has the
    old ``unsubscribe(endpoint)`` signature, which must be scoped too.

    Args:
        request (pytest.FixtureRequest): Carries the service class.

    Yields:
        tuple[AsyncClient, async_sessionmaker[AsyncSession]]: The client and
        a sessionmaker to inspect the table with.
    """
    service_class: type[WebPushSubscriptionService[Any]] = request.param
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> WebPushSubscriptionService[Any]:
        repository: BaseRepository[Any] = BaseRepository(
            session, model=_OwnershipSubscription
        )
        dispatcher = WebPushDispatcher("dummy", vapid_subject="mailto:x@y.z")
        return service_class(repository, dispatcher)

    app = FastAPI()
    app.include_router(
        make_web_push_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=_current_user_id,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sessionmaker
    await engine.dispose()


async def _owners(sessionmaker: async_sessionmaker[AsyncSession]) -> list[UUID]:
    """Return the owner of every stored subscription.

    Args:
        sessionmaker (async_sessionmaker[AsyncSession]): The test database.

    Returns:
        list[UUID]: One ``user_id`` per row.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(_OwnershipSubscription.user_id))
        return list(result.scalars().all())


class TestUnsubscribeOwnership:
    async def test_another_user_cannot_remove_the_subscription(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """Mallory gets the same 200 as for an unknown endpoint; Alice keeps hers."""
        client, sessionmaker = harness
        subscribed = await client.post(
            "/api/push/subscribe", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )
        assert subscribed.status_code == 201

        response = await client.post(
            "/api/push/unsubscribe",
            json=_ALICE_BODY,
            headers={"x-user": str(MALLORY)},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "unsubscribed"}
        assert await _owners(sessionmaker) == [ALICE]

    async def test_owner_removes_the_subscription_idempotently(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """Alice removes her own subscription; repeating it still answers 200."""
        client, sessionmaker = harness
        await client.post(
            "/api/push/subscribe", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )

        first = await client.post(
            "/api/push/unsubscribe", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )
        second = await client.post(
            "/api/push/unsubscribe", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert await _owners(sessionmaker) == []


async def _keys(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[tuple[UUID, str]]:
    """Return ``(owner, p256dh)`` of every stored subscription.

    Args:
        sessionmaker (async_sessionmaker[AsyncSession]): The test database.

    Returns:
        list[tuple[UUID, str]]: One pair per row.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(_OwnershipSubscription.user_id, _OwnershipSubscription.p256dh)
        )
        return [(row[0], row[1]) for row in result.all()]


class TestSubscribeOwnership:
    """``POST /subscribe`` never moves an endpoint to another account (#632)."""

    async def test_another_user_cannot_take_the_subscription(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """Mallory sends Alice's endpoint with her own keys.

        Alice keeps the row and her keys, and Mallory gets the same 201 and
        body a new subscription gets, so the endpoint's owner is not
        revealed.
        """
        client, sessionmaker = harness
        alice = await client.post(
            "/api/push/subscribe", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )
        stolen = {**_ALICE_BODY, "keys": {"p256dh": "mallory", "auth": "mallory"}}
        mallory = await client.post(
            "/api/push/subscribe", json=stolen, headers={"x-user": str(MALLORY)}
        )

        assert (mallory.status_code, mallory.json()) == (
            alice.status_code,
            alice.json(),
        )
        assert await _keys(sessionmaker) == [(ALICE, "p256dh-key")]

    async def test_the_owner_refreshes_the_keys(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, sessionmaker = harness
        await client.post(
            "/api/push/subscribe", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )
        renewed = {**_ALICE_BODY, "keys": {"p256dh": "rotated", "auth": "rotated"}}
        response = await client.post(
            "/api/push/subscribe", json=renewed, headers={"x-user": str(ALICE)}
        )

        assert response.status_code == 201
        assert await _keys(sessionmaker) == [(ALICE, "rotated")]


SERVICE_CLASS = WebPushSubscriptionService
"""The stock service the opt-in router below runs."""


@pytest.fixture
async def transfer_harness() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    """Yield a client over a Web Push router built with ``transfer_ownership=True``.

    Yields:
        tuple[AsyncClient, async_sessionmaker[AsyncSession]]: The client and
        a sessionmaker to inspect the table with.
    """
    service_class = SERVICE_CLASS
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> WebPushSubscriptionService[Any]:
        repository: BaseRepository[Any] = BaseRepository(
            session, model=_OwnershipSubscription
        )
        dispatcher = WebPushDispatcher("dummy", vapid_subject="mailto:x@y.z")
        return service_class(repository, dispatcher)

    app = FastAPI()
    app.include_router(
        make_web_push_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=_current_user_id,
            transfer_ownership=True,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sessionmaker
    await engine.dispose()


class TestTransferOwnershipOptIn:
    async def test_the_opt_in_router_moves_the_device(
        self, transfer_harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """With ``transfer_ownership=True`` the last caller becomes the owner."""
        client, sessionmaker = transfer_harness
        for user in (ALICE, MALLORY):
            response = await client.post(
                "/api/push/subscribe", json=_ALICE_BODY, headers={"x-user": str(user)}
            )
            assert response.status_code == 201
        assert await _owners(sessionmaker) == [MALLORY]
