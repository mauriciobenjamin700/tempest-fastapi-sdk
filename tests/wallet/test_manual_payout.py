"""Manual payout: the Mercado Pago router's withdrawal over a real SQLite wallet.

The route debits through :class:`WalletService`, records the payout and
e-mails the administrators through :class:`ManualPayoutProvider`; the mailer
is :class:`FakeEmailUtils`, everything else is the SDK's own code.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    PixKeyType,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import (
    EmailDeliveryException,
    PayoutRejectedException,
)
from tempest_fastapi_sdk.integrations.payment import PayoutRequest
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoPixProvider,
    MercadoPagoWithdrawConfig,
    make_mercado_pago_router,
)
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils, FakeMercadoPagoOrdersAPI
from tempest_fastapi_sdk.wallet import (
    ManualPayoutProvider,
    ManualPayoutStatusEnum,
    PixDestinationSchema,
    WalletBalanceMixin,
    WalletService,
    format_brl,
    make_manual_payout_model,
    make_wallet_entry_model,
    mask_pix_key,
)

PIX_KEY: str = "motorista@example.com"
"""The user's registered key."""

ADMIN: str = "financeiro@example.com"
"""The administrator who pays by hand."""


class _PayoutUser(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "manual_payout_users"


_PayoutEntry = make_wallet_entry_model(
    user_table="manual_payout_users",
    tablename="manual_payout_entries",
    class_name="_PayoutEntry",
)

_ManualPayout = make_manual_payout_model(
    user_table="manual_payout_users",
    tablename="manual_payout_records",
    class_name="_ManualPayout",
)


def _service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=_PayoutUser),
        entries=BaseRepository(session, model=_PayoutEntry),
    )


@dataclass
class _Harness:
    """The pieces a test inspects."""

    client: AsyncClient
    db: AsyncDatabaseManager
    user_id: UUID
    mailer: FakeEmailUtils


