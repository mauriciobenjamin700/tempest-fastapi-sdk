"""WalletService on a real SQLite database."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    BaseUserModel,
    InsufficientBalanceException,
    PayoutRejectedException,
    PayoutUncertainException,
)
from tempest_fastapi_sdk.integrations.payment import PixKeyType
from tempest_fastapi_sdk.testing.fakes import FakePayoutProvider
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletEntryKind,
    WalletService,
    claim_once,
    make_wallet_entry_model,
)


class _WalletUser(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "wallet_users"


_Entry = make_wallet_entry_model(
    user_table="wallet_users",
    tablename="wallet_test_entries",
    class_name="_WalletTestEntry",
)


class _Order(BaseModel):
    __tablename__ = "wallet_test_orders"

    credited_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)


def _service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=_WalletUser),
        entries=BaseRepository(session, model=_Entry),
    )


async def _user(session: AsyncSession, balance: int = 0) -> UUID:
    user = _WalletUser(
        email=f"{uuid4()}@example.com",
        hashed_password="x",
        wallet_cents=balance,
    )
    session.add(user)
    await session.commit()
    return user.id


async def _stored_balance(session: AsyncSession, user_id: UUID) -> int:
    session.expire_all()
    value = await session.scalar(
        select(_WalletUser.wallet_cents).where(_WalletUser.id == user_id)
    )
    assert value is not None
    return int(value)


class TestConstruction:
    async def test_repositories_must_share_a_session(
        self, session: AsyncSession, db: object
    ) -> None:
        other = AsyncSession(bind=session.bind)
        try:
            with pytest.raises(ValueError, match="share one session"):
                WalletService(
                    balances=BaseRepository(session, model=_WalletUser),
                    entries=BaseRepository(other, model=_Entry),
                )
        finally:
            await other.close()

    async def test_the_balance_attribute_must_exist(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="no attribute 'wallet'"):
            WalletService(
                balances=BaseRepository(session, model=_WalletUser),
                entries=BaseRepository(session, model=_Entry),
                balance_attribute="wallet",
            )


class TestCredit:
    async def test_credit_moves_the_balance_and_writes_the_line(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session, balance=100)

        entry = await _service(session).credit(
            user_id,
            250,
            kind="SALE",
            description="Venda",
            reference_type="order",
            reference_id="o-1",
        )

        assert entry.amount_cents == 250
        assert entry.balance_after_cents == 350
        assert entry.kind == "SALE"
        assert entry.reference_id == "o-1"
        assert await _stored_balance(session, user_id) == 350

    async def test_a_held_credit_is_not_available(self, session: AsyncSession) -> None:
        user_id = await _user(session)
        service = _service(session)

        await service.credit(user_id, 1_000, kind="SALE", hold=timedelta(hours=48))
        balance = await service.balance(user_id)

        assert balance.total_cents == 1_000
        assert balance.held_cents == 1_000
        assert balance.available_cents == 0
        assert balance.next_release_at is not None

    async def test_a_hold_releases_with_time(self, session: AsyncSession) -> None:
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 1_000, kind="SALE", hold=timedelta(hours=48))

        later = datetime.now(UTC) + timedelta(hours=49)
        balance = await service.balance(user_id, now=later)

        assert balance.available_cents == 1_000
        assert balance.next_release_at is None

    @pytest.mark.parametrize("amount", [0, -5])
    async def test_a_non_positive_credit_is_refused(
        self, session: AsyncSession, amount: int
    ) -> None:
        user_id = await _user(session)
        with pytest.raises(ValueError, match="positive"):
            await _service(session).credit(user_id, amount, kind="SALE")

    async def test_an_unknown_wallet_is_refused_and_nothing_is_written(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(LookupError):
            await _service(session).credit(uuid4(), 100, kind="SALE")
        assert (await session.scalars(select(_Entry))).all() == []


class TestDebitAvailable:
    async def test_debit_takes_from_the_available_part(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 1_000, kind="SALE")

        entry = await service.debit_available(user_id, 400, kind="FEE")

        assert entry.amount_cents == -400
        assert entry.balance_after_cents == 600
        assert await _stored_balance(session, user_id) == 600

    async def test_debit_cannot_reach_held_money(self, session: AsyncSession) -> None:
        """The hold is in the UPDATE, so this refuses without a prior read."""
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 300, kind="SALE")
        await service.credit(user_id, 700, kind="SALE", hold=timedelta(hours=48))

        with pytest.raises(InsufficientBalanceException):
            await service.debit_available(user_id, 301, kind="FEE")

        assert await _stored_balance(session, user_id) == 1_000
        await service.debit_available(user_id, 300, kind="FEE")
        assert await _stored_balance(session, user_id) == 700


class TestReverse:
    async def test_reverse_reaches_held_money(self, session: AsyncSession) -> None:
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 500, kind="SALE", hold=timedelta(hours=48))

        entry = await service.reverse(user_id, 500, reference_id="o-9")

        assert entry is not None
        assert entry.kind == WalletEntryKind.REVERSAL
        assert await _stored_balance(session, user_id) == 0

    async def test_reverse_beyond_the_balance_changes_nothing(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 100, kind="SALE")

        assert await service.reverse(user_id, 101) is None
        assert await _stored_balance(session, user_id) == 100


class TestWithdraw:
    async def test_withdraw_pays_the_available_balance(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 900, kind="SALE")
        await service.credit(user_id, 100, kind="SALE", hold=timedelta(hours=48))
        payout = FakePayoutProvider()

        result = await service.withdraw(
            user_id,
            payout=payout,
            pix_key="driver@example.com",
            pix_key_type=PixKeyType.EMAIL,
        )

        assert result.entry.amount_cents == -900
        assert result.entry.kind == WalletEntryKind.WITHDRAW
        assert [t.amount_cents for t in payout.transfers] == [900]
        assert payout.transfers[0].correlation_id == result.entry.reference_id
        assert await _stored_balance(session, user_id) == 100

    async def test_a_rejected_payout_gives_the_money_back(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 500, kind="SALE")
        payout = FakePayoutProvider()
        payout.fail_next(PayoutRejectedException("conta bloqueada"))

        with pytest.raises(PayoutRejectedException):
            await service.withdraw(
                user_id,
                payout=payout,
                pix_key="k",
                pix_key_type=PixKeyType.RANDOM,
            )

        assert await _stored_balance(session, user_id) == 500
        kinds = [
            e.kind
            for e in (
                await session.scalars(select(_Entry).order_by(_Entry.created_at))
            ).all()
        ]
        assert kinds == ["SALE", "WITHDRAW", "WITHDRAW_REFUND"]

    async def test_an_uncertain_payout_keeps_the_debit(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Giving the money back on a timeout could pay it twice."""
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 500, kind="SALE")
        payout = FakePayoutProvider()
        payout.fail_next(TimeoutError("read timeout"))

        with caplog.at_level(logging.CRITICAL), pytest.raises(PayoutUncertainException):
            await service.withdraw(
                user_id,
                payout=payout,
                pix_key="k",
                pix_key_type=PixKeyType.RANDOM,
                correlation_id="w-42",
            )

        assert await _stored_balance(session, user_id) == 0
        assert "w-42" in caplog.text
        assert any(r.levelno == logging.CRITICAL for r in caplog.records)

    async def test_nothing_available_is_refused_before_calling_the_provider(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session)
        service = _service(session)
        await service.credit(user_id, 500, kind="SALE", hold=timedelta(hours=48))
        payout = FakePayoutProvider()

        with pytest.raises(InsufficientBalanceException):
            await service.withdraw(
                user_id, payout=payout, pix_key="k", pix_key_type=PixKeyType.RANDOM
            )

        assert payout.calls == []


class TestStatement:
    async def test_statement_is_newest_first_and_paginated(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session)
        service = _service(session)
        for amount in (100, 200, 300):
            await service.credit(user_id, amount, kind="SALE")

        page = await service.statement(user_id, page=1, page_size=2)

        assert page["total"] == 3
        assert page["pages"] == 2
        assert [e.amount_cents for e in page["items"]] == [300, 200]

    async def test_an_empty_statement_is_a_success(
        self, session: AsyncSession
    ) -> None:
        user_id = await _user(session)
        page = await _service(session).statement(user_id)
        assert page["items"] == []
        assert page["total"] == 0


class TestClaimOnce:
    async def test_only_the_first_claim_wins(self, session: AsyncSession) -> None:
        order = _Order()
        session.add(order)
        await session.commit()

        first = await claim_once(session, _Order, order.id, "credited_at")
        second = await claim_once(session, _Order, order.id, "credited_at")
        await session.commit()

        assert (first, second) == (True, False)
