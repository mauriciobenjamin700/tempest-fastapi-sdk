"""Behavior of the wallet module, sequential calls, on SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.exceptions.i18n import default_message_catalog
from tempest_fastapi_sdk.wallet import (
    REVERSAL_REFERENCE_TYPE,
    WALLET_BALANCE_CHECK_NAME,
    WalletEntryKind,
    WalletEntryNotFoundException,
    WalletInsufficientFundsException,
    WalletNotFoundException,
    WalletReferenceConflictException,
    WalletRepository,
    WalletService,
)
from tests.wallet.support import (
    OverdraftEntry,
    OverdraftUser,
    WalletEntry,
    WalletOrder,
    WalletUser,
    make_service,
    seed_user,
)

NOW: datetime = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def _wallet(maker: async_sessionmaker[AsyncSession], user_id: object) -> int:
    """Read a balance on a fresh session.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.
        user_id (object): The user id.

    Returns:
        int: The committed balance.
    """
    async with maker() as session:
        value = await session.scalar(
            select(WalletUser.wallet_cents).where(WalletUser.id == user_id),
        )
    return int(value or 0)


async def _entry_count(maker: async_sessionmaker[AsyncSession]) -> int:
    """Count committed statement lines.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.

    Returns:
        int: The number of rows in the statement table.
    """
    async with maker() as session:
        return int(
            await session.scalar(select(func.count()).select_from(WalletEntry)) or 0
        )


class TestCredit:
    async def test_credit_moves_balance_and_writes_line(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 1_000)
        entry = await make_service(session).credit(
            user_id,
            250,
            reference_type="order",
            reference_id=uuid4(),
            description="order paid",
            now=NOW,
        )
        assert entry.amount_cents == 250
        assert entry.balance_after_cents == 1_250
        assert entry.kind == WalletEntryKind.CREDIT
        assert entry.available_at == NOW
        assert await _wallet(maker, user_id) == 1_250

    async def test_replayed_reference_credits_once(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A webhook delivered twice returns the first entry and moves nothing."""
        user_id = await seed_user(maker)
        service = make_service(session)
        reference = uuid4()
        first = await service.credit(
            user_id, 500, reference_type="order", reference_id=reference
        )
        second = await service.credit(
            user_id, 500, reference_type="order", reference_id=reference
        )
        assert second.id == first.id
        assert await _wallet(maker, user_id) == 500
        assert await _entry_count(maker) == 1

    async def test_replayed_idempotency_key_credits_once(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker)
        service = make_service(session)
        first = await service.credit(
            user_id,
            300,
            reference_type="manual",
            reference_id=uuid4(),
            idempotency_key="req-1",
        )
        second = await service.credit(
            user_id,
            300,
            reference_type="manual",
            reference_id=uuid4(),
            idempotency_key="req-1",
        )
        assert second.id == first.id
        assert await _wallet(maker, user_id) == 300

    async def test_same_reference_other_amount_is_a_conflict(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker)
        service = make_service(session)
        reference = uuid4()
        await service.credit(
            user_id, 500, reference_type="order", reference_id=reference
        )
        with pytest.raises(WalletReferenceConflictException) as caught:
            await service.credit(
                user_id, 900, reference_type="order", reference_id=reference
            )
        assert caught.value.code == "WALLET_REFERENCE_CONFLICT"
        assert await _wallet(maker, user_id) == 500

    async def test_same_reference_other_wallet_is_a_conflict(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        first_user = await seed_user(maker)
        second_user = await seed_user(maker)
        service = make_service(session)
        reference = uuid4()
        await service.credit(
            first_user, 500, reference_type="order", reference_id=reference
        )
        with pytest.raises(WalletReferenceConflictException):
            await service.credit(
                second_user, 500, reference_type="order", reference_id=reference
            )
        assert await _wallet(maker, second_user) == 0

    async def test_missing_wallet(self, session: AsyncSession) -> None:
        with pytest.raises(WalletNotFoundException) as caught:
            await make_service(session).credit(
                uuid4(), 100, reference_type="order", reference_id=uuid4()
            )
        assert caught.value.status_code == 404

    @pytest.mark.parametrize("amount", [0, -5, True])
    async def test_non_positive_amount_is_refused(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
        amount: int,
    ) -> None:
        """A negative credit would be a debit that skipped every check."""
        user_id = await seed_user(maker, 100)
        with pytest.raises(ValueError):
            await make_service(session).credit(
                user_id, amount, reference_type="order", reference_id=uuid4()
            )
        assert await _wallet(maker, user_id) == 100

    async def test_negative_hold_is_refused(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker)
        with pytest.raises(ValueError):
            await make_service(session).credit(
                user_id,
                100,
                reference_type="order",
                reference_id=uuid4(),
                hold=timedelta(seconds=-1),
            )

    async def test_joins_the_callers_transaction(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A failure later in the caller's block takes the credit back too."""
        user_id = await seed_user(maker)
        service = make_service(session)
        with pytest.raises(RuntimeError):
            async with transaction(session):
                await service.credit(
                    user_id, 100, reference_type="order", reference_id=uuid4()
                )
                raise RuntimeError("later step failed")
        assert await _wallet(maker, user_id) == 0
        assert await _entry_count(maker) == 0


class TestDebitAndHold:
    async def test_held_credit_is_not_available(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 100)
        service = make_service(session)
        await service.credit(
            user_id,
            500,
            reference_type="order",
            reference_id=uuid4(),
            hold=timedelta(hours=48),
            now=NOW,
        )
        balance = await service.balance(user_id, now=NOW)
        assert balance.total_cents == 600
        assert balance.held_cents == 500
        assert balance.available_cents == 100
        assert balance.next_release_at == NOW + timedelta(hours=48)
        with pytest.raises(WalletInsufficientFundsException) as caught:
            await service.debit(
                user_id, 600, reference_type="withdraw", reference_id=uuid4(), now=NOW
            )
        assert caught.value.code == "WALLET_INSUFFICIENT_FUNDS"
        assert await _wallet(maker, user_id) == 600

    async def test_hold_releases_at_available_at(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 100)
        service = make_service(session)
        await service.credit(
            user_id,
            500,
            reference_type="order",
            reference_id=uuid4(),
            hold=timedelta(hours=48),
            now=NOW,
        )
        later = NOW + timedelta(hours=48, seconds=1)
        entry = await service.debit(
            user_id, 600, reference_type="withdraw", reference_id=uuid4(), now=later
        )
        assert entry.amount_cents == -600
        assert entry.balance_after_cents == 0
        balance = await service.balance(user_id, now=later)
        assert balance.next_release_at is None

    async def test_debit_takes_available(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 1_000)
        entry = await make_service(session).debit(
            user_id, 400, reference_type="withdraw", reference_id=uuid4()
        )
        assert entry.kind == WalletEntryKind.DEBIT
        assert entry.balance_after_cents == 600
        assert await _wallet(maker, user_id) == 600

    async def test_replayed_debit_takes_once(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 1_000)
        service = make_service(session)
        reference = uuid4()
        first = await service.debit(
            user_id, 400, reference_type="withdraw", reference_id=reference
        )
        second = await service.debit(
            user_id, 400, reference_type="withdraw", reference_id=reference
        )
        assert second.id == first.id
        assert await _wallet(maker, user_id) == 600

    async def test_debit_on_missing_wallet_is_not_found(
        self,
        session: AsyncSession,
    ) -> None:
        """A missing row and a short balance both match nothing; the error splits."""
        with pytest.raises(WalletNotFoundException):
            await make_service(session).debit(
                uuid4(), 1, reference_type="withdraw", reference_id=uuid4()
            )


class TestReverse:
    async def test_reverse_debit_refunds(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 1_000)
        service = make_service(session)
        debit = await service.debit(
            user_id, 400, reference_type="withdraw", reference_id=uuid4()
        )
        reversal = await service.reverse(debit.id, description="PIX refused")
        assert reversal.kind == WalletEntryKind.REVERSAL
        assert reversal.reference_type == REVERSAL_REFERENCE_TYPE
        assert reversal.reference_id == debit.id
        assert reversal.amount_cents == 400
        assert await _wallet(maker, user_id) == 1_000

    async def test_reverse_twice_moves_once(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 1_000)
        service = make_service(session)
        debit = await service.debit(
            user_id, 400, reference_type="withdraw", reference_id=uuid4()
        )
        first = await service.reverse(debit.id)
        second = await service.reverse(debit.id)
        assert second.id == first.id
        assert await _wallet(maker, user_id) == 1_000

    async def test_reverse_held_credit_clears_the_hold(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, 100)
        service = make_service(session)
        credit = await service.credit(
            user_id,
            500,
            reference_type="order",
            reference_id=uuid4(),
            hold=timedelta(hours=48),
            now=NOW,
        )
        reversal = await service.reverse(credit.id, now=NOW)
        assert reversal.available_at == credit.available_at
        balance = await service.balance(user_id, now=NOW)
        assert (balance.total_cents, balance.held_cents, balance.available_cents) == (
            100,
            0,
            100,
        )

    async def test_reverse_spent_credit_is_refused_by_the_check(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """With the CHECK on, taking back money already withdrawn fails cleanly."""
        user_id = await seed_user(maker)
        service = make_service(session)
        credit = await service.credit(
            user_id, 500, reference_type="order", reference_id=uuid4()
        )
        await service.debit(
            user_id, 500, reference_type="withdraw", reference_id=uuid4()
        )
        with pytest.raises(WalletInsufficientFundsException):
            await service.reverse(credit.id)
        assert await _wallet(maker, user_id) == 0
        assert await _entry_count(maker) == 2

    async def test_overdraft_variant_records_the_debt(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker, overdraft=True)
        service = make_service(session, overdraft=True)
        credit = await service.credit(
            user_id, 500, reference_type="order", reference_id=uuid4()
        )
        await service.debit(
            user_id, 500, reference_type="withdraw", reference_id=uuid4()
        )
        reversal = await service.reverse(credit.id)
        assert reversal.balance_after_cents == -500
        balance = await service.balance(user_id)
        assert balance.total_cents == -500
        with pytest.raises(WalletInsufficientFundsException):
            await service.debit(
                user_id, 1, reference_type="withdraw", reference_id=uuid4()
            )

    async def test_reverse_unknown_entry(self, session: AsyncSession) -> None:
        with pytest.raises(WalletEntryNotFoundException):
            await make_service(session).reverse(uuid4())


class TestReads:
    async def test_statement_is_newest_first_and_paged(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker)
        service = make_service(session)
        for index in range(3):
            await service.credit(
                user_id,
                100,
                reference_type="order",
                reference_id=uuid4(),
                now=NOW + timedelta(minutes=index),
            )
        page = await service.statement(user_id, page=1, page_size=2)
        assert page.total == 3
        assert page.pages == 2
        assert [item.balance_after_cents for item in page.items] == [300, 200]

    async def test_statement_of_empty_wallet_is_empty(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        user_id = await seed_user(maker)
        page = await make_service(session).statement(user_id)
        assert page.items == []
        assert page.total == 0

    async def test_balance_of_missing_wallet(self, session: AsyncSession) -> None:
        with pytest.raises(WalletNotFoundException):
            await make_service(session).balance(uuid4())


class TestClaimOnce:
    async def test_second_claim_returns_false(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        order_id = uuid4()
        async with maker() as seeding:
            seeding.add(WalletOrder(id=order_id))
            await seeding.commit()
        repository = WalletRepository(
            session, model=WalletUser, entry_model=WalletEntry
        )
        assert await repository.claim_once(WalletOrder, order_id, "credited_at") is True
        assert (
            await repository.claim_once(WalletOrder, order_id, "credited_at") is False
        )
        assert await repository.claim_once(WalletOrder, uuid4(), "credited_at") is False

    async def test_failed_credit_releases_the_claim(
        self,
        session: AsyncSession,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Claim and credit in one block: the credit failing undoes the claim."""
        order_id = uuid4()
        async with maker() as seeding:
            seeding.add(WalletOrder(id=order_id))
            await seeding.commit()
        service = make_service(session)
        with pytest.raises(WalletNotFoundException):
            async with transaction(session):
                assert await service.balances.claim_once(
                    WalletOrder, order_id, "credited_at"
                )
                await service.credit(
                    uuid4(), 100, reference_type="order", reference_id=order_id
                )
        async with maker() as reading:
            claimed = await reading.scalar(
                select(WalletOrder.credited_at).where(WalletOrder.id == order_id),
            )
        assert claimed is None


class TestConstruction:
    def test_model_without_balance_column_is_refused(
        self,
        session: AsyncSession,
    ) -> None:
        with pytest.raises(TypeError):
            WalletRepository(session, model=WalletOrder, entry_model=WalletEntry)

    def test_mismatched_entries_repository_is_refused(
        self,
        session: AsyncSession,
    ) -> None:
        with pytest.raises(ValueError):
            WalletService(
                balances=WalletRepository(
                    session, model=WalletUser, entry_model=WalletEntry
                ),
                entries=BaseRepository(session, model=OverdraftEntry),
            )

    async def test_check_constraint_only_on_the_default_mixin(
        self,
        engine: AsyncEngine,
    ) -> None:
        async with engine.connect() as connection:
            checks = await connection.run_sync(
                lambda sync: {
                    table: {
                        str(item["name"])
                        for item in inspect(sync).get_check_constraints(table)
                    }
                    for table in (WalletUser.__tablename__, OverdraftUser.__tablename__)
                },
            )
        assert checks[WalletUser.__tablename__] == {
            f"ck_wallet_users_{WALLET_BALANCE_CHECK_NAME}",
        }
        assert checks[OverdraftUser.__tablename__] == set()

    def test_codes_are_translated(self) -> None:
        catalog = default_message_catalog()
        for exception in (
            WalletInsufficientFundsException,
            WalletNotFoundException,
            WalletEntryNotFoundException,
            WalletReferenceConflictException,
        ):
            assert catalog.resolve(exception.code, locale="pt-BR") != exception.code
            assert catalog.resolve(exception.code, locale="en-US") != exception.code
