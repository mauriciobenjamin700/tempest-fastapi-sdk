"""Ownership scoping of ``POST /unregister`` in ``make_push_router`` (#523)."""

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
    DeviceService,
    PushDevice,
    PushPayloadSchema,
    make_device_token_model,
    make_push_router,
)


class _OwnershipUser(BaseUserModel):
    __tablename__ = "push_ownership_users"


_OwnershipDevice = make_device_token_model(
    user_table="push_ownership_users",
    tablename="device_tokens_ownership",
    class_name="_OwnershipDevice",
)

ALICE: UUID = uuid4()
MALLORY: UUID = uuid4()
_ALICE_BODY: dict[str, Any] = {"platform": "android", "token": "ALICE-FCM-TOKEN"}


class _NullTransport:
    """Transport that accepts everything, for router-level tests."""

    platforms: frozenset[str] = frozenset({"web", "ios", "android"})

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Accept the delivery.

        Args:
            device (PushDevice): The target device.
            payload (PushPayloadSchema): The notification.
        """
        return None


class _LegacyDeviceService(DeviceService[Any]):
    """Override written against the pre-#523 ``unregister(token)`` signature."""

    async def unregister(self, token: str) -> bool:  # type: ignore[override]
        """Delete by token only, as overrides written before #523 do.

        Args:
            token (str): The device token.

        Returns:
            bool: Whatever the base class answers.
        """
        return await super().unregister(token)


async def _current_user_id(x_user: str = Header()) -> UUID:
    """Resolve the caller from the ``x-user`` header.

    Args:
        x_user (str): The user id the test claims to be.

    Returns:
        UUID: The authenticated user.
    """
    return UUID(x_user)


@pytest.fixture(params=[DeviceService, _LegacyDeviceService], ids=["current", "legacy"])
async def harness(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    """Yield a client and the sessionmaker behind the push router.

    Parametrised over the stock service and an override that still has the
    old ``unregister(token)`` signature, which must be scoped too.

    Args:
        request (pytest.FixtureRequest): Carries the service class.

    Yields:
        tuple[AsyncClient, async_sessionmaker[AsyncSession]]: The client and
        a sessionmaker to inspect the table with.
    """
    service_class: type[DeviceService[Any]] = request.param
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> DeviceService[Any]:
        repository: BaseRepository[Any] = BaseRepository(
            session, model=_OwnershipDevice
        )
        return service_class(repository, [_NullTransport()])

    app = FastAPI()
    app.include_router(
        make_push_router(
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
    """Return the owner of every stored device.

    Args:
        sessionmaker (async_sessionmaker[AsyncSession]): The test database.

    Returns:
        list[UUID]: One ``user_id`` per row.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(_OwnershipDevice.user_id))
        return list(result.scalars().all())


class TestUnregisterOwnership:
    async def test_another_user_cannot_remove_the_device(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """Mallory gets the same 200 as for an unknown token; Alice keeps hers."""
        client, sessionmaker = harness
        registered = await client.post(
            "/api/push/register", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )
        assert registered.status_code == 201

        response = await client.post(
            "/api/push/unregister",
            json=_ALICE_BODY,
            headers={"x-user": str(MALLORY)},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "unregistered"}
        assert await _owners(sessionmaker) == [ALICE]

    async def test_owner_removes_the_device_idempotently(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """Alice removes her own device, and repeating the call still answers 200."""
        client, sessionmaker = harness
        await client.post(
            "/api/push/register", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )

        first = await client.post(
            "/api/push/unregister", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )
        second = await client.post(
            "/api/push/unregister", json=_ALICE_BODY, headers={"x-user": str(ALICE)}
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert await _owners(sessionmaker) == []


class TestServiceScoping:
    async def test_user_id_scopes_the_delete(self) -> None:
        """``unregister(token, user_id=other)`` answers False and keeps the row."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            repository: BaseRepository[Any] = BaseRepository(
                session, model=_OwnershipDevice
            )
            service: DeviceService[Any] = DeviceService(repository, [_NullTransport()])
            session.add(_OwnershipDevice(user_id=ALICE, token="t", platform="android"))
            await session.commit()

            assert await service.unregister("t", user_id=MALLORY) is False
            assert await service.unregister("t", user_id=ALICE) is True
        await engine.dispose()


async def _devices(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[tuple[UUID, str | None]]:
    """Return ``(owner, app_version)`` of every stored device.

    Args:
        sessionmaker (async_sessionmaker[AsyncSession]): The test database.

    Returns:
        list[tuple[UUID, str | None]]: One pair per row.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(_OwnershipDevice.user_id, _OwnershipDevice.app_version)
        )
        return [(row[0], row[1]) for row in result.all()]


class TestRegisterOwnership:
    """``POST /register`` never moves a token to another account (#632)."""

    async def test_another_user_cannot_take_the_device(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """Mallory registers Alice's token.

        Alice keeps the row untouched, and Mallory gets the same 201 and
        body a new registration gets.
        """
        client, sessionmaker = harness
        alice = await client.post(
            "/api/push/register",
            json={**_ALICE_BODY, "app_version": "1.0"},
            headers={"x-user": str(ALICE)},
        )
        mallory = await client.post(
            "/api/push/register",
            json={**_ALICE_BODY, "app_version": "9.9"},
            headers={"x-user": str(MALLORY)},
        )

        assert (mallory.status_code, mallory.json()) == (
            alice.status_code,
            alice.json(),
        )
        assert await _devices(sessionmaker) == [(ALICE, "1.0")]

    async def test_the_owner_refreshes_the_device(
        self, harness: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, sessionmaker = harness
        for version in ("1.0", "1.1"):
            response = await client.post(
                "/api/push/register",
                json={**_ALICE_BODY, "app_version": version},
                headers={"x-user": str(ALICE)},
            )
            assert response.status_code == 201
        assert await _devices(sessionmaker) == [(ALICE, "1.1")]


@pytest.fixture
async def transfer_harness() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    """Yield a client over a push router built with ``transfer_ownership=True``.

    Yields:
        tuple[AsyncClient, async_sessionmaker[AsyncSession]]: The client and
        a sessionmaker to inspect the table with.
    """
    service_class = DeviceService
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> DeviceService[Any]:
        repository: BaseRepository[Any] = BaseRepository(
            session, model=_OwnershipDevice
        )
        return service_class(repository, [_NullTransport()])

    app = FastAPI()
    app.include_router(
        make_push_router(
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
                "/api/push/register", json=_ALICE_BODY, headers={"x-user": str(user)}
            )
            assert response.status_code == 201
        assert await _owners(sessionmaker) == [MALLORY]
