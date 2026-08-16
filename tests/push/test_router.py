"""Integration tests for make_push_router."""

from __future__ import annotations

import sys
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
    DeviceService,
    PushDevice,
    PushPayloadSchema,
    make_device_token_model,
    make_push_router,
)


class _RouterUser(BaseUserModel):
    __tablename__ = "push_router_device_users"


_RouterDevice = make_device_token_model(
    user_table="push_router_device_users",
    tablename="device_tokens_router",
    class_name="_RouterDevice",
)

USER_ID = uuid4()

_WEB_BODY: dict[str, Any] = {
    "token": "https://push.example/router-web",
    "platform": "web",
    "p256dh": "p256dh-key",
    "auth": "auth-secret",
}
_MOBILE_BODY: dict[str, Any] = {
    "token": "router-android-token",
    "platform": "android",
    "app_version": "1.4.2+310",
}


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


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Yield a client against an app mounting the push router.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> DeviceService[Any]:
        repository: BaseRepository[Any] = BaseRepository(session, model=_RouterDevice)
        return DeviceService(repository, [_NullTransport()])

    def current_user_id() -> UUID:
        return USER_ID

    app = FastAPI()
    app.include_router(
        make_push_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=current_user_id,
            vapid_public_key="BNc8-public-key",
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


class TestRegister:
    async def test_browser_registration(self, client: AsyncClient) -> None:
        """A browser body registers with its encryption keys."""
        response = await client.post("/api/push/register", json=_WEB_BODY)

        assert response.status_code == 201
        assert response.json() == {"status": "registered"}

    async def test_mobile_registration(self, client: AsyncClient) -> None:
        """A phone posts the same shape without key fields."""
        response = await client.post("/api/push/register", json=_MOBILE_BODY)

        assert response.status_code == 201

    async def test_registration_is_idempotent(self, client: AsyncClient) -> None:
        """Posting twice does not fail on the unique token."""
        await client.post("/api/push/register", json=_MOBILE_BODY)
        response = await client.post("/api/push/register", json=_MOBILE_BODY)

        assert response.status_code == 201

    async def test_unknown_platform_is_rejected(self, client: AsyncClient) -> None:
        """The enum is the contract; a typo is a 422, not a stored row."""
        response = await client.post(
            "/api/push/register",
            json={"token": "x", "platform": "windows-phone"},
        )

        assert response.status_code == 422


class TestUnregister:
    async def test_removes_the_device(self, client: AsyncClient) -> None:
        """Unregister answers 200 for a registered device."""
        await client.post("/api/push/register", json=_MOBILE_BODY)

        response = await client.post("/api/push/unregister", json=_MOBILE_BODY)

        assert response.status_code == 200
        assert response.json() == {"status": "unregistered"}

    async def test_unknown_device_is_a_noop(self, client: AsyncClient) -> None:
        """Unregistering something never registered still answers 200."""
        response = await client.post(
            "/api/push/unregister",
            json={"token": "never-registered", "platform": "ios"},
        )

        assert response.status_code == 200


class TestVapidKey:
    async def test_key_is_public(self, client: AsyncClient) -> None:
        """The browser needs the key before it can subscribe at all."""
        response = await client.get("/api/push/vapid-public-key")

        assert response.status_code == 200
        assert response.json() == {"public_key": "BNc8-public-key"}


def test_module_imports_without_the_firebase_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``import tempest_fastapi_sdk.push`` must not need ``[firebase]``.

    The FCM transport imports ``firebase_admin`` inside its constructor, so
    a web-only service installs no Google packages. This re-imports the
    package with ``firebase_admin`` blocked to prove the import graph does
    not reach it.
    """
    for name in list(sys.modules):
        if name.startswith("tempest_fastapi_sdk.push"):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "firebase_admin", None)
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", None)

    import tempest_fastapi_sdk.push as push_module

    assert push_module.DeviceService is not None

    with pytest.raises(ImportError, match=r"\[firebase\] extra"):
        push_module.FCMTransport()
