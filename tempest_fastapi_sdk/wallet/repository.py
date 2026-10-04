"""Balance writes as single conditional ``UPDATE`` statements.

Every write method here is **one** ``UPDATE ... WHERE ... RETURNING``.
There is no read before it, so there is no window between "check" and
"write" for a concurrent request to fall into — the database evaluates
the condition and moves the balance under the row lock it takes for the
write itself.

The shapes this rules out were measured, forcing two requests to
interleave between read and write (50 trials each, SQLite and
PostgreSQL 17):

* read the balance, add in Python, write the value back — the second
  writer overwrote the first in 50/50 trials on both databases;
* the same with ``SELECT ... FOR UPDATE`` — 0/50 on PostgreSQL, still
  50/50 on SQLite, because the SQLite dialect drops ``FOR UPDATE`` from
  the emitted SQL without a warning;
* ``SET wallet_cents = wallet_cents + :n`` and
  ``... WHERE wallet_cents >= :n RETURNING`` — 0/50 on both.

The held balance follows the same rule: a debit discounts it with a
correlated subquery **inside** the ``WHERE``, never with a number
computed in Python before the statement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tempest_fastapi_sdk.db.expressions import F
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.wallet.exceptions import WalletInsufficientFundsException
from tempest_fastapi_sdk.wallet.models import BaseWalletEntryModel
from tempest_fastapi_sdk.wallet.schemas import WalletBalanceSchema


class WalletRepository(BaseRepository[Any]):
    """Move a wallet balance with one conditional ``UPDATE`` per call.

    Bound to the model that carries ``wallet_cents`` (the application's
    user model, with :class:`~tempest_fastapi_sdk.wallet.WalletBalanceMixin`)
    and to the statement model whose rows define the held balance.

    The write methods are :meth:`credit`, :meth:`debit`,
    :meth:`debit_available` and :meth:`claim_once`. They write the
    balance only — the statement line is the service's job, in the same
    transaction. ``tests/wallet/test_repository_shape.py`` asserts that
    each of them emits exactly one ``UPDATE`` with a ``WHERE``, and fails
    when a public method is added without being classified.

    Attributes:
        entry_model (type[BaseWalletEntryModel]): The statement model.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        model: type[BaseModel],
        entry_model: type[BaseWalletEntryModel],
        autocommit: bool = True,
    ) -> None:
        """Initialize the repository.

        Args:
            session (AsyncSession): The async database session.
            model (type[BaseModel]): The model with the ``wallet_cents``
                column.
            entry_model (type[BaseWalletEntryModel]): The concrete
                statement model; its ``user_id`` references ``model``.
            autocommit (bool): Forwarded to :class:`BaseRepository`.

        Raises:
            TypeError: When ``model`` has no ``wallet_cents`` attribute.
        """
        if not hasattr(model, "wallet_cents"):
            raise TypeError(
                f"{model.__name__} has no wallet_cents column; mix in "
                "WalletBalanceMixin or OverdraftWalletBalanceMixin.",
            )
        super().__init__(
            session,
            model=model,
            autocommit=autocommit,
            bulk_update_conflict_exception=WalletInsufficientFundsException,
            bulk_update_conflict_message="Insufficient available wallet balance",
        )
        self.entry_model: type[BaseWalletEntryModel] = entry_model

    def held_expression(self, now: datetime) -> ColumnElement[Any]:
        """Return the held balance as a subquery correlated to the owner row.

        The signed sum of the owner's entries whose ``available_at`` is
        after ``now``. Signed, so the reversal of a held credit — posted
        with the credit's own ``available_at`` — cancels it out of the
        hold as well as out of the balance.

        Args:
            now (datetime): The instant that separates held from
                available.

        Returns:
            ColumnElement[Any]: A scalar subquery, ``0`` when nothing is
            held.
        """
        entry = self.entry_model
        return (
            select(func.coalesce(func.sum(entry.amount_cents), 0))
            .where(entry.user_id == self.model.id, entry.available_at > now)
            .correlate(self.model)
            .scalar_subquery()
        )

    async def credit(self, user_id: UUID, amount_cents: int) -> int | None:
        """Add ``amount_cents`` to the balance.

        ``UPDATE ... SET wallet_cents = wallet_cents + :n WHERE id = :id
        RETURNING wallet_cents``. Two concurrent credits both land: the
        second waits for the first's row lock and adds to its result.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Cents to add; must be positive.

        Returns:
            int | None: The balance after the credit, or ``None`` when no
            row has ``user_id``.

        Raises:
            ValueError: When ``amount_cents`` is not positive.
        """
        _require_positive(amount_cents)
        return await self._move(user_id, amount_cents, None)

    async def debit(self, user_id: UUID, amount_cents: int) -> int | None:
        """Take ``amount_cents`` from the balance without an availability check.

        A **forced** debit, for reversing a credit: it ignores the hold
        and the balance on purpose. The only thing that can refuse it is
        the database ``CHECK`` of
        :class:`~tempest_fastapi_sdk.wallet.WalletBalanceMixin`. Spending
        money goes through :meth:`debit_available`.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Cents to take; must be positive.

        Returns:
            int | None: The balance after the debit, or ``None`` when no
            row has ``user_id``.

        Raises:
            ValueError: When ``amount_cents`` is not positive.
            WalletInsufficientFundsException: When the ``CHECK
                (wallet_cents >= 0)`` refused the statement.
        """
        _require_positive(amount_cents)
        return await self._move(user_id, -amount_cents, None)

    async def debit_available(
        self,
        user_id: UUID,
        amount_cents: int,
        *,
        now: datetime | None = None,
    ) -> int | None:
        """Take ``amount_cents`` only if the available balance covers it.

        One statement: ``UPDATE ... SET wallet_cents = wallet_cents - :n
        WHERE id = :id AND wallet_cents - (<held subquery>) >= :n
        RETURNING wallet_cents``. The hold is evaluated by the database
        in the same statement that moves the balance, so two concurrent
        debits cannot both see the same "available" and the second
        cannot take money that is still held.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Cents to take; must be positive.
            now (datetime | None): The instant separating held from
                available. Defaults to the current UTC time.

        Returns:
            int | None: The balance after the debit, or ``None`` when the
            row is missing **or** the available balance is short — the
            statement cannot tell the two apart; :meth:`exists` can.

        Raises:
            ValueError: When ``amount_cents`` is not positive.
        """
        _require_positive(amount_cents)
        moment = now if now is not None else utcnow()
        condition = self.model.wallet_cents - self.held_expression(moment) >= (
            amount_cents
        )
        return await self._move(user_id, -amount_cents, condition)

    async def claim_once(
        self,
        model: type[BaseModel],
        id: UUID,
        column: str,
        *,
        value: Any = None,
    ) -> bool:
        """Set ``column`` on one row only if it is still ``NULL``.

        ``UPDATE <model> SET <column> = :value WHERE id = :id AND
        <column> IS NULL RETURNING id``. The first caller gets ``True``
        and does the work the column stands for (credit the seller of a
        paid order, say); every later caller — a redelivered webhook, a
        retried job — gets ``False`` and does nothing. Run it in the same
        transaction as that work, so a failure releases the claim too.

        Args:
            model (type[BaseModel]): The model holding the claim column
                (the application's order, ticket, ...).
            id (UUID): Primary key of the row to claim.
            column (str): Name of a nullable column; ``NULL`` means
                "not claimed yet".
            value (Any): What to store. Defaults to the current UTC time,
                which suits a ``*_at`` column.

        Returns:
            bool: ``True`` when this call claimed the row, ``False`` when
            it was already claimed or does not exist.
        """
        target: BaseRepository[Any] = BaseRepository(
            self.session,
            model=model,
            autocommit=self.autocommit,
        )
        rows = await target.update_returning(
            {"id": id, column: None},
            {column: value if value is not None else utcnow()},
            returning=("id",),
        )
        return bool(rows)

    async def balance(
        self,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> WalletBalanceSchema | None:
        """Read the balance, the held part and the next release, in one query.

        Args:
            user_id (UUID): The wallet owner.
            now (datetime | None): The instant separating held from
                available. Defaults to the current UTC time.

        Returns:
            WalletBalanceSchema | None: The split balance, or ``None``
            when no row has ``user_id``.
        """
        moment = now if now is not None else utcnow()
        entry = self.entry_model
        next_release = (
            select(func.min(entry.available_at))
            .where(
                entry.user_id == self.model.id,
                entry.available_at > moment,
                entry.amount_cents > 0,
            )
            .correlate(self.model)
            .scalar_subquery()
        )
        row = (
            await self.session.execute(
                select(
                    self.model.wallet_cents,
                    self.held_expression(moment),
                    next_release,
                ).where(self.model.id == user_id),
            )
        ).first()
        if row is None:
            return None
        total, held, release = int(row[0]), int(row[1]), row[2]
        return WalletBalanceSchema(
            total_cents=total,
            held_cents=held,
            available_cents=total - held,
            next_release_at=release,
        )

    async def _move(
        self,
        user_id: UUID,
        delta_cents: int,
        condition: ColumnElement[bool] | None,
    ) -> int | None:
        """Add a signed delta to one balance, under an optional condition.

        Args:
            user_id (UUID): The wallet owner.
            delta_cents (int): Signed cents to add.
            condition (ColumnElement[bool] | None): Extra ``WHERE``.

        Returns:
            int | None: The new balance, or ``None`` when nothing matched.
        """
        rows = await self.update_returning(
            {"id": user_id},
            {"wallet_cents": F("wallet_cents") + delta_cents},
            returning=("wallet_cents",),
            where=condition,
        )
        return int(rows[0]["wallet_cents"]) if rows else None


def _require_positive(amount_cents: int) -> None:
    """Refuse a non-positive amount, which would invert the operation.

    A credit of ``-500`` is a debit that skipped every check a debit
    makes, so the sign is the method's job, never the argument's.

    Args:
        amount_cents (int): The amount to check.

    Raises:
        ValueError: When the amount is not a positive ``int``.
    """
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise ValueError("amount_cents must be an int number of cents")
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")


__all__: list[str] = [
    "WalletRepository",
]
