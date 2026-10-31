"""``POST /register`` refuses a web endpoint the dispatcher would refuse (#660).

The unified router stores browser subscriptions too, so it runs the same
check as :func:`make_web_push_router`, through the
:class:`WebPushTransport` dispatcher the service delivers with. Mobile
tokens are not URLs and are never checked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
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
    WebPushDispatcher,
    WebPushTransport,
    make_device_token_model,
    make_push_router,
)
from tempest_fastapi_sdk.utils._egress import Resolver


class _EgressDeviceUser(BaseUserModel):
    __tablename__ = "push_egress_device_users"


_EgressDevice = make_device_token_model(
    user_table="push_egress_device_users",
    tablename="device_tokens_egress_router",
    class_name="_EgressDevice",
)

USER_ID: UUID = uuid4()


class _MobileTransport:
    """Accepts mobile deliveries, for the router-level tests."""

    platforms: frozenset[str] = frozenset({"ios", "android"})

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Accept the delivery.

        Args:
            device (PushDevice): The target device.
            payload (PushPayloadSchema): The notification.
        """
        return None


def _resolver(*addresses: str) -> Resolver:
    """Build a resolver answering ``addresses`` for every host.

    Args:
        *addresses (str): The addresses to answer.

    Returns:
        Resolver: The async resolver.
    """

    async def resolve(host: str) -> Sequence[str]:
        """Answer the fixed addresses.

        Args:
            host (str): The hostname being resolved.

        Returns:
            Sequence[str]: The configured addresses.
        """
        return list(addresses)

    return resolve


def _web(token: str) -> dict[str, Any]:
    """Build a browser registration body.

    Args:
        token (str): The push endpoint.

    Returns:
        dict[str, Any]: The JSON body.
    """
    return {"token": token, "platform": "web", "p256dh": "k", "auth": "a"}


@pytest.fixture
async def make_client() -> AsyncIterator[Callable[..., Any]]:
    """Yield a builder of clients whose web dispatcher is configured per test.

    Yields:
        Callable[..., Any]: ``await make_client(resolver=..., allow=...)``.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    clients: list[AsyncClient] = []

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    async def build(
        *, resolver: Resolver, allow_private_networks: bool = False
    ) -> AsyncClient:
        """Mount the router over a web transport with the given config.

        Args:
            resolver (Resolver): The dispatcher's resolver.
            allow_private_networks (bool): The dispatcher's opt-out.

        Returns:
            AsyncClient: A client bound to the app.
        """
        dispatcher = WebPushDispatcher(
            "dummy",
            vapid_subject="mailto:x@y.z",
            resolver=resolver,
            allow_private_networks=allow_private_networks,
        )

        def service_factory(session: AsyncSession) -> DeviceService[Any]:
            repository: BaseRepository[Any] = BaseRepository(
                session, model=_EgressDevice
            )
            return DeviceService(
                repository, [WebPushTransport(dispatcher), _MobileTransport()]
            )

        def current_user_id() -> UUID:
            return USER_ID

        app = FastAPI()
        app.include_router(
            make_push_router(
                service_factory=service_factory,
                session_factory=session_factory,
                current_user_id=current_user_id,
            )
        )
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield build
    for client in clients:
        await client.aclose()
    await engine.dispose()


class TestRegisterRefusesInternalWebEndpoints:
    async def test_internal_ip_literal_is_422(
        self, make_client: Callable[..., Any]
    ) -> None:
        client: AsyncClient = await make_client(resolver=_resolver("93.184.216.34"))
        resp = await client.post(
            "/api/push/register", json=_web("https://169.254.169.254/x")
        )
        assert resp.status_code == 422

    async def test_dns_answer_inside_the_network_is_422(
        self, make_client: Callable[..., Any]
    ) -> None:
        client: AsyncClient = await make_client(resolver=_resolver("127.0.0.1"))
        resp = await client.post("/api/push/register", json=_web("https://push.test/a"))
        assert resp.status_code == 422

    async def test_public_endpoint_is_accepted(
        self, make_client: Callable[..., Any]
    ) -> None:
        client: AsyncClient = await make_client(resolver=_resolver("93.184.216.34"))
        resp = await client.post("/api/push/register", json=_web("https://push.test/a"))
        assert resp.status_code == 201

    async def test_dispatcher_opt_out_also_opens_registration(
        self, make_client: Callable[..., Any]
    ) -> None:
        client: AsyncClient = await make_client(
            resolver=_resolver("10.0.0.1"), allow_private_networks=True
        )
        resp = await client.post("/api/push/register", json=_web("https://10.1.2.3/x"))
        assert resp.status_code == 201

    async def test_mobile_token_is_never_resolved(
        self, make_client: Callable[..., Any]
    ) -> None:
        async def must_not_resolve(host: str) -> Sequence[str]:
            raise AssertionError("a mobile token is not a URL")

        client: AsyncClient = await make_client(resolver=must_not_resolve)
        resp = await client.post(
            "/api/push/register",
            json={"token": "fcm-token", "platform": "android"},
        )
        assert resp.status_code == 201
