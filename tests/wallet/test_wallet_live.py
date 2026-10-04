"""WalletService under real concurrency, against Postgres in a container.

SQLite in memory serializes every write on one connection, so it cannot
show the race the wallet exists to prevent. Here every task gets its own
session on its own pooled connection and the statements really interleave
on the server.

The control test is what keeps the others from passing vacuously: the
same harness running the naive read-in-Python-then-write credit loses
credits. If the harness stopped producing contention, the control would
fail first.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseRepository,
    BaseUserModel,
    InsufficientBalanceException,
)
from tempest_fastapi_sdk.integrations.payment import PixKeyType
from tempest_fastapi_sdk.testing.fakes import FakePayoutProvider
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletService,
    make_wallet_entry_model,
)

pytestmark = pytest.mark.docker

IMAGE: str = "postgres:16-alpine"
CONTAINER: str = "tempest-wallet-probe"
PORT: int = 55434
TASKS: int = 50


class _LiveWalletUser(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "live_wallet_users"


_LiveEntry = make_wallet_entry_model(
    user_table="live_wallet_users",
    tablename="live_wallet_entries",
    class_name="_LiveWalletEntry",
)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """Start a Postgres container for the module and yield its URL.

    Yields:
        str: An async SQLAlchemy URL for the container.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_PASSWORD=probe",
            "-e",
            "POSTGRES_DB=probe",
            "-p",
            f"{PORT}:5432",
            IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.skip("postgres never became ready")
        time.sleep(1)
        yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{PORT}/probe"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest_asyncio.fixture
async def sessions(
    postgres_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Fresh tables and a session factory with room for every task.

    Args:
        postgres_url (str): URL from the container fixture.

    Yields:
        async_sessionmaker[AsyncSession]: One session per task.
    """
    engine: AsyncEngine = create_async_engine(
        postgres_url, pool_size=TASKS, max_overflow=0
    )
    tables = [_LiveWalletUser.__table__, _LiveEntry.__table__]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: _LiveWalletUser.metadata.drop_all(sync, tables=tables)
        )
        await connection.run_sync(
            lambda sync: _LiveWalletUser.metadata.create_all(sync, tables=tables)
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=_LiveWalletUser),
        entries=BaseRepository(session, model=_LiveEntry),
    )


async def _user(factory: async_sessionmaker[AsyncSession], balance: int) -> UUID:
    async with factory() as session:
        user = _LiveWalletUser(
            email=f"{uuid4()}@example.com",
            hashed_password="x",
            wallet_cents=balance,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _balance(factory: async_sessionmaker[AsyncSession], user_id: UUID) -> int:
    async with factory() as session:
        value = await session.scalar(
            select(_LiveWalletUser.wallet_cents).where(_LiveWalletUser.id == user_id)
        )
        assert value is not None
        return int(value)


async def test_the_harness_loses_credits_with_a_naive_write(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Control: read in Python, add, write back — the defect being fixed."""
    user_id = await _user(sessions, 0)

    async def naive_credit() -> None:
        async with sessions() as session:
            user = await session.get(_LiveWalletUser, user_id)
            assert user is not None
            current = user.wallet_cents
            await asyncio.sleep(0.01)
            user.wallet_cents = current + 100
            await session.commit()

    await asyncio.gather(*(naive_credit() for _ in range(TASKS)))

    assert await _balance(sessions, user_id) < TASKS * 100


async def test_concurrent_credits_all_land(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await _user(sessions, 0)

    async def credit() -> int:
        async with sessions() as session:
            entry = await _service(session).credit(user_id, 100, kind="SALE")
            return entry.balance_after_cents

    balances_after = await asyncio.gather(*(credit() for _ in range(TASKS)))

    assert await _balance(sessions, user_id) == TASKS * 100
    assert sorted(balances_after) == [100 * n for n in range(1, TASKS + 1)]


async def test_concurrent_withdrawals_pay_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await _user(sessions, 0)
    async with sessions() as session:
        await _service(session).credit(user_id, 1_000, kind="SALE")
    payout = FakePayoutProvider()

    async def withdraw() -> str:
        async with sessions() as session:
            try:
                await _service(session).withdraw(
                    user_id,
                    payout=payout,
                    pix_key="k",
                    pix_key_type=PixKeyType.RANDOM,
                    amount_cents=1_000,
                )
            except InsufficientBalanceException:
                return "refused"
            return "paid"

    outcomes = await asyncio.gather(*(withdraw() for _ in range(TASKS)))

    assert outcomes.count("paid") == 1
    assert len(payout.transfers) == 1
    assert await _balance(sessions, user_id) == 0


async def test_concurrent_debits_never_reach_held_money(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The gap alofans-api has: the available amount checked before the debit."""
    user_id = await _user(sessions, 0)
    async with sessions() as session:
        service = _service(session)
        await service.credit(user_id, 300, kind="SALE")
        await service.credit(user_id, 700, kind="SALE", hold=timedelta(hours=48))

    async def debit() -> str:
        async with sessions() as session:
            try:
                await _service(session).debit_available(user_id, 300, kind="FEE")
            except InsufficientBalanceException:
                return "refused"
            return "debited"

    outcomes = await asyncio.gather(*(debit() for _ in range(TASKS)))

    assert outcomes.count("debited") == 1
    assert await _balance(sessions, user_id) == 700