async def _harness(
    db: AsyncDatabaseManager, *, withdraw: bool = True, cents: int = 12_345
) -> AsyncIterator[_Harness]:
    """Build a service with one funded user and the Mercado Pago router.

    Args:
        db (AsyncDatabaseManager): The in-memory database.
        withdraw (bool): Mount the withdrawal route.
        cents (int): The user's starting balance.

    Yields:
        _Harness: Client, database, user id and mailer.
    """
    async with db.get_session_context() as session:
        user = _PayoutUser(
            email=f"{uuid4()}@example.com", hashed_password="x", wallet_cents=0
        )
        session.add(user)
        await session.commit()
        user_id = user.id
        await _service(session).credit(user_id, cents, kind="SALE")
    mailer = FakeEmailUtils()

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with db.get_session_context() as session:
            yield session

    def current_user() -> UUID:
        return user_id

    def destination() -> PixDestinationSchema:
        return PixDestinationSchema(pix_key=PIX_KEY, pix_key_type=PixKeyType.EMAIL)

    api = FakeMercadoPagoOrdersAPI()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_mercado_pago_router(
            MercadoPagoPixProvider(api.http_client()),
            on_delivery=_ignore,
            secret="segredo",
            withdraw=MercadoPagoWithdrawConfig(
                service_factory=_service,
                session_factory=sessions,
                current_user_id=current_user,
                pix_destination=destination,
                mailer=mailer,
                admin_emails=[ADMIN],
                record_model=_ManualPayout,
                record_url="https://api.example.com/admin/m/manual_payouts/{id}",
            )
            if withdraw
            else None,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield _Harness(client=client, db=db, user_id=user_id, mailer=mailer)


async def _ignore(delivery: object) -> None:
    """Accept a delivery and do nothing.

    Args:
        delivery (object): The delivery.
    """


@pytest.fixture
async def harness(db: AsyncDatabaseManager) -> AsyncIterator[_Harness]:
    """A funded user behind the withdrawal route.

    Args:
        db (AsyncDatabaseManager): The in-memory database.

    Yields:
        _Harness: The harness.
    """
    async for value in _harness(db):
        yield value


async def _balance(h: _Harness) -> int:
    async with h.db.get_session_context() as session:
        return (await _service(session).balance(h.user_id)).total_cents


async def _records(h: _Harness) -> list[_ManualPayout]:  # type: ignore[valid-type]
    async with h.db.get_session_context() as session:
        rows = await session.execute(select(_ManualPayout))
        return list(rows.scalars().all())


async def test_withdraw_zeroes_the_wallet_records_and_mails(harness: _Harness) -> None:
    response = await harness.client.post("/api/mercado-pago/withdraw")

    assert response.status_code == 200
    body = response.json()
    assert body["entry"]["amount_cents"] == -12_345
    assert body["entry"]["kind"] == "WITHDRAW"
    assert body["payout"]["status"] == "pending"
    assert body["payout"]["provider_status"] == "manual_review"
    assert await _balance(harness) == 0
    [record] = await _records(harness)
    correlation = body["payout"]["correlation_id"]
    assert record.status == ManualPayoutStatusEnum.PENDING.value
    assert record.correlation_id == correlation
    assert record.amount_cents == 12_345
    assert record.user_id == harness.user_id
    assert record.pix_key_masked == "mo***om"
    [mail] = harness.mailer.outbox
    assert mail.to == (ADMIN,)
    assert "R$ 123,45" in mail.body
    assert PIX_KEY in mail.body
    assert str(harness.user_id) in mail.body
    assert correlation in mail.body
    assert f"/admin/m/manual_payouts/{record.id}" in mail.body


async def test_withdraw_takes_no_amount(harness: _Harness) -> None:
    response = await harness.client.post(
        "/api/mercado-pago/withdraw", json={"amount_cents": 100}
    )

    assert response.status_code == 200
    assert response.json()["entry"]["amount_cents"] == -12_345
    assert await _balance(harness) == 0


async def test_second_withdraw_with_nothing_left_is_409(harness: _Harness) -> None:
    await harness.client.post("/api/mercado-pago/withdraw")

    response = await harness.client.post("/api/mercado-pago/withdraw")

    assert response.status_code == 409
    assert response.json()["code"] == "WALLET_INSUFFICIENT_BALANCE"
    assert len(harness.mailer.outbox) == 1


async def test_mail_failure_returns_the_debit_and_cancels_the_record(
    harness: _Harness,
) -> None:
    harness.mailer.fail_next(EmailDeliveryException())

    response = await harness.client.post("/api/mercado-pago/withdraw")

    assert response.status_code == 502
    assert response.json()["code"] == "WALLET_PAYOUT_REJECTED"
    assert await _balance(harness) == 12_345
    [record] = await _records(harness)
    assert record.status == ManualPayoutStatusEnum.CANCELED.value
    assert harness.mailer.outbox == []


async def test_user_without_a_key_gets_what_pix_destination_raises(
    db: AsyncDatabaseManager,
) -> None:
    async def sessions() -> AsyncIterator[AsyncSession]:
        async with db.get_session_context() as session:
            yield session

    def no_key() -> PixDestinationSchema:
        raise HTTPException(422, "cadastre uma chave Pix")

    app = FastAPI()
    app.include_router(
        make_mercado_pago_router(
            MercadoPagoPixProvider(FakeMercadoPagoOrdersAPI().http_client()),
            on_delivery=_ignore,
            secret="segredo",
            withdraw=MercadoPagoWithdrawConfig(
                service_factory=_service,
                session_factory=sessions,
                current_user_id=lambda: uuid4(),
                pix_destination=no_key,
                mailer=FakeEmailUtils(),
                admin_emails=[ADMIN],
            ),
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/mercado-pago/withdraw")
    assert response.status_code == 422
    assert response.json()["detail"] == "cadastre uma chave Pix"


def test_without_config_the_route_is_absent() -> None:
    router = make_mercado_pago_router(
        MercadoPagoPixProvider(FakeMercadoPagoOrdersAPI().http_client()),
        on_delivery=_ignore,
        secret="segredo",
    )
    app = FastAPI()
    app.include_router(router)
    assert "/api/mercado-pago/withdraw" not in app.openapi()["paths"]
    assert "/webhooks/mercado-pago" in app.openapi()["paths"]


def test_config_needs_exactly_one_way_to_pay() -> None:
    with pytest.raises(ValueError, match="`payout` or `mailer`"):
        MercadoPagoWithdrawConfig(
            service_factory=_service,
            session_factory=lambda: None,  # type: ignore[arg-type,return-value]
            current_user_id=uuid4,
            pix_destination=uuid4,
        )


class TestManualPayoutProvider:
    """The provider on its own."""

    async def test_logs_the_key_masked(self, caplog: pytest.LogCaptureFixture) -> None:
        mailer = FakeEmailUtils()
        provider = ManualPayoutProvider(mailer, admin_emails=[ADMIN])
        caplog.set_level(logging.INFO, "tempest_fastapi_sdk.wallet.manual_payout")

        result = await provider.transfer_to_pix_key(
            PayoutRequest(
                amount_cents=100_000_00,
                pix_key="12345678909",
                pix_key_type=PixKeyType.CPF,
                correlation_id="saque-1",
                payee_id="usuario-1",
            )
        )

        assert result.status == "pending"
        assert "12345678909" not in caplog.text
        assert mask_pix_key("12345678909") in caplog.text
        assert "R$ 100.000,00" in mailer.outbox[0].body
        assert "usuario-1" in mailer.outbox[0].body

    def test_refuses_no_admin(self) -> None:
        with pytest.raises(ValueError, match="admin e-mail"):
            ManualPayoutProvider(FakeEmailUtils(), admin_emails=[])

    @pytest.mark.parametrize(
        ("cents", "text"),
        [
            (0, "R$ 0,00"),
            (5, "R$ 0,05"),
            (123_456_789, "R$ 1.234.567,89"),
            (-150, "-R$ 1,50"),
        ],
    )
    def test_format_brl(self, cents: int, text: str) -> None:
        assert format_brl(cents) == text


async def test_unknown_mail_error_keeps_the_debit_and_the_pending_record(
    harness: _Harness,
) -> None:
    harness.mailer.fail_next(RuntimeError("conexão caiu depois do DATA"))

    response = await harness.client.post("/api/mercado-pago/withdraw")

    assert response.status_code == 502
    assert response.json()["code"] == "WALLET_PAYOUT_UNCERTAIN"
    assert await _balance(harness) == 0
    [record] = await _records(harness)
    assert record.status == ManualPayoutStatusEnum.PENDING.value


class _BrokenRecords:
    """A repository whose insert fails, as a database down would."""

    model = _ManualPayout

    async def add(self, row: object) -> object:
        """Fail the insert.

        Args:
            row (object): The row.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("banco fora")


async def test_record_failure_refuses_without_mailing() -> None:
    mailer = FakeEmailUtils()
    provider = ManualPayoutProvider(
        mailer,
        admin_emails=[ADMIN],
        records=_BrokenRecords(),  # type: ignore[arg-type]
    )

    with pytest.raises(PayoutRejectedException):
        await provider.transfer_to_pix_key(
            PayoutRequest(
                amount_cents=100,
                pix_key=PIX_KEY,
                pix_key_type=PixKeyType.EMAIL,
                correlation_id="saque-x",
                payee_id=str(uuid4()),
            )
        )
    assert mailer.outbox == []
