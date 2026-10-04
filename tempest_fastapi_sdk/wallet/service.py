"""Wallet operations: balance move and statement line in one transaction.

:class:`WalletService` pairs every balance ``UPDATE`` from
:class:`~tempest_fastapi_sdk.wallet.WalletRepository` with the
statement line that explains it, inside one
:func:`~tempest_fastapi_sdk.db.transaction.transaction` block. Either
both are written or neither is, so the cached balance always equals the
last ``balance_after_cents`` of the statement.

Every movement names the event that caused it, and the statement's
unique ``(reference_type, reference_id, kind)`` turns a replay into a
no-op: posting the same reference again returns the entry it already
produced and moves nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.db.transaction import savepoint, transaction
from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.schemas.pagination import BasePaginationSchema
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.wallet.exceptions import (
    WalletEntryNotFoundException,
    WalletInsufficientFundsException,
    WalletNotFoundException,
    WalletReferenceConflictException,
)
from tempest_fastapi_sdk.wallet.repository import WalletRepository, _require_positive
from tempest_fastapi_sdk.wallet.schemas import (
    WalletBalanceSchema,
    WalletEntryKind,
    WalletEntrySchema,
)

REVERSAL_REFERENCE_TYPE: str = "wallet_entry"
"""``reference_type`` of a reversal line; its ``reference_id`` is the
reversed entry's id, so reversing the same entry twice is a replay."""


class _DuplicateEntryError(Exception):
    """Raised inside the savepoint to roll back a movement that replayed.

    Attributes:
        conflict (ConflictException): The refusal of the ``INSERT``,
            re-raised when no replayed entry explains it.
    """

    def __init__(self, conflict: ConflictException) -> None:
        """Initialize the marker.

        Args:
            conflict (ConflictException): The refusal of the ``INSERT``.
        """
        super().__init__(conflict.detail)
        self.conflict: ConflictException = conflict


