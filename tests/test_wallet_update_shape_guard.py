"""Every balance write of ``WalletRepository`` is exactly one ``UPDATE ... WHERE``.

The concurrency guard proves the shipped methods hold under a race. This
one pins the property that makes them hold, so a later edit cannot trade
it away without failing here first: a write method sends **one**
statement to the database, it is an ``UPDATE``, and it has a ``WHERE``.
A ``SELECT`` before it is the read-modify-write shape (the window the
module exists to close); a second ``UPDATE`` is two decisions where one
was atomic; an ``UPDATE`` without ``WHERE`` moves every wallet.

Statements are captured with ``before_cursor_execute`` on the engine.
Transaction control (``BEGIN``, ``SAVEPOINT``, ``RELEASE``) is not a
decision and is ignored.

A public method added to the repository without being listed in
:data:`WRITES` or :data:`READS` fails :func:`test_every_method_is_classified`,
so a new write cannot skip the check by not being named.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.wallet import WalletRepository
from tests.wallet.support import WalletEntry, WalletOrder, WalletUser, seed_user
from tests.wallet.support import engine as engine
from tests.wallet.support import maker as maker
from tests.wallet.support import postgres_url as postgres_url

TRANSACTION_CONTROL: tuple[str, ...] = ("BEGIN", "SAVEPOINT", "RELEASE", "ROLLBACK")

WRITES: frozenset[str] = frozenset(
    {"credit", "debit", "debit_available", "claim_once"},
)
READS: frozenset[str] = frozenset({"balance", "held_expression"})


def _own_public_methods() -> set[str]:
    """Return the public callables ``WalletRepository`` adds to its base.

    Returns:
        set[str]: Method names defined on the class, not inherited.
    """
    inherited = set(dir(BaseRepository))
    return {
        name
        for name, member in vars(WalletRepository).items()
        if not name.startswith("_")
        and name not in inherited
        and inspect.isfunction(member)
    }


@contextmanager
def _captured(engine: AsyncEngine) -> Iterator[list[str]]:
    """Record every statement the engine sends while the block runs.

    Args:
        engine (AsyncEngine): The engine to listen on.

    Yields:
        list[str]: The statements, appended as they execute.
    """
    statements: list[str] = []

    def record(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        """Append one statement.

        Args:
            _connection (Any): Unused.
            _cursor (Any): Unused.
            statement (str): The SQL text.
            _parameters (Any): Unused.
            _context (Any): Unused.
            _executemany (bool): Unused.
        """
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


def violations(statements: list[str]) -> list[str]:
    """Describe how a write method's statements break the one-UPDATE rule.

    Args:
        statements (list[str]): What the method sent, in order.

    Returns:
        list[str]: One message per problem; empty when the method sent
        exactly one ``UPDATE`` with a ``WHERE`` and nothing else.
    """
    decisions = [
        sql
        for sql in statements
        if not sql.lstrip().upper().startswith(TRANSACTION_CONTROL)
    ]
    problems: list[str] = []
    if len(decisions) != 1:
        problems.append(f"expected 1 statement, got {len(decisions)}: {decisions}")
    for sql in decisions:
        upper = " ".join(sql.upper().split())
        if not upper.startswith("UPDATE "):
            problems.append(f"not an UPDATE: {sql}")
        elif " WHERE " not in upper:
            problems.append(f"UPDATE without WHERE: {sql}")
    return problems


def _calls(
    repository: WalletRepository,
    user_id: UUID,
    order_id: UUID,
) -> dict[str, Callable[[], Awaitable[object]]]:
    """Build one call per write method.

    Args:
        repository (WalletRepository): The repository under test.
        user_id (UUID): A seeded wallet owner.
        order_id (UUID): A seeded order row.

    Returns:
        dict[str, Callable[[], Awaitable[object]]]: Name to call.
    """
    return {
        "credit": lambda: repository.credit(user_id, 100),
        "debit": lambda: repository.debit(user_id, 10),
        "debit_available": lambda: repository.debit_available(user_id, 10),
        "claim_once": lambda: repository.claim_once(
            WalletOrder, order_id, "credited_at"
        ),
    }


def test_every_method_is_classified() -> None:
    assert _own_public_methods() == WRITES | READS


@pytest.mark.parametrize("method", sorted(WRITES))
async def test_write_is_one_conditional_update(
    engine: AsyncEngine,
    maker: async_sessionmaker[AsyncSession],
    method: str,
) -> None:
    user_id = await seed_user(maker, 1_000)
    order_id = uuid4()
    async with maker() as seeding:
        seeding.add(WalletOrder(id=order_id))
        await seeding.commit()
    async with maker() as session:
        repository = WalletRepository(
            session, model=WalletUser, entry_model=WalletEntry
        )
        call = _calls(repository, user_id, order_id)[method]
        with _captured(engine) as statements:
            await call()
    assert violations(statements) == []


class _ReadThenWrite(WalletRepository):
    """The shape the guard exists to reject: read the balance, then write it."""

    async def credit(self, user_id: UUID, amount_cents: int) -> int | None:
        """Credit by reading first — the BusCar shape.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Cents to add.

        Returns:
            int | None: The new balance.
        """
        current = await self.session.scalar(
            select(self.model.wallet_cents).where(self.model.id == user_id),
        )
        await self.bulk_update(
            {"id": user_id}, {"wallet_cents": current + amount_cents}
        )
        return int(current + amount_cents)


async def test_guard_fires_on_read_then_write(
    engine: AsyncEngine,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await seed_user(maker, 1_000)
    async with maker() as session:
        repository = _ReadThenWrite(session, model=WalletUser, entry_model=WalletEntry)
        with _captured(engine) as statements:
            await repository.credit(user_id, 100)
    problems = violations(statements)
    assert any("expected 1 statement, got 2" in problem for problem in problems)
    assert any("not an UPDATE" in problem for problem in problems)


def test_guard_fires_on_update_without_where() -> None:
    assert violations(["UPDATE wallet_users SET wallet_cents=0"]) == [
        "UPDATE without WHERE: UPDATE wallet_users SET wallet_cents=0",
    ]
