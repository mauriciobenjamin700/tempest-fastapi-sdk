"""Settle a manual payout: mark it paid, or refuse it and refund the wallet.

:class:`ManualPayoutService` is what the admin panel's actions
(:func:`~tempest_fastapi_sdk.admin.manual_payout_admin`) call, and what a
service calls from its own route when the queue is exposed through the API
instead of the panel. Both transitions leave ``PENDING`` with one
conditional ``UPDATE ... WHERE id = :id AND status = 'pending'``: two
administrators acting on the same row at once move it once, and the second
gets :class:`~tempest_fastapi_sdk.exceptions.ManualPayoutNotPendingException`
with nothing changed.

:class:`ManualPayoutHooks` lets the service write its own records — the
withdrawal line a user sees in their history — in the same transaction as
each transition. A hook that raises rolls the transition back with it.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, NoReturn, Protocol
from uuid import UUID

from sqlalchemy import select, update

from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.exceptions.wallet import (
    ManualPayoutNotFoundException,
    ManualPayoutNotPendingException,
    ManualPayoutReceiptMissingException,
)
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.wallet.manual_payout_models import ManualPayoutStatusEnum
from tempest_fastapi_sdk.wallet.schemas import WalletEntryKindEnum
from tempest_fastapi_sdk.wallet.service import PAYOUT_REFERENCE_TYPE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.repository import BaseRepository
    from tempest_fastapi_sdk.wallet.ledger import WalletLedger

MAX_REJECTION_REASON_LENGTH: Final[int] = 512
"""Longest reason :meth:`ManualPayoutService.reject` takes — the column's size."""

DEFAULT_REFUND_DESCRIPTION: Final[str] = "Estorno: saque recusado"
"""Statement text of the ``WITHDRAW_REFUND`` line a rejection writes."""


class ManualPayoutHook(Protocol):
    """A callback run inside a manual payout's transition.

    It receives the session the transition runs on and the row as it is
    after the transition. Whatever it writes on that session commits with
    the transition; if it raises, the transition, the refund and its own
    writes roll back together. Parameters are positional-only so an
    implementation may name them anything.
    """

    def __call__(self, session: AsyncSession, row: Any, /) -> Awaitable[None]:
        """Run the hook.

        Args:
            session (AsyncSession): The transition's session.
            row (Any): The project's
                :class:`~tempest_fastapi_sdk.wallet.BaseManualPayoutModel`
                row, after the transition.

        Returns:
            Awaitable[None]: Completes when the hook's writes are flushed
            or staged on ``session``.
        """
        ...


@dataclass(frozen=True)
class ManualPayoutHooks:
    """The callbacks a manual payout runs on each transition.

    Pass the same instance to
    :class:`~tempest_fastapi_sdk.wallet.ManualPayoutProvider` (which writes
    the row and runs ``on_requested`` / ``on_canceled``) and to
    :class:`ManualPayoutService` (``on_paid`` / ``on_rejected``).

    Attributes:
        on_requested (ManualPayoutHook | None): After the ``PENDING`` row is
            written, in its transaction. Raising refuses the payout: the row
            is not written, no e-mail goes out, the wallet gets its debit
            back.
        on_canceled (ManualPayoutHook | None): After the row turns
            ``CANCELED`` because the e-mail to the administrators failed, in
            that update's transaction. Raising leaves the row ``PENDING``
            and the outcome unknown to the withdrawal, which keeps the debit
            and answers
            :class:`~tempest_fastapi_sdk.exceptions.PayoutUncertainException`.
        on_paid (ManualPayoutHook | None): After the row turns
            ``COMPLETED``, in that transaction.
        on_rejected (ManualPayoutHook | None): After the row turns
            ``REJECTED`` and the refund line is written, in that transaction.
    """

    on_requested: ManualPayoutHook | None = None
    on_canceled: ManualPayoutHook | None = None
    on_paid: ManualPayoutHook | None = None
    on_rejected: ManualPayoutHook | None = None


