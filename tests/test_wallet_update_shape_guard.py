"""Every balance move of ``WalletService`` decides with one ``UPDATE ... WHERE``.

The concurrency guard proves the shipped methods hold under a race. This
one pins the property that makes them hold, so a later edit cannot trade
it away without failing here first. For a method that moves a balance,
the **first** statement it sends is the ``UPDATE`` of the balance table,
it has a ``WHERE``, and it is the only ``UPDATE``; the ledger line is one
``INSERT`` after it. A ``SELECT`` of the balance before the ``UPDATE`` is
the read-modify-write shape (the window the module exists to close); a
second ``UPDATE`` is two decisions where one was atomic; an ``UPDATE``
without ``WHERE`` moves every wallet. :func:`claim_once` is a single
``UPDATE ... WHERE`` and nothing else.

Statements are captured with ``before_cursor_execute`` on the engine.
Transaction control (``BEGIN``, ``SAVEPOINT``, ``RELEASE``, ``ROLLBACK``,
``COMMIT``) is not a decision and is ignored; reads after the ``INSERT``
(the repository refreshing the row it just wrote) are allowed.

A public method added to the service without being listed in
:data:`BALANCE_MOVES`, :data:`READS` or :data:`COMPOSED` fails
:func:`test_every_method_is_classified`, so a new write cannot skip the
check by not being named.

Ported from PR #410, which pinned the same rule on its
``WalletRepository``; here it is pinned on the ``WalletService`` that
shipped in #409.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.wallet import WalletService, claim_once
from tests.wallet.support import (
    GuardOrder,
    GuardWalletEntry,
    GuardWalletUser,
    make_service,
    seed_order,
    seed_user,
)
from tests.wallet.support import engine as engine
from tests.wallet.support import maker as maker
from tests.wallet.support import postgres_url as postgres_url

TRANSACTION_CONTROL: tuple[str, ...] = (
    "BEGIN",
    "SAVEPOINT",
    "RELEASE",
    "ROLLBACK",
    "COMMIT",
)
BALANCE_TABLE: str = GuardWalletUser.__tablename__
ENTRY_TABLE: str = GuardWalletEntry.__tablename__

BALANCE_MOVES: frozenset[str] = frozenset({"credit", "debit_available", "reverse"})
"""Methods that change a balance and write one ledger line."""

READS: frozenset[str] = frozenset({"balance", "statement"})
"""Methods that only read."""

COMPOSED: frozenset[str] = frozenset({"withdraw"})
"""Methods built from :data:`BALANCE_MOVES` plus a provider call.

