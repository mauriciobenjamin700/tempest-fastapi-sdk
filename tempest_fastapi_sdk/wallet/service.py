"""Business logic for wallet balances, the ledger and Pix withdrawals.

:class:`WalletService` is the only thing that should change a wallet
balance. Every change is a single SQL ``UPDATE ... SET balance = balance
+ :delta`` — never a read in Python followed by a write — and lands in
the same transaction as its ledger line. That is what keeps two requests
crediting the same wallet at once from losing one of the credits, and two
withdrawals from paying the same balance twice.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from tempest_fastapi_sdk.db.expressions import F
from tempest_fastapi_sdk.exceptions.wallet import (
    InsufficientBalanceException,
    PayoutRejectedException,
    PayoutUncertainException,
)
from tempest_fastapi_sdk.integrations.payment.base import (
    PayoutProvider,
    PayoutRequest,
)
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.regex import PixKeyType
from tempest_fastapi_sdk.wallet.schemas import (
    WalletBalanceSchema,
    WalletEntryKind,
    WalletEntrySchema,
    WithdrawalSchema,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.repository import BaseRepository

LOGGER: logging.Logger = logging.getLogger(__name__)

PAYOUT_REFERENCE_TYPE: str = "payout"
"""``reference_type`` of the ledger lines a withdrawal writes."""


async def claim_once(
    session: AsyncSession,
    model: type[Any],
    row_id: UUID,
    column: str,
) -> bool:
    """Stamp ``column`` with now, only if it is still ``NULL``.

    The guard that makes a settlement run once: ``UPDATE ... WHERE id =
    :id AND column IS NULL``. Two concurrent callers (a webhook retry and a
    manual confirmation, say) both issue it; only one finds the column
    still empty. The loser gets ``False`` and must not repeat the effects.

    It does not commit. Call it inside the same
    :func:`~tempest_fastapi_sdk.db.transaction.transaction` block as the
    effects it guards, so a failure in those effects rolls the claim back
    too.

    Args:
        session (AsyncSession): The session the effects run on.
        model (type[Any]): The mapped class that owns the claim column
            (the product, the order, the charge).
        row_id (UUID): The row to claim.
        column (str): A nullable timestamp column, such as
            ``"wallet_credited_at"``.

    Returns:
        bool: ``True`` if this call claimed the row.
    """
    claim_column: InstrumentedAttribute[Any] = getattr(model, column)
    result = await session.execute(
        update(model)
        .where(model.id == row_id, claim_column.is_(None))
        .values({column: utcnow()})
    )
    return bool(getattr(result, "rowcount", 0) == 1)


class WalletService:
    """Credit, debit, withdraw and read wallets backed by a ledger.

    Both repositories must share one session: a balance update and its
    ledger line commit together or not at all.

    Attributes:
        balances (BaseRepository[Any]): Repository of the model that holds
            the balance (usually the application's user model).
        entries (BaseRepository[Any]): Repository of the concrete
            :class:`~tempest_fastapi_sdk.wallet.BaseWalletEntryModel`.
        balance_attribute (str): Name of the balance column on
            ``balances.model``.
    """

    def __init__(
        self,
        *,
        balances: BaseRepository[Any],
        entries: BaseRepository[Any],
        balance_attribute: str = "wallet_cents",
    ) -> None:
        """Initialize the service.

        Args:
            balances (BaseRepository[Any]): Repository of the balance
                owner model.
            entries (BaseRepository[Any]): Repository of the ledger model.
            balance_attribute (str): The balance column's attribute name.
                ``"wallet_cents"`` matches
                :class:`~tempest_fastapi_sdk.wallet.WalletBalanceMixin`; an
                existing model passes its own (``"wallet"``).

        Raises:
            ValueError: If the repositories do not share a session, or the
                balance model has no ``balance_attribute``.
        """
        if balances.session is not entries.session:
            raise ValueError("balances and entries must share one session")
        if not hasattr(balances.model, balance_attribute):
            raise ValueError(
                f"{balances.model.__name__} has no attribute {balance_attribute!r}"
            )
        self.balances: BaseRepository[Any] = balances
        self.entries: BaseRepository[Any] = entries
        self.balance_attribute: str = balance_attribute

    @property
    def _session(self) -> AsyncSession:
        """The session both repositories share.

        Returns:
            AsyncSession: The session.
        """
        session: AsyncSession = self.entries.session
        return session

    @property
    def _balance_column(self) -> InstrumentedAttribute[Any]:
        """The mapped balance column on the balance owner model.

        Returns:
            InstrumentedAttribute[Any]: The column attribute.
        """
        column: InstrumentedAttribute[Any] = getattr(
            self.balances.model, self.balance_attribute
        )
        return column

    async def credit(
        self,
        user_id: UUID,
        amount_cents: int,
        *,
        kind: str,
        description: str = "",
        reference_type: str | None = None,
        reference_id: str | None = None,
        hold: timedelta = timedelta(0),
    ) -> WalletEntrySchema:
        """Add ``amount_cents`` to a wallet and write the ledger line.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Amount to add, in cents.
            kind (str): Ledger kind, in the application's vocabulary.
            description (str): Statement text.
            reference_type (str | None): What the credit refers to.
            reference_id (str | None): Id of that reference.
            hold (timedelta): How long before the credit is withdrawable.
                ``timedelta(0)`` releases it at once.

        Returns:
            WalletEntrySchema: The ledger line.

        Raises:
            ValueError: If ``amount_cents`` is not positive or ``hold`` is
                negative.
            LookupError: If no balance row has ``user_id``.
        """
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if hold < timedelta(0):
            raise ValueError("hold must not be negative")
        async with self.entries.transaction():
            balance_after = await self._apply(user_id, amount_cents)
            if balance_after is None:
                raise LookupError(f"no wallet for user {user_id}")
            now = utcnow()
            return await self._write_entry(
                user_id=user_id,
                kind=kind,
                amount_cents=amount_cents,
                balance_after_cents=balance_after,
                available_at=now + hold,
                description=description,
                reference_type=reference_type,
                reference_id=reference_id,
            )

    async def debit_available(
        self,
        user_id: UUID,
        amount_cents: int,
        *,
        kind: str,
        description: str = "",
        reference_type: str | None = None,
        reference_id: str | None = None,
    ) -> WalletEntrySchema:
        """Take ``amount_cents`` out of the withdrawable part of a wallet.

        The hold is enforced **inside** the ``UPDATE``: the statement only
        matches when ``balance - amount`` stays at or above the sum of
        credits not yet released. Computing "available" first and then
        debiting with a plain ``balance >= amount`` guard lets two
        concurrent withdrawals both pass and drain held money — the gap
        this closes.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Amount to take, in cents.
            kind (str): Ledger kind.
            description (str): Statement text.
            reference_type (str | None): What the debit refers to.
            reference_id (str | None): Id of that reference.

        Returns:
            WalletEntrySchema: The ledger line, with a negative amount.

        Raises:
            ValueError: If ``amount_cents`` is not positive.
            InsufficientBalanceException: If the available balance is
                smaller than ``amount_cents``.
        """
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        async with self.entries.transaction():
            now = utcnow()
            balance_after = await self._apply(
                user_id,
                -amount_cents,
                floor=self._held_cents_query(user_id, now).scalar_subquery(),
            )
            if balance_after is None:
                raise InsufficientBalanceException()
            return await self._write_entry(
                user_id=user_id,
                kind=kind,
                amount_cents=-amount_cents,
                balance_after_cents=balance_after,
                available_at=now,
                description=description,
                reference_type=reference_type,
                reference_id=reference_id,
            )

    async def reverse(
        self,
        user_id: UUID,
        amount_cents: int,
        *,
        description: str = "",
        reference_type: str | None = None,
        reference_id: str | None = None,
    ) -> WalletEntrySchema | None:
        """Take back a credit, held or not, if the balance still covers it.

        For a refund to the payer after the payee was credited. Unlike
        :meth:`debit_available`, money on hold counts: it is exactly the
        money a reversal is supposed to reach first.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Amount to take back, in cents.
            description (str): Statement text.
            reference_type (str | None): What the reversal refers to.
            reference_id (str | None): Id of that reference.

        Returns:
            WalletEntrySchema | None: The ``REVERSAL`` line, or ``None``
            when the balance no longer covers the amount (the payee already
            withdrew it). ``None`` is a loss for the platform and should be
            escalated by the caller; nothing was changed.

        Raises:
            ValueError: If ``amount_cents`` is not positive.
        """
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        async with self.entries.transaction():
            balance_after = await self._apply(user_id, -amount_cents, floor=0)
            if balance_after is None:
                return None
            return await self._write_entry(
                user_id=user_id,
                kind=WalletEntryKind.REVERSAL.value,
                amount_cents=-amount_cents,
                balance_after_cents=balance_after,
                available_at=utcnow(),
                description=description,
                reference_type=reference_type,
                reference_id=reference_id,
            )

    async def withdraw(
        self,
        user_id: UUID,
        *,
        payout: PayoutProvider,
        pix_key: str,
        pix_key_type: PixKeyType,
        amount_cents: int | None = None,
        correlation_id: str | None = None,
        description: str = "Saque Pix",
    ) -> WithdrawalSchema:
        """Pay a wallet out to a Pix key: debit first, then transfer.

        The order is the safety property. The debit commits before the
        provider is called, so a second withdrawal racing this one sees the
        reduced balance. Then:

        * the provider refuses
          (:class:`~tempest_fastapi_sdk.exceptions.PayoutRejectedException`)
          -> the debit is given back as a ``WITHDRAW_REFUND`` line, and the
          exception is re-raised;
        * anything else goes wrong -> the outcome is unknown, so the debit
          is **kept**, the case is logged ``CRITICAL`` with the
          correlation id, and
          :class:`~tempest_fastapi_sdk.exceptions.PayoutUncertainException`
          is raised. Giving the money back here could pay it twice.

        Args:
            user_id (UUID): The wallet owner.
            payout (PayoutProvider): Who sends the Pix.
            pix_key (str): Destination key — read it from the owner's
                profile, never from the request body.
            pix_key_type (PixKeyType): Kind of key.
            amount_cents (int | None): Amount to withdraw. ``None`` takes
                the whole available balance.
            correlation_id (str | None): Id for the provider; a UUID is
                generated when omitted.
            description (str): Statement text, also sent as the comment.

        Returns:
            WithdrawalSchema: The ``WITHDRAW`` line and the provider's
            answer.

        Raises:
            InsufficientBalanceException: If nothing (or not enough) is
                available.
            PayoutRejectedException: If the provider refused; the debit
                was returned.
            PayoutUncertainException: If the outcome is unknown; the debit
                was kept.
        """
        if amount_cents is None:
            amount_cents = (await self.balance(user_id)).available_cents
        if amount_cents <= 0:
            raise InsufficientBalanceException()
        correlation = correlation_id or str(uuid4())
        entry = await self.debit_available(
            user_id,
            amount_cents,
            kind=WalletEntryKind.WITHDRAW.value,
            description=description,
            reference_type=PAYOUT_REFERENCE_TYPE,
            reference_id=correlation,
        )
        try:
            result = await payout.transfer_to_pix_key(
                PayoutRequest(
                    amount_cents=amount_cents,
                    pix_key=pix_key,
                    pix_key_type=pix_key_type,
                    correlation_id=correlation,
                    comment=description,
                )
            )
        except PayoutRejectedException:
            await self.credit(
                user_id,
                amount_cents,
                kind=WalletEntryKind.WITHDRAW_REFUND.value,
                description=f"Estorno: {description}",
                reference_type=PAYOUT_REFERENCE_TYPE,
                reference_id=correlation,
            )
            raise
        except Exception as exc:
            LOGGER.critical(
                "Payout outcome unknown; debit kept for reconciliation "
                "(user=%s, amount_cents=%s, correlation_id=%s): %r",
                user_id,
                amount_cents,
                correlation,
                exc,
            )
            raise PayoutUncertainException() from exc
        return WithdrawalSchema(entry=entry, payout=result)

    async def balance(
        self,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> WalletBalanceSchema:
        """Split a wallet into held and available.

        Args:
            user_id (UUID): The wallet owner.
            now (datetime | None): The instant to evaluate holds at;
                defaults to the current UTC time.

        Returns:
            WalletBalanceSchema: Total, held (capped at the total),
            available, and the next release.

        Raises:
            LookupError: If no balance row has ``user_id``.
        """
        moment = now or utcnow()
        total = await self._session.scalar(
            select(self._balance_column).where(self.balances.model.id == user_id)
        )
        if total is None:
            raise LookupError(f"no wallet for user {user_id}")
        held = await self._session.scalar(self._held_cents_query(user_id, moment))
        entry_model = self.entries.model
        next_release = await self._session.scalar(
            select(func.min(entry_model.available_at)).where(
                entry_model.user_id == user_id,
                entry_model.amount_cents > 0,
                entry_model.available_at > moment,
            )
        )
        held_cents = min(int(held or 0), int(total))
        return WalletBalanceSchema(
            user_id=user_id,
            total_cents=int(total),
            held_cents=held_cents,
            available_cents=int(total) - held_cents,
            next_release_at=next_release,
        )

    async def statement(
        self,
        user_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Return a page of a wallet's ledger, newest first.

        Args:
            user_id (UUID): The wallet owner.
            page (int): 1-indexed page number.
            page_size (int): Lines per page.

        Returns:
            dict[str, Any]: ``items`` (:class:`WalletEntrySchema`),
            ``total``, ``page``, ``page_size`` and ``pages``.
        """
        result = await self.entries.paginate(
            filters={"user_id": user_id},
            order_by="created_at",
            page=page,
            page_size=page_size,
            ascending=False,
        )
        items = [WalletEntrySchema.model_validate(row) for row in result["items"]]
        return {**result, "items": items}

    def _held_cents_query(self, user_id: UUID, now: datetime) -> Select[Any]:
        """Select the credits of ``user_id`` not yet released at ``now``.

        Args:
            user_id (UUID): The wallet owner.
            now (datetime): The instant to evaluate holds at.

        Returns:
            Select[Any]: One row, one column: cents held (``0`` when
            nothing is). Execute it, or turn it into a scalar subquery to
            use inside another statement.
        """
        entry_model = self.entries.model
        return select(func.coalesce(func.sum(entry_model.amount_cents), 0)).where(
            entry_model.user_id == user_id,
            entry_model.amount_cents > 0,
            entry_model.available_at > now,
        )

    async def _apply(
        self,
        user_id: UUID,
        delta_cents: int,
        *,
        floor: Any = None,
    ) -> int | None:
        """Move a balance by ``delta_cents`` in one ``UPDATE ... RETURNING``.

        Delegates to :meth:`BaseRepository.update_returning`, so the new
        balance is computed by the database (``F(column) + delta``) and the
        floor is part of the same statement's ``WHERE``.

        Args:
            user_id (UUID): The wallet owner.
            delta_cents (int): Signed change.
            floor (Any): When given, the update only matches if the new
                balance stays at or above it — an int, or a scalar
                subquery such as :meth:`_held_cents_query`.

        Returns:
            int | None: The new balance, or ``None`` when no row matched
            (unknown user, or the floor would be crossed).
        """
        rows = await self.balances.update_returning(
            {"id": user_id},
            {self.balance_attribute: F(self.balance_attribute) + delta_cents},
            returning=[self.balance_attribute],
            where=(
                None if floor is None else self._balance_column + delta_cents >= floor
            ),
        )
        return int(rows[0][self.balance_attribute]) if rows else None

    async def _write_entry(self, **values: Any) -> WalletEntrySchema:
        """Insert a ledger line inside the caller's transaction block.

        Args:
            **values (Any): Column values of the ledger model, forwarded to
                its constructor.

        Returns:
            WalletEntrySchema: The persisted line.
        """
        row = await self.entries.add(self.entries.model(**values))
        return WalletEntrySchema.model_validate(row)


__all__: list[str] = [
    "PAYOUT_REFERENCE_TYPE",
    "WalletService",
    "claim_once",
]
