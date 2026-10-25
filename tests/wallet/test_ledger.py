"""``WalletLedger``: the wallet's data layer, separate from the service (#617).

A service of a project that moves a balance inside its own transaction —
the transport settlement that credits the driver in the same block that
marks the trip as settled — receives the ledger like a repository. These
tests pin that the ledger works on its own, that the caller's block owns
the commit (both writes land or neither does), and that ``WalletService``
still builds and exposes it the way it did before.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.wallet import (
    WalletEntrySchema,
    WalletLedger,
    WalletService,
    claim_once,
)
from tests.wallet.support import (
    GuardOrder,
    GuardWalletEntry,
    GuardWalletUser,
    make_ledger,
    seed_order,
    seed_user,
)
from tests.wallet.support import engine as engine
from tests.wallet.support import maker as maker
from tests.wallet.support import postgres_url as postgres_url


async def wallet_cents(maker: async_sessionmaker[AsyncSession], user_id: UUID) -> int:
    """Read a wallet balance in a fresh session.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.
        user_id (UUID): The wallet owner.

    Returns:
        int: The stored balance.
    """
    async with maker() as session:
        value = await session.scalar(
            select(GuardWalletUser.wallet_cents).where(GuardWalletUser.id == user_id)
        )
        return int(value or 0)


async def credited_at_is_set(
    maker: async_sessionmaker[AsyncSession], order_id: UUID
) -> bool:
    """Tell whether an order's settlement claim was committed.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.
        order_id (UUID): The order.

    Returns:
        bool: True when ``credited_at`` is stored.
    """
    async with maker() as session:
        value = await session.scalar(
            select(GuardOrder.credited_at).where(GuardOrder.id == order_id)
        )
        return value is not None


class SettlementFailedError(RuntimeError):
    """Raised by the test settlement after both writes, to force a rollback."""


async def settle(
    session: AsyncSession,
    ledger: WalletLedger,
    order_id: UUID,
    user_id: UUID,
    *,
    fail: bool,
) -> WalletEntrySchema | None:
    """Claim an order and credit its payee in one block, as a project service would.

    Args:
        session (AsyncSession): The request session.
        ledger (WalletLedger): The ledger, over the same session.
        order_id (UUID): The order to settle.
        user_id (UUID): The payee.
        fail (bool): Raise after both writes, inside the block.

    Returns:
        WalletEntrySchema | None: The credit line, or ``None`` when the
        claim was already taken.

    Raises:
        SettlementFailedError: When ``fail`` is set.
    """
    async with transaction(session):
        if not await claim_once(session, GuardOrder, order_id, "credited_at"):
            return None
        entry = await ledger.credit(
            user_id, 500, kind="SALE", reference_id=str(order_id)
        )
        if fail:
            raise SettlementFailedError
        return entry


class TestLedgerOnItsOwn:
    async def test_credit_debit_reverse_and_balance(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await seed_user(maker, 1_000)
        async with maker() as session:
            ledger = make_ledger(session)
            await ledger.credit(user_id, 200, kind="SALE")
            await ledger.debit_available(user_id, 300, kind="FEE")
            reversal = await ledger.reverse(user_id, 100)
            balance = await ledger.balance(user_id)
            page = await ledger.statement(user_id)
        assert reversal is not None
        assert balance.total_cents == 800
        assert page["total"] == 3
        assert await wallet_cents(maker, user_id) == 800

    async def test_repositories_must_share_a_session(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        async with maker() as first, maker() as second:
            with pytest.raises(ValueError, match="share one session"):
                WalletLedger(
                    balances=BaseRepository(first, model=GuardWalletUser),
                    entries=BaseRepository(second, model=GuardWalletEntry),
                )


class TestCallerTransaction:
    async def test_the_credit_commits_with_the_caller_writes(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """Cenário: claim do pedido + crédito no mesmo bloco, sem erro.

        Esperado: os dois ficam gravados.
        """
        user_id = await seed_user(maker)
        order_id = await seed_order(maker)
        async with maker() as session:
            entry = await settle(
                session, make_ledger(session), order_id, user_id, fail=False
            )
        assert entry is not None
        assert await wallet_cents(maker, user_id) == 500
        assert await credited_at_is_set(maker, order_id)

    async def test_a_failure_rolls_back_both(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """Cenário: o bloco levanta depois do claim e do crédito.

        Esperado: nem o saldo nem o claim ficam — o crédito não comitou
        sozinho dentro do bloco do chamador.
        """
        user_id = await seed_user(maker)
        order_id = await seed_order(maker)
        async with maker() as session:
            with pytest.raises(SettlementFailedError):
                await settle(
                    session, make_ledger(session), order_id, user_id, fail=True
                )
        assert await wallet_cents(maker, user_id) == 0
        assert not await credited_at_is_set(maker, order_id)


class TestServiceOverTheLedger:
    async def test_the_service_takes_a_ledger(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await seed_user(maker, 100)
        async with maker() as session:
            ledger = make_ledger(session)
            service = WalletService(ledger=ledger)
            await service.credit(user_id, 50, kind="SALE")
            assert service.ledger is ledger
            assert service.balances is ledger.balances
            assert service.entries is ledger.entries
            assert service.balance_attribute == "wallet_cents"
        assert await wallet_cents(maker, user_id) == 150

    async def test_the_old_constructor_builds_the_ledger(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        async with maker() as session:
            balances = BaseRepository(session, model=GuardWalletUser)
            entries = BaseRepository(session, model=GuardWalletEntry)
            service = WalletService(balances=balances, entries=entries)
            assert isinstance(service.ledger, WalletLedger)
            assert service.balances is balances

    async def test_ledger_and_repositories_together_are_refused(
        self, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        async with maker() as session:
            ledger = make_ledger(session)
            with pytest.raises(ValueError, match="not both"):
                WalletService(ledger=ledger, balances=ledger.balances)
            with pytest.raises(ValueError, match="both balances and entries"):
                WalletService(balances=ledger.balances)