class WalletService:
    """Credit, debit, reverse and read a wallet, with its statement.

    Both repositories must share one session: the balance ``UPDATE`` and
    the statement ``INSERT`` are one transaction. Called inside a
    caller's own :func:`~tempest_fastapi_sdk.db.transaction.transaction`
    block, every method joins it and the caller's exit commits.

    Attributes:
        balances (WalletRepository): Repository over the balance column.
        entries (BaseRepository[Any]): Repository over the statement
            model (the same model ``balances.entry_model`` names).
    """

    def __init__(
        self,
        *,
        balances: WalletRepository,
        entries: BaseRepository[Any],
    ) -> None:
        """Initialize the service.

        Args:
            balances (WalletRepository): The balance repository.
            entries (BaseRepository[Any]): The statement repository.

        Raises:
            ValueError: When the two repositories use different sessions
                or ``entries`` is not over ``balances.entry_model`` —
                either would split one movement across two transactions
                or two tables.
        """
        if balances.session is not entries.session:
            raise ValueError("balances and entries must share one session")
        if entries.model is not balances.entry_model:
            raise ValueError(
                "entries must be a repository over balances.entry_model",
            )
        self.balances: WalletRepository = balances
        self.entries: BaseRepository[Any] = entries

    async def credit(
        self,
        user_id: UUID,
        amount_cents: int,
        *,
        reference_type: str,
        reference_id: UUID,
        kind: str = WalletEntryKind.CREDIT,
        hold: timedelta = timedelta(0),
        description: str | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> WalletEntrySchema:
        """Add money to a wallet and record why.

        A replay — the same reference (or ``idempotency_key``) with the
        same owner and amount — returns the entry the first call wrote
        and leaves the balance alone, so a webhook delivered twice
        credits once. It is safe under concurrency too: two deliveries
        racing each other both run the ``UPDATE``, the second ``INSERT``
        hits the unique constraint, and its savepoint takes its
        ``UPDATE`` back with it.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Cents to add; must be positive.
            reference_type (str): Kind of the causing event (``"order"``).
            reference_id (UUID): Id of the causing event.
            kind (str): Statement kind; ``"credit"`` by default.
            hold (timedelta): How long the money stays held before a
                debit can take it. ``0`` makes it available at once.
            description (str | None): Free text for the statement line.
            idempotency_key (str | None): Optional extra replay key.
            now (datetime | None): The posting instant. Defaults to the
                current UTC time.

        Returns:
            WalletEntrySchema: The entry written — or, on a replay, the
            entry the first call wrote.

        Raises:
            ValueError: When ``amount_cents`` is not positive or ``hold``
                is negative.
            WalletNotFoundException: When no wallet row has ``user_id``.
            WalletReferenceConflictException: When the reference already
                moved a different amount or another wallet.
        """
        _require_positive(amount_cents)
        if hold < timedelta(0):
            raise ValueError("hold must not be negative")
        moment = now if now is not None else utcnow()
        return await self._post(
            user_id,
            amount_cents,
            kind=kind,
            reference_type=reference_type,
            reference_id=reference_id,
            available_at=moment + hold,
            description=description,
            idempotency_key=idempotency_key,
            now=moment,
        )

    async def debit(
        self,
        user_id: UUID,
        amount_cents: int,
        *,
        reference_type: str,
        reference_id: UUID,
        kind: str = WalletEntryKind.DEBIT,
        description: str | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> WalletEntrySchema:
        """Take money the wallet can spend now, and record why.

        Only the **available** balance can be taken: the held credits
        are discounted inside the same ``UPDATE`` that moves the
        balance. Replays behave as in :meth:`credit`.

        Args:
            user_id (UUID): The wallet owner.
            amount_cents (int): Cents to take; must be positive.
            reference_type (str): Kind of the causing event.
            reference_id (UUID): Id of the causing event.
            kind (str): Statement kind; ``"debit"`` by default.
            description (str | None): Free text for the statement line.
            idempotency_key (str | None): Optional extra replay key.
            now (datetime | None): The posting instant, which is also the
                instant separating held from available.

        Returns:
            WalletEntrySchema: The entry written, or the replayed one.

        Raises:
            ValueError: When ``amount_cents`` is not positive.
            WalletInsufficientFundsException: When the available balance
                does not cover ``amount_cents``.
            WalletNotFoundException: When no wallet row has ``user_id``.
            WalletReferenceConflictException: When the reference already
                moved a different amount or another wallet.
        """
        _require_positive(amount_cents)
        moment = now if now is not None else utcnow()
        return await self._post(
            user_id,
            -amount_cents,
            kind=kind,
            reference_type=reference_type,
            reference_id=reference_id,
            available_at=moment,
            description=description,
            idempotency_key=idempotency_key,
            now=moment,
        )

    async def reverse(
        self,
        entry_id: UUID,
        *,
        description: str | None = None,
        now: datetime | None = None,
    ) -> WalletEntrySchema:
        """Undo one statement line with an opposite line.

        The original stays — the statement is append-only. The reversal
        line carries ``kind="reversal"`` and references the original, so
        reversing the same entry twice is a replay and moves nothing the
        second time.

        Reversing a debit (a refund) credits the amount back, available
        at once. Reversing a credit is a **forced** debit: it ignores the
        hold, because the money it takes back is exactly the money the
        credit added. A credit still held is reversed with the credit's
        own ``available_at``, which removes it from the hold as well.
        With :class:`~tempest_fastapi_sdk.wallet.WalletBalanceMixin`, a
        reversal that would take the balance below zero — the user
        already spent the money — is refused; with
        :class:`~tempest_fastapi_sdk.wallet.OverdraftWalletBalanceMixin`
        it is written and the balance goes negative.

        Args:
            entry_id (UUID): The statement line to undo.
            description (str | None): Free text for the reversal line.
            now (datetime | None): The posting instant.

        Returns:
            WalletEntrySchema: The reversal line, or the replayed one.

        Raises:
            WalletEntryNotFoundException: When ``entry_id`` does not
                exist.
            WalletInsufficientFundsException: When the balance ``CHECK``
                refused a credit reversal.
        """
        original = await self.entries.get_or_none({"id": entry_id})
        if original is None:
            raise WalletEntryNotFoundException(details={"entry_id": str(entry_id)})
        moment = now if now is not None else utcnow()
        available_at = (
            original.available_at
            if original.amount_cents > 0 and original.available_at > moment
            else moment
        )
        return await self._post(
            original.user_id,
            -original.amount_cents,
            kind=WalletEntryKind.REVERSAL,
            reference_type=REVERSAL_REFERENCE_TYPE,
            reference_id=original.id,
            available_at=available_at,
            description=description,
            idempotency_key=None,
            now=moment,
            forced=True,
        )

    async def balance(
        self,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> WalletBalanceSchema:
        """Return the total, held and available balance of a wallet.

        Args:
            user_id (UUID): The wallet owner.
            now (datetime | None): The instant separating held from
                available. Defaults to the current UTC time.

        Returns:
            WalletBalanceSchema: The split balance.

        Raises:
            WalletNotFoundException: When no wallet row has ``user_id``.
        """
        result = await self.balances.balance(user_id, now=now)
        if result is None:
            raise WalletNotFoundException(details={"user_id": str(user_id)})
        return result

    async def statement(
        self,
        user_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> BasePaginationSchema[WalletEntrySchema]:
        """Return one page of a wallet's statement, newest first.

        Args:
            user_id (UUID): The wallet owner.
            page (int): 1-indexed page number.
            page_size (int): Entries per page.

        Returns:
            BasePaginationSchema[WalletEntrySchema]: The page. A wallet
            with no movement — or an id with no wallet — yields
            ``items=[]``, never an error.
        """
        result = await self.entries.paginate(
            filters={"user_id": user_id},
            order_by="created_at",
            page=page,
            page_size=page_size,
            ascending=False,
        )
        return BasePaginationSchema[WalletEntrySchema](
            items=[WalletEntrySchema.model_validate(row) for row in result["items"]],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
            pages=result["pages"],
        )

    async def _post(
        self,
        user_id: UUID,
        delta_cents: int,
        *,
        kind: str,
        reference_type: str,
        reference_id: UUID,
        available_at: datetime,
        description: str | None,
        idempotency_key: str | None,
        now: datetime,
        forced: bool = False,
    ) -> WalletEntrySchema:
        """Move the balance and write its statement line, or replay.

        The ``UPDATE`` and the ``INSERT`` share a savepoint. When the
        ``INSERT`` hits the replay guard, the savepoint rolls the
        ``UPDATE`` back and the original entry is returned instead.

        Args:
            user_id (UUID): The wallet owner.
            delta_cents (int): Signed cents; never zero.
            kind (str): Statement kind.
            reference_type (str): Kind of the causing event.
            reference_id (UUID): Id of the causing event.
            available_at (datetime): When the movement stops being held.
            description (str | None): Free text.
            idempotency_key (str | None): Optional extra replay key.
            now (datetime): The posting instant.
            forced (bool): Take a negative delta with :meth:`WalletRepository.debit`
                (no availability check) instead of
                :meth:`WalletRepository.debit_available`.

        Returns:
            WalletEntrySchema: The new entry, or the replayed one.

        Raises:
            WalletInsufficientFundsException: When the debit was refused.
            WalletNotFoundException: When no wallet row has ``user_id``.
            WalletReferenceConflictException: When the reference already
                moved something else.
        """
        session = self.balances.session
        async with transaction(session):
            try:
                async with savepoint(session):
                    balance_after = await self._move(user_id, delta_cents, forced, now)
                    try:
                        row = await self.entries.add(
                            self.entries.model(
                                user_id=user_id,
                                kind=str(kind),
                                amount_cents=delta_cents,
                                balance_after_cents=balance_after,
                                available_at=available_at,
                                reference_type=reference_type,
                                reference_id=reference_id,
                                description=description,
                                idempotency_key=idempotency_key,
                            ),
                        )
                    except ConflictException as exc:
                        raise _DuplicateEntryError(exc) from exc
            except _DuplicateEntryError as duplicate:
                existing = await self._find_replayed(
                    kind,
                    reference_type,
                    reference_id,
                    idempotency_key,
                )
                if existing is None:
                    raise duplicate.conflict from None
                if existing.user_id != user_id or existing.amount_cents != delta_cents:
                    raise WalletReferenceConflictException(
                        details={
                            "reference_type": reference_type,
                            "reference_id": str(reference_id),
                            "entry_id": str(existing.id),
                        },
                    ) from None
                return WalletEntrySchema.model_validate(existing)
            return WalletEntrySchema.model_validate(row)

    async def _move(
        self,
        user_id: UUID,
        delta_cents: int,
        forced: bool,
        now: datetime,
    ) -> int:
        """Run the one balance ``UPDATE`` a movement needs.

        Args:
            user_id (UUID): The wallet owner.
            delta_cents (int): Signed cents.
            forced (bool): Whether a negative delta skips availability.
            now (datetime): The instant separating held from available.

        Returns:
            int: The balance after the movement.

        Raises:
            WalletInsufficientFundsException: When the debit was refused.
            WalletNotFoundException: When no wallet row has ``user_id``.
        """
        if delta_cents > 0:
            balance_after = await self.balances.credit(user_id, delta_cents)
        elif forced:
            balance_after = await self.balances.debit(user_id, -delta_cents)
        else:
            balance_after = await self.balances.debit_available(
                user_id,
                -delta_cents,
                now=now,
            )
        if balance_after is not None:
            return balance_after
        if not await self.balances.exists({"id": user_id}):
            raise WalletNotFoundException(details={"user_id": str(user_id)})
        raise WalletInsufficientFundsException(
            details={"user_id": str(user_id), "amount_cents": -delta_cents},
        )

    async def _find_replayed(
        self,
        kind: str,
        reference_type: str,
        reference_id: UUID,
        idempotency_key: str | None,
    ) -> Any:
        """Find the entry a refused ``INSERT`` collided with.

        Args:
            kind (str): Statement kind.
            reference_type (str): Kind of the causing event.
            reference_id (UUID): Id of the causing event.
            idempotency_key (str | None): Optional extra replay key.

        Returns:
            Any: The existing entry row, or ``None`` when the collision
            was on something else.
        """
        existing = await self.entries.get_or_none(
            {
                "reference_type": reference_type,
                "reference_id": reference_id,
                "kind": str(kind),
            },
        )
        if existing is None and idempotency_key is not None:
            existing = await self.entries.get_or_none(
                {"idempotency_key": idempotency_key},
            )
        return existing


__all__: list[str] = [
    "REVERSAL_REFERENCE_TYPE",
    "WalletService",
]
