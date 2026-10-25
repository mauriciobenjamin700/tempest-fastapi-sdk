"""Wallet orchestration: the Pix withdrawal, over a :class:`WalletLedger`.

The balance moves themselves — one ``UPDATE ... SET balance = balance +
:delta`` and its ledger line per call — live in
:class:`~tempest_fastapi_sdk.wallet.WalletLedger`, the data layer.
:class:`WalletService` adds what talks to a third party (``withdraw``:
debit, ``PayoutProvider``, give the money back on refusal) and keeps the
ledger's operations as delegations, so code written against the service
keeps working (#617).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

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
from tempest_fastapi_sdk.wallet.ledger import WalletLedger
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
    """Withdraw wallets to Pix, and every ledger operation by delegation.

    The writes and reads belong to :attr:`ledger`, a
    :class:`~tempest_fastapi_sdk.wallet.WalletLedger`. A service of the
    project that has to credit or debit inside its own transaction receives
    the ledger, not this class — a service never receives another service.
    This class is what the wallet router and a withdrawal use.

    Attributes:
        ledger (WalletLedger): The data layer the moves go through.
    """

    def __init__(
        self,
        *,
        ledger: WalletLedger | None = None,
        balances: BaseRepository[Any] | None = None,
        entries: BaseRepository[Any] | None = None,
        balance_attribute: str = "wallet_cents",
    ) -> None:
        """Initialize the service over a ledger, or build one.

        Args:
            ledger (WalletLedger | None): The ledger to delegate to. Pass
                it, or ``balances`` and ``entries`` — the form every
                service before #617 used, which builds the ledger here.
            balances (BaseRepository[Any] | None): Repository of the
                balance owner model, when no ``ledger`` is given.
            entries (BaseRepository[Any] | None): Repository of the ledger
                model, when no ``ledger`` is given.
            balance_attribute (str): The balance column's attribute name,
                used only when building the ledger here.

        Raises:
            ValueError: If both a ``ledger`` and repositories are given,
                if neither is, or if the ledger refuses the repositories
                (no shared session, no ``balance_attribute``).
        """
        if ledger is not None:
            if balances is not None or entries is not None:
                raise ValueError("pass a ledger or balances and entries, not both")
            self.ledger: WalletLedger = ledger
            return
        if balances is None or entries is None:
            raise ValueError("pass a ledger, or both balances and entries")
        self.ledger = WalletLedger(
            balances=balances,
            entries=entries,
            balance_attribute=balance_attribute,
        )

    @property
    def balances(self) -> BaseRepository[Any]:
        """The ledger's balance repository, as before #617.

        Returns:
            BaseRepository[Any]: Repository of the balance owner model.
        """
        return self.ledger.balances

    @property
    def entries(self) -> BaseRepository[Any]:
        """The ledger's entry repository, as before #617.

        Returns:
            BaseRepository[Any]: Repository of the ledger model.
        """
        return self.ledger.entries

    @property
    def balance_attribute(self) -> str:
        """The balance column's attribute name, as before #617.

        Returns:
            str: The attribute name.
        """
        return self.ledger.balance_attribute

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
        """Delegate to :meth:`WalletLedger.credit`.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Amount to add, in cents.
            kind (str): Ledger kind, in the application's vocabulary.
            description (str): Statement text.
            reference_type (str | None): What the credit refers to.
            reference_id (str | None): Id of that reference.
            hold (timedelta): How long before the credit is withdrawable.

        Returns:
            WalletEntrySchema: The ledger line.
        """
        return await self.ledger.credit(
            user_id,
            amount_cents,
            kind=kind,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
            hold=hold,
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
        """Delegate to :meth:`WalletLedger.debit_available`.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Amount to take, in cents.
            kind (str): Ledger kind.
            description (str): Statement text.
            reference_type (str | None): What the debit refers to.
            reference_id (str | None): Id of that reference.

        Returns:
            WalletEntrySchema: The ledger line, with a negative amount.
        """
        return await self.ledger.debit_available(
            user_id,
            amount_cents,
            kind=kind,
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
        """Delegate to :meth:`WalletLedger.reverse`.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Amount to take back, in cents.
            description (str): Statement text.
            reference_type (str | None): What the reversal refers to.
            reference_id (str | None): Id of that reference.

        Returns:
            WalletEntrySchema | None: The ``REVERSAL`` line, or ``None``
            when the balance no longer covers the amount.
        """
        return await self.ledger.reverse(
            user_id,
            amount_cents,
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
        """Delegate to :meth:`WalletLedger.balance`.

        Args:
            user_id (UUID): The wallet owner.
            now (datetime | None): The instant to evaluate holds at.

        Returns:
            WalletBalanceSchema: Total, held, available and next release.
        """
        return await self.ledger.balance(user_id, now=now)

    async def statement(
        self,
        user_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Delegate to :meth:`WalletLedger.statement`.

        Args:
            user_id (UUID): The wallet owner.
            page (int): 1-indexed page number.
            page_size (int): Lines per page.

        Returns:
            dict[str, Any]: A page of :class:`WalletEntrySchema` lines.
        """
        return await self.ledger.statement(user_id, page=page, page_size=page_size)


__all__: list[str] = [
    "PAYOUT_REFERENCE_TYPE",
    "WalletService",
    "claim_once",
]
