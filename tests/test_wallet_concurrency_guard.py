"""Two requests racing for one wallet, with the interleaving forced.

The wallet module rests on one claim: every balance write is a single
conditional ``UPDATE``, so there is no gap between deciding and writing
for a concurrent request to fall into. This guard races two sessions
against the same row on SQLite and on PostgreSQL, and **forces** the
dangerous interleaving instead of hoping the scheduler produces it: the
first request does its work and then holds its transaction open for
:data:`GAP` seconds; the second starts only after the first signalled,
so it always runs while the first is uncommitted.

A harness that cannot fail proves nothing, so :class:`TestHarnessFires`
runs the same harness against the shape the module replaces — read the
balance, add in Python, write the value back — and asserts that it loses
money. It also pins why the module does not use a row lock:
``SELECT ... FOR UPDATE`` protects that shape on PostgreSQL and does not
on SQLite, whose dialect drops the clause from the SQL it emits.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.wallet import (
    WalletEntrySchema,
    WalletInsufficientFundsException,
)
from tests.wallet.support import WalletEntry, WalletUser, make_service, seed_user
from tests.wallet.support import engine as engine
from tests.wallet.support import maker as maker
from tests.wallet.support import postgres_url as postgres_url

TRIALS: int = 50
"""Races per scenario and dialect — the N every reported rate is out of."""

GAP: float = 0.02
"""Seconds the first request holds its transaction open after its write."""

NOW: datetime = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

Step = Callable[[AsyncSession], Awaitable[object]]


async def _race(
    maker: async_sessionmaker[AsyncSession],
    first: Step,
    second: Step,
    *,
    first_gap: Callable[[], Awaitable[None]] | None = None,
) -> tuple[object, object]:
    """Run ``second`` while ``first`` holds its transaction open.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory; each
            request gets its own session, hence its own connection.
        first (Step): The request that writes first.
        second (Step): The request started after ``first`` signalled.
        first_gap (Callable[[], Awaitable[None]] | None): Replaces the
            default sleep between ``first``'s work and its commit.

    Returns:
        tuple[object, object]: Each request's result, or the exception it
        raised.
    """
    started = asyncio.Event()

    async def run_first() -> object:
        async with maker() as session, transaction(session):
            result = await first(session)
            started.set()
            await (first_gap() if first_gap is not None else asyncio.sleep(GAP))
            return result

    async def run_second() -> object:
        await started.wait()
        async with maker() as session, transaction(session):
            return await second(session)

    outcomes = await asyncio.gather(run_first(), run_second(), return_exceptions=True)
    return outcomes[0], outcomes[1]


async def _balance(maker: async_sessionmaker[AsyncSession], user_id: UUID) -> int:
    """Read a committed balance.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.
        user_id (UUID): The wallet owner.

    Returns:
        int: The balance.
    """
    async with maker() as session:
        value = await session.scalar(
            select(WalletUser.wallet_cents).where(WalletUser.id == user_id),
        )
    return int(value or 0)


async def _entries(maker: async_sessionmaker[AsyncSession], user_id: UUID) -> int:
    """Count a wallet's committed statement lines.

    Args:
        maker (async_sessionmaker[AsyncSession]): Session factory.
        user_id (UUID): The wallet owner.

    Returns:
        int: The number of lines.
    """
    async with maker() as session:
        value = await session.scalar(
            select(func.count())
            .select_from(WalletEntry)
            .where(WalletEntry.user_id == user_id),
        )
    return int(value or 0)


def _credit(
    user_id: UUID,
    amount: int,
    reference: UUID | None = None,
    *,
    hold: timedelta = timedelta(0),
) -> Step:
    """Build a request that credits through the service.

    Args:
        user_id (UUID): The wallet owner.
        amount (int): Cents to credit.
        reference (UUID | None): The event id; a fresh one by default.
        hold (timedelta): How long the credit stays held.

    Returns:
        Step: The request.
    """

    async def step(session: AsyncSession) -> object:
        return await make_service(session).credit(
            user_id,
            amount,
            reference_type="order",
            reference_id=reference or uuid4(),
            hold=hold,
            now=NOW,
        )

    return step


def _debit(user_id: UUID, amount: int) -> Step:
    """Build a request that debits the available balance through the service.

    Args:
        user_id (UUID): The wallet owner.
        amount (int): Cents to take.

    Returns:
        Step: The request.
    """

    async def step(session: AsyncSession) -> object:
        return await make_service(session).debit(
            user_id,
            amount,
            reference_type="withdraw",
            reference_id=uuid4(),
            now=NOW,
        )

    return step


def _read_modify_write(user_id: UUID, amount: int, *, lock: bool) -> Step:
    """Build the shape the module replaces: read, add in Python, write back.

    The read and the write are separated by :data:`GAP`, so the other
    request can read the same value in between.

    Args:
        user_id (UUID): The wallet owner.
        amount (int): Cents to add.
        lock (bool): Read with ``SELECT ... FOR UPDATE``.

    Returns:
        Step: The request.
    """

    async def step(session: AsyncSession) -> object:
        query = select(WalletUser.wallet_cents).where(WalletUser.id == user_id)
        if lock:
            query = query.with_for_update()
        current = int(await session.scalar(query) or 0)
        await asyncio.sleep(GAP)
        await session.execute(
            update(WalletUser)
            .where(WalletUser.id == user_id)
            .values(wallet_cents=current + amount),
        )
        return current + amount

    return step


class TestWalletRaces:
    """Every scenario must hold in all :data:`TRIALS` races, on both dialects."""

    async def test_two_credits_both_land(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        for _ in range(TRIALS):
            user_id = await seed_user(maker)
            first, second = await _race(
                maker, _credit(user_id, 100), _credit(user_id, 100)
            )
            assert isinstance(first, WalletEntrySchema), first
            assert isinstance(second, WalletEntrySchema), second
            assert await _balance(maker, user_id) == 200
            assert {first.balance_after_cents, second.balance_after_cents} == {
                100,
                200,
            }

    async def test_two_debits_take_once(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        for _ in range(TRIALS):
            user_id = await seed_user(maker, 100)
            first, second = await _race(
                maker, _debit(user_id, 100), _debit(user_id, 100)
            )
            assert isinstance(first, WalletEntrySchema), first
            assert isinstance(second, WalletInsufficientFundsException), second
            assert await _balance(maker, user_id) == 0
            assert await _entries(maker, user_id) == 1

    async def test_two_debits_never_take_held_money(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """1000 in the wallet, 500 of it held: two debits of 500, one succeeds.

        The shape that leaked in production computed "available" in
        Python and debited with ``WHERE wallet >= amount``: both requests
        saw 500 available and both passed the check.
        """
        for _ in range(TRIALS):
            user_id = await seed_user(maker, 500)
            async with maker() as session:
                await make_service(session).credit(
                    user_id,
                    500,
                    reference_type="order",
                    reference_id=uuid4(),
                    hold=timedelta(hours=48),
                    now=NOW,
                )
            first, second = await _race(
                maker, _debit(user_id, 500), _debit(user_id, 500)
            )
            assert isinstance(first, WalletEntrySchema), first
            assert isinstance(second, WalletInsufficientFundsException), second
            assert await _balance(maker, user_id) == 500

    async def test_debit_waits_for_and_respects_an_incoming_hold(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A held credit in flight never becomes spendable to a racing debit.

        100 available; the first request credits 500 with a 48 h hold and
        holds its transaction open; the second tries to take 600.

        The two databases refuse it for different reasons, and only one
        of them exercises the hold. SQLite serializes writers, so the
        debit waits for the credit's commit and then sees 600 in the
        wallet **and** 500 held. PostgreSQL filters the row against the
        version the debit's snapshot sees — 100 — so the debit is refused
        without waiting. Measured by replacing the held subquery with
        ``0``: this scenario then fails on SQLite and still passes on
        PostgreSQL, where :meth:`test_two_debits_never_take_held_money`
        is the one that catches it.
        """
        for _ in range(TRIALS):
            user_id = await seed_user(maker, 100)

            first, second = await _race(
                maker,
                _credit(user_id, 500, hold=timedelta(hours=48)),
                _debit(user_id, 600),
            )
            assert isinstance(first, WalletEntrySchema), first
            assert isinstance(second, WalletInsufficientFundsException), second
            assert await _balance(maker, user_id) == 600

    async def test_replayed_event_racing_itself_credits_once(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """The same webhook delivered twice at once: one credit, one entry."""
        for _ in range(TRIALS):
            user_id = await seed_user(maker)
            reference = uuid4()
            first, second = await _race(
                maker,
                _credit(user_id, 100, reference),
                _credit(user_id, 100, reference),
            )
            assert isinstance(first, WalletEntrySchema), first
            assert isinstance(second, WalletEntrySchema), second
            assert second.id == first.id
            assert await _balance(maker, user_id) == 100
            assert await _entries(maker, user_id) == 1


class TestHarnessFires:
    """The same harness, pointed at the shape it exists to reject.

    On SQLite the control runs on a second engine over the same file
    **without** :func:`~tempest_fastapi_sdk.enable_sqlite_savepoints` —
    the driver's default configuration, the one a hand-built engine has.
    There the race is silent: both requests commit and one credit
    vanishes. Under the explicit ``BEGIN`` that the SDK's manager
    configures, the same race fails loudly instead (one request gets
    ``database is locked``), which is safer but still loses the credit —
    measured at 46/50 trials, with the other 4 leaving a pooled
    connection unusable for the next ``BEGIN``. Asserting the silent
    variant keeps the control deterministic.
    """

    @staticmethod
    def _control_maker(
        engine: AsyncEngine,
        maker: async_sessionmaker[AsyncSession],
    ) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine | None]:
        """Return the sessions the control runs on, and an engine to dispose.

        Args:
            engine (AsyncEngine): The parametrized engine.
            maker (async_sessionmaker[AsyncSession]): Its session factory.

        Returns:
            tuple[async_sessionmaker[AsyncSession], AsyncEngine | None]: The
            factory, and the plain SQLite engine built for it (``None`` on
            PostgreSQL, where the parametrized engine is used as is).
        """
        if engine.dialect.name != "sqlite":
            return maker, None
        plain = create_async_engine(engine.url)
        return async_sessionmaker(plain, expire_on_commit=False), plain

    async def _lost_credits(
        self,
        engine: AsyncEngine,
        maker: async_sessionmaker[AsyncSession],
        *,
        lock: bool,
    ) -> int:
        """Race two read-modify-write credits :data:`TRIALS` times.

        Args:
            engine (AsyncEngine): The parametrized engine.
            maker (async_sessionmaker[AsyncSession]): Its session factory.
            lock (bool): Read with ``FOR UPDATE``.

        Returns:
            int: Trials whose final balance is not the 200 two credits
            of 100 should leave.
        """
        control, plain = self._control_maker(engine, maker)
        wrong = 0
        try:
            for _ in range(TRIALS):
                user_id = await seed_user(control)
                await _race(
                    control,
                    _read_modify_write(user_id, 100, lock=lock),
                    _read_modify_write(user_id, 100, lock=lock),
                )
                if await _balance(control, user_id) != 200:
                    wrong += 1
        finally:
            if plain is not None:
                await plain.dispose()
        return wrong

    async def test_read_modify_write_loses_money(
        self,
        engine: AsyncEngine,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Without a lock, the second write overwrites the first, every time."""
        assert await self._lost_credits(engine, maker, lock=False) == TRIALS

    async def test_for_update_protects_only_on_postgres(
        self,
        engine: AsyncEngine,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Why the wallet does not lock: ``FOR UPDATE`` is PostgreSQL-only.

        The SQLite dialect compiles ``select(...).with_for_update()``
        without the clause — no error, no warning — so the read takes no
        lock and the race is exactly the unlocked one. A design that
        leans on the lock is correct in production and broken under the
        test database, which is where nobody looks. A conditional
        ``UPDATE`` behaves the same on both.
        """
        compiled = str(
            select(WalletUser.wallet_cents)
            .with_for_update()
            .compile(dialect=sqlite.dialect()),
        )
        assert "FOR UPDATE" not in compiled
        lost = await self._lost_credits(engine, maker, lock=True)
        assert lost == (TRIALS if engine.dialect.name == "sqlite" else 0)
