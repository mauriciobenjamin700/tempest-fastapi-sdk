"""``POST /subscribe`` refuses an endpoint the dispatcher would refuse (#660).

Delivery already refused endpoints inside the server's network; the router
still stored them, so every send re-tried and re-refused a row that could
never work. The router now runs the dispatcher's own check — same resolver,
same ``allow_private_networks`` switch — before storing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
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
from tempest_fastapi_sdk.utils._egress import Resolver


class _EgressUser(BaseUserModel):
    __tablename__ = "push_egress_router_users"


_EgressSubscription = make_web_push_subscription_model(
    user_table="push_egress_router_users",
    tablename="webpush_subscriptions_egress_router",
    class_name="_EgressSubscription",
)

USER_ID: UUID = uuid4()

_KEYS: dict[str, str] = {"p256dh": "p256dh-key", "auth": "auth-secret"}


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


@pytest.fixture
async def make_client() -> AsyncIterator[Callable[..., Any]]:
    """Yield a builder of clients whose dispatcher is configured per test.

    Yields:
        Callable[..., Any]: ``await make_client(resolver=..., allow=...)``
        returns ``(client, service_factory)``.
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
        """Mount the router over a dispatcher with the given egress config.

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

        def service_factory(session: AsyncSession) -> WebPushSubscriptionService[Any]:
            repo: BaseRepository[Any] = BaseRepository(
                session, model=_EgressSubscription
            )
            return WebPushSubscriptionService(repo, dispatcher)

        def current_user_id() -> UUID:
            return USER_ID

        app = FastAPI()
        app.state.sessionmaker = sessionmaker
        app.include_router(
            make_web_push_router(
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


class TestSubscribeRefusesInternalEndpoints:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://127.0.0.1/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://[::1]/x",
            "https://10.1.2.3:8443/x",
        ],
    )
    async def test_internal_ip_literal_is_422(
        self, make_client: Callable[..., Any], endpoint: str
    ) -> None:
        client: AsyncClient = await make_client(resolver=_resolver("93.184.216.34"))
        resp = await client.post(
            "/api/push/subscribe", json={"endpoint": endpoint, "keys": _KEYS}
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Push endpoint must be a public https URL"

    @pytest.mark.parametrize(
        "answer", [("127.0.0.1",), ("93.184.216.34", "10.0.0.1"), ()]
    )
    async def test_dns_answer_inside_the_network_is_422(
        self, make_client: Callable[..., Any], answer: Sequence[str]
    ) -> None:
        client: AsyncClient = await make_client(resolver=_resolver(*answer))
        resp = await client.post(
            "/api/push/subscribe",
            json={"endpoint": "https://push.test/a", "keys": _KEYS},
        )
        assert resp.status_code == 422

    async def test_refused_endpoint_is_not_stored(
        self, make_client: Callable[..., Any]
    ) -> None:
        refusing: AsyncClient = await make_client(resolver=_resolver("10.0.0.1"))
        body = {"endpoint": "https://push.test/stored", "keys": _KEYS}
        assert (
            await refusing.post("/api/push/subscribe", json=body)
        ).status_code == 422
        app: Any = refusing._transport.app  # type: ignore[attr-defined]
        async with app.state.sessionmaker() as session:
            rows = await session.execute(select(_EgressSubscription))
            assert rows.scalars().all() == []

    async def test_public_endpoint_is_accepted(
        self, make_client: Callable[..., Any]
    ) -> None:
        client: AsyncClient = await make_client(resolver=_resolver("93.184.216.34"))
        resp = await client.post(
            "/api/push/subscribe",
            json={"endpoint": "https://push.test/ok", "keys": _KEYS},
        )
        assert resp.status_code == 201

    async def test_unresolvable_host_is_accepted(
        self, make_client: Callable[..., Any]
    ) -> None:
        """A DNS hiccup does not turn a browser away; delivery re-checks."""

        async def failing(host: str) -> Sequence[str]:
            raise OSError("no such host")

        client: AsyncClient = await make_client(resolver=failing)
        resp = await client.post(
            "/api/push/subscribe",
            json={"endpoint": "https://push.test/later", "keys": _KEYS},
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize(
        "endpoint", ["https://10.1.2.3/self-hosted", "https://push.internal/x"]
    )
    async def test_dispatcher_opt_out_also_opens_registration(
        self, make_client: Callable[..., Any], endpoint: str
    ) -> None:
        client: AsyncClient = await make_client(
            resolver=_resolver("10.0.0.1"), allow_private_networks=True
        )
        resp = await client.post(
            "/api/push/subscribe", json={"endpoint": endpoint, "keys": _KEYS}
        )
        assert resp.status_code == 201
