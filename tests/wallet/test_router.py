"""make_wallet_router over a real SQLite database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    register_exception_handlers,
)
from tempest_fastapi_sdk.integrations.payment import PixKeyType
from tempest_fastapi_sdk.testing.fakes import FakePayoutProvider
from tempest_fastapi_sdk.wallet import (
    PixDestinationSchema,
    WalletBalanceMixin,
    WalletService,
    make_wallet_entry_model,
    make_wallet_router,
)


class _RouterWalletUser(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "router_wallet_users"


_RouterEntry = make_wallet_entry_model(
    user_table="router_wallet_users",
    tablename="router_wallet_entries",
    class_name="_RouterWalletEntry",
)


def _service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=_RouterWalletUser),
        entries=BaseRepository(session, model=_RouterEntry),
    )


@pytest.fixture
async def wallet_app(
    db: AsyncDatabaseManager,
) -> AsyncIterator[tuple[AsyncClient, UUID, FakePayoutProvider]]:
    """An app with the wallet router, one funded user and a fake payout.

    Args:
        db (AsyncDatabaseManager): The in-memory database.

    Yields:
        tuple[AsyncClient, UUID, FakePayoutProvider]: Client, user id,
        payout provider.
    """
    async with db.get_session_context() as session:
        user = _RouterWalletUser(
            email=f"{uuid4()}@example.com", hashed_password="x", wallet_cents=0
        )
        session.add(user)
        await session.commit()
        user_id = user.id
        await _service(session).credit(user_id, 1_500, kind="SALE")
    payout = FakePayoutProvider()

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with db.get_session_context() as session:
            yield session

    def current_user() -> UUID:
        return user_id

    def destination() -> PixDestinationSchema:
        return PixDestinationSchema(
            pix_key="driver@example.com", pix_key_type=PixKeyType.EMAIL
        )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_wallet_router(
            service_factory=_service,
            session_factory=sessions,
            current_user_id=current_user,
            payout_provider=lambda: payout,
            pix_destination=destination,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, user_id, payout


async def test_balance(
    wallet_app: tuple[AsyncClient, UUID, FakePayoutProvider],
) -> None:
    client, user_id, _payout = wallet_app

    response = await client.get("/api/wallet/balance")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert body["available_cents"] == 1_500


async def test_statement(
    wallet_app: tuple[AsyncClient, UUID, FakePayoutProvider],
) -> None:
    client, _user_id, _payout = wallet_app

    response = await client.get("/api/wallet/statement")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["amount_cents"] == 1_500


async def test_withdraw_goes_to_the_registered_key(
    wallet_app: tuple[AsyncClient, UUID, FakePayoutProvider],
) -> None:
    client, _user_id, payout = wallet_app

    response = await client.post("/api/wallet/withdraw", json={"amount_cents": 500})

    assert response.status_code == 200
    assert response.json()["payout"]["status"] == "confirmed"
    assert payout.transfers[0].pix_key == "driver@example.com"
    assert payout.transfers[0].amount_cents == 500


async def test_withdraw_ignores_a_key_in_the_body(
    wallet_app: tuple[AsyncClient, UUID, FakePayoutProvider],
) -> None:
    """The key comes from the profile dependency, never from the request."""
    client, _user_id, payout = wallet_app

    await client.post(
        "/api/wallet/withdraw",
        json={"amount_cents": 100, "pix_key": "attacker@example.com"},
    )

    assert payout.transfers[0].pix_key == "driver@example.com"


async def test_withdraw_beyond_the_balance_is_409(
    wallet_app: tuple[AsyncClient, UUID, FakePayoutProvider],
) -> None:
    client, _user_id, payout = wallet_app

    response = await client.post(
        "/api/wallet/withdraw", json={"amount_cents": 99_999}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "WALLET_INSUFFICIENT_BALANCE"
    assert payout.transfers == []


async def test_a_user_without_a_key_never_reaches_the_service(
    db: AsyncDatabaseManager,
) -> None:
    payout = FakePayoutProvider()

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with db.get_session_context() as session:
            yield session

    def no_key() -> PixDestinationSchema:
        raise HTTPException(status_code=400, detail="Cadastre uma chave Pix")

    app = FastAPI()
    app.include_router(
        make_wallet_router(
            service_factory=_service,
            session_factory=sessions,
            current_user_id=lambda: uuid4(),
            payout_provider=lambda: payout,
            pix_destination=no_key,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/wallet/withdraw", json={})

    assert response.status_code == 400
    assert payout.calls == []