class ManualPayoutService:
    """Mark a manual payout paid, or refuse it and give the money back.

    Each method is one transaction on the repository's session: the
    conditional ``UPDATE`` over ``PENDING``, the refund (for a rejection)
    and the hook. Inside a caller's
    :func:`~tempest_fastapi_sdk.db.transaction.transaction` block it joins
    that block and commits with it.

    Attributes:
        records (BaseRepository[Any]): Repository of the project's concrete
            :class:`~tempest_fastapi_sdk.wallet.BaseManualPayoutModel`.
        ledger (WalletLedger | None): The wallet the refund goes to.
        hooks (ManualPayoutHooks): The transition callbacks.
    """

    def __init__(
        self,
        *,
        records: BaseRepository[Any],
        ledger: WalletLedger | None = None,
        hooks: ManualPayoutHooks | None = None,
        refund_description: str = DEFAULT_REFUND_DESCRIPTION,
    ) -> None:
        """Store the repositories and the hooks.

        Args:
            records (BaseRepository[Any]): Repository of the manual payout
                model.
            ledger (WalletLedger | None): The wallet ledger the refund of
                :meth:`reject` is credited through — ``WalletService.ledger``
                when the project already has a service. ``None`` allows
                :meth:`mark_paid` only.
            hooks (ManualPayoutHooks | None): Transition callbacks.
            refund_description (str): Statement text of the refund line.

        Raises:
            ValueError: If ``ledger`` is on another session than
                ``records`` — the refund and the status change would commit
                apart.
        """
        if ledger is not None and ledger.entries.session is not records.session:
            raise ValueError("records and ledger must share one session")
        self.records: BaseRepository[Any] = records
        self.ledger: WalletLedger | None = ledger
        self.hooks: ManualPayoutHooks = hooks or ManualPayoutHooks()
        self._refund_description: str = refund_description

    @property
    def _session(self) -> AsyncSession:
        """The session every transition runs on.

        Returns:
            AsyncSession: The repository's session.
        """
        session: AsyncSession = self.records.session
        return session

    async def _load(self, row_id: UUID) -> Any:
        """Read a row fresh from the database.

        Args:
            row_id (UUID): The row id.

        Returns:
            Any: The row, or ``None`` when no row has the id.
        """
        model = self.records.model
        result = await self._session.execute(
            select(model)
            .where(model.id == row_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _refuse(self, row_id: UUID) -> NoReturn:
        """Raise the reason a conditional ``UPDATE`` matched nothing.

        Args:
            row_id (UUID): The row the ``UPDATE`` targeted.

        Raises:
            ManualPayoutNotFoundException: If no row has the id.
            ManualPayoutNotPendingException: If the row is no longer
                ``PENDING``.
            ManualPayoutReceiptMissingException: If the row is ``PENDING``
                and has no receipt.
        """
        row = await self._load(row_id)
        if row is None:
            raise ManualPayoutNotFoundException()
        if row.status != ManualPayoutStatusEnum.PENDING.value:
            raise ManualPayoutNotPendingException()
        raise ManualPayoutReceiptMissingException()

    async def mark_paid(
        self,
        row_id: UUID,
        end_to_end_id: str | None = None,
        *,
        actor: str | None = None,
        require_receipt: bool = False,
    ) -> Any:
        """Move a ``PENDING`` payout to ``COMPLETED`` and run ``on_paid``.

        Args:
            row_id (UUID): The payout row.
            end_to_end_id (str | None): The Pix end-to-end id of the
                transfer the administrator made, when known.
            actor (str | None): Who marked it paid, stored in
                ``completed_by``.
            require_receipt (bool): Refuse a row without ``receipt_key``.
                The admin panel passes ``True``; a route that does not
                take receipts keeps the default.

        Returns:
            Any: The row after the transition.

        Raises:
            ManualPayoutNotFoundException: If no row has the id.
            ManualPayoutNotPendingException: If the row is no longer
                ``PENDING``.
            ManualPayoutReceiptMissingException: If ``require_receipt`` and
                the row has no receipt.
        """
        model = self.records.model
        conditions: list[Any] = [
            model.id == row_id,
            model.status == ManualPayoutStatusEnum.PENDING.value,
        ]
        if require_receipt:
            conditions.append(model.receipt_key.is_not(None))
        async with transaction(self._session):
            result = await self._session.execute(
                update(model)
                .where(*conditions)
                .values(
                    status=ManualPayoutStatusEnum.COMPLETED.value,
                    completed_at=utcnow(),
                    completed_by=actor,
                    end_to_end_id=end_to_end_id,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                await self._refuse(row_id)
            row = await self._load(row_id)
            if self.hooks.on_paid is not None:
                await self.hooks.on_paid(self._session, row)
        return row

    async def reject(
        self,
        row_id: UUID,
        reason: str,
        *,
        actor: str | None = None,
    ) -> Any:
        """Refuse a ``PENDING`` payout and credit the amount back.

        The row turns ``REJECTED`` with the reason, the wallet gets a
        ``WITHDRAW_REFUND`` line for the amount (``reference_id`` is the
        correlation id, as in the ``WITHDRAW`` line), and ``on_rejected``
        runs — all in one commit.

        Args:
            row_id (UUID): The payout row.
            reason (str): Why it was refused; stored in
                ``rejection_reason``.
            actor (str | None): Who refused it, stored in ``rejected_by``.

        Returns:
            Any: The row after the transition.

        Raises:
            ValueError: If ``reason`` is blank or longer than
                :data:`MAX_REJECTION_REASON_LENGTH`, or the service has no
                ``ledger``. Nothing was written.
            ManualPayoutNotFoundException: If no row has the id.
            ManualPayoutNotPendingException: If the row is no longer
                ``PENDING``.
        """
        text = reason.strip()
        if not text:
            raise ValueError("a rejection needs a reason")
        if len(text) > MAX_REJECTION_REASON_LENGTH:
            raise ValueError(
                f"the reason exceeds {MAX_REJECTION_REASON_LENGTH} characters"
            )
        if self.ledger is None:
            raise ValueError("ManualPayoutService.reject needs a ledger")
        model = self.records.model
        async with transaction(self._session):
            result = await self._session.execute(
                update(model)
                .where(
                    model.id == row_id,
                    model.status == ManualPayoutStatusEnum.PENDING.value,
                )
                .values(
                    status=ManualPayoutStatusEnum.REJECTED.value,
                    rejection_reason=text,
                    rejected_at=utcnow(),
                    rejected_by=actor,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                await self._refuse(row_id)
            row = await self._load(row_id)
            await self.ledger.credit(
                row.user_id,
                row.amount_cents,
                kind=WalletEntryKindEnum.WITHDRAW_REFUND.value,
                description=self._refund_description,
                reference_type=PAYOUT_REFERENCE_TYPE,
                reference_id=row.correlation_id,
            )
            if self.hooks.on_rejected is not None:
                await self.hooks.on_rejected(self._session, row)
        return row


__all__: list[str] = [
    "DEFAULT_REFUND_DESCRIPTION",
    "MAX_REJECTION_REASON_LENGTH",
    "ManualPayoutHook",
    "ManualPayoutHooks",
    "ManualPayoutService",
]