``withdraw`` is ``debit_available``, then the payout, then (on refusal)
``credit`` — each of which this guard already checks on its own.
"""


def _own_public_methods() -> set[str]:
    """Return the public coroutine methods ``WalletService`` defines.

    Returns:
        set[str]: Method names defined on the class.
    """
    return {
        name
        for name, member in vars(WalletService).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
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


def _decisions(statements: list[str]) -> list[str]:
    """Drop transaction control and normalize whitespace and case.

    Args:
        statements (list[str]): What was sent, in order.

    Returns:
        list[str]: The remaining statements, upper-cased, one space apart.
    """
    return [
        " ".join(sql.upper().split())
        for sql in statements
        if not sql.lstrip().upper().startswith(TRANSACTION_CONTROL)
    ]


def balance_move_violations(statements: list[str]) -> list[str]:
    """Describe how a balance move breaks the one-``UPDATE`` rule.

    Args:
        statements (list[str]): What the method sent, in order.

    Returns:
        list[str]: One message per problem; empty when the first decision
        is the only ``UPDATE``, it targets the balance table with a
        ``WHERE``, and exactly one ``INSERT`` into the ledger follows.
    """
    decisions = _decisions(statements)
    problems: list[str] = []
    updates = [sql for sql in decisions if sql.startswith("UPDATE ")]
    inserts = [sql for sql in decisions if sql.startswith("INSERT ")]
    if not decisions or not decisions[0].startswith(f"UPDATE {BALANCE_TABLE.upper()}"):
        problems.append(f"first statement is not the balance UPDATE: {decisions[:1]}")
    if len(updates) != 1:
        problems.append(f"expected 1 UPDATE, got {len(updates)}: {updates}")
    problems.extend(
        f"UPDATE without WHERE: {sql}" for sql in updates if " WHERE " not in sql
    )
    if len(inserts) != 1 or ENTRY_TABLE.upper() not in inserts[0]:
        problems.append(f"expected 1 INSERT into {ENTRY_TABLE}, got {inserts}")
    return problems


def single_update_violations(statements: list[str]) -> list[str]:
    """Describe how a claim breaks the single ``UPDATE ... WHERE`` rule.

    Args:
        statements (list[str]): What the call sent, in order.

    Returns:
        list[str]: One message per problem; empty for exactly one
        ``UPDATE`` with a ``WHERE``.
    """
    decisions = _decisions(statements)
    problems: list[str] = []
    if len(decisions) != 1:
        problems.append(f"expected 1 statement, got {len(decisions)}: {decisions}")
    for sql in decisions:
        if not sql.startswith("UPDATE "):
            problems.append(f"not an UPDATE: {sql}")
        elif " WHERE " not in sql:
            problems.append(f"UPDATE without WHERE: {sql}")
    return problems


def _moves(
    service: WalletService, user_id: UUID
) -> dict[str, Callable[[], Awaitable[object]]]:
    """Build one call per balance move.

    Args:
        service (WalletService): The service under test.
        user_id (UUID): A seeded wallet owner with 1000 cents.

    Returns:
        dict[str, Callable[[], Awaitable[object]]]: Name to call.
    """
    return {
        "credit": lambda: service.credit(user_id, 100, kind="SALE"),
        "debit_available": lambda: service.debit_available(user_id, 10, kind="FEE"),
        "reverse": lambda: service.reverse(user_id, 10),
    }


def test_every_method_is_classified() -> None:
    assert _own_public_methods() == BALANCE_MOVES | READS | COMPOSED


@pytest.mark.parametrize("method", sorted(BALANCE_MOVES))
async def test_balance_move_decides_with_one_update(
    engine: AsyncEngine,
    maker: async_sessionmaker[AsyncSession],
    method: str,
) -> None:
    user_id = await seed_user(maker, 1_000)
    async with maker() as session:
        call = _moves(make_service(session), user_id)[method]
        with _captured(engine) as statements:
            await call()
    assert balance_move_violations(statements) == []


async def test_claim_once_is_one_conditional_update(
    engine: AsyncEngine,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    order_id = await seed_order(maker)
    async with maker() as session:
        with _captured(engine) as statements:
            await claim_once(session, GuardOrder, order_id, "credited_at")
        await session.commit()
    assert single_update_violations(statements) == []


class _ReadThenWrite(WalletService):
    """The shape the guard exists to reject: read the balance, then write it."""

    async def credit(self, user_id: UUID, amount_cents: int, **_: Any) -> Any:
        """Credit by reading first — the transport-backend shape.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Cents to add.
            **_ (Any): Ignored keyword arguments of the real signature.

        Returns:
            Any: The new balance.
        """
        model = self.balances.model
        current = int(
            await self.balances.session.scalar(
                select(model.wallet_cents).where(model.id == user_id)
            )
            or 0
        )
        await self.balances.session.execute(
            update(model)
            .where(model.id == user_id)
            .values(wallet_cents=current + amount_cents)
        )
        await self.entries.add(
            self.entries.model(
                user_id=user_id,
                kind="SALE",
                amount_cents=amount_cents,
                balance_after_cents=current + amount_cents,
                available_at=datetime.now(UTC),
            )
        )
        return current + amount_cents


async def test_guard_fires_on_read_then_write(
    engine: AsyncEngine,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await seed_user(maker, 1_000)
    async with maker() as session:
        service = _ReadThenWrite(
            balances=BaseRepository(session, model=GuardWalletUser),
            entries=BaseRepository(session, model=GuardWalletEntry),
        )
        with _captured(engine) as statements:
            await service.credit(user_id, 100)
    problems = balance_move_violations(statements)
    assert any("first statement is not the balance UPDATE" in p for p in problems)


def test_guard_fires_on_update_without_where() -> None:
    assert single_update_violations([f"UPDATE {BALANCE_TABLE} SET credited_at=1"]) == [
        f"UPDATE without WHERE: UPDATE {BALANCE_TABLE.upper()} SET CREDITED_AT=1",
    ]


def test_guard_fires_on_a_second_update() -> None:
    problems = balance_move_violations(
        [
            f"UPDATE {BALANCE_TABLE} SET wallet_cents=1 WHERE id=1",
            f"UPDATE {BALANCE_TABLE} SET wallet_cents=2 WHERE id=1",
            f"INSERT INTO {ENTRY_TABLE} VALUES (1)",
        ]
    )
    assert any("expected 1 UPDATE, got 2" in p for p in problems)
