"""Tests for tempest_fastapi_sdk.db.transaction and the repository hooks.

The suite runs on the SDK's test backend (``sqlite+aiosqlite``). Two of
the classes exist specifically to pin backend behavior the code claims
in its docstrings — savepoint semantics, and the atomicity of a block —
so a regression in a future SQLAlchemy or driver release fails here
rather than silently corrupting a recovery path in a consuming service.
"""

import pytest
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, ConflictException
from tempest_fastapi_sdk.db.transaction import (
    in_transaction,
    savepoint,
    transaction,
    transaction_depth,
)


class Account(BaseModel):
    __tablename__ = "account_for_tx_test"

    label: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class Ledger(BaseModel):
    __tablename__ = "ledger_for_tx_test"

    note: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture
def accounts(session: AsyncSession) -> BaseRepository[Account]:
    return BaseRepository(session, model=Account)


@pytest.fixture
def ledgers(session: AsyncSession) -> BaseRepository[Ledger]:
    return BaseRepository(session, model=Ledger)


async def _labels(session: AsyncSession) -> list[str]:
    """Read every account label straight from the database.

    Args:
        session (AsyncSession): The session to query through.

    Returns:
        list[str]: The labels, sorted, so assertions are order-stable.
    """
    result = await session.execute(select(Account.label))
    return sorted(result.scalars().all())


class TestDepthTracking:
    async def test_no_block_open_by_default(self, session: AsyncSession) -> None:
        assert transaction_depth(session) == 0
        assert in_transaction(session) is False

    async def test_depth_increments_and_restores(self, session: AsyncSession) -> None:
        async with transaction(session):
            assert transaction_depth(session) == 1
            async with transaction(session):
                assert transaction_depth(session) == 2
            assert transaction_depth(session) == 1
        assert transaction_depth(session) == 0

    async def test_depth_restored_after_exception(self, session: AsyncSession) -> None:
        with pytest.raises(RuntimeError):
            async with transaction(session):
                raise RuntimeError("boom")
        assert transaction_depth(session) == 0

    async def test_block_is_shared_across_repositories(
        self,
        session: AsyncSession,
        accounts: BaseRepository[Account],
        ledgers: BaseRepository[Ledger],
    ) -> None:
        """The counter lives on the session, so a sibling repository joins it."""
        async with accounts.transaction():
            assert in_transaction(ledgers.session) is True


class TestAtomicity:
    async def test_clean_exit_commits_once(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        async with transaction(session):
            await accounts.add(Account(label="a"))
            await accounts.add(Account(label="b"))
        assert await _labels(session) == ["a", "b"]

    async def test_exception_rolls_the_whole_block_back(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        with pytest.raises(RuntimeError):
            async with transaction(session):
                await accounts.add(Account(label="a"))
                await accounts.add(Account(label="b"))
                raise RuntimeError("boom")
        assert await _labels(session) == []

    async def test_write_inside_block_is_not_durable_until_exit(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        """A flushed row is visible to the session but not yet committed."""
        async with transaction(session):
            await accounts.add(Account(label="a"))
            in_flight = await session.execute(select(func.count()).select_from(Account))
            assert in_flight.scalar_one() == 1
            assert session.in_transaction() is True
        assert session.in_transaction() is False

    async def test_two_repositories_share_one_commit(
        self,
        session: AsyncSession,
        accounts: BaseRepository[Account],
        ledgers: BaseRepository[Ledger],
    ) -> None:
        """The failure case that motivated the feature: no half-written pair."""
        with pytest.raises(ConflictException):
            async with transaction(session):
                await ledgers.add(Ledger(note="opened"))
                await accounts.add(Account(label="dup"))
                await accounts.add(Account(label="dup"))

        remaining = await session.execute(select(func.count()).select_from(Ledger))
        assert remaining.scalar_one() == 0


class TestAutocommitFlag:
    async def test_autocommit_false_defers_the_commit(
        self, session: AsyncSession
    ) -> None:
        repository: BaseRepository[Account] = BaseRepository(
            session, model=Account, autocommit=False
        )
        await repository.add(Account(label="pending"))
        assert session.in_transaction() is True

        await repository.commit()
        assert session.in_transaction() is False
        assert await _labels(session) == ["pending"]

    async def test_autocommit_true_is_the_default(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        """A plain ``add`` is durable — a later rollback cannot undo it.

        Durability is asserted through a rollback rather than through
        ``session.in_transaction()``, because ``add`` refreshes the row
        after committing and that read opens a fresh transaction.
        """
        await accounts.add(Account(label="durable"))
        await session.rollback()
        assert await _labels(session) == ["durable"]

    async def test_block_overrides_autocommit_true(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        async with transaction(session):
            await accounts.add(Account(label="held"))
            assert session.in_transaction() is True


class TestExplicitMethods:
    async def test_commit_is_a_noop_inside_a_block(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        """Safe to leave in place when a caller later wraps it in a block."""
        with pytest.raises(RuntimeError):
            async with transaction(session):
                await accounts.add(Account(label="a"))
                await accounts.commit()
                raise RuntimeError("boom")
        assert await _labels(session) == []

    async def test_flush_makes_the_row_readable_without_committing(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        repository: BaseRepository[Account] = BaseRepository(
            session, model=Account, autocommit=False
        )
        await repository.add(Account(label="a"))
        await repository.flush()
        assert await _labels(session) == ["a"]
        await repository.rollback()
        assert await _labels(session) == []

    async def test_rollback_inside_a_block_is_refused(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        async with transaction(session):
            await accounts.add(Account(label="a"))
            with pytest.raises(RuntimeError, match="entire block"):
                await accounts.rollback()
        assert await _labels(session) == ["a"]


class TestSavepoint:
    async def test_inner_failure_is_reverted_and_outer_work_survives(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        async with transaction(session):
            await accounts.add(Account(label="keep"))
            with pytest.raises(ConflictException):
                async with savepoint(session):
                    await accounts.add(Account(label="keep"))
            await accounts.add(Account(label="after"))

        assert await _labels(session) == ["after", "keep"]

    async def test_repository_savepoint_sugar_binds_the_session(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        async with transaction(session):
            await accounts.add(Account(label="keep"))
            with pytest.raises(ConflictException):
                async with accounts.savepoint():
                    await accounts.add(Account(label="keep"))
        assert await _labels(session) == ["keep"]

    async def test_savepoint_writes_do_not_commit_on_their_own(
        self, session: AsyncSession, accounts: BaseRepository[Account]
    ) -> None:
        with pytest.raises(RuntimeError):
            async with transaction(session):
                async with savepoint(session):
                    await accounts.add(Account(label="a"))
                raise RuntimeError("boom")
        assert await _labels(session) == []

    async def test_depth_restored_after_savepoint(self, session: AsyncSession) -> None:
        async with transaction(session):
            async with savepoint(session):
                assert transaction_depth(session) == 2
            assert transaction_depth(session) == 1
        assert transaction_depth(session) == 0
